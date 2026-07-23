"""Safe cancellation and Linux USB reset support for the selected cutter port."""

from __future__ import annotations

import os
import time

import serial
import serial.tools.list_ports

import globals


USBDEVFS_RESET = 0x5514


class SerialResetError(RuntimeError):
    """The selected serial adapter could not be safely reset."""


def cancel_active_write() -> bool:
    """Interrupt a pending pyserial write without closing it from another thread."""
    with globals.active_serial_lock:
        active = globals.active_serial
        if active is None:
            return False
        cancel = getattr(active, "cancel_write", None)
        if callable(cancel):
            try:
                cancel()
            except (OSError, serial.SerialException):
                pass
        reset_output = getattr(active, "reset_output_buffer", None)
        if callable(reset_output):
            try:
                reset_output()
            except (OSError, serial.SerialException):
                pass
        return True


def _detected_usb_ports():
    detected = {}
    for port in serial.tools.list_ports.comports():
        device = os.path.realpath(port.device)
        if getattr(port, "vid", None) is not None and getattr(port, "pid", None) is not None:
            detected[device] = port
    return detected


def validate_usb_serial_port(selected_port: str, configured_port: str) -> tuple[str, str]:
    """Resolve a stable by-id path and prove that it is a detected USB serial port."""
    if not selected_port or selected_port != configured_port:
        raise SerialResetError("Reset is limited to the configured cutter port.")
    by_id_root = os.path.realpath("/dev/serial/by-id")
    absolute = os.path.abspath(selected_port)
    if os.path.dirname(absolute) != by_id_root:
        raise SerialResetError("Configure a stable /dev/serial/by-id port before resetting USB.")
    if not os.path.islink(absolute) or not os.path.exists(absolute):
        raise SerialResetError("The configured USB serial adapter is not currently connected.")
    tty_device = os.path.realpath(absolute)
    detected = _detected_usb_ports()
    if tty_device not in detected:
        raise SerialResetError("The configured port is not an enumerated USB serial adapter.")
    return absolute, tty_device


def _usb_device_node(tty_device: str) -> str:
    """Find the exact /dev/bus/usb node owning a ttyUSB/ttyACM device."""
    tty_name = os.path.basename(tty_device)
    node = os.path.realpath(os.path.join("/sys/class/tty", tty_name, "device"))
    if not os.path.exists(node):
        raise SerialResetError("The serial adapter has no Linux sysfs device entry.")
    while node and node != "/":
        vendor = os.path.join(node, "idVendor")
        product = os.path.join(node, "idProduct")
        busnum = os.path.join(node, "busnum")
        devnum = os.path.join(node, "devnum")
        if all(os.path.isfile(path) for path in (vendor, product, busnum, devnum)):
            with open(busnum, "r", encoding="ascii") as source:
                bus = int(source.read().strip())
            with open(devnum, "r", encoding="ascii") as source:
                device = int(source.read().strip())
            usb_node = f"/dev/bus/usb/{bus:03d}/{device:03d}"
            if not os.path.exists(usb_node):
                raise SerialResetError("The USB device node disappeared before it could be reset.")
            return usb_node
        node = os.path.dirname(node)
    raise SerialResetError("The selected serial port is not backed by a resettable USB device.")


def reset_usb_serial(
    selected_port: str,
    configured_port: str,
    baudrate: int = 9600,
    timeout: float = 10.0,
    progress=None,
) -> list[dict]:
    """Reset and probe exactly one validated, configured USB serial adapter."""
    phases: list[dict] = []

    def report(phase: str, message: str, status: str = "working"):
        item = {"phase": phase, "message": message, "status": status}
        phases.append(item)
        if progress:
            progress(item)

    if os.name != "posix":
        raise SerialResetError("USB reset is only available on the Raspberry Pi host.")

    stable_port, tty_device = validate_usb_serial_port(selected_port, configured_port)
    report("closing", "Waiting for the serial sender to release the port")
    if not globals.serial_port_closed.wait(timeout=timeout):
        raise SerialResetError("Timed out waiting for the active serial write to stop.")

    with globals.serial_operation_lock:
        stable_port, tty_device = validate_usb_serial_port(selected_port, configured_port)
        usb_node = _usb_device_node(tty_device)
        report("resetting", "Resetting the selected USB serial adapter")
        try:
            import fcntl

            descriptor = os.open(usb_node, os.O_WRONLY)
            try:
                fcntl.ioctl(descriptor, USBDEVFS_RESET, 0)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise SerialResetError("The USB adapter reset failed: " + str(exc)) from exc

        report("waiting", "Waiting for the stable serial port to return")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(stable_port):
                try:
                    validate_usb_serial_port(stable_port, configured_port)
                    break
                except SerialResetError:
                    pass
            time.sleep(0.25)
        else:
            raise SerialResetError("The USB adapter did not reappear after reset.")

        report("probing", "Opening the cutter port at 9600 8N1 RTS/CTS")
        try:
            probe = serial.Serial(
                port=stable_port,
                baudrate=int(baudrate),
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                rtscts=True,
                timeout=1.0,
                write_timeout=2.0,
            )
            probe.close()
        except (OSError, serial.SerialException) as exc:
            raise SerialResetError("The adapter returned but the serial probe failed: " + str(exc)) from exc

    report("ready", "USB serial connection is ready", "complete")
    return phases

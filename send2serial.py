#!/usr/bin/python

# Based on vogelchr/hp7475a-send (https://github.com/vogelchr/hp7475a-send)

import sys
import time
import os
import serial
import serial.tools.list_ports
from serial import SerialException
from flask_socketio import SocketIO, emit

# import PySimpleGUI as sg
import notification
import globals

# answer to <ESC>.O Output Extended Status Information [Manual: 10-42]
EXT_STATUS_BUF_EMPTY = 0x08  # buffer empty
EXT_STATUS_VIEW = 0x10  # "view" button has been pressed, plotting suspended
EXT_STATUS_LEVER = 0x20  # paper lever raised, potting suspended

ERRORS = {
    # our own code
    -1: 'Timeout',
    -2: 'Parse error of decimal return from plotter',
    # from the manual
    0: 'no error',
    10: 'overlapping output instructions',
    11: 'invalid byte after <ESC>.',
    12: 'invalid byte while parsing device control instruction',
    13: 'parameter out of range',
    14: 'too many parameters received',
    15: 'framing error, parity error or overrun',
    16: 'input buffer has overflowed'
}

# Chunk size used when streaming to the Creation PCut CT-1200 vinyl cutter.
# The cutter buffers commands internally; 256 bytes keeps latency manageable
# while leaving room for RTS/CTS to stall the sender when needed.
_CREATION_CHUNK_SIZE = 256

# Opening the PCut's built-in FTDI port can make the controller initialise.
# Give it time to return online before sending HPGL.  An additional pause after
# IN prevents the remainder of a job being queued while that command is being
# processed.
_CREATION_PORT_SETTLE_SECONDS = 1.5
_CREATION_INIT_SETTLE_SECONDS = 0.75
_CREATION_INTER_COMMAND_SECONDS = 0.002
_CREATION_BITS_PER_BYTE = 10  # start + 8 data + stop at 8N1
_CREATION_BAUDRATE = 9600
_CREATION_BACKPRESSURE_POLL_SECONDS = 0.01
_CREATION_STALL_TIMEOUT_SECONDS = 30.0


class _CreationCancelled(Exception):
    """Internal control flow used to stop a PCut transfer immediately."""


def _creation_cancelled(cancel_check=None):
    return not globals.printing or bool(cancel_check and cancel_check())


def _creation_emit_phase(socketio, phase, message):
    payload = {"phase": phase, "message": message}
    socketio.emit("plotter_phase", payload)
    socketio.emit("status_log", {"data": message})


def _creation_transport_mode():
    """Return the selected temporary PCut transport implementation."""
    mode = os.environ.get("PCP_PCUT_TRANSPORT", "legacy").strip().lower()
    return mode if mode in ("legacy", "buffered") else "legacy"


def _creation_command_fragments(command):
    """Yield PCut-safe writes for one semicolon-terminated HPGL command.

    vpype and PCP may compact an entire contour into one ``PD`` instruction.
    Some PCut controllers have a much smaller command parser buffer than their
    advertised job memory.  Splitting coordinate lists into equivalent
    one-coordinate commands preserves geometry and order while bounding every
    instruction.
    """
    stripped = command.strip()
    if not stripped:
        return

    terminator = b';' if stripped.endswith(b';') else b''
    body = stripped[:-1] if terminator else stripped
    opcode = body[:2].upper()
    parameters = body[2:]
    if opcode in (b'PU', b'PD') and parameters:
        values = [value.strip() for value in parameters.split(b',')]
        if (
            len(values) >= 2
            and len(values) % 2 == 0
            and all(value.lstrip(b'+-').isdigit() for value in values)
        ):
            for index in range(0, len(values), 2):
                yield opcode + values[index] + b',' + values[index + 1] + b';'
            return

    for index in range(0, len(stripped), _CREATION_CHUNK_SIZE):
        yield stripped[index:index + _CREATION_CHUNK_SIZE]


def _creation_hpgl_commands(hpgl):
    """Yield complete HPGL commands without loading a large job into memory."""
    pending = b''
    while True:
        data = hpgl.read(4096)
        if not data:
            break
        pending += data
        while b';' in pending:
            command, pending = pending.split(b';', 1)
            yield command + b';'
    if pending:
        yield pending

class HPGLError(Exception):
    def __init__(self, n, cause=None):
        self.errcode = n
        if cause:
            self.causes = [cause]
        else:
            self.causes = []

    def add_cause(self, cause):
        self.causes.append(cause)

    def __repr__(self):
        if type(self.errcode) is str:
            errstr = self.errcode
        else:
            errstr = f'Error {self.errcode}: {ERRORS.get(self.errcode)}'

        if self.causes:
            cstr = ', '.join(self.causes)
            return f'HPGLError: {errstr}, caused by {cstr}'
        return f'HPGLError: {errstr}'

    def __str__(self):
        return repr(self)


# read decimal number, followed by carriage return from plotter
def read_answer(tty):
    buf = bytearray()
    while True:
        c = tty.read(1)
        if not c:  # timeout
            raise HPGLError(-1)  # timeout
        if c == b'\r':
            break
        buf += c
    try:
        return int(buf)
    except ValueError as e:
        print(repr(e))
        raise HPGLError(-2)


def chk_error(tty):
    tty.write(b'\033.E')
    ret = None
    try:
        ret = read_answer(tty)
    except HPGLError as e:
        e.add_cause('ESC.E (Output extended error code).')
        raise e
    if ret:
        raise HPGLError(ret)


def plotter_cmd(tty, cmd, get_answer=False):
    tty.write(cmd)
    try:
        if get_answer:
            answ = read_answer(tty)
        chk_error(tty)
        if get_answer:
            return answ
    except HPGLError as e:
        e.add_cause(f'after sending {repr(cmd)[1:]}')
        raise e

def baud_rate_test(serial_port, packet = b'IN;OI;'):
    ser = serial.Serial(serial_port)
    ser.timeout = 0.5
    for baudrate in ser.BAUDRATES:
        if 75 <= baudrate <= 19200:
            ser.baudrate = baudrate
            ser.write(packet)
            resp = ser.readall()
            if resp == packet:
                return baudrate
    return 'Unknown'

def listComPorts(extra_port=None):
    """Return a dict with key *content* listing available serial ports.

    *extra_port* — if supplied and not already in the detected list (e.g. a
    stable ``/dev/serial/by-id/...`` symlink configured on a Raspberry Pi) it
    is prepended so the UI can always select it even before it is enumerated by
    the OS.
    """
    ports = dict(name='ports', content=[])
    for i in serial.tools.list_ports.comports():
        ports['content'].append(str(i).split(" ")[0])
    if extra_port and extra_port not in ports['content']:
        ports['content'].insert(0, extra_port)
    return ports


def _send_creation_1200(
    socketio, hpglfile, port, cancel_check=None, diagnostics_callback=None
):
    """Own the serial lock and select the temporary PCut transport mode."""
    mode = _creation_transport_mode()
    started = time.monotonic()
    diagnostics = {
        "mode": mode,
        "baudrate": _CREATION_BAUDRATE,
        "bytes": 0,
        "duration_seconds": 0.0,
        "effective_bytes_per_second": 0.0,
        "partial_writes": 0,
        "cts_low_seconds": 0.0,
        "stall_count": 0,
        "drain_seconds": 0.0,
        "cancelled": False,
        "phase": "opening",
    }
    try:
        diagnostics["source_bytes"] = os.path.getsize(hpglfile)
        diagnostics["estimated_wire_seconds"] = round(
            diagnostics["source_bytes"] * _CREATION_BITS_PER_BYTE
            / _CREATION_BAUDRATE,
            3,
        )
    except OSError:
        diagnostics["source_bytes"] = 0
        diagnostics["estimated_wire_seconds"] = 0.0
    globals.serial_port_closed.clear()
    try:
        with globals.serial_operation_lock:
            if mode == "buffered":
                result = _send_creation_1200_buffered_unlocked(
                    socketio, hpglfile, port, cancel_check, diagnostics
                )
            else:
                _creation_emit_phase(
                    socketio,
                    "transmitting",
                    "Using the temporary legacy paced PCut transport.",
                )
                result = _send_creation_1200_legacy_unlocked(
                    socketio, hpglfile, port, cancel_check
                )
                diagnostics["phase"] = "transferred" if result else "stopped"
                if result:
                    diagnostics["bytes"] = diagnostics["source_bytes"]
                    _creation_emit_phase(
                        socketio,
                        "transferred",
                        "Legacy transfer complete. The cutter may still be executing cached commands.",
                    )
                elif not _creation_cancelled(cancel_check):
                    diagnostics["error"] = "Legacy PCut transmission failed."
            diagnostics["cancelled"] = _creation_cancelled(cancel_check)
            return result
    finally:
        diagnostics["duration_seconds"] = round(time.monotonic() - started, 3)
        if diagnostics["duration_seconds"] > 0:
            diagnostics["effective_bytes_per_second"] = round(
                diagnostics["bytes"] / diagnostics["duration_seconds"], 2
            )
        if diagnostics_callback:
            try:
                diagnostics_callback(dict(diagnostics))
            except Exception as exc:
                print("Could not persist PCut transport diagnostics:", repr(exc))
        globals.serial_port_closed.set()


def _send_creation_1200_legacy_unlocked(socketio, hpglfile, port, cancel_check=None):
    """Stream an HPGL file to a Creation PCut CT-1200 vinyl cutter.

    Serial profile (verified against Inkcut's Creation 1200 driver):
      9600 baud · 8 data bits · no parity · 1 stop bit · RTS/CTS flow control

    IMPORTANT: No HP-specific ESC status/error/buffer/abort/init commands are
    ever emitted here.  Only the raw HPGL payload is sent, in bounded chunks,
    with the cancellation flag checked between each chunk.

    Returns True on normal completion (EOF), False on error or cancellation.
    """
    input_bytes = None
    try:
        ss = os.stat(hpglfile)
        if ss.st_size != 0:
            input_bytes = ss.st_size
    except Exception as e:
        socketio.emit('error', {'data': 'Error stat\'ing file: ' + str(e)})
        return False

    try:
        tty = serial.Serial(
            port=port,
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            rtscts=True,
            timeout=10.0,
            write_timeout=30.0,
        )
    except SerialException as e:
        socketio.emit('error', {'data': repr(e)})
        print(repr(e))
        return False

    socketio.emit('status_log', {'data': f'Opened {port} at 9600 8N1 RTS/CTS'})

    with globals.active_serial_lock:
        globals.active_serial = tty

    total_bytes_written = 0
    hpgl = None
    try:
        hpgl = open(hpglfile, 'rb')

        socketio.emit(
            'status_log',
            {'data': 'Waiting for the PCut controller to finish initialising.'},
        )
        time.sleep(_CREATION_PORT_SETTLE_SECONDS)

        for command in _creation_hpgl_commands(hpgl):
            if not globals.printing or (cancel_check and cancel_check()):
                return False

            for fragment in _creation_command_fragments(command):
                if not globals.printing or (cancel_check and cancel_check()):
                    return False
                try:
                    tty.write(fragment)
                except SerialException as e:
                    socketio.emit('error', {'data': 'Serial write error: ' + repr(e)})
                    print(repr(e))
                    return False
                # Pace writes at (slightly below) the actual 9600 8N1 wire
                # rate.  Unlike tcdrain()/flush(), this remains immediately
                # cancellable even if the cutter drops CTS.
                time.sleep(
                    _CREATION_INTER_COMMAND_SECONDS
                    + len(fragment) * _CREATION_BITS_PER_BYTE / 9600.0
                )

            total_bytes_written += len(command)
            if command.strip().upper() == b'IN;':
                socketio.emit(
                    'status_log',
                    {'data': 'PCut initialised; waiting before movement commands.'},
                )
                time.sleep(_CREATION_INIT_SETTLE_SECONDS)

            if input_bytes:
                percent = min(100.0, 100.0 * total_bytes_written / input_bytes)
                globals.print_progress = percent
                print(f'{percent:.2f}%, {total_bytes_written} source bytes sent.')
                socketio.emit(
                    'status_log',
                    {'data': f'{percent:.2f}%, {total_bytes_written} source bytes sent.'},
                )
                socketio.emit('print_progress', {'data': f'{percent:.2f}'})

        print('*** EOF reached, exiting.')
        notification.telegram_sendNotification('*** EOF reached, exiting.')
        socketio.emit('status_log', {'data': '*** EOF reached, exiting.'})
        return True

    finally:
        with globals.active_serial_lock:
            if globals.active_serial is tty:
                globals.active_serial = None
        if hpgl is not None:
            try:
                hpgl.close()
            except Exception:
                pass
        try:
            tty.close()
        except Exception:
            pass


def _creation_write_buffered(
    tty, data, cancel_check, diagnostics, source_progress=None
):
    """Write every byte with cancellable RTS/CTS backpressure handling."""
    offset = 0
    no_progress_since = time.monotonic()
    in_stall = False
    cts_low_since = None
    while offset < len(data):
        if _creation_cancelled(cancel_check):
            raise _CreationCancelled()
        remaining = data[offset:]
        try:
            written = tty.write(remaining)
        except serial.SerialTimeoutException:
            written = 0
        if written is None:
            # A few serial test doubles model successful write() with None.
            written = len(remaining)
        if not isinstance(written, int) or written < 0 or written > len(remaining):
            raise SerialException("Serial write returned an invalid byte count.")
        if written:
            if written < len(remaining):
                diagnostics["partial_writes"] += 1
            offset += written
            diagnostics["bytes"] += written
            no_progress_since = time.monotonic()
            in_stall = False
            if cts_low_since is not None:
                diagnostics["cts_low_seconds"] += time.monotonic() - cts_low_since
                cts_low_since = None
            if source_progress:
                source_progress(written)
            continue

        now = time.monotonic()
        if not in_stall:
            diagnostics["stall_count"] += 1
            in_stall = True
        try:
            cts = bool(tty.cts)
        except (AttributeError, OSError, SerialException):
            cts = True
        if not cts and cts_low_since is None:
            cts_low_since = now
        if now - no_progress_since >= _CREATION_STALL_TIMEOUT_SECONDS:
            try:
                waiting = int(tty.out_waiting)
            except (AttributeError, OSError, SerialException, TypeError, ValueError):
                waiting = -1
            raise SerialException(
                "PCut stopped accepting serial data for 30 seconds "
                f"(CTS={'high' if cts else 'low'}, output buffer={waiting})."
            )
        time.sleep(_CREATION_BACKPRESSURE_POLL_SECONDS)

    if cts_low_since is not None:
        diagnostics["cts_low_seconds"] += time.monotonic() - cts_low_since


def _creation_source_chunks(hpgl):
    """Yield the stored HPGL unchanged, omitting only a leading IN preamble."""
    prefix = hpgl.read(_CREATION_CHUNK_SIZE)
    upper = prefix.upper()
    first_non_space = len(prefix) - len(prefix.lstrip())
    if upper[first_non_space:first_non_space + 3] == b"IN;":
        init_start = first_non_space
        init_end = init_start + 3
        prefix = prefix[:init_start] + prefix[init_end:]
    if prefix:
        yield prefix
    while True:
        chunk = hpgl.read(_CREATION_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


def _send_creation_1200_buffered_unlocked(
    socketio, hpglfile, port, cancel_check, diagnostics
):
    """Continuously stream exact HPGL while Linux RTS/CTS controls backpressure."""
    try:
        input_bytes = os.path.getsize(hpglfile)
    except OSError as exc:
        socketio.emit("error", {"data": "Error stat'ing file: " + str(exc)})
        diagnostics["phase"] = "error"
        return False
    diagnostics["source_bytes"] = input_bytes
    diagnostics["estimated_wire_seconds"] = round(
        input_bytes * _CREATION_BITS_PER_BYTE / _CREATION_BAUDRATE, 3
    )

    tty = None
    hpgl = None
    source_bytes_handled = 0

    def progress(count):
        nonlocal source_bytes_handled
        source_bytes_handled += count
        if not input_bytes:
            return
        percent = min(100.0, 100.0 * source_bytes_handled / input_bytes)
        globals.print_progress = percent
        socketio.emit("print_progress", {"data": f"{percent:.2f}"})

    try:
        _creation_emit_phase(socketio, "opening", f"Opening {port} at 9600 8N1 RTS/CTS.")
        tty = serial.Serial(
            port=port,
            baudrate=_CREATION_BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            rtscts=True,
            xonxoff=False,
            dsrdtr=False,
            timeout=0,
            write_timeout=0,
            exclusive=True,
        )
        with globals.active_serial_lock:
            globals.active_serial = tty

        diagnostics["phase"] = "settling"
        _creation_emit_phase(
            socketio, "settling", "Waiting for the PCut controller to finish initialising."
        )
        time.sleep(_CREATION_PORT_SETTLE_SECONDS)
        if _creation_cancelled(cancel_check):
            raise _CreationCancelled()

        diagnostics["phase"] = "initializing"
        _creation_emit_phase(socketio, "initializing", "Initialising the PCut controller.")
        _creation_write_buffered(tty, b"IN;", cancel_check, diagnostics)
        time.sleep(_CREATION_INIT_SETTLE_SECONDS)

        hpgl = open(hpglfile, "rb")
        # The generated files begin with IN;. It was just sent once as the
        # transport preamble, so count those source bytes as handled.
        prefix_position = hpgl.tell()
        prefix = hpgl.read(_CREATION_CHUNK_SIZE)
        first_non_space = len(prefix) - len(prefix.lstrip())
        has_init = prefix.upper()[first_non_space:first_non_space + 3] == b"IN;"
        hpgl.seek(prefix_position)
        if has_init:
            source_bytes_handled = 3

        diagnostics["phase"] = "transmitting"
        _creation_emit_phase(socketio, "transmitting", "Transmitting the exact HPGL command stream.")
        for chunk in _creation_source_chunks(hpgl):
            _creation_write_buffered(
                tty, chunk, cancel_check, diagnostics, source_progress=progress
            )

        diagnostics["phase"] = "draining"
        _creation_emit_phase(
            socketio, "draining", "Waiting for the operating-system serial buffer to drain."
        )
        drain_started = time.monotonic()
        unchanged_since = drain_started
        last_waiting = None
        while True:
            if _creation_cancelled(cancel_check):
                raise _CreationCancelled()
            try:
                waiting = int(tty.out_waiting)
            except (AttributeError, OSError, SerialException, TypeError, ValueError):
                waiting = 0
            if waiting <= 0:
                break
            now = time.monotonic()
            if waiting != last_waiting:
                last_waiting = waiting
                unchanged_since = now
            elif now - unchanged_since >= _CREATION_STALL_TIMEOUT_SECONDS:
                try:
                    cts = bool(tty.cts)
                except (AttributeError, OSError, SerialException):
                    cts = True
                raise SerialException(
                    "PCut serial output did not drain for 30 seconds "
                    f"(CTS={'high' if cts else 'low'}, output buffer={waiting})."
                )
            time.sleep(_CREATION_BACKPRESSURE_POLL_SECONDS)
        diagnostics["drain_seconds"] = round(time.monotonic() - drain_started, 3)
        diagnostics["cts_low_seconds"] = round(diagnostics["cts_low_seconds"], 3)
        diagnostics["phase"] = "transferred"
        globals.print_progress = 100.0
        socketio.emit("print_progress", {"data": "100.00"})
        _creation_emit_phase(
            socketio,
            "transferred",
            "Transfer complete. The cutter may still be executing commands from its cache.",
        )
        return True
    except _CreationCancelled:
        diagnostics["cancelled"] = True
        diagnostics["phase"] = "cancelled"
        _creation_emit_phase(
            socketio,
            "cancelled",
            "Transmission cancelled. Commands already cached by the cutter may still finish.",
        )
        return False
    except (OSError, SerialException) as exc:
        diagnostics["phase"] = "error"
        diagnostics["error"] = str(exc)
        socketio.emit("error", {"data": "Serial write error: " + str(exc)})
        print(repr(exc))
        return False
    finally:
        with globals.active_serial_lock:
            if globals.active_serial is tty:
                globals.active_serial = None
        if hpgl is not None:
            try:
                hpgl.close()
            except Exception:
                pass
        if tty is not None:
            try:
                tty.close()
            except Exception:
                pass


def sendToPlotter(socketio, hpglfile, port = 'COM3', baud = 9600, plotter = '7475a',
                  cancel_check=None, diagnostics_callback=None):
    print(plotter)

    globals.printing = True
    input_bytes = None

    # ------------------------------------------------------------------ #
    # Creation PCut CT-1200 — payload-only path (no HP ESC commands)      #
    # ------------------------------------------------------------------ #
    if plotter == 'creation_1200':
        return _send_creation_1200(
            socketio, hpglfile, port, cancel_check, diagnostics_callback
        )

    # ------------------------------------------------------------------ #
    # Shared file-stat for HP / Graphtec paths                            #
    # ------------------------------------------------------------------ #
    try:
        ss = os.stat(hpglfile)
        if ss.st_size != 0:
            input_bytes = ss.st_size
    except Exception as e:
        print('Error stat\'ing file', hpglfile, str(e))
        socketio.emit('error', {'error': 'Error stat\'ing file'})

    hpgl = open(hpglfile, 'rb')

    if (plotter == 'mp4200'):
        try:
            tty = serial.Serial(port = port, baudrate = 9600, parity = serial.PARITY_NONE, stopbits = serial.STOPBITS_ONE, bytesize = serial.EIGHTBITS, xonxoff = True, timeout = 2.0)
        except SerialException as e:
            socketio.emit('error', {'error': repr(e)})
            print(repr(e))
            return False
    else:
        try:
            tty = serial.Serial(port, baudrate = 9600, timeout=2.0)
        except SerialException as e:
            socketio.emit('error', {'error': repr(e)})
            print(repr(e))
            return False

    # <ESC>.@<dec>;<dec>:
    #  1st parameter is buffer size 0..1024, optional
    #  2nd parameter is bit flags for operation mode
    #     0x01 : enable HW handhaking
    #     0x02 : ignored
    #     0x04 : monitor mode 1 if set, mode 0 if unset (for terminal)
    #     0x08 : 0: disable monitor mode, 1: enable monitor mode
    #     0x10 : 0: normal mode, 1: block mode
    try:
        plotter_cmd(tty, b'\033.@;0:')  # Plotter Configuration [Manual 10-27]
        plotter_cmd(tty, b'\033.Y')  # Plotter On [Manual 10-26]
        plotter_cmd(tty, b'\033.K')  # abort graphics
        plotter_cmd(tty, b'IN;')  # HPGL initialize
#        plotter_cmd(tty, b'\033.0')  # raise error
        # Output Buffer Size [Manual 10-36]
        bufsz = plotter_cmd(tty, b'\033.L', True)
    except HPGLError as e:
        print('*** Error initializing the plotter!')
        print(e)

        socketio.emit('error', {'data': '*** Error initializing the plotter!'})
        socketio.emit('error', {'data': str(e)})

        # sys.exit(1)
        return False

    print('Buffer size of plotter is', bufsz, 'bytes.')
    socketio.emit('status_log', {'data': 'Buffer size of plotter is ' + str(bufsz) + ' bytes.'})

    total_bytes_written = 0

    while globals.printing == True:
        status = plotter_cmd(tty, b'\033.O', True)
        if (status & (EXT_STATUS_VIEW | EXT_STATUS_LEVER)):
            print('*** Printer is viewing plot, pausing data.')
            socketio.emit('status_log', {'data': '*** Printer is viewing plot, pausing data.'})
            time.sleep(5.0)
            continue

        bufsz = plotter_cmd(tty, b'\033.B', True)
        if bufsz < 256:
            sys.stdout.flush()
            time.sleep(0.25)
            continue

        data = hpgl.read(bufsz - 128)
        bufsz_read = len(data)

        if bufsz_read == 0:
            print('*** EOF reached, exiting.')
            notification.telegram_sendNotification('*** EOF reached, exiting.')
            socketio.emit('status_log', {'data': '*** EOF reached, exiting.'})
            return True

        if input_bytes != None:
            percent = 100.0 * total_bytes_written/input_bytes
            globals.print_progress = percent
            print(f'{percent:.2f}%, {total_bytes_written} byte written.')
            socketio.emit('status_log', {'data': f'{percent:.2f}%, {total_bytes_written} byte written.'})
            socketio.emit('print_progress', {'data': f'{percent:.2f}'})

        else:
            print(f'{bufsz_read} byte added.')
            socketio.emit('status_log', {'data': f'{bufsz_read} byte added.'})

        tty.write(data)
        total_bytes_written += bufsz_read

    return False  # cancelled


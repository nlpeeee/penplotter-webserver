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


def _send_creation_1200(socketio, hpglfile, port, cancel_check=None):
    """Own the shared serial-operation lock for a complete PCut transfer."""
    globals.serial_port_closed.clear()
    try:
        with globals.serial_operation_lock:
            return _send_creation_1200_unlocked(socketio, hpglfile, port, cancel_check)
    finally:
        globals.serial_port_closed.set()


def _send_creation_1200_unlocked(socketio, hpglfile, port, cancel_check=None):
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


def sendToPlotter(socketio, hpglfile, port = 'COM3', baud = 9600, plotter = '7475a',
                  cancel_check=None):
    print(plotter)

    globals.printing = True
    input_bytes = None

    # ------------------------------------------------------------------ #
    # Creation PCut CT-1200 — payload-only path (no HP ESC commands)      #
    # ------------------------------------------------------------------ #
    if plotter == 'creation_1200':
        return _send_creation_1200(socketio, hpglfile, port, cancel_check)

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


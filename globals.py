import threading


def initialize():
    global printing
    printing = False

    # Signalled whenever a new job is enqueued so the worker wakes up promptly.
    global job_worker_event
    job_worker_event = threading.Event()

    global active_job_id
    active_job_id = None

    # Plot transmission and USB reset must never own the serial adapter at the
    # same time.  The closed event also lets reset wait without polling.
    global serial_operation_lock
    serial_operation_lock = threading.RLock()

    global serial_port_closed
    serial_port_closed = threading.Event()
    serial_port_closed.set()

    global active_serial_lock
    active_serial_lock = threading.Lock()

    global active_serial
    active_serial = None

    global reset_in_progress
    reset_in_progress = False


# Keep module users (including Flask's test client) in a valid initial state.
initialize()

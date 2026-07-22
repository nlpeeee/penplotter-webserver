import threading


def initialize():
    global printing
    printing = False

    # Signalled whenever a new job is enqueued so the worker wakes up promptly.
    global job_worker_event
    job_worker_event = threading.Event()

    global active_job_id
    active_job_id = None

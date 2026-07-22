"""
SQLite-backed single-job queue and job history for WebPlot.

Only one job runs at a time; the background worker in main.py owns the serial
port for the duration of each job, preventing concurrent writes.
"""

import sqlite3
import os

DB_PATH = os.environ.get('WEBPLOT_DB', 'jobs.db')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file        TEXT    NOT NULL,
    port        TEXT    NOT NULL,
    baudrate    TEXT    NOT NULL DEFAULT '9600',
    device      TEXT    NOT NULL DEFAULT '7475a',
    tasmota     TEXT    NOT NULL DEFAULT 'off',
    status      TEXT    NOT NULL DEFAULT 'queued',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    error       TEXT
);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the jobs table if it does not already exist."""
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def enqueue_job(file, port, baudrate='9600', device='7475a', tasmota='off'):
    """Insert a new job in *queued* state and return its row id."""
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO jobs (file, port, baudrate, device, tasmota, status) "
            "VALUES (?, ?, ?, ?, ?, 'queued')",
            (file, port, str(baudrate), device, tasmota),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_next_queued():
    """Return the oldest queued job as a dict, or None if the queue is empty."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_job_status(job_id, status, error=None):
    """Advance a job to *status* and record timestamps / error text."""
    conn = _connect()
    try:
        if status == 'transmitting':
            conn.execute(
                "UPDATE jobs SET status=?, started_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, job_id),
            )
        elif status in ('completed', 'failed', 'cancelled'):
            conn.execute(
                "UPDATE jobs SET status=?, finished_at=CURRENT_TIMESTAMP, error=? WHERE id=?",
                (status, error, job_id),
            )
        else:
            conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
        conn.commit()
    finally:
        conn.close()


def get_recent_jobs(limit=20):
    """Return the most recent *limit* jobs (newest first) as a list of dicts."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def cancel_queued_job(job_id):
    """Cancel a job that is still in *queued* state.  Returns True if changed."""
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE jobs SET status='cancelled', finished_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status='queued'",
            (job_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()

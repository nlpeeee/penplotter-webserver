"""
SQLite-backed single-job queue and job history for WebPlot.

Only one job runs at a time; the background worker in main.py owns the serial
port for the duration of each job, preventing concurrent writes.
"""

import sqlite3
import os
import tempfile
import uuid

DB_PATH = os.environ.get('WEBPLOT_DB', 'jobs.db')
SPOOL_PATH = os.environ.get('PCP_SPOOL_DIR', 'spool')

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
    error       TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db():
    """Create the jobs table and fail interrupted transmissions safely."""
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "cancel_requested" not in columns:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
            )
        migrations = {
            "display_file": "ALTER TABLE jobs ADD COLUMN display_file TEXT",
            "project_id": "ALTER TABLE jobs ADD COLUMN project_id TEXT",
            "project_revision": "ALTER TABLE jobs ADD COLUMN project_revision INTEGER",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)
        conn.execute(
            """
            UPDATE jobs
            SET status='failed', finished_at=CURRENT_TIMESTAMP,
                error='PCP restarted during transmission'
            WHERE status='transmitting'
            """
        )
        conn.commit()
    finally:
        conn.close()


def snapshot_for_queue(source_file):
    """Atomically preserve the exact queued bytes independently of their source."""
    if not os.path.isfile(source_file):
        raise FileNotFoundError("The selected HPGL file does not exist.")
    os.makedirs(SPOOL_PATH, exist_ok=True)
    extension = os.path.splitext(source_file)[1].lower() or ".hpgl"
    destination = os.path.abspath(os.path.join(SPOOL_PATH, str(uuid.uuid4()) + extension))
    temporary = tempfile.NamedTemporaryFile(
        prefix=".pcp-spool-", suffix=".tmp",
        dir=os.path.abspath(SPOOL_PATH), delete=False,
    )
    try:
        with open(source_file, "rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                temporary.write(chunk)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temporary.name, destination)
        return destination
    finally:
        if not temporary.closed:
            temporary.close()
        if os.path.exists(temporary.name):
            os.unlink(temporary.name)


def enqueue_job(
    file, port, baudrate='9600', device='7475a', tasmota='off',
    display_file=None, project_id=None, project_revision=None,
):
    """Insert a new job in *queued* state and return its row id."""
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO jobs (
                file, display_file, port, baudrate, device, tasmota,
                project_id, project_revision, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued')
            """,
            (
                file, display_file or os.path.basename(file), port, str(baudrate),
                device, tasmota, project_id, project_revision,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def claim_next_queued():
    """Atomically claim the oldest queued job, or return None when idle."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        claimed = conn.execute(
            """
            UPDATE jobs
            SET status='transmitting', started_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='queued' AND cancel_requested=0
            """,
            (row["id"],),
        )
        if claimed.rowcount != 1:
            conn.commit()
            return None
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        conn.commit()
        return dict(job)
    finally:
        conn.close()


def update_job_status(job_id, status, error=None):
    """Finalize a transmitting job without reviving a cancellation."""
    conn = _connect()
    try:
        if status in ('completed', 'failed', 'cancelled'):
            conn.execute(
                """
                UPDATE jobs
                SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE ? END,
                    finished_at=CURRENT_TIMESTAMP, error=?
                WHERE id=? AND status='transmitting'
                """,
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


def get_queue_count():
    """Return the complete queued-job count, independent of history paging."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM jobs WHERE status='queued'"
        ).fetchone()
        return int(row["count"])
    finally:
        conn.close()


def request_cancel(job_id):
    """Cancel a queued job or request cancellation of a transmitting job."""
    conn = _connect()
    try:
        cur = conn.execute(
            """
            UPDATE jobs
            SET cancel_requested=1,
                status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,
                finished_at=CASE WHEN status='queued' THEN CURRENT_TIMESTAMP ELSE finished_at END
            WHERE id=? AND status IN ('queued', 'transmitting')
            """,
            (job_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def is_cancel_requested(job_id):
    """Return whether the worker should stop sending additional bytes."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        return bool(row and row["cancel_requested"])
    finally:
        conn.close()

"""
Focused tests for the Creation PCut CT-1200 transport profile and the
SQLite job queue.

Run with:  pytest tests/test_creation_1200.py -v
"""

import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch, call

# Make sure the repo root is on the path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import globals


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_socketio_mock():
    sio = MagicMock()
    sio.emit = MagicMock()
    return sio


def _hpgl_file(content=b'IN;SP1;PU0,0;PD100,100;PU;SP0;'):
    """Write HPGL bytes to a temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix='.hpgl')
    os.write(fd, content)
    os.close(fd)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# PCut CT-1200 serial settings
# ─────────────────────────────────────────────────────────────────────────────

class TestCreation1200SerialSettings(unittest.TestCase):
    """Verify that _send_creation_1200 opens the port with the correct
    9600 / 8N1 / RTS-CTS parameters and never emits HP ESC commands."""

    def setUp(self):
        globals.initialize()
        globals.printing = True  # sendToPlotter sets this; we pre-set it here
        self.sio = _make_socketio_mock()
        self.hpgl_path = _hpgl_file()

    def tearDown(self):
        globals.printing = False
        if os.path.exists(self.hpgl_path):
            os.remove(self.hpgl_path)

    @patch('serial.Serial')
    def test_serial_opened_with_correct_settings(self, mock_serial_cls):
        """Port must be opened at 9600 baud, EIGHTBITS, PARITY_NONE,
        STOPBITS_ONE, rtscts=True."""
        import serial as ser_mod
        mock_tty = MagicMock()
        mock_tty.write = MagicMock()
        mock_serial_cls.return_value = mock_tty

        import send2serial
        send2serial._send_creation_1200(self.sio, self.hpgl_path, '/dev/ttyFAKE')

        mock_serial_cls.assert_called_once()
        kwargs = mock_serial_cls.call_args.kwargs
        self.assertEqual(kwargs.get('baudrate'), 9600)
        self.assertEqual(kwargs.get('bytesize'), ser_mod.EIGHTBITS)
        self.assertEqual(kwargs.get('parity'), ser_mod.PARITY_NONE)
        self.assertEqual(kwargs.get('stopbits'), ser_mod.STOPBITS_ONE)
        self.assertTrue(kwargs.get('rtscts'), 'rtscts must be True for CT-1200')

    @patch('serial.Serial')
    def test_no_hp_esc_commands_sent(self, mock_serial_cls):
        """The CT-1200 path must NEVER write any HP ESC (0x1B) bytes."""
        mock_tty = MagicMock()
        written_chunks = []

        def capture_write(data):
            written_chunks.append(data)

        mock_tty.write.side_effect = capture_write
        mock_serial_cls.return_value = mock_tty

        import send2serial
        send2serial._send_creation_1200(self.sio, self.hpgl_path, '/dev/ttyFAKE')

        all_written = b''.join(written_chunks)
        self.assertNotIn(b'\x1b', all_written,
                         'ESC byte (HP-specific command prefix) must never be sent to CT-1200')

    @patch('serial.Serial')
    def test_payload_is_sent_in_chunks(self, mock_serial_cls):
        """Data is delivered in ≤ CHUNK_SIZE pieces, not in a single write."""
        import send2serial
        chunk_size = send2serial._CREATION_CHUNK_SIZE
        payload = b'X' * (chunk_size * 3 + 50)  # larger than one chunk
        path = _hpgl_file(payload)

        mock_tty = MagicMock()
        write_sizes = []
        mock_tty.write.side_effect = lambda d: write_sizes.append(len(d))
        mock_serial_cls.return_value = mock_tty

        try:
            send2serial._send_creation_1200(self.sio, path, '/dev/ttyFAKE')
        finally:
            os.remove(path)

        self.assertGreater(len(write_sizes), 1,
                           'Large payload must be split into multiple writes')
        for size in write_sizes:
            self.assertLessEqual(size, chunk_size,
                                 f'Each write must be ≤ {chunk_size} bytes')

    @patch('serial.Serial')
    def test_cancellation_stops_transmission(self, mock_serial_cls):
        """Setting globals.printing = False mid-stream must stop further writes."""
        import send2serial
        chunk_size = send2serial._CREATION_CHUNK_SIZE
        # 10 chunks worth of data
        payload = b'P' * (chunk_size * 10)
        path = _hpgl_file(payload)

        mock_tty = MagicMock()
        write_count = [0]

        def cancel_after_first(data):
            write_count[0] += 1
            if write_count[0] >= 2:
                globals.printing = False  # simulate /stop_plot

        mock_tty.write.side_effect = cancel_after_first
        mock_serial_cls.return_value = mock_tty

        try:
            result = send2serial._send_creation_1200(self.sio, path, '/dev/ttyFAKE')
        finally:
            os.remove(path)

        self.assertFalse(result, 'Should return False on cancellation')
        self.assertLess(write_count[0], 10,
                        'Cancellation must stop transmission before full payload')

    @patch('serial.Serial')
    def test_serial_port_closed_on_completion(self, mock_serial_cls):
        """The serial port must be closed even on normal completion."""
        mock_tty = MagicMock()
        mock_serial_cls.return_value = mock_tty

        import send2serial
        send2serial._send_creation_1200(self.sio, self.hpgl_path, '/dev/ttyFAKE')

        mock_tty.close.assert_called_once()

    @patch('serial.Serial')
    def test_serial_port_closed_on_serial_error(self, mock_serial_cls):
        """The serial port must be closed even when a write raises SerialException."""
        import serial as ser_mod
        mock_tty = MagicMock()
        mock_tty.write.side_effect = ser_mod.SerialException('write failed')
        mock_serial_cls.return_value = mock_tty

        import send2serial
        result = send2serial._send_creation_1200(self.sio, self.hpgl_path, '/dev/ttyFAKE')

        self.assertFalse(result)
        mock_tty.close.assert_called_once()
        # Error must be surfaced via socket
        error_calls = [str(c) for c in self.sio.emit.call_args_list]
        self.assertTrue(any('error' in c for c in error_calls),
                        'Serial error must be emitted via socket')

    @patch('serial.Serial')
    def test_returns_true_on_eof(self, mock_serial_cls):
        """_send_creation_1200 must return True when the whole file is sent."""
        mock_tty = MagicMock()
        mock_serial_cls.return_value = mock_tty

        import send2serial
        result = send2serial._send_creation_1200(self.sio, self.hpgl_path, '/dev/ttyFAKE')

        self.assertTrue(result, 'Must return True on normal completion (EOF)')

    @patch('serial.Serial')
    def test_full_sendToPlotter_routes_to_creation_1200(self, mock_serial_cls):
        """sendToPlotter with plotter='creation_1200' must reach the payload-only path."""
        mock_tty = MagicMock()
        mock_serial_cls.return_value = mock_tty

        import send2serial
        # Patch _send_creation_1200 to verify it is called
        with patch.object(send2serial, '_send_creation_1200', return_value=True) as mock_c:
            send2serial.sendToPlotter(
                self.sio, self.hpgl_path, '/dev/ttyFAKE', 9600, 'creation_1200'
            )
            mock_c.assert_called_once()

    @patch('serial.Serial')
    def test_hp7475a_still_works(self, mock_serial_cls):
        """Ensure the HP 7475A path is NOT routed to creation_1200."""
        mock_tty = MagicMock()
        # Simulate HP plotter responding to ESC.L with buffer size
        mock_tty.read.return_value = b'\r'
        mock_serial_cls.return_value = mock_tty

        import send2serial
        with patch.object(send2serial, '_send_creation_1200') as mock_c:
            # Will fail trying to talk to a non-existent HP plotter,
            # but what matters is _send_creation_1200 is never called.
            try:
                send2serial.sendToPlotter(
                    self.sio, self.hpgl_path, '/dev/ttyFAKE', 9600, '7475a'
                )
            except Exception:
                pass
            mock_c.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Job queue serialization
# ─────────────────────────────────────────────────────────────────────────────

class TestJobQueue(unittest.TestCase):
    """Verify SQLite job queue CRUD and state transitions."""

    def setUp(self):
        # Use an in-memory / temp DB for each test
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        import jobqueue
        self._original_db = jobqueue.DB_PATH
        self._original_spool = jobqueue.SPOOL_PATH
        self._spool = tempfile.TemporaryDirectory()
        jobqueue.DB_PATH = self._tmp.name
        jobqueue.SPOOL_PATH = self._spool.name
        jobqueue.init_db()

    def tearDown(self):
        import jobqueue
        jobqueue.DB_PATH = self._original_db
        jobqueue.SPOOL_PATH = self._original_spool
        self._spool.cleanup()
        os.unlink(self._tmp.name)

    def test_enqueue_and_get_next(self):
        import jobqueue
        jid = jobqueue.enqueue_job('test.hpgl', '/dev/ttyFAKE', '9600', 'creation_1200')
        self.assertIsInstance(jid, int)
        job = jobqueue.claim_next_queued()
        self.assertIsNotNone(job)
        self.assertEqual(job['id'], jid)
        self.assertEqual(job['status'], 'transmitting')

    def test_fifo_order(self):
        import jobqueue
        id1 = jobqueue.enqueue_job('a.hpgl', '/dev/p', '9600', 'creation_1200')
        id2 = jobqueue.enqueue_job('b.hpgl', '/dev/p', '9600', 'creation_1200')
        job = jobqueue.claim_next_queued()
        self.assertEqual(job['id'], id1, 'Queue must be FIFO')

    def test_status_progression(self):
        import jobqueue
        jid = jobqueue.enqueue_job('x.hpgl', '/dev/p', '9600', 'creation_1200')
        jobqueue.claim_next_queued()
        jobqueue.update_job_status(jid, 'completed')
        jobs = jobqueue.get_recent_jobs(limit=1)
        self.assertEqual(jobs[0]['status'], 'completed')
        self.assertIsNotNone(jobs[0]['started_at'])
        self.assertIsNotNone(jobs[0]['finished_at'])

    def test_failed_stores_error(self):
        import jobqueue
        jid = jobqueue.enqueue_job('x.hpgl', '/dev/p', '9600', 'creation_1200')
        jobqueue.claim_next_queued()
        jobqueue.update_job_status(jid, 'failed', error='port busy')
        jobs = jobqueue.get_recent_jobs(limit=1)
        self.assertEqual(jobs[0]['status'], 'failed')
        self.assertEqual(jobs[0]['error'], 'port busy')

    def test_cancel_queued_job(self):
        import jobqueue
        jid = jobqueue.enqueue_job('x.hpgl', '/dev/p', '9600', 'creation_1200')
        changed = jobqueue.request_cancel(jid)
        self.assertTrue(changed)
        jobs = jobqueue.get_recent_jobs(limit=1)
        self.assertEqual(jobs[0]['status'], 'cancelled')

    def test_cancel_transmitting_job_is_requested(self):
        """A transmitting job records cancellation without changing the next job."""
        import jobqueue
        jid = jobqueue.enqueue_job('x.hpgl', '/dev/p', '9600', 'creation_1200')
        jobqueue.claim_next_queued()
        changed = jobqueue.request_cancel(jid)
        self.assertTrue(changed)
        jobs = jobqueue.get_recent_jobs(limit=1)
        self.assertEqual(jobs[0]['status'], 'transmitting')
        self.assertTrue(jobs[0]['cancel_requested'])

    def test_transmitting_job_not_returned_as_queued(self):
        """A job being processed must not be picked up by get_next_queued again."""
        import jobqueue
        jid = jobqueue.enqueue_job('x.hpgl', '/dev/p', '9600', 'creation_1200')
        jobqueue.claim_next_queued()
        job = jobqueue.claim_next_queued()
        self.assertIsNone(job)

    def test_cancelled_job_cannot_be_claimed(self):
        import jobqueue
        jid = jobqueue.enqueue_job('x.hpgl', '/dev/p', '9600', 'creation_1200')
        self.assertTrue(jobqueue.request_cancel(jid))
        self.assertIsNone(jobqueue.claim_next_queued())

    def test_restart_fails_interrupted_job(self):
        import jobqueue
        jid = jobqueue.enqueue_job('x.hpgl', '/dev/p', '9600', 'creation_1200')
        jobqueue.claim_next_queued()
        jobqueue.init_db()
        job = jobqueue.get_recent_jobs(limit=1)[0]
        self.assertEqual(job['id'], jid)
        self.assertEqual(job['status'], 'failed')
        self.assertIn('restarted', job['error'])

    def test_queue_serialization_thread_safety(self):
        """Rapid concurrent enqueues from multiple threads must all be recorded."""
        import jobqueue

        errors = []

        def enqueue_many():
            for i in range(10):
                try:
                    jobqueue.enqueue_job(f'{i}.hpgl', '/dev/p', '9600', 'creation_1200')
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=enqueue_many) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f'Thread-safety errors: {errors}')
        jobs = jobqueue.get_recent_jobs(limit=100)
        self.assertEqual(len(jobs), 50, 'All 50 jobs must be stored')

    def test_spooled_job_survives_source_deletion_and_links_project(self):
        import jobqueue
        source = tempfile.NamedTemporaryFile(suffix='.hpgl', delete=False)
        source.write(b'IN;PU0,0;PD40,40;')
        source.close()
        spooled = jobqueue.snapshot_for_queue(source.name)
        os.unlink(source.name)
        with open(spooled, 'rb') as snapshot:
            self.assertEqual(snapshot.read(), b'IN;PU0,0;PD40,40;')
        jid = jobqueue.enqueue_job(
            spooled, '/dev/p', display_file='Saved sign.hpgl',
            project_id='project-id', project_revision=3,
        )
        job = jobqueue.get_recent_jobs(limit=1)[0]
        self.assertEqual(job['id'], jid)
        self.assertEqual(job['display_file'], 'Saved sign.hpgl')
        self.assertEqual(job['project_id'], 'project-id')
        self.assertEqual(job['project_revision'], 3)


# ─────────────────────────────────────────────────────────────────────────────
# listComPorts with extra_port
# ─────────────────────────────────────────────────────────────────────────────

class TestListComPorts(unittest.TestCase):
    @patch('serial.tools.list_ports.comports', return_value=[])
    def test_extra_port_added_when_not_detected(self, _):
        import send2serial
        result = send2serial.listComPorts(extra_port='/dev/serial/by-id/fake-port')
        self.assertIn('/dev/serial/by-id/fake-port', result['content'])

    @patch('serial.tools.list_ports.comports', return_value=[])
    def test_extra_port_prepended(self, _):
        import send2serial
        result = send2serial.listComPorts(extra_port='/dev/serial/by-id/fake-port')
        self.assertEqual(result['content'][0], '/dev/serial/by-id/fake-port')

    @patch('serial.tools.list_ports.comports', return_value=[])
    def test_no_extra_port_when_none(self, _):
        import send2serial
        result = send2serial.listComPorts()
        self.assertEqual(result['content'], [])


if __name__ == '__main__':
    unittest.main()

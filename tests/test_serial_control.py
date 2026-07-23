"""Tests for immediate cancellation and guarded USB reset orchestration."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import globals
import main
import serial_control


class TestCancellation(unittest.TestCase):
    def setUp(self):
        globals.initialize()

    def test_cancel_active_write_uses_pyserial_interrupt(self):
        tty = MagicMock()
        globals.active_serial = tty
        self.assertTrue(serial_control.cancel_active_write())
        tty.cancel_write.assert_called_once_with()
        tty.reset_output_buffer.assert_called_once_with()

    def test_cancel_with_no_open_port_is_safe(self):
        self.assertFalse(serial_control.cancel_active_write())

    @patch.object(main, '_emit_job_update')
    @patch.object(main.serial_control, 'cancel_active_write', return_value=True)
    @patch.object(main.jobqueue, 'request_cancel', return_value=True)
    def test_stop_endpoint_returns_structured_buffer_warning(self, request_cancel, cancel_write, _emit):
        globals.active_job_id = 42
        response = main.app.test_client().post('/stop_plot')
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body['job_id'], 42)
        self.assertTrue(body['write_interrupted'])
        self.assertIn('buffer', body['warning'])
        request_cancel.assert_called_once_with(42)
        cancel_write.assert_called_once_with()


class TestUsbReset(unittest.TestCase):
    def setUp(self):
        globals.initialize()

    @patch.object(serial_control.os, 'name', 'posix')
    @patch.object(serial_control, 'validate_usb_serial_port', return_value=('/dev/serial/by-id/cutter', '/dev/ttyUSB0'))
    @patch.object(serial_control, '_usb_device_node', return_value='/dev/bus/usb/001/002')
    @patch.object(serial_control.os, 'open', return_value=9)
    @patch.object(serial_control.os, 'close')
    @patch.object(serial_control.os.path, 'exists', return_value=True)
    @patch.object(serial_control.serial, 'Serial')
    def test_reset_targets_validated_device_and_probes_port(
        self, serial_cls, _exists, close_fd, open_fd, device_node, validate
    ):
        ioctl = MagicMock()
        fake_fcntl = MagicMock(ioctl=ioctl)
        with patch.dict(sys.modules, {'fcntl': fake_fcntl}):
            phases = serial_control.reset_usb_serial(
                '/dev/serial/by-id/cutter', '/dev/serial/by-id/cutter', timeout=0.1
            )

        open_fd.assert_called_once_with('/dev/bus/usb/001/002', os.O_WRONLY)
        ioctl.assert_called_once_with(9, serial_control.USBDEVFS_RESET, 0)
        close_fd.assert_called_once_with(9)
        serial_cls.assert_called_once()
        serial_cls.return_value.close.assert_called_once_with()
        self.assertEqual(phases[-1]['phase'], 'ready')

    def test_reset_rejects_nonconfigured_path_before_touching_usb(self):
        with self.assertRaisesRegex(serial_control.SerialResetError, 'configured cutter port'):
            serial_control.validate_usb_serial_port('/dev/ttyUSB1', '/dev/serial/by-id/cutter')

    @patch.object(main, '_emit_job_update')
    @patch.object(main.serial_control, 'cancel_active_write')
    @patch.object(main.serial_control, 'reset_usb_serial')
    def test_reset_endpoint_cancels_then_returns_phases(self, reset, cancel, _emit):
        if not main.config.has_section('plotter'):
            main.config.add_section('plotter')
        main.config['plotter']['port'] = '/dev/serial/by-id/cutter'
        main.config['plotter']['baudrate'] = '9600'

        def complete(_port, _configured, baudrate, progress):
            progress({'phase': 'ready', 'message': 'ready', 'status': 'complete'})
            return []
        reset.side_effect = complete
        response = main.app.test_client().post(
            '/reset_plotter_connection', json={'port': '/dev/serial/by-id/cutter'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'ready')
        cancel.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()

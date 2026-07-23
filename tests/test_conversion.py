"""Tests for canonical SVG workspace conversion and its Flask interface."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
import production_store
import workspace


SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" '
    'viewBox="0 0 100 50"><g transform="translate(10 5)">'
    '<path d="M0 0 C20 0 60 40 80 40 L0 40 Z"/></g></svg>'
)


class TestConversion(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.source = Path(self.directory.name, 'design.svg')
        self.source.write_text(SVG)

    def tearDown(self):
        self.directory.cleanup()

    def test_success_is_atomic_and_preserves_source(self):
        output, width, height = main.convert(str(self.source), 300, 150)
        hpgl = Path(output).read_bytes()

        self.assertTrue(self.source.exists())
        self.assertEqual(Path(output).name, 'design_300x150mm.hpgl')
        self.assertAlmostEqual(width, 300, places=1)
        self.assertAlmostEqual(height, 150, places=1)
        self.assertTrue(hpgl.startswith(b'IN;SP1;PA;'))
        self.assertIn(b'PU', hpgl)
        self.assertIn(b'PD', hpgl)
        self.assertNotIn(b'PS', hpgl)
        self.assertEqual(list(Path(self.directory.name).glob('.hpgl-conversion-*')), [])

    def test_invalid_dimensions_and_roll_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'must be a number'):
            main.convert(str(self.source), 'wide', 100)
        with self.assertRaisesRegex(ValueError, 'greater than zero'):
            main.convert(str(self.source), 0, 100)
        with self.assertRaisesRegex(ValueError, 'Roll width'):
            main.convert(str(self.source), 100, 100, roll_width_mm=1201)
        with self.assertRaisesRegex(ValueError, 'outside the loaded roll'):
            main.convert(str(self.source), 300, 150, roll_width_mm=250)
        with self.assertRaisesRegex(ValueError, 'cannot be negative'):
            main.convert(str(self.source), 100, 50, offset_x_mm=-1)

    def test_server_preserves_aspect_ratio_for_unlinked_input(self):
        _output, width, height = main.convert(str(self.source), 200, 200)
        self.assertAlmostEqual(width, 200, places=1)
        self.assertAlmostEqual(height, 100, places=1)

    def test_transform_options_are_written_into_exact_hpgl(self):
        output, width, height = main.convert(
            str(self.source), 200, 100,
            roll_width_mm=1200, offset_x_mm=10, offset_y_mm=5,
            rotation=90, mirror_x='on', mirror_y='',
        )
        paths, _warnings = workspace.load_hpgl_paths(output)
        bounds = workspace._bounds(paths)
        self.assertAlmostEqual(width, 100, places=1)
        self.assertAlmostEqual(height, 200, places=1)
        self.assertAlmostEqual(bounds[0], 10, places=1)
        self.assertAlmostEqual(bounds[1], 5, places=1)
        self.assertIn('_r90_mx_at10x5.hpgl', output)

    @patch.object(workspace.os, 'replace', side_effect=OSError('disk full'))
    def test_publish_failure_preserves_source_and_removes_temporary(self, _replace):
        with self.assertRaisesRegex(ValueError, 'disk full'):
            main.convert(str(self.source), 100, 50)
        self.assertTrue(self.source.exists())
        self.assertEqual(list(Path(self.directory.name).glob('.hpgl-conversion-*')), [])


class TestConversionEndpoint(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_upload_path = main.app.config['UPLOAD_PATH']
        self.original_production_db = production_store.DB_PATH
        main.app.config['UPLOAD_PATH'] = self.directory.name
        production_store.DB_PATH = str(Path(self.directory.name, 'production.db'))
        production_store.init_db()
        self.client = main.app.test_client()

    def tearDown(self):
        main.app.config['UPLOAD_PATH'] = self.original_upload_path
        production_store.DB_PATH = self.original_production_db
        self.directory.cleanup()

    @patch.object(main, 'convert')
    def test_endpoint_passes_complete_workspace_transform(self, convert):
        output = Path(self.directory.name, 'art_150x300mm_r90.hpgl')
        convert.return_value = str(output), 150.0, 300.0
        response = self.client.post('/start_conversion', data={
            'file': 'art.svg', 'target_width_mm': '300', 'target_height_mm': '150',
            'roll_width_mm': '610', 'offset_x_mm': '12.5', 'offset_y_mm': '7',
            'rotation': '90', 'mirror_x': 'on',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['filename'], output.name)
        kwargs = convert.call_args.kwargs
        self.assertEqual(kwargs['roll_width_mm'], '610')
        self.assertEqual(kwargs['rotation'], '90')
        self.assertEqual(kwargs['mirror_x'], 'on')

    @patch.object(main, 'convert', side_effect=main.ConversionError('conversion failed'))
    def test_endpoint_exposes_internal_conversion_error(self, _convert):
        response = self.client.post('/start_conversion', data={
            'file': 'art.svg', 'target_width_mm': '300', 'target_height_mm': '150',
        })
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()['error'], 'conversion failed')

    def test_endpoint_rejects_path_traversal(self):
        response = self.client.post('/start_conversion', data={
            'file': '../art.svg', 'target_width_mm': '300', 'target_height_mm': '150',
        })
        self.assertEqual(response.status_code, 400)

    def test_workspace_endpoint_returns_svg_geometry_and_travel(self):
        Path(self.directory.name, 'art.svg').write_text(SVG)
        response = self.client.get('/cut_workspace/art.svg')
        body = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body['source_type'], 'svg')
        self.assertFalse(body['read_only'])
        self.assertGreater(body['point_count'], 3)
        self.assertEqual(body['units'], 'mm')
        self.assertIn('travel_paths', body)

    def test_workspace_endpoint_returns_exact_read_only_hpgl(self):
        Path(self.directory.name, 'art.hpgl').write_bytes(b'IN;PU400,800;PD800,800,800,1200;PU;')
        response = self.client.get('/cut_workspace/art.hpgl')
        body = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body['read_only'])
        self.assertEqual(body['cut_paths'][0][0], [10.0, 20.0])
        self.assertEqual(body['travel_paths'][0], [[0.0, 0.0], [10.0, 20.0]])

    def test_workspace_preview_api_returns_exact_hash_and_statistics(self):
        duplicate_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="10mm" '
            'viewBox="0 0 20 10"><path d="M0 0 L10 0 L10 10 Z"/>'
            '<path d="M10 10 L10 0 L0 0 Z"/></svg>'
        )
        Path(self.directory.name, 'duplicate.svg').write_text(duplicate_svg)
        response = self.client.post('/api/workspace/preview', json={
            'filename': 'duplicate.svg',
            'transform': {
                'target_width_mm': 10, 'target_height_mm': 10,
                'roll_width_mm': 100, 'offset_x_mm': 0, 'offset_y_mm': 0,
                'rotation': 0,
            },
            'preparation': {},
        })
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body['valid'])
        self.assertEqual(body['manifest_version'], 1)
        self.assertEqual(body['before']['path_count'], 2)
        self.assertEqual(body['after']['path_count'], 1)
        self.assertEqual(len(body['geometry_hash']), 64)
        self.assertEqual(body['cut_paths'], body['intended_paths'][:1])

    def test_workspace_generate_rejects_stale_geometry_hash(self):
        Path(self.directory.name, 'art.svg').write_text(SVG)
        response = self.client.post('/api/workspace/generate', json={
            'filename': 'art.svg',
            'geometry_hash': '0' * 64,
            'transform': {
                'target_width_mm': 100, 'target_height_mm': 50,
                'roll_width_mm': 1200, 'offset_x_mm': 0, 'offset_y_mm': 0,
                'rotation': 0,
            },
            'preparation': {},
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn('changed after preview', response.get_json()['error'])

    def test_workspace_manifest_supports_mixed_designs_and_copies(self):
        Path(self.directory.name, 'first.svg').write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="10mm">'
            '<path d="M0 0H20V10H0Z"/></svg>'
        )
        Path(self.directory.name, 'second.svg').write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm">'
            '<circle cx="5" cy="5" r="5"/></svg>'
        )
        request_body = {
            'manifest_version': 1,
            'roll_width_mm': 100,
            'items': [
                {
                    'filename': 'first.svg', 'target_width_mm': 20,
                    'target_height_mm': 10, 'copies': 2,
                },
                {
                    'filename': 'second.svg', 'target_width_mm': 10,
                    'target_height_mm': 10, 'copies': 1,
                },
            ],
            'layout': {'automatic': True, 'edge_margin_mm': 5, 'spacing_mm': 5},
            'preparation': {'enabled': False},
        }
        preview = self.client.post('/api/workspace/preview', json=request_body)
        body = preview.get_json()
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(body['valid'])
        self.assertEqual(len(body['instances']), 3)
        self.assertEqual(
            [instance['filename'] for instance in body['instances']],
            ['first.svg', 'first.svg', 'second.svg'],
        )

        request_body['geometry_hash'] = body['geometry_hash']
        generated = self.client.post('/api/workspace/generate', json=request_body)
        generated_body = generated.get_json()
        self.assertEqual(generated.status_code, 200)
        self.assertEqual(generated_body['geometry_hash'], body['geometry_hash'])
        hpgl_paths, _warnings = workspace.load_hpgl_paths(
            Path(self.directory.name, generated_body['filename'])
        )
        self.assertEqual(hpgl_paths, workspace._quantized_paths(body['cut_paths']))

    def test_compensation_test_pattern_requires_operator_confirmation(self):
        created = self.client.post('/api/workspace/test-pattern')
        body = created.get_json()
        self.assertEqual(created.status_code, 200)
        self.assertTrue(body['requires_operator_confirmation'])
        self.assertTrue(Path(self.directory.name, body['filename']).is_file())

        hpgl_name = 'PCP_compensation_test_90x55mm.hpgl'
        Path(self.directory.name, hpgl_name).write_bytes(b'IN;SP1;PA;PU;SP0;')
        refused = self.client.post('/start_plot', data={
            'file': hpgl_name,
            'port': '/dev/serial/by-id/test',
            'baudrate': '9600',
            'device': 'creation_1200',
        })
        self.assertEqual(refused.status_code, 409)
        self.assertTrue(refused.get_json()['requires_operator_confirmation'])

    def test_compensated_preview_matches_generated_hpgl(self):
        Path(self.directory.name, 'square.svg').write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm">'
            '<path d="M0 0H20V20H0Z"/></svg>'
        )
        request_body = {
            'manifest_version': 1,
            'roll_width_mm': 100,
            'items': [{
                'filename': 'square.svg',
                'target_width_mm': 20,
                'target_height_mm': 20,
                'copies': 1,
            }],
            'layout': {'automatic': True, 'edge_margin_mm': 5, 'spacing_mm': 5},
            'preparation': {},
            'cutting_aids': {
                'overcut_enabled': True,
                'overcut_mm': 1,
                'blade_compensation_enabled': True,
                'blade_offset_mm': 0.25,
            },
        }
        preview = self.client.post('/api/workspace/preview', json=request_body)
        preview_body = preview.get_json()
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview_body['valid'])
        self.assertNotEqual(preview_body['intended_paths'], preview_body['cut_paths'])
        request_body['geometry_hash'] = preview_body['geometry_hash']
        generated = self.client.post('/api/workspace/generate', json=request_body)
        generated_body = generated.get_json()
        self.assertEqual(generated.status_code, 200)
        emitted, _warnings = workspace.load_hpgl_paths(
            Path(self.directory.name, generated_body['filename'])
        )
        self.assertEqual(emitted, workspace._quantized_paths(preview_body['cut_paths']))

    def test_material_profile_lifecycle_and_operator_only_settings(self):
        created = self.client.post('/api/material-profiles', json={
            'name': 'Cast vinyl',
            'roll_width_mm': 610,
            'suggested_pressure': 'Panel 90 g',
            'suggested_speed': 'Panel 200 mm/s',
            'blade_offset_enabled': True,
        })
        profile = created.get_json()
        self.assertEqual(created.status_code, 201)
        self.assertFalse(profile['blade_offset_enabled'])
        self.assertFalse(profile['verified'])
        refused = self.client.post(
            f"/api/material-profiles/{profile['id']}/verify",
            json={'test_cut_accepted': False},
        )
        self.assertEqual(refused.status_code, 422)
        verified = self.client.post(
            f"/api/material-profiles/{profile['id']}/verify",
            json={'test_cut_accepted': True},
        )
        self.assertTrue(verified.get_json()['verified'])
        exported = self.client.get('/api/material-profiles/export').get_json()
        self.assertEqual(exported['format'], 'pcp-material-profiles')
        self.assertIn('Unprofiled', [item['name'] for item in exported['profiles']])

    def test_calibration_api_and_canonical_preview(self):
        candidate = self.client.post('/api/cutter-calibrations', json={
            'serial_port': '/dev/serial/by-id/test-cutter',
            'device': 'creation_1200',
            'measured_x_mm': 99,
            'measured_y_mm': 101,
            'accept': False,
        })
        self.assertEqual(candidate.status_code, 200)
        self.assertFalse(candidate.get_json()['accepted'])
        accepted = self.client.post('/api/cutter-calibrations', json={
            'serial_port': '/dev/serial/by-id/test-cutter',
            'device': 'creation_1200',
            'measured_x_mm': 99,
            'measured_y_mm': 101,
            'accept': True,
            'enabled': True,
        })
        self.assertEqual(accepted.status_code, 201)
        calibration = accepted.get_json()
        self.assertTrue(calibration['enabled'])

        Path(self.directory.name, 'square.svg').write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm">'
            '<path d="M0 0H20V20H0Z"/></svg>'
        )
        request_body = {
            'manifest_version': 1,
            'roll_width_mm': 100,
            'items': [{
                'filename': 'square.svg',
                'target_width_mm': 20,
                'target_height_mm': 20,
                'copies': 1,
            }],
            'layout': {'automatic': True, 'edge_margin_mm': 5, 'spacing_mm': 5},
            'preparation': {'enabled': False},
            'calibration': {
                'enabled': True,
                'serial_port': calibration['serial_port'],
                'device': calibration['device'],
                # Browser factors are deliberately ignored in favour of SQLite.
                'factor_x': 1,
                'factor_y': 1,
            },
        }
        preview = self.client.post('/api/workspace/preview', json=request_body)
        body = preview.get_json()
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(body['calibration']['enabled'])
        self.assertAlmostEqual(body['calibration']['factor_x'], 100 / 99)
        self.assertNotEqual(body['intended_paths'], body['cut_paths'])
        request_body['geometry_hash'] = body['geometry_hash']
        generated = self.client.post('/api/workspace/generate', json=request_body)
        generated_body = generated.get_json()
        self.assertEqual(generated.status_code, 200)
        self.assertIn('_cal1.010101x0.990099.hpgl', generated_body['filename'])
        emitted, _warnings = workspace.load_hpgl_paths(
            Path(self.directory.name, generated_body['filename'])
        )
        self.assertEqual(emitted, workspace._quantized_paths(body['cut_paths']))

    def test_calibration_pattern_requires_operator_confirmation(self):
        pattern = self.client.post('/api/cutter-calibrations/pattern')
        name = pattern.get_json()['filename']
        self.assertEqual(pattern.status_code, 200)
        self.assertAlmostEqual(
            workspace.workspace_payload(Path(self.directory.name, name))['width_mm'],
            100.0,
            places=2,
        )
        hpgl_name = 'PCP_calibration_100mm_100x100mm.hpgl'
        Path(self.directory.name, hpgl_name).write_bytes(b'IN;SP1;PA;PU;SP0;')
        refused = self.client.post('/start_plot', data={
            'file': hpgl_name,
            'port': '/dev/serial/by-id/test',
            'device': 'creation_1200',
        })
        self.assertEqual(refused.status_code, 409)
        self.assertTrue(refused.get_json()['requires_operator_confirmation'])

    @patch.object(main, 'svg_geometry_dimensions', return_value=(210.0, 105.0))
    def test_dimensions_endpoint_returns_aspect_ratio(self, _dimensions):
        Path(self.directory.name, 'art.svg').write_text('<svg/>')
        response = self.client.get('/svg_dimensions/art.svg')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['aspect_ratio'], 2.0)


if __name__ == '__main__':
    unittest.main()

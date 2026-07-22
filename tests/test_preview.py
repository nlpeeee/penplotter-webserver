"""Tests for dependency-free SVG and HPGL cut-path previews."""

import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from preview import PreviewError, hpgl_preview, parse_hpgl, svg_preview


class TestHpglPreview(unittest.TestCase):
    def test_absolute_pen_moves_only_include_cut_lines(self):
        paths, warnings = parse_hpgl(b"IN;SP1;PU0,0;PD100,0,100,50;PU200,200;PD250,200;SP0;")

        self.assertEqual(paths, [
            [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0)],
            [(200.0, 200.0), (250.0, 200.0)],
        ])
        self.assertEqual(warnings, [])

    def test_relative_coordinates_are_resolved(self):
        paths, _ = parse_hpgl(b"IN;PU100,100;PR;PD50,0,0,50,-50,0,0,-50;")

        self.assertEqual(paths, [[
            (100.0, 100.0), (150.0, 100.0), (150.0, 150.0),
            (100.0, 150.0), (100.0, 100.0),
        ]])

    def test_arc_circle_and_rectangle_create_visible_paths(self):
        paths, _ = parse_hpgl(b"IN;PU100,0;PD;AA0,0,90;PU200,200;CI50;PU300,300;EA400,350;")

        self.assertEqual(len(paths), 3)
        self.assertAlmostEqual(paths[0][-1][0], 0.0, places=5)
        self.assertAlmostEqual(paths[0][-1][1], 100.0, places=5)
        self.assertEqual(paths[2][0], paths[2][-1])

    def test_svg_has_bounds_paths_and_physical_dimensions(self):
        result = hpgl_preview(b"IN;PU0,0;PD400,0,400,800;PU;")
        svg = result.svg.decode("utf-8")

        self.assertIn("viewBox=", svg)
        self.assertIn("stroke=\"#e11d48\"", svg)
        self.assertEqual(result.path_count, 1)
        self.assertEqual(result.width_units / 40, 10)
        self.assertEqual(result.height_units / 40, 20)

    def test_no_pen_down_geometry_is_an_error(self):
        with self.assertRaisesRegex(PreviewError, "No pen-down"):
            hpgl_preview(b"IN;PU0,0,100,100;")

    def test_unsupported_geometry_commands_are_reported(self):
        result = hpgl_preview(b"IN;PU0,0;PD10,10;PU;WG0,90,5;")
        self.assertIn("WG", result.warnings[0])


class TestSvgPreview(unittest.TestCase):
    def test_vector_geometry_is_restyled_as_cut_lines(self):
        result = svg_preview(b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="black" d="M0 0L1 1"/></svg>')
        text = result.svg.decode("utf-8")

        self.assertIn("fill: none !important", text)
        self.assertIn("#e11d48", text)
        self.assertEqual(result.path_count, 1)

    def test_scripts_and_event_handlers_are_removed(self):
        result = svg_preview(b'<svg xmlns="http://www.w3.org/2000/svg" onload="bad()"><script>bad()</script><line x2="1"/></svg>')
        text = result.svg.decode("utf-8")

        self.assertNotIn("onload", text)
        self.assertNotIn("<script", text)

    def test_svg_without_vector_geometry_is_an_error(self):
        with self.assertRaisesRegex(PreviewError, "No vector"):
            svg_preview(b'<svg xmlns="http://www.w3.org/2000/svg"><text>hello</text></svg>')


class TestPreviewEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import app
        cls.app = app

    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.original_upload_path = self.app.config['UPLOAD_PATH']
        self.app.config['UPLOAD_PATH'] = self.uploads.name
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.config['UPLOAD_PATH'] = self.original_upload_path
        self.uploads.cleanup()

    def test_hpgl_endpoint_returns_rendered_svg_and_metadata(self):
        Path(self.uploads.name, 'square.hpgl').write_bytes(
            b'IN;PU0,0;PD400,0,400,400,0,400,0,0;PU;'
        )

        response = self.client.get('/preview/square.hpgl')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/svg+xml')
        self.assertEqual(response.headers['X-Preview-Paths'], '1')
        self.assertEqual(response.headers['X-Preview-Width-MM'], '10.0')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn(b'<path', response.data)

    def test_empty_cut_file_returns_a_useful_json_error(self):
        Path(self.uploads.name, 'empty.hpgl').write_bytes(b'IN;PU0,0;')

        response = self.client.get('/preview/empty.hpgl')

        self.assertEqual(response.status_code, 422)
        self.assertIn('No pen-down', response.get_json()['error'])


if __name__ == '__main__':
    unittest.main()

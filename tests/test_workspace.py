"""Focused affine-transform and HPGL serialization parity tests."""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import workspace


RECT = [[(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0), (0.0, 0.0)]]


class TestWorkspaceTransform(unittest.TestCase):
    def transform(self, **values):
        defaults = {
            'target_width_mm': 200, 'target_height_mm': 100,
            'roll_width_mm': 1200, 'offset_x_mm': 0, 'offset_y_mm': 0,
            'rotation': 0,
        }
        defaults.update(values)
        return workspace.transform_paths(RECT, workspace.parse_transform(defaults))

    def test_scale_and_aspect_ratio(self):
        paths, metadata = self.transform(target_width_mm=200, target_height_mm=200)
        self.assertEqual(paths[0][2], (200.0, 100.0))
        self.assertEqual(metadata['width_mm'], 200.0)
        self.assertEqual(metadata['height_mm'], 100.0)

    def test_every_rotation_matches_expected_bounds(self):
        for rotation, expected in ((0, (200, 100)), (90, (100, 200)), (180, (200, 100)), (270, (100, 200))):
            _paths, metadata = self.transform(rotation=rotation)
            self.assertAlmostEqual(metadata['width_mm'], expected[0])
            self.assertAlmostEqual(metadata['height_mm'], expected[1])

    def test_mirror_rotation_and_offset_coordinates(self):
        paths, metadata = self.transform(rotation=90, mirror_x='on', offset_x_mm=10, offset_y_mm=5)
        self.assertEqual(paths[0][0], (110.0, 205.0))
        self.assertAlmostEqual(metadata['min_x_mm'], 10)
        self.assertAlmostEqual(metadata['min_y_mm'], 5)

    def test_roll_overflow_is_reported(self):
        _paths, metadata = self.transform(roll_width_mm=199)
        self.assertTrue(metadata['out_of_bounds'])

    def test_hpgl_uses_40_units_per_mm_and_no_page_command(self):
        hpgl = workspace.hpgl_bytes([[(1.25, 2.5), (3.0, 4.0)]])
        self.assertEqual(hpgl, b'IN;SP1;PA;PU50,100;PD120,160;PU;SP0;')
        self.assertNotIn(b'PS', hpgl)

    def test_invalid_rotation_and_nonfinite_input(self):
        with self.assertRaisesRegex(workspace.WorkspaceError, 'Rotation'):
            self.transform(rotation=45)
        with self.assertRaisesRegex(workspace.WorkspaceError, 'finite'):
            self.transform(target_width_mm=float('nan'))


class TestCutPreparation(unittest.TestCase):
    def test_duplicate_open_and_cyclic_closed_paths_are_removed(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        shifted = [(10, 10), (0, 10), (0, 0), (10, 0), (10, 10)]
        line = [(20, 0), (30, 0)]
        paths, diagnostics = workspace.prepare_paths(
            [square, list(reversed(square)), shifted, line, list(reversed(line))],
            workspace.Preparation(),
        )
        self.assertEqual(len(paths), 2)
        self.assertEqual(
            next(item for item in diagnostics['warnings'] if item['code'] == 'duplicates_removed')['count'],
            3,
        )

    def test_disabled_preparation_preserves_hpgl_order_and_points(self):
        source = [[(5.001, 0), (5.002, 0), (10, 0)], [(1, 1), (2, 2)]]
        prepared, diagnostics = workspace.prepare_paths(
            source, workspace.Preparation(enabled=False)
        )
        self.assertEqual(workspace.hpgl_bytes(prepared), workspace.hpgl_bytes(source))
        self.assertEqual(diagnostics['before'], diagnostics['after'])

    def test_open_paths_then_deepest_closed_contours_are_cut_first(self):
        outer = [(0, 0), (30, 0), (30, 30), (0, 30), (0, 0)]
        inner = [(10, 10), (20, 10), (20, 20), (10, 20), (10, 10)]
        open_line = [(40, 0), (45, 0)]
        prepared, _diagnostics = workspace.prepare_paths(
            [outer, inner, open_line],
            workspace.Preparation(minimize_travel=False),
        )
        self.assertEqual(prepared[0], open_line)
        self.assertEqual(prepared[1], inner)
        self.assertEqual(prepared[2], outer)

    def test_travel_minimization_preserves_path_direction(self):
        far = [(100, 0), (110, 0)]
        near = [(10, 0), (20, 0)]
        prepared, _diagnostics = workspace.prepare_paths(
            [far, near],
            workspace.Preparation(inside_first=False),
        )
        self.assertEqual(prepared, [near, far])
        self.assertEqual(prepared[0][0], (10, 0))

    def test_optional_simplification_reduces_points(self):
        noisy = [[(0, 0), (1, 0.001), (2, -0.001), (3, 0)]]
        prepared, diagnostics = workspace.prepare_paths(
            noisy,
            workspace.Preparation(
                inside_first=False,
                minimize_travel=False,
                simplify_enabled=True,
                simplify_tolerance_mm=0.05,
            ),
        )
        self.assertEqual(len(prepared[0]), 2)
        self.assertLess(diagnostics['after']['point_count'], diagnostics['before']['point_count'])

    def test_hash_represents_exact_emitted_hpgl(self):
        paths, diagnostics = workspace.prepare_paths(
            [[(1.25, 2.5), (3, 4)]], workspace.Preparation()
        )
        import hashlib
        self.assertEqual(
            diagnostics['geometry_hash'],
            hashlib.sha256(workspace.hpgl_bytes(paths)).hexdigest(),
        )


if __name__ == '__main__':
    unittest.main()

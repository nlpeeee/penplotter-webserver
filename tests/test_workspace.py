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


if __name__ == '__main__':
    unittest.main()

"""Focused affine-transform and HPGL serialization parity tests."""

import os
import sys
import unittest
import math


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
        self.assertEqual(diagnostics['maximum_deviation_mm'], 0.05)

    def test_safe_simplification_stays_within_physical_tolerance(self):
        from shapely.geometry import LineString

        original = [
            (index * 0.02, math.sin(index / 20.0) * 2.0)
            for index in range(1000)
        ]
        prepared, diagnostics = workspace.prepare_paths(
            [original],
            workspace.Preparation(
                inside_first=False,
                minimize_travel=False,
                simplify_enabled=True,
                simplify_tolerance_mm=0.05,
            ),
        )
        deviation = LineString(original).hausdorff_distance(LineString(prepared[0]))
        self.assertLessEqual(deviation, 0.05 + 1e-9)
        self.assertLess(diagnostics['after']['point_count'], 200)

    def test_dense_curve_regression_is_reduced_to_streamable_size(self):
        dense = [[
            (
                index * 0.01,
                5.0 + math.sin(index / 150.0) * 2.0
                + math.sin(index / 9.0) * 0.01,
            )
            for index in range(6050)
        ]]
        _prepared, diagnostics = workspace.prepare_paths(
            dense,
            workspace.Preparation(
                inside_first=False,
                minimize_travel=False,
                simplify_enabled=True,
                simplify_tolerance_mm=0.05,
            ),
        )
        self.assertEqual(diagnostics['before']['point_count'], 6050)
        self.assertLess(diagnostics['after']['point_count'], 300)
        self.assertLess(diagnostics['after']['hpgl_bytes'], 3000)

    def test_transport_preflight_matches_9600_baud_limits(self):
        high = workspace.transport_preflight({
            'hpgl_bytes': 63553,
            'cut_length_mm': 1412.662,
            'travel_length_mm': 0,
        }, operator_speed_mm_s=50)
        low = workspace.transport_preflight({
            'hpgl_bytes': 2699,
            'cut_length_mm': 1410.9,
            'travel_length_mm': 0,
        }, operator_speed_mm_s=50)
        self.assertAlmostEqual(high['estimated_wire_seconds'], 66.201, places=3)
        self.assertEqual(high['risk'], 'high')
        self.assertEqual(low['risk'], 'low')

    def test_simplification_never_accepts_more_than_point_one_mm(self):
        with self.assertRaisesRegex(workspace.WorkspaceError, '0.1 mm'):
            workspace.parse_preparation({
                'simplify_enabled': True,
                'simplify_tolerance_mm': 0.101,
            })

    def test_hash_represents_exact_emitted_hpgl(self):
        paths, diagnostics = workspace.prepare_paths(
            [[(1.25, 2.5), (3, 4)]], workspace.Preparation()
        )
        import hashlib
        self.assertEqual(
            diagnostics['geometry_hash'],
            hashlib.sha256(workspace.hpgl_bytes(paths)).hexdigest(),
        )


class TestCopiesAndRollLayout(unittest.TestCase):
    def item(self, filename, paths, width, height, copies=1, placements=None):
        return {
            'filename': filename,
            'filepath': filename,
            'transform': workspace.Transform(
                target_width_mm=width,
                target_height_mm=height,
                roll_width_mm=1200,
            ),
            'copies': copies,
            'placements': placements or [],
            '_paths': paths,
        }

    def preview(
        self, items, roll_width=100, layout=None, cutting_aids=None,
        calibration=None,
    ):
        sources = {item['filepath']: item['_paths'] for item in items}
        clean_items = [{key: value for key, value in item.items() if key != '_paths'} for item in items]
        with unittest.mock.patch.object(
            workspace, 'load_svg_paths', side_effect=lambda filename: sources[filename]
        ):
            return workspace.build_manifest_preview(
                clean_items,
                roll_width,
                layout or workspace.Layout(),
                workspace.Preparation(enabled=False),
                cutting_aids,
                calibration,
            )

    def test_deterministic_rows_preserve_design_and_copy_order(self):
        first = self.item('first.svg', RECT, 40, 20, copies=3)
        second = self.item('second.svg', RECT, 20, 10, copies=1)
        _paths, metadata = self.preview([first, second], roll_width=100)
        instances = metadata['instances']
        self.assertEqual(
            [(item['filename'], item['copy_index']) for item in instances],
            [('first.svg', 0), ('first.svg', 1), ('first.svg', 2), ('second.svg', 0)],
        )
        self.assertEqual(
            [(item['x'], item['y']) for item in instances],
            [(5.0, 5.0), (50.0, 5.0), (5.0, 30.0), (50.0, 30.0)],
        )
        self.assertAlmostEqual(metadata['roll_length_mm'], 70.0)
        self.assertAlmostEqual(metadata['design_area_mm2'], 2600.0)

    def test_manual_collisions_and_overflow_block_generation(self):
        item = self.item(
            'art.svg', RECT, 40, 20, copies=2,
            placements=[
                {'x_mm': 0, 'y_mm': 0, 'rotation': 0},
                {'x_mm': 30, 'y_mm': 0, 'rotation': 0},
            ],
        )
        _paths, metadata = self.preview(
            [item], roll_width=60,
            layout=workspace.Layout(automatic=False),
        )
        self.assertFalse(metadata['valid'])
        self.assertEqual(len(metadata['collisions']), 2)
        self.assertTrue(metadata['out_of_bounds'])
        self.assertEqual(
            {warning['code'] for warning in metadata['warnings'] if warning['severity'] == 'error'},
            {'layout_collisions', 'layout_out_of_bounds'},
        )

    def test_auto_rotation_is_only_used_when_enabled(self):
        item = self.item('wide.svg', RECT, 80, 40)
        _paths, plain = self.preview([item], roll_width=70)
        self.assertTrue(plain['out_of_bounds'])
        _paths, rotated = self.preview(
            [item], roll_width=70,
            layout=workspace.Layout(allow_rotation=True),
        )
        self.assertTrue(rotated['valid'])
        self.assertEqual(rotated['instances'][0]['rotation'], 90)
        self.assertEqual(
            (rotated['instances'][0]['width'], rotated['instances'][0]['height']),
            (40.0, 80.0),
        )

    def test_invalid_fractional_copy_count_is_rejected(self):
        item = self.item('art.svg', RECT, 40, 20, copies=1.5)
        with self.assertRaisesRegex(workspace.WorkspaceError, 'whole number'):
            self.preview([item])


class TestVinylCuttingAids(TestCopiesAndRollLayout):
    def test_weed_lines_are_in_free_strips_and_border_is_last(self):
        item = self.item('art.svg', RECT, 20, 10, copies=2)
        _paths, metadata = self.preview(
            [item],
            layout=workspace.Layout(edge_margin_mm=5, spacing_mm=10),
            cutting_aids=workspace.CuttingAids(
                weed_enabled=True,
                weed_border_mode='layout',
                weed_margin_mm=5,
                weed_vertical=True,
            ),
        )
        self.assertTrue(metadata['valid'])
        self.assertEqual(len(metadata['weed_paths']), 1)
        weed = metadata['weed_paths'][0]
        self.assertAlmostEqual(weed[0][0], 30)
        self.assertEqual(metadata['path_roles'][-2:], ['weed_line', 'weed_border'])
        self.assertEqual(metadata['cut_paths'][-1], metadata['weed_border_paths'][0])

    def test_copy_borders_that_enter_other_designs_block_generation(self):
        item = self.item('art.svg', RECT, 20, 10, copies=2)
        _paths, metadata = self.preview(
            [item],
            layout=workspace.Layout(edge_margin_mm=10, spacing_mm=2),
            cutting_aids=workspace.CuttingAids(
                weed_enabled=True,
                weed_border_mode='copy',
                weed_margin_mm=5,
            ),
        )
        self.assertFalse(metadata['valid'])
        self.assertIn(
            'weed_border_collision',
            {warning['code'] for warning in metadata['warnings']},
        )

    def test_overcut_follows_initial_closed_path_trajectory(self):
        square = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
        overcut = workspace._overcut_path(square, 3)
        self.assertEqual(overcut[:len(square)], square)
        self.assertEqual(overcut[-1], (3.0, 0.0))
        self.assertFalse(workspace._is_closed(overcut))

    def test_blade_compensation_adds_pivot_arcs_and_validates_square(self):
        square = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
        compensated = workspace._blade_compensated_path(square, 0.25)
        self.assertEqual(compensated[0], (0.25, 0.0))
        self.assertEqual(compensated[-1], compensated[0])
        self.assertGreater(len(compensated), len(square))
        self.assertTrue(workspace._compensation_is_valid([compensated]))

    def test_self_intersecting_compensation_is_invalid(self):
        bow_tie = [(0, 0), (20, 20), (0, 20), (20, 0), (0, 0)]
        compensated = workspace._blade_compensated_path(bow_tie, 0.25)
        self.assertFalse(workspace._compensation_is_valid([compensated]))

    def test_emitted_hpgl_hash_covers_compensated_carriage_path(self):
        item = self.item('art.svg', RECT, 20, 10)
        paths, metadata = self.preview(
            [item],
            cutting_aids=workspace.CuttingAids(
                overcut_enabled=True,
                overcut_mm=1,
                blade_compensation_enabled=True,
                blade_offset_mm=0.25,
            ),
        )
        import hashlib
        self.assertTrue(metadata['valid'])
        self.assertNotEqual(metadata['intended_paths'], metadata['cut_paths'])
        self.assertEqual(metadata['compensated_paths'], metadata['cut_paths'])
        self.assertEqual(
            metadata['geometry_hash'],
            hashlib.sha256(workspace.hpgl_bytes(paths)).hexdigest(),
        )


class TestCutterCalibration(TestCopiesAndRollLayout):
    def test_calibration_scales_commanded_path_and_is_recorded(self):
        item = self.item('art.svg', RECT, 20, 10)
        paths, metadata = self.preview(
            [item],
            calibration=workspace.Calibration(
                enabled=True,
                factor_x=1.01,
                factor_y=0.99,
                serial_port='/dev/serial/by-id/cutter',
                device='creation_1200',
            ),
        )
        self.assertAlmostEqual(metadata['cut_paths'][0][1][0], 25.25)
        self.assertAlmostEqual(metadata['cut_paths'][0][2][1], 14.85)
        self.assertNotEqual(metadata['intended_paths'], metadata['cut_paths'])
        self.assertEqual(metadata['calibration']['factor_x'], 1.01)
        self.assertEqual(metadata['calibration']['factor_y'], 0.99)
        import hashlib
        self.assertEqual(
            metadata['geometry_hash'],
            hashlib.sha256(workspace.hpgl_bytes(paths)).hexdigest(),
        )

    def test_calibration_factor_safety_bounds(self):
        with self.assertRaisesRegex(workspace.WorkspaceError, 'between 0.90 and 1.10'):
            workspace.parse_calibration({'enabled': True, 'factor_x': 1.2, 'factor_y': 1})


if __name__ == '__main__':
    unittest.main()

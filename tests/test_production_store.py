"""Material-profile and physical-cutter calibration persistence tests."""

import os
import tempfile
import unittest
from pathlib import Path

import production_store


class ProductionStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = production_store.DB_PATH
        production_store.DB_PATH = str(Path(self.directory.name, "pcp.db"))
        production_store.init_db()

    def tearDown(self):
        production_store.DB_PATH = self.original_db
        self.directory.cleanup()

    def test_migrations_are_idempotent_and_unprofiled_is_permanent(self):
        production_store.init_db()
        profiles = production_store.list_profiles()
        self.assertEqual([profile["id"] for profile in profiles], ["unprofiled"])
        self.assertFalse(profiles[0]["deletable"])
        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            production_store.delete_profile("unprofiled")

    def test_new_profile_starts_unverified_with_compensation_disabled(self):
        profile = production_store.create_profile({
            "name": "Gloss vinyl",
            "roll_width_mm": 610,
            "suggested_pressure": "Panel: 90 g",
            "suggested_speed": "Panel: 200 mm/s",
            "blade_offset_enabled": True,
            "overcut_enabled": True,
        })
        self.assertFalse(profile["verified"])
        self.assertFalse(profile["blade_offset_enabled"])
        self.assertFalse(profile["overcut_enabled"])
        with self.assertRaisesRegex(ValueError, "Accept"):
            production_store.mark_profile_verified(profile["id"], False)
        verified = production_store.mark_profile_verified(profile["id"], True)
        self.assertTrue(verified["verified"])

    def test_profile_export_and_import(self):
        production_store.create_profile({"name": "Paper", "notes": "Test"})
        document = production_store.export_profiles()
        self.assertEqual(document["format"], "pcp-material-profiles")
        imported_db = str(Path(self.directory.name, "imported.db"))
        production_store.DB_PATH = imported_db
        production_store.init_db()
        imported = production_store.import_profiles(document)
        self.assertEqual([profile["name"] for profile in imported], ["Paper"])
        self.assertEqual(
            [profile["name"] for profile in production_store.list_profiles()],
            ["Unprofiled", "Paper"],
        )

    def test_calibration_arithmetic_bounds_and_large_warning(self):
        candidate = production_store.calibration_candidate(98, 102)
        self.assertAlmostEqual(candidate["factor_x"], 100 / 98)
        self.assertAlmostEqual(candidate["factor_y"], 100 / 102)
        self.assertTrue(candidate["large_correction"])
        with self.assertRaisesRegex(ValueError, "additional confirmation"):
            production_store.save_calibration(
                "/dev/serial/by-id/cutter", "creation_1200", 98, 102,
                enabled=True,
            )
        accepted = production_store.save_calibration(
            "/dev/serial/by-id/cutter", "creation_1200", 98, 102,
            enabled=True, confirm_large_correction=True,
        )
        self.assertTrue(accepted["accepted"])
        self.assertTrue(accepted["enabled"])
        self.assertTrue(accepted["large_correction"])
        disabled = production_store.set_calibration_enabled(
            accepted["serial_port"], accepted["device"], False
        )
        self.assertFalse(disabled["enabled"])
        with self.assertRaisesRegex(ValueError, "between 0.90 and 1.10"):
            production_store.calibration_candidate(50, 100)


if __name__ == "__main__":
    unittest.main()

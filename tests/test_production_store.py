"""Material-profile and physical-cutter calibration persistence tests."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import production_store


class ProductionStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = production_store.DB_PATH
        self.original_projects_root = production_store.PROJECTS_ROOT
        production_store.DB_PATH = str(Path(self.directory.name, "pcp.db"))
        production_store.PROJECTS_ROOT = str(Path(self.directory.name, "projects"))
        production_store.init_db()

    def tearDown(self):
        production_store.DB_PATH = self.original_db
        production_store.PROJECTS_ROOT = self.original_projects_root
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

    def project_source(self):
        source = Path(self.directory.name, "source.svg")
        source.write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L10 10"/></svg>')
        manifest = {
            "manifest_version": 1,
            "items": [{"filename": "source.svg", "target_width_mm": 10, "target_height_mm": 10}],
            "layout": {},
            "preparation": {},
            "cutting_aids": {},
            "calibration_snapshot": {"enabled": False, "factor_x": 1, "factor_y": 1},
        }
        sources = [{
            "item_index": 0,
            "source_path": str(source),
            "original_filename": "source.svg",
        }]
        return source, manifest, sources

    def test_project_revisions_are_self_contained_immutable_and_deduplicated(self):
        source, manifest, sources = self.project_source()
        first = production_store.save_project_revision(
            {"name": "Signs", "notes": "First", "tags": ["vinyl"]},
            manifest, sources, b"FIRST", b"<svg/>", "a" * 64,
        )
        project_id = first["project_id"]
        stored_item = first["manifest"]["items"][0]
        asset = production_store.get_project_asset(stored_item["project_asset_id"])
        self.assertEqual(Path(asset["stored_path"]).stem, asset["sha256"])
        source.unlink()
        self.assertTrue(Path(asset["stored_path"]).is_file())
        self.assertEqual(Path(first["hpgl_path"]).read_bytes(), b"FIRST")

        second_sources = [{
            "item_index": 0,
            "source_path": asset["stored_path"],
            "original_filename": asset["original_filename"],
        }]
        second = production_store.save_project_revision(
            {"name": "Signs", "notes": "Second", "tags": ["vinyl"]},
            first["manifest"], second_sources, b"SECOND", b"<svg/>", "b" * 64,
            project_id=project_id,
        )
        self.assertEqual(second["revision_number"], 2)
        self.assertEqual(Path(first["hpgl_path"]).read_bytes(), b"FIRST")
        self.assertEqual(Path(second["hpgl_path"]).read_bytes(), b"SECOND")
        assets = list(Path(production_store.PROJECTS_ROOT, project_id, "assets").glob("*.svg"))
        self.assertEqual(len(assets), 1)

    def test_recovery_draft_requires_deliberate_save(self):
        _source, manifest, sources = self.project_source()
        with self.assertRaisesRegex(ValueError, "deliberate"):
            production_store.save_recovery_draft(str(__import__("uuid").uuid4()), manifest, sources)
        revision = production_store.save_project_revision(
            {"name": "Draftable"}, manifest, sources, b"HPGL", b"<svg/>", "c" * 64
        )
        asset = production_store.get_project_asset(
            revision["manifest"]["items"][0]["project_asset_id"]
        )
        result = production_store.save_recovery_draft(
            revision["project_id"],
            revision["manifest"],
            [{
                "item_index": 0,
                "source_path": asset["stored_path"],
                "original_filename": asset["original_filename"],
            }],
        )
        self.assertTrue(result["saved"])
        self.assertIsNotNone(
            production_store.get_project(revision["project_id"])["recovery_draft"]
        )

    def test_soft_delete_restore_and_guarded_purge(self):
        _source, manifest, sources = self.project_source()
        revision = production_store.save_project_revision(
            {"name": "Recoverable"}, manifest, sources, b"HPGL", b"<svg/>", "d" * 64
        )
        project_id = revision["project_id"]
        with self.assertRaisesRegex(ValueError, "Soft-delete"):
            production_store.purge_project(project_id)
        self.assertTrue(production_store.soft_delete_project(project_id))
        self.assertIsNone(production_store.get_project(project_id))
        self.assertIsNotNone(production_store.restore_project(project_id))
        self.assertTrue(production_store.soft_delete_project(project_id))
        self.assertTrue(production_store.purge_project(project_id))
        self.assertFalse(Path(production_store.PROJECTS_ROOT, project_id).exists())

    def test_failed_first_revision_leaves_no_project_or_directory(self):
        _source, manifest, sources = self.project_source()
        with patch.object(production_store, "_atomic_bytes", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                production_store.save_project_revision(
                    {"name": "Atomic"}, manifest, sources,
                    b"HPGL", b"<svg/>", "e" * 64,
                )
        self.assertEqual(production_store.list_projects(), [])
        self.assertEqual(list(Path(production_store.PROJECTS_ROOT).iterdir()), [])

    def test_project_directory_rejects_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "Invalid project directory"):
            production_store._project_directory("../outside")


if __name__ == "__main__":
    unittest.main()

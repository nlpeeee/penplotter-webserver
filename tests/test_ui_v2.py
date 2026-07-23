import os
import unittest
from unittest.mock import patch

import main


class V2InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not main.config.has_section("telegram"):
            main.config.read("config.ini.sample")

    def setUp(self):
        self.client = main.app.test_client()
        main.globals.initialize()

    def test_v1_and_v2_routes_are_permanent(self):
        v1 = self.client.get("/v1")
        v2 = self.client.get("/v2")
        workbench = self.client.get("/v2/workbench")

        self.assertEqual(v1.status_code, 200)
        self.assertIn("<title>PCP</title>", v1.get_data(as_text=True))
        self.assertEqual(v2.status_code, 200)
        self.assertIn("<title>PCP V2</title>", v2.get_data(as_text=True))
        self.assertIn('data-initial-view="new-cut"', v2.get_data(as_text=True))
        self.assertIn('data-initial-view="workbench"', workbench.get_data(as_text=True))
        self.assertEqual(self.client.get("/v2/not-a-view").status_code, 404)

    def test_v2_uses_local_versioned_assets(self):
        html = self.client.get("/v2").get_data(as_text=True)
        self.assertIn("/static/v2/vendor.css", html)
        self.assertIn("/static/v2/vendor.js", html)
        self.assertIn("/static/v2/app.css", html)
        self.assertIn("/static/v2/app.js", html)
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertNotIn("cdnjs.cloudflare.com", html)
        self.assertNotIn("ajax.googleapis.com", html)

    def test_default_ui_can_be_selected_without_changing_v1_route(self):
        with patch.dict(os.environ, {"PCP_UI_DEFAULT": "v2"}):
            default_html = self.client.get("/").get_data(as_text=True)
        v1_html = self.client.get("/v1").get_data(as_text=True)
        self.assertIn("<title>PCP V2</title>", default_html)
        self.assertIn("<title>PCP</title>", v1_html)

    @patch.object(main.jobqueue, "get_queue_count", return_value=0)
    @patch.object(main.jobqueue, "get_recent_jobs", return_value=[])
    @patch.object(main.send2serial, "listComPorts")
    def test_ui_state_distinguishes_available_and_missing_ports(self, ports, _jobs, _count):
        configured = main.config["plotter"].get("port", "")
        ports.return_value = {"content": [configured] if configured else []}
        available = self.client.get("/api/ui-state").get_json()
        expected = "available" if configured else "unknown"
        self.assertEqual(available["plotter"]["port_state"], expected)
        self.assertEqual(available["queue_count"], 0)

        ports.return_value = {"content": []}
        missing = self.client.get("/api/ui-state").get_json()
        self.assertEqual(
            missing["plotter"]["port_state"],
            "missing" if configured else "unknown",
        )

    @patch.object(main.os.path, "exists", return_value=True)
    @patch.object(main.jobqueue, "get_queue_count", return_value=0)
    @patch.object(main.jobqueue, "get_recent_jobs", return_value=[])
    @patch.object(main.send2serial, "listComPorts", return_value={"content": ["/dev/ttyUSB0"]})
    def test_ui_state_resolves_stable_serial_symlink(self, _ports, _jobs, _count, _exists):
        configured = main.config["plotter"].get("port", "")
        payload = self.client.get("/api/ui-state").get_json()
        if configured:
            self.assertEqual(payload["plotter"]["port_state"], "available")
            self.assertIn(configured, payload["plotter"]["detected_ports"])

    @patch.object(main.jobqueue, "get_queue_count", return_value=3)
    @patch.object(main.send2serial, "listComPorts", return_value={"content": []})
    @patch.object(main.jobqueue, "get_recent_jobs")
    def test_ui_state_includes_active_progress(self, jobs, _ports, _count):
        jobs.return_value = [{
            "id": 42,
            "display_file": "current.hpgl",
            "file": "spool/current.hpgl",
            "status": "transmitting",
        }]
        main.globals.active_job_id = 42
        main.globals.print_progress = 37.25
        payload = self.client.get("/api/ui-state").get_json()
        self.assertEqual(payload["plotter"]["port_state"], "busy")
        self.assertEqual(payload["plotter"]["serial_operation"], "cut")
        self.assertEqual(payload["active_job"]["id"], 42)
        self.assertEqual(payload["active_job"]["progress"], 37.25)
        self.assertEqual(payload["queue_count"], 3)


if __name__ == "__main__":
    unittest.main()

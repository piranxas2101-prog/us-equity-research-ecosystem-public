"""Control center: security boundary and actions (no real registry or launcher)."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
import unittest

import control_center as cc


class ControlCenterHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "output").mkdir()
        (self.root / "start_full_research_with_notify.ps1").write_text("# test", encoding="utf-8")
        self.launched: list[Path] = []
        actions = cc.Actions(self.root, launcher=lambda path: self.launched.append(path) or 4242)
        self.token = "test-token-123"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), cc.make_handler(cc.Views(self.root), actions, self.token, 0))
        self.port = self.server.server_address[1]
        # the handler was built for port 0; rebuild with the real port so Host/Origin checks match
        self.server.RequestHandlerClass = cc.make_handler(cc.Views(self.root), actions, self.token, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method: str, path: str, *, host: str | None = None, headers: dict | None = None, body: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.port}")
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.putheader("Content-Length", str(len(body or b"")))
        conn.endheaders(body)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def action(self, path: str, payload: dict, **overrides):
        headers = {"Origin": f"http://127.0.0.1:{self.port}", "X-Control-Token": self.token, "Content-Type": "application/json"}
        headers.update(overrides)
        return self.request("POST", path, headers={k: v for k, v in headers.items() if v is not None},
                            body=json.dumps(payload).encode())

    def test_page_carries_session_token_and_strict_headers(self):
        response, body = self.request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertIn(self.token.encode(), body)
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")

    def test_foreign_host_header_is_refused(self):  # DNS rebinding
        response, _ = self.request("GET", "/api/status", host="evil.example:80")
        self.assertEqual(response.status, 403)

    def test_actions_need_token_origin_and_json(self):
        path = "/api/actions/start-research-run"
        self.assertEqual(self.action(path, {}, **{"X-Control-Token": None})[0].status, 403)
        self.assertEqual(self.action(path, {}, **{"X-Control-Token": "wrong"})[0].status, 403)
        self.assertEqual(self.action(path, {}, Origin="https://evil.example")[0].status, 403)
        self.assertEqual(self.action(path, {}, Origin=None)[0].status, 403)
        self.assertEqual(self.action(path, {}, **{"Content-Type": "text/plain"})[0].status, 415)
        self.assertEqual(self.launched, [])

    def test_only_whitelisted_actions_exist(self):
        self.assertEqual(self.action("/api/actions/run-shell", {"cmd": "whoami"})[0].status, 404)
        self.assertEqual(self.action("/api/actions/start-trader", {})[0].status, 404)
        self.assertEqual(self.request("OPTIONS", "/api/actions/start-research-run")[0].status, 405)
        self.assertEqual(self.request("GET", "/../.env")[0].status, 404)

    def test_research_run_starts_once_and_is_logged(self):
        response, body = self.action("/api/actions/start-research-run", {})
        self.assertEqual(response.status, 200, body)
        self.assertEqual(json.loads(body)["research_only"], True)
        self.assertEqual(len(self.launched), 1)
        log = (self.root / "data" / "control_center" / "actions.jsonl").read_text(encoding="utf-8")
        self.assertIn("start_research_run", log)

    def test_launcher_uses_flags_under_which_windowless_powershell_really_runs(self):
        from unittest import mock

        with mock.patch.object(cc.subprocess, "Popen") as popen:
            popen.return_value.pid = 99
            self.assertEqual(cc.Actions(self.root)._launch_detached(self.root / "x.ps1"), 99)
        flags = popen.call_args.kwargs["creationflags"]
        self.assertEqual(flags & 0x00000008, 0)  # DETACHED_PROCESS makes powershell.exe exit without running the script
        self.assertTrue(flags & 0x08000000)       # CREATE_NO_WINDOW: hidden console instead

    def test_second_run_is_refused_while_one_is_active(self):
        pointer = {"run_id": "full_research_x", "status": "running_pipeline", "pid": os.getpid()}
        (self.root / "output" / "full_research_control_plane_background_latest.json").write_text(json.dumps(pointer), encoding="utf-8")
        response, body = self.action("/api/actions/start-research-run", {})
        self.assertEqual(response.status, 400)
        self.assertIn("already active", json.loads(body)["error"])
        self.assertEqual(self.launched, [])

    def test_progress_counts_scanned_and_deferred_symbols_apart(self):
        routes = [{"ticker": "ABC"}, {"ticker": "XYZ"},
                  {"ticker": "FUND", "defer_market_scan": True, "separate_lane": "etf_lane"},
                  {"ticker": "ABCW", "defer_market_scan": True, "issuer_ticker": "ABC"}]
        (self.root / "output" / "all_listed_universe_routes_latest.json").write_text(
            json.dumps({"routes": routes}), encoding="utf-8")
        (self.root / "logs").mkdir()
        (self.root / "logs" / "full_research_control_plane_1.out.log").write_text(
            "[OK] ABC | x\n[DEFER] ABCW | linked\n[DEFER] FUND | lane\n", encoding="utf-8")
        views = cc.Views(self.root)
        progress = views._progress({"status": "running_pipeline", "started_at": "2026-09-29T00:00:00+00:00"})
        self.assertEqual((progress["babis_scanned"], progress["babis_deferred"], progress["babis_to_scan"]), (1, 2, 2))
        self.assertEqual(progress["lanes"], {"etf_lane": 1, "issuer_card": 1})
        self.assertEqual(views.lanes()["by_ticker"]["FUND"], {"lane": "etf_lane", "issuer_ticker": None})

    def test_step_timings_are_shown_only_for_the_current_run(self):
        timings = {"run_id": "full_research_x", "current_step": "giannis_triage", "updated_at": "t",
                   "steps": [{"key": "babis_refresh", "agent": "babis", "started_at": "a", "finished_at": "b", "seconds": 5},
                             {"key": "giannis_triage", "agent": "giannis", "started_at": "b", "finished_at": None}]}
        (self.root / "output" / "pipeline_step_timings_latest.json").write_text(json.dumps(timings), encoding="utf-8")
        views = cc.Views(self.root)
        steps = views._steps({"run_id": "full_research_x"})
        self.assertEqual((steps["current_step"], [s["agent"] for s in steps["steps"]]), ("giannis_triage", ["babis", "giannis"]))
        self.assertIsNone(views._steps({"run_id": "another_run"}))

    def test_an_interrupted_run_is_a_finished_state_not_a_stalled_one_and_a_new_run_may_start(self):
        pointer = {"run_id": "full_research_x", "status": "interrupted", "pid": 999999, "last_step": "control_plane_batch"}
        (self.root / "output" / "full_research_control_plane_background_latest.json").write_text(json.dumps(pointer), encoding="utf-8")
        status = cc.Views(self.root).status()["run"]
        self.assertEqual((status["status"], status["stalled"], status["process_alive"]), ("interrupted", False, False))
        response, body = self.action("/api/actions/start-research-run", {})
        self.assertEqual(response.status, 200, body)

    def test_status_view_works_without_any_run(self):
        response, body = self.request("GET", "/api/status")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body)["runs"], [])


if __name__ == "__main__":
    unittest.main()

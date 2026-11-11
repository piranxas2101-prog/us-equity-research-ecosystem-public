"""Control center: security boundary and actions (no real registry or launcher)."""
from __future__ import annotations

from datetime import datetime
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

    def test_trading_view_reads_the_session_reports_and_never_writes(self):
        response, body = self.request("GET", "/api/trading")
        self.assertEqual((response.status, json.loads(body)["report"]), (200, None))
        folder = self.root / "output" / "paper_session"
        folder.mkdir()
        for name, average in (("20261109", -0.05), ("20261110", -1.76)):
            (folder / f"session_report_{name}.json").write_text(json.dumps({
                "session_date": f"{name[:4]}-{name[4:6]}-{name[6:]}", "spy_open_to_close_pct": 0.28,
                "day_result": {"positions": 25, "average_result_pct": average, "winners": 9, "losers": 15},
                "entries": {"total": 37}, "koulis": {"approved_count": 168, "approved": [{"ticker": "X"}] * 5},
                "held_positions": [{"ticker": "AEM", "result_pct": 1.0}, {"ticker": "NVDA", "result_pct": None}],
                "flip_shadow": {"flips": 17, "cases": [{"ticker": "X"}]},
                "news": {"statuses": {"found": 1}, "material": [{"symbol": "NVDA"}] * 80, "unknown_symbols": ["Q"]},
            }), encoding="utf-8")
        (folder / "session_report_latest.json").write_text("{}", encoding="utf-8")
        before = sorted(p.name for p in folder.iterdir())
        data = json.loads(self.request("GET", "/api/trading")[1])
        self.assertEqual((data["session"], data["sessions"]), ("20261110", ["20261110", "20261109"]))
        self.assertEqual(data["history"][0]["held_average_pct"], 1.0)        # the unknown result is not a zero
        self.assertNotIn("approved", data["report"]["koulis"])
        self.assertNotIn("cases", data["report"]["flip_shadow"])
        self.assertEqual((len(data["report"]["news"]["material"]), data["report"]["news"]["material_total"]), (60, 80))
        self.assertIsNone(data["learning"])
        older = json.loads(self.request("GET", "/api/trading?session=20261109")[1])
        self.assertEqual(older["report"]["day_result"]["average_result_pct"], -0.05)
        fallback = json.loads(self.request("GET", "/api/trading?session=..%2F..%2Fsecret")[1])
        self.assertEqual(fallback["session"], "20261110")                    # an unknown name falls back, no path use
        self.assertEqual(sorted(p.name for p in folder.iterdir()), before)

    def test_live_view_builds_today_from_the_session_files_and_says_how_fresh_they_are(self):
        quiet = json.loads(self.request("GET", "/api/trading/live")[1])
        self.assertEqual((quiet["active"], quiet["report"]), (False, None))
        self.assertTrue(all(s["updated_at"] is None for s in quiet["sources"]))     # no file: unknown, not a time
        stamp = datetime.now().astimezone().isoformat()
        out = self.root / "output"
        (out / "multi_day_monitor_latest.json").write_text(json.dumps({
            "run_timestamp": stamp, "positions": 2, "holds_count": 2, "actions_count": 0,
            "holds": [{"ticker": "AEM", "agent": "yankos", "trade_direction": "long", "position_plpc": 0.0123},
                      {"ticker": "NVDA", "agent": "yankos", "trade_direction": "short", "position_plpc": None}]}),
            encoding="utf-8")
        (out / "miltos_entry_stalker_latest.json").write_text(json.dumps({
            "run_timestamp": stamp, "entry_window_open": True, "still_waiting_count": 1,
            "still_waiting": [{"agent_name": "yankos", "ticker": "MU", "trade_direction": "long",
                               "entry_trigger_reason": "no break"}]}), encoding="utf-8")
        before = sorted(p.name for p in out.rglob("*"))
        live = json.loads(self.request("GET", "/api/trading/live")[1])
        self.assertTrue(live["active"])
        held = {h["ticker"]: h["result_pct"] for h in live["report"]["held_positions"]}
        self.assertEqual(held, {"AEM": 1.23, "NVDA": None})
        self.assertIsNone(live["report"]["spy_open_to_close_pct"])                   # known only after the close
        self.assertEqual(live["miltos"]["still_waiting"][0]["ticker"], "MU")
        self.assertEqual(live["monitors"]["multi_day_monitor"]["positions"], 2)
        fresh = {s["key"]: s["today"] for s in live["sources"]}
        self.assertEqual((fresh["multi_day"], fresh["miltos"], fresh["koulis"]), (True, True, False))
        self.assertEqual(sorted(p.name for p in out.rglob("*")), before)             # nothing written

    def test_the_logo_is_the_owners_picture_when_saved_else_the_drawing(self):
        assets = Path(self.tmp.name) / "assets"
        assets.mkdir()
        (assets / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        views = cc.Views(self.root)
        self.assertEqual(views.logo(assets)[1], "image/svg+xml")
        (assets / "logo_local.png").write_bytes(b"\x89PNG")
        self.assertEqual(views.logo(assets), (b"\x89PNG", "image/png"))
        response, body = self.request("GET", "/logo")
        self.assertEqual((response.status, response.getheader("Content-Type")), (200, "image/svg+xml"))
        self.assertIn(b"<svg", body)

    def test_learning_counts_come_from_the_indexes_and_are_kept_until_the_file_changes(self):
        import sqlite3

        (self.root / "data").mkdir()
        path = self.root / "data" / "market_intel.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE agent_learning_episodes (episode_id TEXT PRIMARY KEY, agent_id TEXT, observed_at TEXT)")
        conn.execute("CREATE TABLE agent_learning_outcomes (outcome_id TEXT PRIMARY KEY, episode_id TEXT)")
        conn.execute("CREATE TABLE agent_model_registry (agent_id TEXT, task_id TEXT, model_version TEXT, state TEXT,"
                     " training_rows INTEGER, updated_at TEXT)")
        conn.executemany("INSERT INTO agent_learning_episodes VALUES (?,?,?)",
                         [("e1", "yankos", "2026-11-09"), ("e2", "yankos", "2026-11-10"), ("e3", "babis", "2026-11-08")])
        conn.executemany("INSERT INTO agent_learning_outcomes VALUES (?,?)", [("o1", "e1"), ("o2", "e1"), ("o3", "e2")])
        conn.commit()
        conn.close()
        views = cc.Views(self.root)
        learning = views.agents()["learning"]
        self.assertEqual(learning, [
            {"agent_id": "yankos", "episodes": 2, "outcomes": 3, "last_recorded": "2026-11-10"},
            {"agent_id": "babis", "episodes": 1, "outcomes": 0, "last_recorded": "2026-11-08"}])
        self.assertIs(views.agents()["learning"], learning)          # the file did not change: the kept answer

    def test_only_reads_exist_for_trading(self):
        for path in ("/api/trading", "/api/trading/live", "/logo"):
            response, _ = self.action(path, {})
            self.assertEqual(response.status, 404)

    def test_status_view_works_without_any_run(self):
        response, body = self.request("GET", "/api/status")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body)["runs"], [])


if __name__ == "__main__":
    unittest.main()

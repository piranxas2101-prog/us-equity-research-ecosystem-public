"""Listing sources: a refusal is asked again; the profiles may be the last good file for three days, said openly;
the symbol directories are never reused."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from research_registry import acquire_universe as au

NOW = datetime(2026, 11, 11, 0, 34, tzinfo=timezone.utc)
PROFILE = (au.PROFILES, "https://example.test/profiles")
DIRECTORY = ("nasdaqlisted.txt", "https://example.test/listed")


def refused(url):
    raise HTTPError(url, 403, "Forbidden", None, None)


class FetchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dest = Path(tmp.name)
        self.sleeps: list[float] = []

    def fetch(self, item, get, previous=None):
        return au.fetch(item, dest=self.dest, previous=previous, get=get, sleep=self.sleeps.append, now=NOW)

    def test_a_refusal_is_asked_again(self):
        answers = iter([refused, refused, lambda url: b'{"data": 1}'])
        out = self.fetch(PROFILE, lambda url: next(answers)(url))
        self.assertEqual((out["status"], out["attempts"], self.sleeps), ("ok", 3, [5, 15]))
        self.assertEqual(json.loads((self.dest / au.PROFILES).read_bytes()), {"data": 1})

    def test_the_last_good_profiles_are_reused_and_said(self):
        (self.dest / au.PROFILES).write_bytes(b'{"data": "yesterday"}')
        previous = {"name": au.PROFILES, "status": "ok", "retrieved_at": (NOW - timedelta(hours=24)).isoformat()}
        out = self.fetch(PROFILE, refused, previous)
        self.assertEqual((out["status"], out["attempts"], out["age_hours"]), (au.STALE, 4, 24.0))
        self.assertEqual(out["retrieved_at"], previous["retrieved_at"])     # when it was really retrieved
        self.assertEqual(out["attempted_at"], NOW.isoformat())
        self.assertEqual(out["last_failure"], {"status": "http_error", "http_status": 403})
        again = self.fetch(PROFILE, refused, out)                           # a second bad night: still its real age
        self.assertEqual(again["retrieved_at"], previous["retrieved_at"])

    def aged_file(self, hours):
        path = self.dest / au.PROFILES
        path.write_bytes(b'{"data": "old"}')
        stamp = (NOW - timedelta(hours=hours)).timestamp()
        os.utime(path, (stamp, stamp))

    def test_too_old_or_unknown_is_not_reused(self):
        self.aged_file(73)
        old = {"name": au.PROFILES, "status": "ok", "retrieved_at": (NOW - timedelta(hours=73)).isoformat()}
        self.assertEqual(self.fetch(PROFILE, refused, old)["status"], "http_error")
        self.assertEqual(self.fetch(PROFILE, refused, None)["status"], "http_error")
        failed = {"name": au.PROFILES, "status": "http_error", "retrieved_at": NOW.isoformat()}
        self.assertEqual(self.fetch(PROFILE, refused, failed)["status"], "http_error")
        (self.dest / au.PROFILES).unlink()
        self.assertEqual(self.fetch(PROFILE, refused, failed)["status"], "http_error")       # no file at all

    def test_a_second_bad_night_reuses_by_the_files_own_time(self):
        # 2026-11-12: the night before had failed, the manifest held only that failure and the reuse never applied
        self.aged_file(48)
        failed = {"name": au.PROFILES, "status": "http_error", "http_status": 403, "retrieved_at": NOW.isoformat()}
        out = self.fetch(PROFILE, refused, failed)
        self.assertEqual((out["status"], out["age_hours"], out["age_from"]), (au.STALE, 48.0, "file_time"))
        self.assertEqual(out["retrieved_at"], (NOW - timedelta(hours=48)).isoformat())
        self.assertEqual(self.fetch(PROFILE, refused, None)["status"], au.STALE)             # no manifest either
        third = self.fetch(PROFILE, refused, out)                                            # then from the manifest
        self.assertEqual((third["retrieved_at"], third["age_from"]), (out["retrieved_at"], "manifest"))

    def test_the_daytime_refresh_replaces_only_the_profiles_row(self):
        night = [{"name": DIRECTORY[0], "status": "ok", "retrieved_at": "n"},
                 {"name": au.PROFILES, "status": "http_error", "http_status": 403, "retrieved_at": "n"}]
        (self.dest / "manifest.json").write_text(json.dumps(night), encoding="utf-8")
        out = au.refresh_profiles(dest=self.dest, get=lambda url: b'{"data": "noon"}', sleep=self.sleeps.append, now=NOW)
        self.assertEqual((out["refreshed"], out["attempts"]), (True, 1))
        rows = {r["name"]: r for r in json.loads((self.dest / "manifest.json").read_text(encoding="utf-8"))}
        self.assertEqual(rows[DIRECTORY[0]], night[0])
        self.assertEqual((rows[au.PROFILES]["status"], rows[au.PROFILES]["retrieved_at"]), ("ok", NOW.isoformat()))
        tonight = self.fetch(PROFILE, refused, rows[au.PROFILES])                  # the night falls back on it
        self.assertEqual((tonight["status"], tonight["age_from"]), (au.STALE, "manifest"))

    def test_a_refused_daytime_refresh_changes_nothing(self):
        self.aged_file(10)
        (self.dest / "manifest.json").write_text("[]", encoding="utf-8")
        before = (self.dest / au.PROFILES).read_bytes(), (self.dest / au.PROFILES).stat().st_mtime
        out = au.refresh_profiles(dest=self.dest, get=refused, sleep=self.sleeps.append, now=NOW)
        self.assertEqual(out, {"refreshed": False, "status": "http_error", "http_status": 403, "attempts": 4})
        self.assertEqual(((self.dest / au.PROFILES).read_bytes(), (self.dest / au.PROFILES).stat().st_mtime), before)
        self.assertEqual((self.dest / "manifest.json").read_text(encoding="utf-8"), "[]")

    def test_a_symbol_directory_is_never_reused(self):
        (self.dest / DIRECTORY[0]).write_bytes(b"Symbol|Security Name|\n")
        previous = {"name": DIRECTORY[0], "status": "ok", "retrieved_at": (NOW - timedelta(hours=1)).isoformat()}
        self.assertEqual(self.fetch(DIRECTORY, refused, previous)["status"], "http_error")


class TransportTests(unittest.TestCase):
    """2026-11-12: the screener answered 403 to urllib and 200 to requests in the same minute."""

    def test_the_second_transport_is_asked_when_the_first_is_refused(self):
        asked = []

        def good(url):
            asked.append("good")
            return b"{}"

        def bad(url):
            asked.append("bad")
            return refused(url)

        self.assertEqual(au.download("u", (bad, good)), b"{}")
        self.assertEqual(au.download("u", (good, bad)), b"{}")
        self.assertEqual(asked, ["bad", "good", "good"])

    def test_all_refused_raises_the_http_refusal(self):
        def broken(url):
            raise OSError("no network")

        with self.assertRaises(HTTPError) as caught:
            au.download("u", (broken, refused))
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(OSError):
            au.download("u", (broken,))

    def test_requests_comes_first_and_a_bad_status_is_a_refusal(self):
        from unittest import mock

        self.assertEqual(au.TRANSPORTS, (au.via_requests, au.via_urllib))
        answer = mock.Mock(status_code=403, reason="Forbidden", content=b"")
        with mock.patch("requests.get", return_value=answer) as get:
            with self.assertRaises(HTTPError) as caught:
                au.via_requests("https://example.test/x")
        self.assertEqual((caught.exception.code, get.call_args.kwargs["timeout"]), (403, 25))
        with mock.patch("requests.get", return_value=mock.Mock(status_code=200, content=b"{}")):
            self.assertEqual(au.via_requests("https://example.test/x"), b"{}")


class BuildReadsTheSameRuleTests(unittest.TestCase):
    """2026-11-12: the fetch reused the profiles and the universe build still refused them."""

    def sources(self, profile_status, directory_status="ok", profile_bytes=b'{"data": {"rows": []}}'):
        import hashlib

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        rows = []
        for name, raw, status in (("nasdaqlisted.txt", b"Symbol|Security Name|\n", directory_status),
                                  ("otherlisted.txt", b"ACT Symbol|Security Name|\n", "ok"),
                                  (au.PROFILES, b'{"data": {"rows": []}}', profile_status)):
            (folder / name).write_bytes(profile_bytes if name == au.PROFILES else raw)
            rows.append({"name": name, "status": status, "retrieved_at": "t", "sha256": hashlib.sha256(raw).hexdigest()})
        (folder / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")
        return folder

    def test_reused_profiles_are_read_and_nothing_else_is_let_through(self):
        from research_registry import full_universe

        metadata, raws = full_universe.read_sources(self.sources(au.STALE))
        self.assertEqual((len(metadata), sorted(raws)), (3, sorted(["nasdaqlisted.txt", "otherlisted.txt", au.PROFILES])))
        with self.assertRaisesRegex(ValueError, "Source unavailable: nasdaq_profiles.json"):
            full_universe.read_sources(self.sources("http_error"))
        with self.assertRaisesRegex(ValueError, "Source unavailable: nasdaqlisted.txt"):
            full_universe.read_sources(self.sources("ok", directory_status=au.STALE))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):       # a reused file must still be that file
            full_universe.read_sources(self.sources(au.STALE, profile_bytes=b'{"data": "changed"}'))


class RuleTests(unittest.TestCase):
    def test_usable_and_what_is_stale(self):
        ok = [{"name": n, "status": "ok"} for n in au.URLS]
        self.assertTrue(au.usable(ok))
        self.assertEqual(au.stale_sources(ok), [])
        reused = [{**r, "status": au.STALE, "retrieved_at": "t", "age_hours": 24.0} if r["name"] == au.PROFILES else r
                  for r in ok]
        self.assertTrue(au.usable(reused))
        self.assertEqual(au.stale_sources(reused), [{"name": au.PROFILES, "retrieved_at": "t", "age_hours": 24.0}])
        directory = [{**r, "status": au.STALE} if r["name"] == "otherlisted.txt" else r for r in ok]
        self.assertFalse(au.usable(directory))
        self.assertFalse(au.usable(ok[:2]))
        self.assertFalse(au.usable([{**ok[0], "status": "http_error"}, *ok[1:]]))


if __name__ == "__main__":
    unittest.main()

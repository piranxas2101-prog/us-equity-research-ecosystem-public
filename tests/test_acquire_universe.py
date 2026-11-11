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

    def test_a_symbol_directory_is_never_reused(self):
        (self.dest / DIRECTORY[0]).write_bytes(b"Symbol|Security Name|\n")
        previous = {"name": DIRECTORY[0], "status": "ok", "retrieved_at": (NOW - timedelta(hours=1)).isoformat()}
        self.assertEqual(self.fetch(DIRECTORY, refused, previous)["status"], "http_error")


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

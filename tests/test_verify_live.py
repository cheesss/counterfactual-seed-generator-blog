from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_live

SITE = "https://example.test/site"


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


MANIFEST = {"files": [
    {"path": "index.html", "sha256": sha(b"index")},
    {"path": "posts/a b.html", "sha256": sha(b"post")},
    {"path": "assets/heroes/new.webp", "sha256": sha(b"new-image")},
    {"path": "assets/heroes/old.webp", "sha256": sha(b"old-image")},
]}


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class VerifyLiveTests(unittest.TestCase):
    def test_selects_all_text_files_and_only_changed_binaries(self):
        rows = verify_live.selected_rows(MANIFEST, {"assets/heroes/new.webp", "README.md"})
        self.assertEqual(["index.html", "posts/a b.html", "assets/heroes/new.webp"], [row["path"] for row in rows])

    def test_mismatch_and_fetch_failure_are_reported(self):
        live = {f"{SITE}/index.html": b"index", f"{SITE}/posts/a%20b.html": b"stale"}

        def fetcher(url):
            if url not in live:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return live[url]

        problems = verify_live.live_mismatches(verify_live.selected_rows(MANIFEST, {"assets/heroes/new.webp"}),
                                               site_url=SITE, fetcher=fetcher)
        self.assertEqual({"posts/a b.html", "assets/heroes/new.webp"}, set(problems))
        self.assertIn("live sha256", problems["posts/a b.html"])
        self.assertIn("fetch failed", problems["assets/heroes/new.webp"])

    def test_retries_only_pending_files_with_backoff_until_cdn_catches_up(self):
        clock = FakeClock()
        calls = []

        def fetcher(url):
            calls.append(url)
            path = url.split("/site/", 1)[1].split("?", 1)[0]
            if path == "index.html" and clock.now < 40:
                return b"stale"
            return {"index.html": b"index", "posts/a%20b.html": b"post"}[path]

        rows = verify_live.selected_rows(MANIFEST, set())
        problems = verify_live.verify(rows, site_url=SITE, max_wait=600, fetcher=fetcher, sleep=clock.sleep,
                                      clock=clock.clock, nonce="abc", log=lambda _: None)
        self.assertEqual({}, problems)
        self.assertEqual([15.0, 30.0], clock.sleeps)
        self.assertEqual(4, len(calls))  # both once, then only index.html twice
        self.assertTrue(all("?verify=abc-" in url for url in calls))

    def test_gives_up_at_the_deadline_with_the_remaining_mismatches(self):
        clock = FakeClock()
        problems = verify_live.verify([MANIFEST["files"][0]], site_url=SITE, max_wait=100,
                                      fetcher=lambda url: b"never", sleep=clock.sleep, clock=clock.clock,
                                      log=lambda _: None)
        self.assertEqual({"index.html"}, set(problems))
        self.assertLessEqual(clock.now, 100)
        self.assertEqual(100, sum(clock.sleeps))

    def test_changed_paths_use_the_push_range_or_fall_back_to_the_last_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)

            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                                      check=True).stdout.strip()

            git("init", "-q")
            git("config", "user.email", "t@example.test")
            git("config", "user.name", "t")
            (repo / "a.txt").write_text("1", encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "one")
            first = git("rev-parse", "HEAD")
            (repo / "b.png").write_bytes(b"x")
            git("add", ".")
            git("commit", "-q", "-m", "two")
            (repo / "c.png").write_bytes(b"y")
            git("add", ".")
            git("commit", "-q", "-m", "three")
            head = git("rev-parse", "HEAD")
            self.assertEqual({"b.png", "c.png"}, verify_live.changed_paths(first, head, repo))
            self.assertEqual({"c.png"}, verify_live.changed_paths(verify_live.ZERO_SHA, head, repo))
            self.assertEqual({"c.png"}, verify_live.changed_paths("f" * 40, head, repo))

    def test_identifies_as_the_bot_not_a_browser(self):
        self.assertIn("BottleneckBriefsBot", verify_live.USER_AGENT)
        self.assertNotIn("Mozilla", verify_live.USER_AGENT)


if __name__ == "__main__":
    unittest.main()

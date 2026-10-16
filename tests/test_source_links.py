from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import issue_notify
import source_links
from source_links import Response

POST = """<html><body>
<header><a href="https://github.com/cheesss/counterfactual-seed-generator-blog">repo</a></header>
<figure><a href="https://commons.wikimedia.org/wiki/File:x.png">photo credit</a></figure>
<section class="article-body">
  <p>See <a href="https://a.example/doc#part">doc</a> and <a href="https://a.example/doc">again</a>.</p>
  <section class="inner"><a href="https://b.example/x?y=1&amp;z=2">nested</a></section>
  <a href="/counterfactual-seed-generator-blog/ledger.html">internal</a>
  <a href="mailto:x@example.test">mail</a>
  <p><a href="https://c.example/after-inner">still body</a></p>
</section>
<footer><a href="https://d.example/footer">footer</a></footer>
</body></html>"""


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class SourceLinkTests(unittest.TestCase):
    def test_only_http_links_inside_the_article_body_are_collected(self):
        self.assertEqual(["https://a.example/doc", "https://a.example/doc", "https://b.example/x?y=1&z=2",
                          "https://c.example/after-inner"], source_links.article_links(POST))
        with tempfile.TemporaryDirectory() as temp:
            posts = Path(temp)
            (posts / "one.html").write_text(POST, encoding="utf-8")
            (posts / "two.html").write_text(POST.replace("c.example", "e.example"), encoding="utf-8")
            cited = source_links.collect(posts)
        self.assertEqual(["one.html", "two.html"], cited["https://a.example/doc"])
        self.assertEqual(["one.html"], cited["https://c.example/after-inner"])
        self.assertEqual(4, len(cited))

    def test_only_404_and_410_are_dead(self):
        for status, verdict in ((404, "dead"), (410, "dead"), (200, "ok"), (301, "ok"), (401, "unverified"),
                                (403, "unverified"), (429, "unverified"), (500, "unverified"),
                                (503, "unverified"), (400, "unverified")):
            self.assertEqual(verdict, source_links.classify(Response(status=status))[0], status)
        self.assertEqual("unverified", source_links.classify(Response(error="TimeoutError: timed out"))[0])

    def test_hosts_are_spaced_and_interleaved(self):
        clock = FakeTime()
        seen = []

        def prober(url):
            seen.append((url, clock.now))
            clock.now += 1
            return Response(status=200)

        urls = ["https://a.example/1", "https://a.example/2", "https://b.example/1"]
        outcomes = source_links.scan(urls, prober=prober, host_spacing=5, clock=clock.clock, sleep=clock.sleep)
        self.assertEqual(["https://a.example/1", "https://b.example/1", "https://a.example/2"], [u for u, _ in seen])
        self.assertGreaterEqual(seen[2][1] - (seen[0][1] + 1), 5)
        self.assertTrue(all(outcome.verdict == "ok" for outcome in outcomes))

    def test_retry_after_is_honoured_once_and_long_waits_back_off_the_host(self):
        clock = FakeTime()
        replies = {"https://a.example/1": [Response(status=429, retry_after="30"), Response(status=200)],
                   "https://b.example/1": [Response(status=503, retry_after="3600")],
                   "https://b.example/2": [Response(status=200)],
                   "https://c.example/1": [Response(status=429)]}
        calls = []

        def prober(url):
            calls.append(url)
            return replies[url].pop(0)

        outcomes = {item.url: item for item in source_links.scan(
            replies, prober=prober, host_spacing=1, clock=clock.clock, sleep=clock.sleep)}
        self.assertEqual("ok", outcomes["https://a.example/1"].verdict)
        self.assertIn(30, [round(value) for value in clock.sleeps])
        self.assertEqual("unverified", outcomes["https://b.example/1"].verdict)
        self.assertIn("asked to wait 3600s", outcomes["https://b.example/2"].detail)
        self.assertNotIn("https://b.example/2", calls)
        self.assertEqual("unverified", outcomes["https://c.example/1"].verdict)
        self.assertEqual(1, calls.count("https://c.example/1"))

    def test_retry_after_http_date(self):
        now = dt.datetime(2026, 10, 16, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(90, source_links.retry_seconds("Fri, 16 Oct 2026 12:01:30 GMT", now))
        self.assertEqual(7, source_links.retry_seconds("7"))
        self.assertIsNone(source_links.retry_seconds("soon"))

    def test_request_cap_and_time_budget_leave_the_rest_unchecked(self):
        clock = FakeTime()
        urls = [f"https://h{i}.example/" for i in range(5)]
        outcomes = source_links.scan(urls, prober=lambda url: Response(status=200), max_requests=2,
                                     clock=clock.clock, sleep=clock.sleep)
        self.assertEqual(["ok", "ok", "unchecked", "unchecked", "unchecked"], [o.verdict for o in outcomes])
        self.assertIn("request cap", outcomes[-1].detail)

        def slow(url):
            clock.now += 50
            return Response(status=200)
        outcomes = source_links.scan(urls, prober=slow, max_seconds=120, clock=clock.clock, sleep=clock.sleep)
        self.assertEqual(3, sum(o.verdict == "ok" for o in outcomes))
        self.assertIn("time budget", outcomes[-1].detail)

    def test_issue_body_and_single_issue_lifecycle(self):
        outcomes = [source_links.Outcome("https://a.example/gone", "dead", "HTTP 404"),
                    source_links.Outcome("https://b.example/x", "unverified", "HTTP 403")]
        body = source_links.markdown(outcomes, {"https://a.example/gone": ["one.html"]}, dt.date(2026, 10, 16))
        self.assertIn("1 dead (404/410), 1 unverified", body)
        self.assertIn("- [ ] HTTP 404 <https://a.example/gone> in [one.html]", body)
        self.assertIn("HTTP 403 <https://b.example/x>", body)
        self.assertIn("only notifies", body)

        calls = []

        def runner(*args):
            calls.append(args)
            return '[{"number": 3, "state": "OPEN"}]' if args[:2] == ("issue", "list") else ""
        number = issue_notify.publish_issue("clean", open_items=False, title="t", label="source-links",
                                            color="d93f0b", description="d", clean_comment="clean now",
                                            runner=runner)
        self.assertEqual("3", number)
        self.assertIn(("issue", "close", "3", "--comment", "clean now"), calls)
        calls.clear()
        empty = lambda *args: (calls.append(args) or ("[]" if args[:2] == ("issue", "list") else "url"))
        self.assertEqual("", issue_notify.publish_issue("b", open_items=False, title="t", label="l", color="c",
                                                        description="d", clean_comment="x", runner=empty))
        self.assertFalse(any(args[:2] == ("issue", "create") for args in calls))
        self.assertEqual("url", issue_notify.publish_issue("b", open_items=True, title="t", label="l", color="c",
                                                           description="d", clean_comment="x", runner=empty))

    def test_main_publishes_the_issue_only_with_dead_links(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "one.html").write_text(POST, encoding="utf-8")
            with mock.patch.object(source_links, "probe", return_value=Response(status=404)), \
                    mock.patch.object(source_links.time, "sleep"), \
                    mock.patch.object(issue_notify, "publish_issue", return_value="9") as publish, \
                    mock.patch("sys.stdout"):
                self.assertEqual(0, source_links.main(["--posts", temp, "--issue", "--host-spacing", "0"]))
        self.assertTrue(publish.call_args.kwargs["open_items"])
        self.assertEqual("source-links", publish.call_args.kwargs["label"])

    def test_bot_identity_and_long_bodies_are_clipped(self):
        self.assertTrue(source_links.USER_AGENT.startswith("BottleneckBriefsBot (+https://github.com/"))
        self.assertNotIn("Mozilla", source_links.USER_AGENT)
        self.assertLessEqual(len(issue_notify.clip("x\n" * 50000)), issue_notify.MAX_BODY + 100)

    def test_workflow_is_weekly_notify_only(self):
        workflow = (ROOT / ".github/workflows/source-links.yml").read_text(encoding="utf-8")
        for needle in ("schedule:", "workflow_dispatch:", "issues: write", "contents: read",
                       "python scripts/source_links.py --posts posts --issue", "GH_TOKEN: ${{ github.token }}",
                       "timeout-minutes: 60"):
            self.assertIn(needle, workflow)
        for forbidden in ("contents: write", "pull_request", "git push", "pages: write"):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()

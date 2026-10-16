#!/usr/bin/env python3
"""Weekly, notify-only scan of the source links inside article bodies.

Collects http(s) links inside `<section class="article-body">` of posts/*.html,
requests each unique URL once (GET, body not read) as BottleneckBriefsBot and
classifies the final response:

  dead        404 or 410 only
  unverified  401, 403, 429, 5xx, other 4xx, timeouts and network errors
              (the source may be fine; the server would not confirm it)
  ok          2xx or 3xx after redirects

Requests are spaced per host, Retry-After is honoured (a host asking for a
longer pause is not contacted again this run) and the total number of requests
and the run time are capped. With --issue it keeps one GitHub issue current:
open while dead links exist, closed when none remain. It never edits an
article; corrections go through the source vault.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urldefrag, urlsplit

import issue_notify

SITE_URL = "https://cheesss.github.io/counterfactual-seed-generator-blog"
USER_AGENT = "BottleneckBriefsBot (+https://github.com/cheesss/counterfactual-seed-generator-blog)"
ISSUE_TITLE = "Dead source links"
ISSUE_LABEL = "source-links"
DEAD = {404, 410}


@dataclass
class Response:
    status: int | None = None
    retry_after: str | None = None
    error: str = ""


@dataclass
class Outcome:
    url: str
    verdict: str  # ok | dead | unverified | unchecked
    detail: str


class ArticleLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "section":
            if self.depth:
                self.depth += 1
            elif "article-body" in (values.get("class") or "").split():
                self.depth = 1
        href = (values.get("href") or "").strip()
        if tag == "a" and self.depth and href.lower().startswith(("http://", "https://")):
            self.links.append(urldefrag(href)[0])

    def handle_endtag(self, tag: str) -> None:
        if tag == "section" and self.depth:
            self.depth -= 1


def article_links(html: str) -> list[str]:
    parser = ArticleLinks()
    parser.feed(html)
    return parser.links


def collect(posts: Path) -> dict[str, list[str]]:
    """Unique URL -> the posts citing it."""
    found: dict[str, list[str]] = defaultdict(list)
    for path in sorted(posts.glob("*.html")):
        for url in dict.fromkeys(article_links(path.read_text(encoding="utf-8"))):
            found[url].append(path.name)
    return dict(found)


def probe(url: str, timeout: float = 20.0) -> Response:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Response(status=response.status)
    except urllib.error.HTTPError as exc:
        retry = exc.headers.get("Retry-After") if exc.headers else None
        exc.close()
        return Response(status=exc.code, retry_after=retry)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return Response(error=f"{type(exc).__name__}: {reason}"[:160])


def retry_seconds(value: str | None, now: dt.datetime | None = None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (when - (now or dt.datetime.now(dt.timezone.utc))).total_seconds())


def classify(response: Response) -> tuple[str, str]:
    if response.status is None:
        return "unverified", response.error or "no response"
    if response.status in DEAD:
        return "dead", f"HTTP {response.status}"
    if response.status < 400:
        return "ok", f"HTTP {response.status}"
    return "unverified", f"HTTP {response.status}"


def interleave(urls: Iterable[str]) -> list[str]:
    """Round-robin across hosts so per-host spacing rarely has to wait."""
    by_host: dict[str, list[str]] = defaultdict(list)
    for url in sorted(urls):
        by_host[urlsplit(url).hostname or ""].append(url)
    queues = [by_host[host] for host in sorted(by_host)]
    ordered = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered


def scan(urls: Iterable[str], *, prober: Callable[[str], Response] | None = None, host_spacing: float = 5.0,
         max_requests: int = 1500, max_seconds: float = 2700.0, max_retry_after: float = 120.0,
         clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None) -> list[Outcome]:
    prober, clock, sleep = prober or probe, clock or time.monotonic, sleep or time.sleep
    start = clock()
    next_allowed: dict[str, float] = {}
    backed_off: dict[str, str] = {}
    requests = 0
    outcomes = []
    for url in interleave(urls):
        host = urlsplit(url).hostname or ""
        if host in backed_off:
            outcomes.append(Outcome(url, "unverified", backed_off[host]))
            continue
        verdict, detail = "unchecked", ""
        for attempt in range(2):  # one retry, only after a honoured Retry-After
            if requests >= max_requests:
                detail = f"request cap of {max_requests} reached"
                break
            wait = next_allowed.get(host, 0.0) - clock()
            if clock() + max(wait, 0.0) - start > max_seconds:
                detail = f"time budget of {int(max_seconds)}s reached"
                break
            if wait > 0:
                sleep(wait)
            requests += 1
            response = prober(url)
            next_allowed[host] = clock() + host_spacing
            verdict, detail = classify(response)
            if response.status in {429, 503} and attempt == 0:
                pause = retry_seconds(response.retry_after)
                if pause is None:
                    break
                if pause > max_retry_after:
                    backed_off[host] = f"HTTP {response.status}; host asked to wait {int(pause)}s"
                    detail = backed_off[host]
                    break
                next_allowed[host] = clock() + max(pause, host_spacing)
                continue
            break
        outcomes.append(Outcome(url, verdict, detail))
    return outcomes


def markdown(outcomes: list[Outcome], cited: dict[str, list[str]], today: dt.date) -> str:
    groups: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        groups[outcome.verdict].append(outcome)
    posts = len({post for urls in cited.values() for post in urls})
    lines = [f"Checked {today.isoformat()}: {len(outcomes)} unique article-body source link(s) in {posts} post(s); "
             f"{len(groups['ok'])} ok, {len(groups['dead'])} dead (404/410), {len(groups['unverified'])} unverified, "
             f"{len(groups['unchecked'])} not checked (request/time cap).", ""]

    def cite(url: str) -> str:
        names = cited.get(url, [])
        shown = ", ".join(f"[{name}]({SITE_URL}/posts/{name})" for name in names[:3])
        return shown + (f" and {len(names) - 3} more" if len(names) > 3 else "")

    if groups["dead"]:
        lines += ["Dead links need a sourced replacement or an archived copy, corrected in the source vault and "
                  "republished through the normal release path:", ""]
        lines += [f"- [ ] {item.detail} <{item.url}> in {cite(item.url)}" for item in groups["dead"]]
    else:
        lines.append("No article-body source link returned 404 or 410.")
    if groups["unverified"]:
        lines += ["", "<details><summary>Unverified (server did not confirm; not treated as dead)</summary>", ""]
        lines += [f"- {item.detail} <{item.url}>" for item in groups["unverified"][:300]]
        if len(groups["unverified"]) > 300:
            lines.append(f"- ... and {len(groups['unverified']) - 300} more")
        lines += ["", "</details>"]
    lines += ["", f"Requests identify as `{USER_AGENT}`. This issue only notifies; it never edits an article."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--posts", type=Path, default=Path("posts"))
    parser.add_argument("--max-requests", type=int, default=1500)
    parser.add_argument("--max-seconds", type=float, default=2700.0)
    parser.add_argument("--host-spacing", type=float, default=5.0)
    parser.add_argument("--issue", action="store_true", help="create/update the tracking issue with gh")
    args = parser.parse_args(argv)
    cited = collect(args.posts)
    outcomes = scan(cited, max_requests=args.max_requests, max_seconds=args.max_seconds,
                    host_spacing=args.host_spacing)
    body = markdown(outcomes, cited, dt.datetime.now(dt.timezone.utc).date())
    if args.issue:
        dead = any(outcome.verdict == "dead" for outcome in outcomes)
        print(issue_notify.publish_issue(
            body, open_items=dead, title=ISSUE_TITLE, label=ISSUE_LABEL, color="d93f0b",
            description="Article-body source links returning 404/410",
            clean_comment="No article-body source link returns 404 or 410.") or "no issue needed")
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

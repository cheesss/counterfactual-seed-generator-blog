from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import forecast_due

DATA = {"articles": [{"slug": "a", "title": "Column A", "url": "posts/a.html", "watch_signals": [
    {"signal_id": "a:1", "text": "Agency rules by September 1, 2026", "resolve_by": "2026-09-01",
     "resolve_by_text": "September 1, 2026", "state": "open"},
    {"signal_id": "a:2", "text": "Settled already", "resolve_by": "2026-08-01", "state": "confirmed"},
    {"signal_id": "a:3", "text": "Later checkpoint", "resolve_by": "2027-06-30", "state": "open"},
    {"signal_id": "a:4", "text": "Open-ended", "state": "open"},
]}]}


class ForecastDueTests(unittest.TestCase):
    def test_only_open_signals_past_their_checkpoint_are_due(self):
        rows = forecast_due.due_signals(DATA, dt.date(2026, 10, 12))
        self.assertEqual(["a:1"], [row["signal_id"] for row in rows])
        body = forecast_due.markdown(rows, forecast_due.summary(DATA, dt.date(2026, 10, 12)))
        self.assertIn("2026-09-01", body)
        self.assertIn("only notifies", body)

    def test_body_reports_cadence_against_the_weekly_target(self):
        data = {"articles": [{"slug": s, "date": d, "watch_signals": []} for s, d in (
            ("a", "2026-10-10"), ("b", "2026-09-20"), ("c", "2026-09-01"), ("d", "not-a-date"))]}
        stats = forecast_due.summary(data, dt.date(2026, 10, 12))
        self.assertEqual({"last_28_days": 2, "target_last_28_days": 8, "last_published": "2026-10-10"},
                         stats["cadence"])
        body = forecast_due.markdown([], stats)
        self.assertIn("Cadence: 2 article(s) dated in the last 28 days against a target of 8 (below target)", body)
        on_target = {"articles": [{"slug": str(i), "date": "2026-10-01"} for i in range(8)]}
        self.assertIn("(on target)", forecast_due.markdown([], forecast_due.summary(on_target, dt.date(2026, 10, 12))))

    def test_workflow_refreshes_on_publication_data_pushes_and_stays_notify_only(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/forecast-due.yml").read_text(
            encoding="utf-8")
        self.assertIn('    branches:\n      - main\n    paths:\n      - publication-data.json\n', workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("issues: write", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertIn("python scripts/forecast_due.py --data publication-data.json --issue", workflow)

    def test_undated_data_explains_itself(self):
        data = {"articles": [{"slug": "a", "watch_signals": [{"text": "x", "state": "open"}]}]}
        body = forecast_due.markdown([], forecast_due.summary(data, dt.date(2026, 10, 12)))
        self.assertIn("No watch signal carries a structured", body)

    def test_issue_is_closed_when_nothing_is_due_and_never_created_empty(self):
        calls = []

        def fake(*args):
            calls.append(args)
            return '[{"number": 7, "state": "OPEN"}]' if args[:2] == ("issue", "list") else ""
        with mock.patch.object(forecast_due, "gh", side_effect=fake):
            self.assertEqual("7", forecast_due.publish_issue("body", open_items=False))
        self.assertIn(("issue", "close", "7", "--comment", "No open signal is past its checkpoint."), calls)
        with mock.patch.object(forecast_due, "gh", side_effect=lambda *args: "[]" if args[:2] == ("issue", "list") else ""):
            self.assertEqual("", forecast_due.publish_issue("body", open_items=False))


if __name__ == "__main__":
    unittest.main()

"""
Charts: the answer prompts allow a ```chart block, and the admin dashboard
gets per-day counts. No network.
"""

import unittest
from datetime import datetime, timezone

from admin.routes import _daily_counts
from agents import llm_agent
from agents import rag_agent as rag
from prompts.chart_prompt import CHART_RULES


class DailyCounts(unittest.TestCase):

    TODAY = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)

    def test_counts_per_utc_day_oldest_first_with_empty_days(self):
        result = _daily_counts(
            [
                "2026-10-21T01:00:00+00:00",
                "2026-10-21T23:59:00Z",
                "2026-10-20T10:00:00+00:00",
                "2026-10-18T09:00:00+00:00",
            ],
            days=4,
            today=self.TODAY,
        )
        self.assertEqual(result["days"], ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21"])
        self.assertEqual(result["counts"], [1, 0, 1, 2])

    def test_other_timezones_are_converted_to_utc(self):
        # 02:00 in India (+05:30) on the 21st is still the 20th in UTC.
        result = _daily_counts(["2026-10-21T02:00:00+05:30"], days=2, today=self.TODAY)
        self.assertEqual(result["counts"], [1, 0])

    def test_old_missing_and_invalid_times_are_ignored(self):
        result = _daily_counts(
            ["2026-09-01T00:00:00Z", None, "", "not a date", datetime(2026, 10, 21, tzinfo=timezone.utc)],
            days=3,
            today=self.TODAY,
        )
        self.assertEqual(result["counts"], [0, 0, 1])

    def test_default_window_is_fourteen_days(self):
        self.assertEqual(len(_daily_counts([])["days"]), 14)


class ChartPrompt(unittest.TestCase):

    def test_answer_prompts_describe_the_chart_block(self):
        for prompt in (llm_agent.build_prompt("compare GDP", "data"), rag.build_pdf_prompt("compare", "ctx")):
            self.assertIn("```chart", prompt)
            self.assertIn('"type": "line"', prompt)                 # braces survive str.format
            self.assertIn("Never invent or estimate", prompt)

    def test_rules_are_short(self):
        # Every answer prompt carries them; keep the token cost small.
        self.assertLess(len(CHART_RULES), 1100)


if __name__ == "__main__":
    unittest.main()

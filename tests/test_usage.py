"""python -m unittest test_usage -v   (skill_usage: counting skill calls in Claude Code logs)"""

import json
import os
import tempfile
import time
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path

import skill_usage as u


def tool_line(tid: str, skill: str, ts: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "uuid": "a-" + tid,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": "Skill", "input": {"skill": skill}}]},
        },
        separators=(",", ":"),
    )


def slash_line(uid: str, name: str, ts: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "timestamp": ts,
            "uuid": uid,
            "message": {"content": f"<command-message>x</command-message>\n<command-name>/{name}</command-name>"},
        },
        separators=(",", ":"),
    )


class UsageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)
        self.projects = self.t / "projects"
        self.cache = self.t / "cache.json"
        (self.projects / "p1").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, lines: list[str]) -> Path:
        f = self.projects / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return f

    def test_counts_tool_calls_and_slash_commands_per_month(self):
        self.write(
            "p1/a.jsonl",
            [
                tool_line("t1", "tdd", "2026-09-10T03:00:00Z"),
                tool_line("t2", "tdd", "2026-10-02T03:00:00Z"),
                tool_line("t3", "superpowers:brainstorming", "2026-10-03T03:00:00Z"),
                slash_line("u1", "ui-polish", "2026-10-04T03:00:00Z"),
                slash_line("u2", "clear", "2026-10-04T04:00:00Z"),  # a built-in: counted, the app filters it out
                '{"type":"system","note":"the tools list mentions \\"name\\":\\"Skill\\" too"}',
                "not json at all <command-name>/tdd</command-name>",
            ],
        )
        c = u.monthly_counts(self.projects, self.cache)
        self.assertEqual(c["2026-10"], Counter({"tdd": 1, "superpowers:brainstorming": 1, "ui-polish": 1, "clear": 1}))
        self.assertEqual(c["2026-09"], Counter({"tdd": 1}))
        self.assertEqual(c[u.ALL]["tdd"], 2)

    def test_json_spacing_does_not_matter(self):
        block = {"type": "tool_use", "id": "sp", "name": "Skill", "input": {"skill": "tdd"}}
        spaced = json.dumps(
            {"type": "assistant", "timestamp": "2026-10-02T03:00:00Z", "message": {"content": [block]}}
        )  # `"name": "Skill"`
        self.write("p1/a.jsonl", [spaced])
        self.assertEqual(u.monthly_counts(self.projects, self.cache)["2026-10"]["tdd"], 1)

    def test_repeated_lines_in_a_resumed_conversation_count_once(self):
        line = tool_line("same-id", "tdd", "2026-10-02T03:00:00Z")
        self.write("p1/first.jsonl", [line])
        self.write("p1/resumed.jsonl", [line, tool_line("other", "tdd", "2026-10-05T03:00:00Z")])
        self.assertEqual(u.monthly_counts(self.projects, self.cache)["2026-10"]["tdd"], 2)

    def test_cache_skips_unchanged_files_and_rereads_grown_ones(self):
        f = self.write("p1/a.jsonl", [tool_line("t1", "tdd", "2026-10-02T03:00:00Z")])
        u.monthly_counts(self.projects, self.cache)
        cached = json.loads(self.cache.read_text(encoding="utf-8"))
        cached[str(f)]["events"] = [["fake", "2026-10", "from-cache"]]  # proves the next run reads the cache
        self.cache.write_text(json.dumps(cached), encoding="utf-8")
        self.assertIn("from-cache", u.monthly_counts(self.projects, self.cache)["2026-10"])
        with f.open("a", encoding="utf-8") as fh:  # the conversation went on
            fh.write(tool_line("t2", "pdf", "2026-10-03T03:00:00Z") + "\n")
        os.utime(f, (time.time() + 5, time.time() + 5))
        c = u.monthly_counts(self.projects, self.cache)["2026-10"]
        self.assertEqual((c["tdd"], c["pdf"], c["from-cache"]), (1, 1, 0))

    def test_missing_or_broken_inputs(self):
        self.assertEqual(u.monthly_counts(self.t / "nope", self.cache), {})
        self.cache.write_text("{broken", encoding="utf-8")
        self.write("p1/a.jsonl", [tool_line("t1", "tdd", "not a date"), tool_line("t2", "tdd", "2026-10-02T03:00:00Z")])
        self.assertEqual(u.monthly_counts(self.projects, self.cache)["2026-10"]["tdd"], 1)
        self.assertIsNone(u.month_of(None))

    def test_names_match_with_or_without_plugin_prefix(self):
        c = Counter({"anthropic-skills:pdf": 2, "brainstorming": 1, "tdd": 4})
        self.assertEqual(u.count_for(c, "pdf"), 2)
        self.assertEqual(u.count_for(c, "superpowers:brainstorming"), 1)
        self.assertEqual(u.count_for(c, "tdd"), 4)
        self.assertEqual(u.count_for(c, "nothing"), 0)

    def test_idle_skills_are_those_not_called_this_month_or_last(self):
        counts = {
            "2026-07": Counter({"old-fav": 3}),
            "2026-09": Counter({"anthropic-skills:pdf": 1}),
            "2026-10": Counter({"tdd": 2}),
            u.ALL: Counter({"old-fav": 3, "anthropic-skills:pdf": 1, "tdd": 2}),
        }
        got = u.idle_since(counts, ["tdd", "pdf", "old-fav", "never"], datetime(2026, 10, 20))
        self.assertEqual(got, {"old-fav": "2026-07", "never": None})  # last month's call of pdf counts, under its plugin name too
        self.assertEqual(u.last_used(counts, "pdf"), "2026-09")
        # a month later the September call has left the window
        self.assertEqual(u.idle_since(counts, ["pdf", "tdd"], datetime(2026, 11, 1)), {"pdf": "2026-09"})

    def test_logs_without_any_skill_call_give_no_candidates(self):
        self.assertEqual(u.idle_since({}, ["tdd"], datetime(2026, 10, 20)), {})  # every skill would look idle

    def test_recent_months_cross_the_new_year(self):
        self.assertEqual(u.recent_months(datetime(2027, 1, 15)), ["2027-01", "2026-12"])
        self.assertEqual(u.recent_months(datetime(2026, 10, 1), 3), ["2026-10", "2026-09", "2026-08"])

    def test_scan_logs_also_says_how_far_back_the_logs_reach(self):
        self.assertEqual(u.scan_logs(self.projects, self.cache), ({}, None))  # the folder exists but holds no log
        self.assertEqual(u.scan_logs(self.t / "nope", self.cache), ({}, None))
        a = self.write("p1/a.jsonl", [tool_line("t1", "tdd", "2026-10-02T03:00:00Z")])
        b = self.write("p2/b.jsonl", ["{}"])
        old = datetime(2026, 8, 15, 12).timestamp()
        os.utime(a, (old, old))
        os.utime(b, (old + 40 * 86400, old + 40 * 86400))
        counts, start = u.scan_logs(self.projects, self.cache)
        self.assertEqual((counts["2026-10"]["tdd"], start), (1, datetime(2026, 8, 15, 12)))
        self.assertEqual(u.scan_logs(self.projects, self.cache)[1], start)  # the same from the cache
        self.assertEqual(u.monthly_counts(self.projects, self.cache), counts)  # the old entry point is unchanged


if __name__ == "__main__":
    unittest.main()

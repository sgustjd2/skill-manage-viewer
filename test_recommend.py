"""python -m unittest test_recommend -v   (tools/update_recommend.py and the loader behind the 추천 tab; no network)"""

import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import skill_manager as mgr

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import update_recommend as u  # noqa: E402  # pyright: ignore[reportMissingImports]

# A cut-down skills.sh /trending page, in the form the real one has: the list sits inside a Next.js script as the
# escaped contents of a JS string.
REAL = (
    r'<html><body><script>self.__next_f.push([0])</script><script>self.__next_f.push([1,"1:\"$Sreact.fragment\"\n"])</script>'
    r'<script>self.__next_f.push([1,"4e:[\"$\",\"$L55\",null,{\"initialSkills\":['
    r'{\"source\":\"uizze.sh\",\"skillId\":\"ui-taste\",\"name\":\"ui-taste\",\"installs\":30024},'
    r'{\"source\":\"heygen-com/hyperframes\",\"skillId\":\"media-use\",\"name\":\"media-use\",\"installs\":25382},'
    r'{\"source\":\"vercel-labs/skills\",\"skillId\":\"find-skills\",\"name\":\"find-skills\",\"installs\":19388,\"isOfficial\":true},'
    r'{\"source\":\"vercel-labs/agent-browser\",\"skillId\":\"agent-browser\",\"name\":\"agent-browser\",\"installs\":13755,'
    r'\"weeklyInstalls\":[1,2,3]},'
    r'{\"source\":\"mbfinotti/partnerships-skills\",\"skillId\":\"affiliate-fraud-detection\",\"name\":\"affiliate-fraud-detection\",'
    r'\"installs\":576}],\"totalSkills\":9973,\"allTimeTotal\":1405196,\"view\":\"trending\"}]\n"])</script></body></html>'
)


def sk(source: str, name: str, installs: int) -> dict:
    return {"source": source, "skillId": name, "name": name, "installs": installs}


def page(items) -> str:
    """A page whose list is `items`, wrapped the way skills.sh wraps it."""
    body = json.dumps({"initialSkills": items, "view": "trending"}, separators=(",", ":"))  # compact, like the site
    chunk = '4e:["$","$L55",null,' + body + "]\n"
    return f"<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>"


def ids(skills) -> list[tuple[str, str]]:
    return [(s["source"], s["skill"]) for s in skills]


class ParseTest(unittest.TestCase):
    def test_the_list_is_read_from_the_page_data(self):
        skills = u.parse_skills(REAL)
        self.assertEqual([s["skillId"] for s in skills],
                         ["ui-taste", "media-use", "find-skills", "agent-browser", "affiliate-fraud-detection"])
        self.assertEqual(skills[2], {"source": "vercel-labs/skills", "skillId": "find-skills", "name": "find-skills", "installs": 19388,
                                     "isOfficial": True})

    def test_only_github_repos_are_kept(self):
        got = u.pick([sk("uizze.sh", "a", 5000), sk("open.feishu.cn", "b", 5000), sk("mintlify.com/docs", "c", 5000),
                      sk("owner/repo", "d", 5000), sk("Owner-1/re.po_x", "e", 4000)])
        self.assertEqual(ids(got), [("owner/repo", "d"), ("Owner-1/re.po_x", "e")])

    def test_one_repo_adds_at_most_two(self):
        got = u.pick([sk("a/skills", f"s{i}", 9000 - i) for i in range(5)] + [sk("b/skills", "t", 1500)])
        self.assertEqual(ids(got), [("a/skills", "s0"), ("a/skills", "s1"), ("b/skills", "t")])
        same_repo_any_case = [sk("A/skills", "x", 9000), sk("a/Skills", "y", 8000), sk("a/skills", "z", 7000)]
        self.assertEqual(len(u.pick(same_repo_any_case)), 2)

    def test_skills_with_few_installs_are_dropped(self):
        got = u.pick([sk("a/a", "low", u.MIN_INSTALLS - 1), sk("b/b", "edge", u.MIN_INSTALLS)])
        self.assertEqual(ids(got), [("b/b", "edge")])

    def test_a_skill_copied_into_other_repos_counts_once(self):
        got = u.pick([sk("a/a", "same", 9000), sk("b/b", "same", 8000), sk("c/c", "Same", 7000), sk("d/d", "other", 6000)])
        self.assertEqual(ids(got), [("a/a", "same"), ("d/d", "other")])

    def test_most_installed_first_and_at_most_a_list_long(self):
        got = u.pick([sk(f"o{i}/r", f"s{i}", 2000 + i) for i in range(u.SIZE + 5)])
        self.assertEqual(len(got), u.SIZE)
        self.assertEqual(got[0]["installs"], 2000 + u.SIZE + 4)
        self.assertEqual([e["installs"] for e in got], sorted((e["installs"] for e in got), reverse=True))
        self.assertEqual(set(got[0]), {"source", "skill", "installs"})

    def test_skill_names_must_be_safe_folder_names(self):
        self.assertEqual(u.pick([sk("a/a", "../x", 5000), sk("a/a", "has space", 5000), sk("a/a", "ok", 5000)])[0]["skill"], "ok")

    def test_a_page_without_the_list_is_an_error(self):
        garbled = '<script>self.__next_f.push([1,"initialSkills"])</script>'
        for html in ("<html></html>", "", page([]).replace("initialSkills", "skills"), garbled):
            with self.subTest(html=html[:40]), self.assertRaises(ValueError):
                u.parse_skills(html)

    def test_odd_entries_are_skipped_and_an_empty_result_is_an_error(self):
        mixed = [sk("a/a", "good", 5000), {"source": "a/a"}, "text", {"source": 1, "skillId": "x", "installs": 5}, sk("a/b", "bool", True)]
        self.assertEqual([s["skillId"] for s in u.parse_skills(page(mixed))], ["good"])
        with self.assertRaises(ValueError):
            u.pick([])
        with self.assertRaises(ValueError):
            u.pick([sk("uizze.sh", "x", 5000)])


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "skill_recommend.json"
        self.out.write_text('{"old": true}', encoding="utf-8")
        patch = mock.patch.object(u, "OUT", self.out)
        patch.start()
        self.addCleanup(patch.stop)

    def run_main(self, fetch) -> tuple[int, str]:
        err = io.StringIO()
        with mock.patch.object(u, "fetch", side_effect=fetch), redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = u.main()
        return code, err.getvalue()

    def test_only_the_two_public_pages_are_requested_once(self):
        asked = []

        def fetch(url):
            asked.append(url)
            return page([sk("a/a", "s", 5000)])
        self.assertEqual(self.run_main(fetch)[0], 0)
        self.assertEqual(asked, ["https://www.skills.sh/", "https://www.skills.sh/trending"])

    def test_the_file_is_written_in_the_form_the_app_loads(self):
        code, _ = self.run_main(lambda url: page([sk("a/a", "s", 5000), sk("uizze.sh", "x", 9000)]))
        self.assertEqual(code, 0)
        text = self.out.read_text(encoding="utf-8")
        self.assertEqual(json.loads(text)["popular"], [{"source": "a/a", "skill": "s", "installs": 5000}])
        self.assertRegex(json.loads(text)["updated"], r"\d{4}-\d{2}-\d{2}")
        self.assertIn('\n  {"source": "a/a", "skill": "s", "installs": 5000}\n', text)  # one entry per line
        with mock.patch.object(mgr, "RECOMMENDED", self.out):
            loaded = mgr.load_recommended()
        self.assertEqual((ids(loaded["popular"]), ids(loaded["trending"])), ([("a/a", "s")], [("a/a", "s")]))

    def test_a_failure_leaves_the_old_file_alone(self):
        good = page([sk("a/a", "s", 5000)])
        for name, fetch in {
            "network down": OSError("offline"),
            "format changed": lambda url: "<html>nothing here</html>",
            "second page fails": lambda url: good if url.endswith("skills.sh/") else (_ for _ in ()).throw(OSError("503")),
            "nothing usable": lambda url: page([sk("uizze.sh", "x", 5000)]),
        }.items():
            with self.subTest(name):
                code, err = self.run_main(fetch if callable(fetch) else mock.Mock(side_effect=fetch))
                self.assertEqual(code, 1)
                self.assertIn("not updated", err)
                self.assertEqual(self.out.read_text(encoding="utf-8"), '{"old": true}')


class LoaderTest(unittest.TestCase):
    def load(self, content: str | None) -> dict:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "skill_recommend.json"
            if content is not None:
                path.write_text(content, encoding="utf-8")
            with mock.patch.object(mgr, "RECOMMENDED", path):
                return mgr.load_recommended()

    def test_a_missing_or_broken_file_is_an_empty_list(self):
        empty = {"updated": "", "popular": [], "trending": []}
        for content in (None, "", "{not json", "[]", "null", '"text"', "{}", '{"popular": 3, "trending": {"a": 1}, "updated": 7}'):
            with self.subTest(content=content):
                self.assertEqual(self.load(content), empty)

    def test_good_entries_load_and_odd_ones_are_dropped(self):
        good = {"source": "a/b", "skill": "s", "installs": 5}
        bad = [{"source": "a/b"}, {"source": "../x", "skill": "s", "installs": 1}, {"source": "a/b", "skill": "../s", "installs": 1},
               {"source": "a/b", "skill": "s", "installs": "5"}, {"source": "a/b", "skill": "s", "installs": True}, "x", None, 3,
               {"source": "https://evil/x", "skill": "s", "installs": 1}, {"source": "a.b/c", "skill": "s", "installs": 1}]
        data = self.load(json.dumps({"updated": "2026-10-21", "popular": [good, *bad], "trending": []}))
        self.assertEqual(data, {"updated": "2026-10-21", "popular": [good], "trending": []})

    def test_the_bundled_snapshot_loads_without_losing_an_entry(self):
        raw = json.loads(mgr.RECOMMENDED.read_text(encoding="utf-8"))
        data = mgr.load_recommended()
        self.assertRegex(data["updated"], r"^\d{4}-\d{2}-\d{2}$")
        for key in ("popular", "trending"):
            self.assertTrue(data[key])
            self.assertEqual(data[key], raw[key])  # what the updater writes is what the loader accepts
        self.assertFalse(re.search(r"https?://", mgr.RECOMMENDED.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()

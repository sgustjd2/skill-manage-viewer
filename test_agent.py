"""python -m unittest test_agent -v   (a fake `claude` script stands in for the real agent: no cost, ~/.claude untouched)"""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import platform_support as plat
import skill_agent as sa
import skill_manager as m

# What the fake agent does depends on a MODE word in the request it receives on stdin. It logs how it was started to $FAKE_LOG.
FAKE = r'''
import json, os, sys, time
prompt = sys.stdin.read()
json.dump({"args": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt}, open(os.environ["FAKE_LOG"], "w"))
mode = prompt.rsplit("[요청]", 1)[1]
def reply(text, error=False):
    print(json.dumps({"result": text, "is_error": error}))
if "MODE:slow" in mode:
    time.sleep(60)
elif "MODE:crash" in mode:
    sys.stderr.write("error: unknown option '--restricted'")
    sys.exit(1)
elif "MODE:apierror" in mode:
    reply("Not logged in", error=True)
elif "MODE:nothing" in mode:
    reply("I could not do that")
elif "MODE:two" in mode:
    for n in ("one", "two"):
        os.makedirs(n); open(n + "/SKILL.md", "w").write("---\nname: " + n + "\n---\n")
    reply("two")
elif "MODE:badname" in mode:
    os.makedirs("bad name"); open("bad name/SKILL.md", "w").write("---\nname: x\n---\n")
    reply("bad")
elif "MODE:link" in mode:
    os.makedirs("linky"); open("linky/SKILL.md", "w").write("---\nname: linky\n---\n")
    os.symlink(os.getcwd(), "linky/out", target_is_directory=True)
    reply("link")
else:
    os.makedirs("my-skill/references")
    open("my-skill/SKILL.md", "w", encoding="utf-8").write("---\nname: my-skill\ndescription: does it\n---\n\n# body\n")
    open("my-skill/references/a.md", "w").write("ref")
    reply("my-skill 을 만들었습니다")
'''


class AgentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = self.tmp = Path(self._tmp.name).resolve()
        self.patches = [
            mock.patch.object(m, "STORE", t / "store"),
            mock.patch.object(m, "AGENT_DIRS", {"Claude Code": t / "claude", "Codex": t / "codex", "Antigravity": t / "agy"}),
            mock.patch.dict("os.environ", {"FAKE_LOG": str(t / "log.json")}),
        ]
        for p in self.patches:
            p.start()
        (t / "fake.py").write_text(FAKE, encoding="utf-8")
        self.cli = [sys.executable, str(t / "fake.py")]
        self.drafts: list[sa.Draft] = []

    def tearDown(self):
        for d in self.drafts:
            d.discard()
        for p in self.patches:
            p.stop()
        for d in m.AGENT_DIRS.values():  # unlink junctions first so cleanup can't walk into a skill folder through them
            for p in d.iterdir() if d.is_dir() else []:
                if m.is_link(p):
                    plat.unlink_dir(p)
        self._tmp.cleanup()

    def make(self, request="요약 스킬", **kw) -> sa.Draft:
        d = sa.generate(request, self.cli, **kw)
        self.drafts.append(d)
        return d

    def log(self) -> dict:
        return json.loads((self.tmp / "log.json").read_text(encoding="utf-8"))

    # ---- finding the program ----------------------------------------------------------------
    def test_no_program_found_says_so_and_points_to_the_template_tab(self):
        with mock.patch.object(sa, "find_cli", return_value=None):
            cli, text = sa.availability()
        self.assertIsNone(cli)
        self.assertIn("찾지 못했습니다", text)
        self.assertIn("새로 만들기", text)
        self.assertIn("Codex", text)  # the others are named as not supported yet

    def test_program_found_on_path_or_in_a_known_install_folder(self):
        exe = self.tmp / ("bin/claude.exe" if plat.WIN else "bin/claude")
        exe.parent.mkdir()
        exe.write_text("x")
        with mock.patch("shutil.which", return_value=None), mock.patch.object(sa, "known_paths", return_value=[self.tmp / "nope", exe]):
            self.assertEqual(sa.find_cli(), exe)
            self.assertEqual(sa.availability()[0], exe)
        with mock.patch("shutil.which", return_value=str(exe)), mock.patch.object(sa, "known_paths", return_value=[]):
            self.assertEqual(sa.find_cli(), exe)
        with mock.patch("shutil.which", return_value=None), mock.patch.object(sa, "known_paths", return_value=[self.tmp / "nope"]):
            self.assertIsNone(sa.find_cli())

    # ---- what the agent is given ------------------------------------------------------------
    def test_the_agent_runs_confined_in_a_scratch_folder_and_gets_only_the_request(self):
        (m.STORE / "skills" / "private-local-skill").mkdir(parents=True)
        d = self.make("회의록 요약 스킬 MODE:ok")
        log = self.log()
        args = log["args"]
        for flag in ("-p", "--restricted", "--no-session-persistence"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(args[args.index("--permission-prompts") + 1], "none")
        tools = args[args.index("--tools") + 1].split(",")
        self.assertEqual(sorted(tools), sorted(["Read", "Write", "Edit", "Glob", "Grep"]))  # no Bash / PowerShell / WebFetch
        self.assertEqual(args[args.index("--allowedTools") + 1].split(","), tools)
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertTrue(Path(log["cwd"]).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.assertTrue(d.dir.resolve().is_relative_to(d.root))
        self.assertTrue(log["prompt"].endswith("회의록 요약 스킬 MODE:ok"))
        for private in ("private-local-skill", str(m.STORE), str(Path.home())):
            self.assertNotIn(private, log["prompt"])

    def test_nothing_reaches_the_skill_folders_until_the_user_installs(self):
        d = self.make()
        self.assertEqual(d.name, "my-skill")
        self.assertFalse(m.STORE.exists())
        for folder in m.AGENT_DIRS.values():
            self.assertFalse(folder.exists())

    # ---- preview, then install --------------------------------------------------------------
    def test_install_is_refused_until_the_preview_was_produced(self):
        d = self.make()
        with self.assertRaises(m.ManagerError) as cm:
            sa.install_draft(d, ["Claude Code"])
        self.assertIn("미리보기", str(cm.exception))
        self.assertFalse((m.AGENT_DIRS["Claude Code"] / "my-skill").exists())
        text = d.preview()
        self.assertIn("name: my-skill", text)
        self.assertIn("references/a.md", text)
        self.assertIn("SKILL.md", text)
        res = sa.install_draft(d, ["Claude Code", "Codex"])
        self.assertEqual([r.status for r in res], ["installed", "installed"])
        for a in ("Claude Code", "Codex"):
            self.assertTrue((m.AGENT_DIRS[a] / "my-skill/SKILL.md").is_file())
            self.assertTrue(m.is_link(m.AGENT_DIRS[a] / "my-skill"))
        self.assertEqual((m.STORE / "skills/my-skill/references/a.md").read_text(), "ref")
        self.assertFalse(d.root.exists())  # the scratch folder is gone

    def test_a_name_that_is_already_taken_keeps_the_draft_readable(self):
        (m.AGENT_DIRS["Claude Code"] / "my-skill").mkdir(parents=True)  # the user's own skill with that name
        d = self.make()
        d.preview()
        with self.assertRaises(m.ManagerError):
            sa.install_draft(d, ["Claude Code"])
        self.assertTrue((d.dir / "SKILL.md").is_file())
        self.assertTrue((m.AGENT_DIRS["Claude Code"] / "my-skill").is_dir())
        self.assertFalse(m.is_link(m.AGENT_DIRS["Claude Code"] / "my-skill"))
        self.assertFalse((m.STORE / "skills/my-skill").exists())

    def test_discard_removes_only_the_scratch_folder(self):
        d = self.make()
        d.discard()
        self.assertFalse(d.root.exists())

    # ---- failures ---------------------------------------------------------------------------
    def test_bad_requests_never_start_the_agent(self):
        for bad in ("", "   ", "x" * (sa.MAX_REQUEST + 1)):
            with self.assertRaises(m.ManagerError):
                sa.generate(bad, self.cli)
        self.assertFalse((self.tmp / "log.json").exists())

    def test_a_missing_program_and_a_crash_and_an_api_error_are_explained(self):
        with self.assertRaises(m.ManagerError) as cm:
            sa.generate("a", [str(self.tmp / "no-such-program")])
        self.assertIn("실행하지 못했습니다", str(cm.exception))
        with self.assertRaises(m.ManagerError) as cm:
            sa.generate("MODE:crash", self.cli)
        self.assertIn("업데이트", str(cm.exception))  # an old Claude Code that does not know --restricted
        with self.assertRaises(m.ManagerError) as cm:
            sa.generate("MODE:apierror", self.cli)
        self.assertIn("Not logged in", str(cm.exception))

    def test_results_that_are_not_one_clean_skill_folder_are_rejected_and_cleaned_up(self):
        before = set(Path(tempfile.gettempdir()).glob("skilldesk-agent-*"))
        for mode in ("nothing", "two", "badname"):
            with self.assertRaises(m.ManagerError, msg=mode):
                sa.generate(f"MODE:{mode}", self.cli)
        if plat.WIN:  # making a link needs a privilege there; the same check covers junctions
            return
        with self.assertRaises(m.ManagerError):
            sa.generate("MODE:link", self.cli)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("skilldesk-agent-*")), before)

    def test_time_out_stops_the_agent(self):
        t = time.monotonic()
        with self.assertRaises(m.ManagerError) as cm:
            sa.generate("MODE:slow", self.cli, timeout=1)
        self.assertIn("시간 초과", str(cm.exception))
        self.assertLess(time.monotonic() - t, 15)

    def test_cancel_stops_the_agent_and_cleans_up(self):
        job = sa.Job()
        threading.Timer(1.5, job.cancel).start()
        before = set(Path(tempfile.gettempdir()).glob("skilldesk-agent-*"))
        t = time.monotonic()
        with self.assertRaises(sa.Cancelled):
            sa.generate("MODE:slow", self.cli, job=job)
        self.assertLess(time.monotonic() - t, 15)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("skilldesk-agent-*")), before)


if __name__ == "__main__":
    unittest.main()

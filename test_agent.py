"""python -m unittest test_agent -v   (a fake agent stands in for Claude Code: nothing here runs a real one)"""

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import platform_support as plat
import skill_agent as sa
import skill_manager as mgr

FAKE = r'''
import json, os, sys, time
from pathlib import Path
mode = sys.argv[1]
prompt = sys.stdin.read()
Path("prompt.txt").write_text(prompt, encoding="utf-8")
Path("argv.json").write_text(json.dumps(sys.argv[2:]), encoding="utf-8")
Path("cwd.txt").write_text(os.getcwd(), encoding="utf-8")
if mode == "fail":
    print("로그인이 필요합니다", file=sys.stderr); sys.exit(3)
if mode == "hang":
    time.sleep(60)
if mode == "none":
    sys.exit(0)
for name in (["alpha", "beta"] if mode == "two" else ["bad name!"] if mode == "badname" else ["my-skill"]):
    d = Path(name); d.mkdir()
    desc = "" if mode == "nodesc" else 'description: "회의록을 정리할 때 쓴다"\n'
    (d / "SKILL.md").write_text(f"---\nname: {name}\n{desc}---\n\n# {name}\n", encoding="utf-8")
    (d / "ref.txt").write_text("x", encoding="utf-8")
'''


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.fake = self.tmp / "fake_agent.py"
        self.fake.write_text(FAKE, encoding="utf-8")
        self.store, self.claude = self.tmp / "store", self.tmp / "claude-skills"
        for patch in (mock.patch.object(mgr, "STORE", self.store),
                      mock.patch.dict(mgr.AGENT_DIRS, {"Claude Code": self.claude, "Codex": self.tmp / "codex-skills"})):
            patch.start()
            self.addCleanup(patch.stop)
        made = []
        real = tempfile.mkdtemp
        patch = mock.patch.object(sa.tempfile, "mkdtemp", side_effect=lambda **kw: made.append(real(**kw)) or made[-1])
        patch.start()
        self.addCleanup(patch.stop)
        self.made = made

    def gen(self, mode: str, request: str = "회의록 정리 스킬", **kw) -> sa.Draft:
        return sa.generate(request, kw.pop("cancel", threading.Event()), base_cmd=[sys.executable, str(self.fake), mode], **kw)

    # -- finding the command
    def test_status_without_the_command_falls_back_to_the_template(self):
        with mock.patch.object(plat, "find_claude", return_value=None):
            exe, text = sa.agent_status()
            self.assertIsNone(exe)
            self.assertIn("새로 만들기", text)
            self.assertIn("Codex", text)
            with self.assertRaises(mgr.ManagerError):
                sa.generate("무언가", threading.Event())
        self.assertEqual(self.made, [])  # no temp folder was made for an agent that cannot run

    def test_status_with_the_command(self):
        with mock.patch.object(plat, "find_claude", return_value=Path("claude")):
            self.assertEqual(sa.agent_status()[0], Path("claude"))

    def test_find_claude_checks_the_installers_folder_when_not_on_the_path(self):
        home = self.tmp / "home"
        exe = home / ".local/bin" / ("claude.exe" if plat.WIN else "claude")
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        with mock.patch.object(plat.shutil, "which", return_value=None), mock.patch.object(plat.Path, "home", return_value=home):
            self.assertEqual(plat.find_claude(), exe)

    # -- generating
    def test_draft_is_previewable_and_nothing_is_installed(self):
        d = self.gen("ok")
        self.assertEqual((d.name, d.files), ("my-skill", ["SKILL.md", "ref.txt"]))
        self.assertIn("# my-skill", d.skill_md)
        self.assertFalse(self.store.exists())  # generating alone never reaches the user's folders
        self.assertFalse(self.claude.exists())

    def test_agent_runs_in_a_temp_folder_with_only_the_request_and_the_checked_flags(self):
        d = self.gen("ok", "  회의록을\n정리하는 스킬 ")
        self.assertEqual(Path((d.root / "cwd.txt").read_text(encoding="utf-8")).resolve(), d.root.resolve())
        self.assertTrue(d.root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()))
        prompt = (d.root / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("회의록을 정리하는 스킬", prompt)
        for private in (str(Path.home()), str(mgr.STORE), str(self.claude)):
            self.assertNotIn(private, prompt)  # no local paths or skill lists go to the agent
        flags = json.loads((d.root / "argv.json").read_text(encoding="utf-8"))
        self.assertEqual(flags, sa.FLAGS)
        for must in ("--restricted", "--tools", "--permission-prompts", "none"):
            self.assertIn(must, flags)
        self.assertNotIn("--dangerously-skip-permissions", flags)

    def test_install_only_after_the_user_presses_install(self):
        d = self.gen("ok")
        dest, results = sa.install_draft(d, ["Claude Code"])
        self.assertEqual(dest, self.store / "skills/my-skill")
        self.assertEqual([r.status for r in results], ["installed"])
        self.assertTrue(mgr.is_link(self.claude / "my-skill"))
        self.assertTrue((dest / "ref.txt").is_file())
        self.assertFalse(d.root.exists())  # the temp folder is cleaned up

    def test_install_refuses_a_name_that_is_taken(self):
        d = self.gen("ok")
        (self.claude / "my-skill").mkdir(parents=True)  # the user's own skill
        with self.assertRaises(mgr.ManagerError):
            sa.install_draft(d, ["Claude Code"])
        self.assertFalse((self.store / "skills/my-skill").exists())
        self.assertTrue((self.claude / "my-skill").is_dir())  # untouched
        sa.discard(d)
        self.assertFalse(d.root.exists())

    def test_agent_failure_shows_the_reason_and_cleans_up(self):
        with self.assertRaises(mgr.ManagerError) as cm:
            self.gen("fail")
        self.assertIn("로그인이 필요합니다", str(cm.exception))
        self.assertFalse(Path(self.made[0]).exists())

    def test_timeout_stops_the_agent(self):
        start = time.monotonic()
        with self.assertRaises(mgr.ManagerError) as cm:
            self.gen("hang", timeout=1)
        self.assertIn("1초", str(cm.exception))
        self.assertLess(time.monotonic() - start, 15)  # killed, not waited out
        self.assertFalse(Path(self.made[0]).exists())

    def test_cancel_stops_the_agent(self):
        cancel = threading.Event()
        threading.Timer(1.5, cancel.set).start()
        start = time.monotonic()
        with self.assertRaises(sa.Cancelled):
            self.gen("hang", cancel=cancel)
        self.assertLess(time.monotonic() - start, 15)
        self.assertFalse(Path(self.made[0]).exists())

    def test_bad_results_are_rejected(self):
        for mode, word in (("none", "하나"), ("two", "2개"), ("nodesc", "description"), ("badname", "이름")):
            with self.subTest(mode), self.assertRaises(mgr.ManagerError) as cm:
                self.gen(mode)
            self.assertIn(word, str(cm.exception))
        self.assertTrue(all(not Path(p).exists() for p in self.made))

    def test_empty_request_never_starts_the_agent(self):
        with self.assertRaises(mgr.ManagerError):
            self.gen("ok", "   ")
        self.assertEqual(self.made, [])


if __name__ == "__main__":
    unittest.main()

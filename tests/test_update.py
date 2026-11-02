"""python -m unittest test_update -v   (skill_update: when the app may update itself, and that it can't be left broken)

Works on a throwaway "official" bare repo and a clone of it; nothing outside the temp folder is touched.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import skill_update as up

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
GOOD_APP = "import sys\nprint('ok', sys.argv[1:])\n"
BROKEN_APP = "import sys\nsys.exit(3)\n"  # a release that can't start


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SelfUpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """The slow part (three git clones) is done once; every test gets a fast copy of it."""
        cls._tmp = tempfile.TemporaryDirectory()
        t = Path(cls._tmp.name).resolve()
        sh(t, "init", "-q", "--bare", "--initial-branch=main", str(t / "origin.git"))
        sh(t, "clone", "-q", (t / "origin.git").as_uri(), str(t / "work"))
        for name, text in {"skill_viewer.py": GOOD_APP, "requirements.txt": "pillow\n"}.items():
            (t / "work" / name).write_text(text, encoding="utf-8")
        sh(t / "work", "add", "-A")
        sh(t / "work", "commit", "-qm", "init")
        sh(t / "work", "push", "-q", "origin", "HEAD:main")
        sh(t, "clone", "-q", (t / "origin.git").as_uri(), str(t / "installed"))
        cls.template = t

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self._tmp_case = tempfile.TemporaryDirectory()
        t = self.t = Path(self._tmp_case.name).resolve()
        for name in ("origin.git", "work", "installed"):
            shutil.copytree(self.template / name, t / name)
        self.origin, self.work, self.app = t / "origin.git", t / "work", t / "installed"
        for repo in (self.work, self.app):  # the copies still point at the template's origin
            sh(repo, "remote", "set-url", "origin", self.origin.as_uri())
        self.official = self.origin.as_uri()
        self.ok_pip = [sys.executable, "-c", "pass"]
        self.bad_pip = [sys.executable, "-c", "raise SystemExit('no such package')"]
        self.smoke = [sys.executable, str(self.app / "skill_viewer.py"), "--check"]  # absolute: runs the INSTALLED copy

    def tearDown(self):
        self._tmp_case.cleanup()

    def push(self, files: dict[str, str], message: str) -> None:
        for name, text in files.items():
            (self.work / name).write_text(text, encoding="utf-8")
        sh(self.work, "add", "-A")
        sh(self.work, "commit", "-qm", message)
        sh(self.work, "push", "-q", "origin", "HEAD:main")

    def apply(self, **kw):
        return up.apply(self.app, self.official, pip_cmd=kw.get("pip", self.ok_pip), smoke_cmd=self.smoke)

    def head(self) -> str:
        return sh(self.app, "rev-parse", "--short", "HEAD")

    # ---- what counts as the same repo ---------------------------------------------------------
    def test_slug_ignores_url_style_and_case(self):
        for url in ("https://github.com/Owner/Repo.git", "git@github.com:owner/repo", "https://github.com/owner/repo/"):
            self.assertEqual(up.slug(url), "owner/repo", url)

    # ---- check ---------------------------------------------------------------------------------
    def test_up_to_date_and_available(self):
        self.assertEqual(up.check(self.app, self.official).status, "up-to-date")
        self.push({"a.txt": "1"}, "Feat: 첫 변경")
        self.push({"b.txt": "2"}, "Fix: 두 번째 변경")
        r = up.check(self.app, self.official)
        self.assertEqual((r.status, r.changes), ("available", ["Fix: 두 번째 변경", "Feat: 첫 변경"]))
        self.assertNotEqual(r.old, r.new)
        self.assertFalse((self.app / "a.txt").exists())  # checking never changes the working tree

    # ---- the cases where it must do nothing ----------------------------------------------------
    def test_never_updates_unless_it_is_the_official_clean_main_clone(self):
        self.push({"a.txt": "1"}, "Feat: 새 버전")
        before = self.head()
        cases = {
            "not official": lambda: up.apply(self.app, "https://github.com/someone/else", self.ok_pip, self.smoke),
        }
        r = cases["not official"]()
        self.assertEqual(r.status, "skipped")
        self.assertIn("공식", r.detail)

        (self.app / "skill_viewer.py").write_text("# my own edit\n", encoding="utf-8")
        r = self.apply()
        self.assertEqual(r.status, "skipped")
        self.assertIn("수정한 파일", r.detail)
        self.assertEqual((self.app / "skill_viewer.py").read_text(encoding="utf-8"), "# my own edit\n")  # untouched
        sh(self.app, "checkout", "-q", "--", "skill_viewer.py")

        sh(self.app, "checkout", "-q", "-b", "feat/mine")
        r = self.apply()
        self.assertEqual(r.status, "skipped")
        self.assertIn("feat/mine", r.detail)
        sh(self.app, "checkout", "-q", "main")

        (self.app / "local.txt").write_text("x", encoding="utf-8")
        sh(self.app, "add", "-A")
        sh(self.app, "commit", "-qm", "local only")
        self.assertEqual(self.apply().status, "skipped")  # unpushed work: a pull would have to merge
        sh(self.app, "reset", "-q", "--hard", before)

        self.assertEqual(self.head(), before)
        zip_install = self.t / "zip"
        zip_install.mkdir()
        self.assertEqual(up.apply(zip_install, self.official).status, "skipped")  # no .git at all

    # ---- applying ------------------------------------------------------------------------------
    def test_updates_by_fast_forward_and_runs_the_new_code_once(self):
        self.push({"a.txt": "1"}, "Feat: 새 버전")
        r = self.apply()
        self.assertEqual((r.status, r.changes), ("updated", ["Feat: 새 버전"]))
        self.assertEqual(self.head(), r.new)
        self.assertTrue((self.app / "a.txt").exists())
        self.assertEqual(sh(self.app, "rev-list", "--merges", "--count", "HEAD"), "0")  # plain fast-forward, no merge commit
        self.assertEqual(up.check(self.app, self.official).status, "up-to-date")

    def test_a_data_only_update_needs_no_restart_and_no_pip_or_start_check(self):
        log = self.t / "ran.log"
        probe = [sys.executable, "-c", f"open({str(log)!r}, 'a').write('ran')"]
        self.push({"skill_recommend.json": "{}\n"}, "Chore: 추천 목록 갱신")
        r = up.apply(self.app, self.official, pip_cmd=probe, smoke_cmd=probe)
        self.assertEqual((r.status, r.data_only, r.changes), ("updated", True, ["Chore: 추천 목록 갱신"]))
        self.assertEqual((self.app / "skill_recommend.json").read_text(encoding="utf-8"), "{}\n")
        self.assertFalse(log.exists())
        # one code file in the same pull, or code in an earlier commit of it, brings the restart back
        self.push({"skill_recommend.json": "{1}\n"}, "Chore: 추천 목록 갱신")
        self.push({"a.txt": "1"}, "Feat: 코드 변경")
        r = self.apply()
        self.assertEqual((r.status, r.data_only), ("updated", False))

    def test_dependencies_are_installed_only_when_requirements_changed(self):
        log = self.t / "pip.log"
        pip = [sys.executable, "-c", f"open({str(log)!r}, 'a').write('ran')"]
        self.push({"a.txt": "1"}, "Feat: 코드만")
        up.apply(self.app, self.official, pip_cmd=pip, smoke_cmd=self.smoke)
        self.assertFalse(log.exists())  # nothing in requirements.txt changed: pip was not asked
        self.push({"requirements.txt": "pillow\nnewpkg\n"}, "Chore: 의존성 추가")
        self.assertEqual(up.apply(self.app, self.official, pip_cmd=pip, smoke_cmd=self.smoke).status, "updated")
        self.assertEqual(log.read_text(), "ran")

    def test_failed_dependency_install_rolls_back_to_the_exact_old_commit(self):
        before = self.head()
        self.push({"requirements.txt": "does-not-exist\n", "a.txt": "1"}, "Chore: 의존성 추가")
        r = self.apply(pip=self.bad_pip)
        self.assertEqual(r.status, "failed")
        self.assertIn("되돌렸습니다", r.detail)
        self.assertEqual(self.head(), before)
        self.assertFalse((self.app / "a.txt").exists())
        self.assertEqual(up.check(self.app, self.official).status, "available")  # still offered, not lost

    def test_a_release_that_cannot_start_is_rolled_back(self):
        before = self.head()
        self.push({"skill_viewer.py": BROKEN_APP}, "Feat: 깨진 버전")
        r = self.apply()
        self.assertEqual((r.status, self.head()), ("failed", before))
        self.assertEqual((self.app / "skill_viewer.py").read_text(encoding="utf-8"), GOOD_APP)
        self.push({"skill_viewer.py": GOOD_APP}, "Fix: 고친 버전")  # the maintainer fixes it: the next check succeeds
        self.assertEqual(self.apply().status, "updated")

    def test_unreachable_remote_is_a_failure_not_a_crash(self):
        sh(self.app, "remote", "set-url", "origin", (self.t / "gone.git").as_uri())
        r = up.check(self.app, (self.t / "gone.git").as_uri())
        self.assertEqual(r.status, "failed")


if __name__ == "__main__":
    unittest.main()

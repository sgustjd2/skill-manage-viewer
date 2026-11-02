"""python -m unittest tests.test_update_check -v   (update_check: the "newer release exists" notice for installs git can't update)

No network: the GitHub answer is faked.
"""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from skilldesk import skill_manager as mgr
from skilldesk import skill_update as upd
from skilldesk import update_check as uc

REPO = Path(__file__).resolve().parent.parent


def answer(body: object) -> mock.MagicMock:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value = io.BytesIO(raw)
    return cm


class VersionTest(unittest.TestCase):
    def test_numbers_are_compared_as_numbers(self):
        self.assertTrue(uc.is_newer("1.10.0", "1.9.0"))
        self.assertTrue(uc.is_newer("v2.0.0", "1.99.99"))
        self.assertFalse(uc.is_newer("1.9.0", "1.10.0"))
        self.assertFalse(uc.is_newer("1.2.3", "1.2.3"))  # up to date

    def test_anything_not_x_y_z_is_never_newer(self):
        for bad in ("", "latest", "1.2", "1.2.3.4", "1.2.3-beta", "v1.2.x", "99999.0.0", "1.2.3\n; rm"):
            self.assertFalse(uc.is_newer(bad, "1.0.0"), bad)
            self.assertFalse(uc.is_newer("9.9.9", bad), bad)

    def test_this_repos_own_version_file_is_read(self):
        self.assertEqual(uc.current_version(), (REPO / "VERSION").read_text(encoding="utf-8").strip())
        self.assertIsNotNone(uc.parse(uc.current_version()))

    def test_in_the_exe_the_version_is_read_from_the_bundle_folder(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(sys, "_MEIPASS", t, create=True):
            self.assertEqual(uc.current_version(), "")  # no file
            (Path(t) / "VERSION").write_text("3.4.5\n", encoding="utf-8")
            self.assertEqual(uc.current_version(), "3.4.5")
            (Path(t) / "VERSION").write_text("<html>", encoding="utf-8")
            self.assertEqual(uc.current_version(), "")  # not a version

    def test_release_page_is_under_the_official_repo(self):
        self.assertEqual(uc.release_page("1.2.3"), "https://github.com/sgustjd2/skill-manage-viewer/releases/tag/v1.2.3")


class ReleaseAnswerTest(unittest.TestCase):
    def test_only_a_published_release_with_a_plain_tag_counts(self):
        self.assertEqual(uc.latest_version({"tag_name": "v1.2.3"}), "1.2.3")
        for bad in (None, [], "v1.2.3", {}, {"tag_name": 5}, {"tag_name": "1.2.3"}, {"tag_name": "v1.2"}, {"tag_name": "v1.2.3-rc1"},
                    {"tag_name": "v1.2.3", "draft": True}, {"tag_name": "v1.2.3", "prerelease": True},
                    {"tag_name": "v1.2.3/../../x"}):
            self.assertIsNone(uc.latest_version(bad), bad)

    def test_nothing_the_answer_says_ends_up_in_the_page_to_open(self):
        evil = {"tag_name": "v1.2.3", "html_url": "https://evil.example/x", "assets": [{"browser_download_url": "https://evil.example/a.exe"}]}
        with mock.patch.object(uc.urllib.request, "urlopen", return_value=answer(evil)):
            latest = uc.fetch_latest()
        self.assertEqual(latest, "1.2.3")
        self.assertTrue(uc.release_page(latest).startswith(f"https://github.com/{uc.REPO}/"))


class FetchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patches = [mock.patch.object(mgr, "STORE", Path(self._tmp.name)), mock.patch.object(uc, "_logged", False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.log = Path(self._tmp.name) / "update-check.log"

    def test_newer_release_is_found_and_up_to_date_is_not(self):
        with mock.patch.object(uc.urllib.request, "urlopen", side_effect=lambda *a, **k: answer({"tag_name": "v1.10.0"})):
            self.assertEqual(uc.newer_release("1.9.0"), "1.10.0")
            self.assertIsNone(uc.newer_release("1.10.0"))
            self.assertIsNone(uc.newer_release("2.0.0"))  # a build ahead of the release (a developer's) is not "old"
        self.assertFalse(self.log.exists())

    def test_offline_and_bad_answers_are_quiet_and_logged_once(self):
        failures = [
            urllib.error.URLError("offline"),
            TimeoutError("slow"),
            urllib.error.HTTPError(uc.API, 403, "rate limit", {}, None),  # type: ignore[arg-type]
            answer(b"<html>not json</html>"),
            answer({"tag_name": "nightly"}),
            answer([1, 2]),
        ]
        for i, f in enumerate(failures):
            kw = {"side_effect": f} if isinstance(f, Exception) else {"return_value": f}
            with mock.patch.object(uc.urllib.request, "urlopen", **kw):
                self.assertIsNone(uc.newer_release("1.0.0"))
            if i == 0:
                first = self.log.read_text(encoding="utf-8")
                self.assertIn("offline", first)
        self.assertEqual(self.log.read_text(encoding="utf-8"), first)  # later failures didn't write again

    def test_an_unwritable_log_does_not_break_the_check(self):
        with mock.patch.object(mgr, "STORE", Path(self._tmp.name) / "file-not-folder"):
            (Path(self._tmp.name) / "file-not-folder").write_text("x", encoding="utf-8")
            with mock.patch.object(uc.urllib.request, "urlopen", side_effect=OSError("down")):
                self.assertIsNone(uc.newer_release("1.0.0"))


class WhoIsToldTest(unittest.TestCase):
    def test_git_clone_keeps_its_own_updater_and_the_notice_stays_out(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(upd, "HERE", Path(t)):
            (Path(t) / ".git").mkdir()
            self.assertFalse(uc.applies())

    def test_zip_copy_without_git_gets_the_notice(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(upd, "HERE", Path(t)):
            self.assertTrue(uc.applies())

    def test_exe_gets_the_notice_and_never_the_git_updater(self):
        with (
            tempfile.TemporaryDirectory() as t,
            mock.patch.object(upd, "HERE", Path(t)),
            mock.patch.object(sys, "frozen", True, create=True),
        ):
            (Path(t) / ".git").mkdir()  # even next to a .git folder
            self.assertTrue(uc.applies())
            self.assertIn("설치 파일", upd.eligible(Path(t)))
            self.assertEqual(upd.apply(Path(t)).status, "skipped")


if __name__ == "__main__":
    unittest.main()

"""Link install without Git (zip fallback) and the Korean error messages. No network: downloads are faked."""
import io
import stat
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from skilldesk import skill_manager as m


def make_zip(path: Path, files: dict[str, str], links: dict[str, str] | None = None) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
        for name, target in (links or {}).items():
            i = zipfile.ZipInfo(name)
            i.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(i, target)


GOOD = {"unslop-main/skills/unslop/SKILL.md": "---\nname: unslop\ndescription: UI design\n---\nbody\n",
        "unslop-main/README.md": "hi"}


class Gitless(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        for patch in (mock.patch.object(m, "STORE", self.tmp / "store"),
                      mock.patch.object(m, "AGENT_DIRS", {"Codex": self.tmp / "codex"}),
                      mock.patch.object(m.shutil, "which", return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)

    def fake_download(self, files, links=None):
        return mock.patch.object(m, "download_zip", lambda url, dest: (setattr(self, "url", url), make_zip(dest, files, links)))

    def test_zip_fallback_installs(self):
        with self.fake_download(GOOD):
            dest, root = m.fetch("https://github.com/heydamir/unslop")
        self.assertEqual(self.url, "https://github.com/heydamir/unslop/archive/HEAD.zip")
        found = m.discover(root)
        self.assertEqual([d.name for d in found], ["unslop"])
        self.assertEqual(m.install(found, ["Codex"])[0].status, "installed")
        self.assertTrue((m.AGENT_DIRS["Codex"] / "unslop/SKILL.md").is_file())
        self.assertEqual(m.origin(m.AGENT_DIRS["Codex"] / "unslop")[0], "link")  # no .git: no update check
        self.assertEqual([p.name for p in dest.parent.iterdir()], [dest.name])  # no temp leftovers

    def test_second_fetch_replaces_the_copy_and_ref_goes_into_the_url(self):
        with self.fake_download(GOOD):
            m.fetch("https://github.com/o/r/tree/v2/skills/unslop")
        self.assertEqual(self.url, "https://github.com/o/r/archive/v2.zip")
        with self.fake_download({"r-v2/skills/unslop/SKILL.md": "---\nname: unslop\n---\nnew\n"}):
            _, root = m.fetch("https://github.com/o/r/tree/v2/skills/unslop")
        self.assertIn("new", (root / "SKILL.md").read_text(encoding="utf-8"))

    def test_without_git_only_github_hosts(self):
        with self.assertRaises(m.ManagerError) as e:
            m.fetch("https://gitlab.com/o/r")
        self.assertIn("Git", str(e.exception))

    def test_unsafe_zips_are_refused(self):
        for files, links in [({"r/../../evil": "x"}, None), ({"/abs/evil": "x"}, None),
                             ({"r/a": "x"}, {"r/link": "/etc"}), ({"a/x": "1", "b/y": "2"}, None)]:
            with self.subTest(files=list(files), links=links), self.fake_download(files, links), self.assertRaises(m.ManagerError):
                m.fetch("https://github.com/o/bad")
                self.assertFalse((self.tmp / "evil").exists())
        self.assertFalse((m.STORE / "sources" / "github.com__o__bad").exists())

    def test_size_limit_counts_real_bytes(self):
        with mock.patch.object(m, "ZIP_MAX_BYTES", 100), self.fake_download({"r/big": "x" * 500}), self.assertRaises(m.ManagerError) as e:
            m.fetch("https://github.com/o/big")
        self.assertIn("너무 커서", str(e.exception))

    def test_not_a_zip(self):
        with mock.patch.object(m, "download_zip", lambda url, dest: dest.write_text("<html>")), self.assertRaises(m.ManagerError):
            m.fetch("https://github.com/o/r")

    def test_redirect_only_to_github_hosts(self):
        h = m._ZipRedirects()
        req = mock.Mock()
        for bad in ("https://evil.example/x.zip", "http://codeload.github.com/x.zip"):
            with self.assertRaises(m.ManagerError):
                h.redirect_request(req, None, 302, "", {}, bad)
        with mock.patch.object(m.urllib.request.HTTPRedirectHandler, "redirect_request", return_value="ok"):
            self.assertEqual(h.redirect_request(req, None, 302, "", {}, "https://codeload.github.com/o/r/zip/HEAD"), "ok")

    def test_download_errors_in_korean(self):
        def failing(err):
            opener = mock.Mock()
            opener.open.side_effect = err
            return mock.patch.object(m.urllib.request, "build_opener", return_value=opener)
        dest = self.tmp / "x.zip"
        with failing(urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO())), self.assertRaises(m.ManagerError) as e:
            m.download_zip("https://github.com/o/r/archive/HEAD.zip", dest)
        self.assertIn("비공개", str(e.exception))
        with failing(urllib.error.URLError("no route")), self.assertRaises(m.ManagerError) as e:
            m.download_zip("https://github.com/o/r/archive/HEAD.zip", dest)
        self.assertIn("인터넷", str(e.exception))


class Messages(unittest.TestCase):
    def test_run_without_git_executable(self):
        with mock.patch.object(m.subprocess, "run", side_effect=FileNotFoundError), self.assertRaises(m.ManagerError) as e:
            m.run(["git", "--version"])
        self.assertIn("Git이 설치돼 있지 않습니다", str(e.exception))

    def test_git_complaints_are_explained(self):
        self.assertIn("찾지 못했습니다", m.explain_git("remote: Repository not found.\nfatal: repository 'x' not found"))
        self.assertIn("네트워크", m.explain_git("fatal: unable to access 'https://x/': Could not resolve host: x"))
        self.assertIn("브랜치", m.explain_git("fatal: Remote branch nope not found in upstream origin"))
        self.assertEqual(m.explain_git("something else"), "something else")

    def test_bad_links(self):
        for bad in ("", "ftp://x/y", "http://github.com/o/r", "not a link"):
            with self.assertRaises(m.ManagerError):
                m.parse_link(bad)

    def test_install_cannot_make_agent_folder(self):
        with tempfile.TemporaryDirectory() as t:
            blocker = Path(t, "file")
            blocker.write_text("x")
            src = Path(t, "s", "alpha")
            src.mkdir(parents=True)
            with mock.patch.object(m, "AGENT_DIRS", {"Codex": blocker / "sub"}), self.assertRaises(m.ManagerError):
                m.install([src], ["Codex"])


if __name__ == "__main__":
    unittest.main()

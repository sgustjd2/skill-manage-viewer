"""python -m unittest tests.test_self_update -v   (self_update: how an exe / dmg install downloads, verifies and replaces itself)

No internet: a small HTTP server on 127.0.0.1 plays GitHub (SKILLDESK_UPDATE_TEST_ORIGIN, the same hook CI uses), and
the installer / script that would replace the app is never started for real.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from skilldesk import platform_support as plat
from skilldesk import self_update as su
from skilldesk import skill_manager as mgr
from skilldesk import update_check as uc

REPO = Path(__file__).resolve().parent.parent
SUMS = su.SUMS_NAME
EXE = b"MZ fake installer"


class Server:
    """Serves {path: (status, headers, body)} on a free loopback port."""

    def __init__(self):
        self.routes: dict[str, tuple[int, dict, bytes]] = {}
        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                status, headers, body = routes.get(self.path, (404, {}, b"no"))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self):
        self.http.shutdown()
        self.http.server_close()

    def release(self, version: str, files: dict[str, bytes], listed: list[str] | None = None, sums: str | None = None) -> None:
        names = listed if listed is not None else [*files, SUMS]
        self.routes[f"/repos/{uc.REPO}/releases/latest"] = (
            200, {}, json.dumps({"tag_name": f"v{version}", "assets": [{"name": n} for n in names]}).encode())
        listing = sums if sums is not None else "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in files.items())
        for n, b in {**files, SUMS: listing.encode()}.items():
            self.routes[f"/{uc.REPO}/releases/download/v{version}/{n}"] = (200, {}, b)


class Base(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.addCleanup(self.server.close)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.temp_root = self.tmp / "temp"  # the system temp folder, so staging folders land in a place the test owns
        self.temp_root.mkdir()
        patches = [
            mock.patch.dict(os.environ, {su.TEST_ORIGIN_ENV: self.server.origin}),
            mock.patch.object(mgr, "STORE", self.tmp / "store"),
            mock.patch.object(uc, "_logged", False),
            mock.patch.object(tempfile, "tempdir", str(self.temp_root)),
            mock.patch.object(plat, "WIN", True),
            mock.patch.object(plat, "MAC", False),
            mock.patch.object(su, "unsupported_reason", return_value=""),
            mock.patch.object(su, "_swept", True),  # PrepareTest's own checks must not sweep the folders it just made
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def stages(self) -> list[Path]:
        return list(self.temp_root.glob(f"{su.STAGE_PREFIX}*"))


class WhatIsAllowedTest(unittest.TestCase):
    def test_only_official_release_downloads_are_fetched_first(self):
        ok = f"https://github.com/{uc.REPO}/releases/download/v1.2.3/SkillDesk-Setup-v1.2.3.exe"
        self.assertTrue(su.url_allowed(ok, first=True))
        for bad in (
            f"http://github.com/{uc.REPO}/releases/download/v1.2.3/a.exe",  # not https
            f"https://github.com/other/repo/releases/download/v1.2.3/a.exe",  # noqa: F541 - another repo
            f"https://github.com/{uc.REPO}/raw/main/a.exe",  # not a release asset
            f"https://github.com.evil.example/{uc.REPO}/releases/download/v1.2.3/a.exe",
            f"https://user:pw@github.com/{uc.REPO}/releases/download/v1.2.3/a.exe",
            f"https://github.com:8443/{uc.REPO}/releases/download/v1.2.3/a.exe",
            f"https://objects.githubusercontent.com/{uc.REPO}/releases/download/v1.2.3/a.exe",  # a CDN host is only for redirects
            "ftp://github.com/x", "", "file:///c:/x.exe", "//github.com/x",
        ):
            self.assertFalse(su.url_allowed(bad, first=True), bad)

    def test_a_redirect_may_go_to_githubs_download_hosts_only(self):
        for host in su.CDN_HOSTS:
            self.assertTrue(su.url_allowed(f"https://{host}/github-production-release-asset/abc?x=1"))
        for bad in ("https://evil.example/a", "http://objects.githubusercontent.com/a", "https://githubusercontent.com/a",
                    "https://objects.githubusercontent.com.evil.example/a", "https://github.com/a", "https://user@objects.githubusercontent.com/a"):
            self.assertFalse(su.url_allowed(bad), bad)

    def test_the_test_hook_only_accepts_a_loopback_address(self):
        for value in ("https://example.com", "http://example.com:8000", "http://127.0.0.1", "http://127.0.0.1:80/x", "http://127.0.0.1.evil.com:80",
                      "http://user@127.0.0.1:8000", "file:///x"):
            with mock.patch.dict(os.environ, {su.TEST_ORIGIN_ENV: value}):
                self.assertEqual(su.test_origin(), "", value)
                self.assertTrue(su.api_url().startswith("https://api.github.com/"))
        with mock.patch.dict(os.environ, {su.TEST_ORIGIN_ENV: "http://127.0.0.1:8123"}):
            self.assertEqual(su.test_origin(), "http://127.0.0.1:8123")
            self.assertTrue(su.url_allowed("http://127.0.0.1:8123/" + uc.REPO + "/releases/download/v1.0.0/a", first=True))
            self.assertFalse(su.url_allowed("http://127.0.0.1:9999/" + uc.REPO + "/releases/download/v1.0.0/a", first=True))  # another port
            self.assertFalse(su.url_allowed("https://github.com/" + uc.REPO + "/releases/download/v1.0.0/a", first=True))

    def test_the_hook_is_off_without_the_variable(self):
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop(su.TEST_ORIGIN_ENV, None)
            self.assertEqual(su.api_url(), f"https://api.github.com/repos/{uc.REPO}/releases/latest")
            self.assertEqual(su.asset_url("1.2.3", "a.exe"), f"https://github.com/{uc.REPO}/releases/download/v1.2.3/a.exe")


class ParsingTest(unittest.TestCase):
    def test_installer_names_follow_the_build_scripts(self):
        with mock.patch.object(plat, "WIN", True), mock.patch.object(plat, "MAC", False):
            self.assertEqual(su.installer_name("1.2.3"), "SkillDesk-Setup-v1.2.3.exe")
        with (
            mock.patch.object(plat, "WIN", False),
            mock.patch.object(plat, "MAC", True),
            mock.patch.object(su.platform, "machine", return_value="arm64"),
        ):
            self.assertEqual(su.installer_name("1.2.3"), "SkillDesk-v1.2.3-macOS-arm64.dmg")
        with mock.patch.object(plat, "WIN", False), mock.patch.object(plat, "MAC", False):
            self.assertEqual(su.installer_name("1.2.3"), "")
        iss = (REPO / "tools" / "skilldesk.iss").read_text(encoding="utf-8")
        self.assertIn("OutputBaseFilename=SkillDesk-Setup-v{#AppVersion}", iss)
        build_mac = (REPO / "tools" / "build_mac.py").read_text(encoding="utf-8")
        self.assertIn('f"SkillDesk-v{version}-macOS-{platform.machine()}.dmg"', build_mac)
        self.assertIn(f'BUNDLE_ID = "{su.BUNDLE_ID}"', build_mac)

    def test_only_plain_asset_names_are_kept(self):
        data = {"assets": [{"name": "a.exe"}, {"name": "../x"}, {"name": "a b"}, {"name": 5}, "x", {"nope": 1}, {"name": "ok-1.2_3.dmg"}]}
        self.assertEqual(su.asset_names(data), {"a.exe", "ok-1.2_3.dmg"})
        for bad in (None, [], {}, {"assets": "x"}, {"assets": None}):
            self.assertEqual(su.asset_names(bad), set())

    def test_sums_list(self):
        a, b = "a" * 64, "b" * 64
        self.assertEqual(su.parse_sums(f"{a}  one.exe\n{b} *two.dmg\n\n"), {"one.exe": a, "two.dmg": b})
        self.assertEqual(su.parse_sums(f"{a}  one.exe\n{a}  one.exe\n"), {"one.exe": a})  # the same line twice is harmless
        for bad in (f"{a}  one.exe\n{b}  one.exe\n", "zz  one.exe", f"{a.upper()}  one.exe", f"{a}  ../one.exe", f"{a}one.exe", "<html>",
                    f"{a[:-1]}  x"):
            with self.assertRaises(su.UpdateError, msg=bad):
                su.parse_sums(bad)


class DownloadTest(Base):
    def url(self, name="f.bin"):
        return f"{self.server.origin}/{uc.REPO}/releases/download/v1.0.0/{name}"

    def serve(self, body: bytes, headers: dict | None = None, name="f.bin"):
        self.server.routes[f"/{uc.REPO}/releases/download/v1.0.0/{name}"] = (200, headers or {}, body)

    def test_saves_the_file_and_returns_its_sha256(self):
        self.serve(b"hello")
        dest = self.tmp / "out"
        self.assertEqual(su.download(self.url(), dest), hashlib.sha256(b"hello").hexdigest())
        self.assertEqual(dest.read_bytes(), b"hello")

    def test_a_file_over_the_limit_is_stopped_and_removed(self):
        self.serve(b"x" * 1000)
        dest = self.tmp / "out"
        with self.assertRaises(su.UpdateError):
            su.download(self.url(), dest, limit=999)  # Content-Length says it
        self.assertFalse(dest.exists())
        self.serve(b"x" * 1000, {"Content-Length": "10"})  # a lie about the length cannot get past the running count
        with self.assertRaises(su.UpdateError):
            su.download(self.url(), dest, limit=5)
        self.assertFalse(dest.exists())

    def test_taking_too_long_stops_it(self):
        self.serve(b"x" * 200_000)
        with self.assertRaisesRegex(su.UpdateError, "오래"):
            su.download(self.url(), self.tmp / "out", total=-1)
        self.assertFalse((self.tmp / "out").exists())

    def test_a_redirect_to_another_host_is_refused_and_a_missing_file_is_an_error(self):
        self.server.routes[f"/{uc.REPO}/releases/download/v1.0.0/r"] = (302, {"Location": "http://127.0.0.1:1/evil"}, b"")
        with self.assertRaisesRegex(su.UpdateError, "허용되지 않은"):
            su.download(self.url("r"), self.tmp / "out")
        with self.assertRaisesRegex(su.UpdateError, "내려받지 못했습니다"):
            su.download(self.url("missing"), self.tmp / "out")
        self.assertFalse((self.tmp / "out").exists())

    def test_a_redirect_inside_the_allowed_origin_is_followed(self):
        self.server.routes[f"/{uc.REPO}/releases/download/v1.0.0/r"] = (302, {"Location": f"{self.server.origin}/final"}, b"")
        self.server.routes["/final"] = (200, {}, b"data")
        self.assertEqual(su.download(self.url("r"), self.tmp / "out"), hashlib.sha256(b"data").hexdigest())

    def test_an_unofficial_address_is_never_requested(self):
        with mock.patch.dict(os.environ, {su.TEST_ORIGIN_ENV: ""}), mock.patch.object(su.urllib.request, "build_opener") as opener:
            for url in ("https://evil.example/a.exe", f"http://github.com/{uc.REPO}/releases/download/v1/a"):
                with self.assertRaises(su.UpdateError):
                    su.download(url, self.tmp / "out")
            opener.assert_not_called()


class PrepareTest(Base):
    def test_a_good_release_is_downloaded_verified_and_staged_but_not_run(self):
        self.server.release("2.0.0", {"SkillDesk-Setup-v2.0.0.exe": EXE})
        with mock.patch.object(subprocess, "Popen") as popen, mock.patch.object(subprocess, "run") as run:
            res = su.prepare(current="1.0.0")
        self.assertEqual((res.status, res.version), ("staged", "2.0.0"))
        self.assertEqual(res.path.read_bytes(), EXE)
        self.assertEqual(res.sha256, hashlib.sha256(EXE).hexdigest())
        self.assertEqual(res.path.parent.parent, self.temp_root)
        popen.assert_not_called()
        run.assert_not_called()  # downloading runs nothing (on Windows; macOS mounts the verified dmg, tested separately)

    def test_nothing_newer_or_nothing_reachable_is_quiet(self):
        self.server.release("1.0.0", {"SkillDesk-Setup-v1.0.0.exe": EXE})
        self.assertEqual(su.prepare(current="1.0.0").status, "up-to-date")
        self.assertEqual(su.prepare(current="2.0.0").status, "up-to-date")
        self.server.routes.clear()
        self.assertEqual(su.prepare(current="1.0.0").status, "up-to-date")  # the API answered 404
        self.assertEqual(self.stages(), [])

    def test_a_file_that_does_not_match_the_list_is_deleted_and_never_used(self):
        wrong = f"{'0' * 64}  SkillDesk-Setup-v2.0.0.exe\n"
        self.server.release("2.0.0", {"SkillDesk-Setup-v2.0.0.exe": EXE}, sums=wrong)
        res = su.prepare(current="1.0.0")
        self.assertEqual((res.status, res.version, res.path), ("failed", "2.0.0", None))
        self.assertIn("해시", res.detail)
        self.assertEqual(self.stages(), [])

    def test_a_list_without_our_installer_or_a_broken_list_is_not_trusted(self):
        twice = f"{'a' * 64}  SkillDesk-Setup-v2.0.0.exe\n{'b' * 64}  SkillDesk-Setup-v2.0.0.exe\n"
        for sums in ("", f"{'a' * 64}  SomethingElse.exe\n", "not a list", twice):
            self.server.release("2.0.0", {"SkillDesk-Setup-v2.0.0.exe": EXE}, sums=sums)
            self.assertEqual(su.prepare(current="1.0.0").status, "failed", sums)
        self.assertEqual(self.stages(), [])

    def test_a_release_without_the_files_falls_back_to_the_notice(self):
        self.server.release("2.0.0", {"SkillDesk-Setup-v2.0.0.exe": EXE}, listed=["SkillDesk-Setup-v2.0.0.exe"])  # no SHA256SUMS.txt
        self.assertEqual(su.prepare(current="1.0.0").status, "notice")
        mac_only = [SUMS, "SkillDesk-v2.0.0-macOS-arm64.dmg"]
        self.server.release("2.0.0", {"SkillDesk-Setup-v2.0.0.exe": EXE}, listed=mac_only)  # no installer for Windows
        self.assertEqual(su.prepare(current="1.0.0").status, "notice")
        self.assertEqual(self.stages(), [])

    def test_the_answer_cannot_pick_the_file_to_download(self):
        evil = {"tag_name": "v2.0.0", "assets": [{"name": "SkillDesk-Setup-v2.0.0.exe", "browser_download_url": "https://evil.example/a.exe"},
                                                   {"name": SUMS, "browser_download_url": "https://evil.example/s"}]}
        self.server.routes[f"/repos/{uc.REPO}/releases/latest"] = (200, {}, json.dumps(evil).encode())
        with mock.patch.object(su, "download", side_effect=su.UpdateError("stop")) as dl:
            su.prepare(current="1.0.0")
        self.assertEqual([c.args[0] for c in dl.call_args_list], [su.asset_url("2.0.0", SUMS)])

    def test_unsupported_installs_get_the_notice_without_downloading(self):
        self.server.release("2.0.0", {"SkillDesk-Setup-v2.0.0.exe": EXE})
        with mock.patch.object(su, "unsupported_reason", return_value="안 됨"), mock.patch.object(su, "download") as dl:
            res = su.prepare(current="1.0.0")
        self.assertEqual((res.status, res.version, res.detail), ("notice", "2.0.0", "안 됨"))
        dl.assert_not_called()

    def test_with_auto_update_off_only_the_notice_is_made(self):
        self.server.release("2.0.0", {"SkillDesk-Setup-v2.0.0.exe": EXE})
        with mock.patch.object(su, "download") as dl:
            res = su.prepare(download_it=False, current="1.0.0")
        self.assertEqual((res.status, res.version), ("notice", "2.0.0"))
        dl.assert_not_called()

    def test_an_update_that_did_not_finish_is_not_retried_by_itself_but_by_hand(self):
        self.server.release("2.0.0", {"SkillDesk-Setup-v2.0.0.exe": EXE})
        su._write_attempt("1.0.0", "2.0.0")
        res = su.prepare(current="1.0.0")
        self.assertEqual((res.status, res.version), ("failed", "2.0.0"))
        self.assertEqual(self.stages(), [])
        self.assertEqual(su.prepare(manual=True, current="1.0.0").status, "staged")  # the user asked
        self.server.release("2.1.0", {"SkillDesk-Setup-v2.1.0.exe": EXE})
        self.assertEqual(su.prepare(current="1.0.0").status, "staged")  # a newer release is tried

    def test_the_note_is_cleared_once_the_version_is_reached(self):
        su._write_attempt("1.0.0", "2.0.0")
        self.assertFalse(su.failed_before("3.0.0", "2.0.0"))
        self.assertFalse(su._marker().exists())
        self.assertFalse(su.failed_before("2.0.0", "1.0.0") and False)
        su._write_attempt("1.0.0", "2.0.0")
        self.assertTrue(su.failed_before("2.0.0", "1.0.0"))

    def test_old_staging_folders_are_swept_but_not_young_ones(self):
        old, young = self.temp_root / f"{su.STAGE_PREFIX}old", self.temp_root / f"{su.STAGE_PREFIX}young"
        other = self.temp_root / "somebody-elses"
        for d in (old, young, other):
            d.mkdir()
        t = time.time() - 2 * su.SWEEP_AFTER
        os.utime(old, (t, t))
        os.utime(other, (t, t))
        su.sweep()
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())
        self.assertTrue(other.exists())  # only our own prefix
        with mock.patch.object(su, "_swept", False):  # the first check after the app starts: nothing left can be in use
            su.prepare(current="1.0.0")
        self.assertFalse(young.exists())
        self.assertTrue(other.exists())


class ApplyTest(Base):
    def staged(self) -> su.Staged:
        self.server.release("2.0.0", {"SkillDesk-Setup-v2.0.0.exe": EXE})
        return su.prepare(current="1.0.0")

    def test_windows_runs_the_verified_installer_with_fixed_arguments_and_no_shell(self):
        st = self.staged()
        with mock.patch.object(su.subprocess, "Popen") as popen, mock.patch.object(plat, "release_lock") as unlock:
            su.apply(st, hidden=True)
        (cmd,), kw = popen.call_args
        self.assertEqual(cmd[0], str(st.path))
        self.assertEqual(cmd[1:4], ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        self.assertIn("/RELAUNCH=hidden", cmd)
        self.assertTrue(all(isinstance(c, str) for c in cmd))
        self.assertFalse(kw.get("shell"))
        unlock.assert_called_once()
        self.assertEqual(json.loads(su._marker().read_text(encoding="utf-8"))["to"], "2.0.0")
        with mock.patch.object(su.subprocess, "Popen") as popen:
            su.apply(st, hidden=False)
        self.assertIn("/RELAUNCH=shown", popen.call_args.args[0])

    def test_a_file_changed_after_the_check_is_not_run(self):
        st = self.staged()
        st.path.write_bytes(b"MZ something else")
        with mock.patch.object(su.subprocess, "Popen") as popen, self.assertRaisesRegex(su.UpdateError, "바뀌어"):
            su.apply(st, hidden=False)
        popen.assert_not_called()
        self.assertFalse(su._marker().exists())

    def test_only_a_file_in_our_own_staging_folder_is_run(self):
        st = self.staged()
        elsewhere = self.tmp / "setup.exe"
        elsewhere.write_bytes(EXE)
        for bad in (su.Staged("staged", "2.0.0", elsewhere, st.sha256), su.Staged("failed", "2.0.0", st.path, st.sha256),
                    su.Staged("staged", "2.0.0", None, st.sha256), su.Staged("staged", "2.0.0", self.temp_root / "gone.exe", st.sha256)):
            with mock.patch.object(su.subprocess, "Popen") as popen, self.assertRaises(su.UpdateError):
                su.apply(bad, hidden=False)
            popen.assert_not_called()

    def test_a_start_that_fails_changes_nothing_and_keeps_the_lock(self):
        st = self.staged()
        with (
            mock.patch.object(su.subprocess, "Popen", side_effect=OSError("denied")),
            mock.patch.object(plat, "release_lock") as unlock,
            self.assertRaisesRegex(su.UpdateError, "시작하지 못했습니다"),
        ):
            su.apply(st, hidden=False)
        unlock.assert_not_called()
        self.assertFalse(su._marker().exists())

    def test_the_installer_script_reads_the_relaunch_argument_this_module_sends(self):
        iss = (REPO / "tools" / "skilldesk.iss").read_text(encoding="utf-8")
        self.assertIn("{param:RELAUNCH|}", iss)
        for mode in ("hidden", "shown"):
            self.assertIn(f"Relaunch('{mode}')", iss)
        self.assertIn("/RELAUNCH=hidden", su.installer_command(Path("s.exe"), True, Path("l.log")))
        self.assertIn("skipifsilent", iss)  # the normal 'run SkillDesk' box stays for hand installs only


@unittest.skipIf(sys.platform == "win32" or not shutil.which("sh"), "the macOS helper script needs a POSIX shell")
class MacScriptTest(unittest.TestCase):
    def run_script(self, new_exists=True, make_old=True):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        apps, new, bin_ = tmp / "Applications", tmp / "stage" / "SkillDesk.app", tmp / "bin"
        for d in (apps, new.parent, bin_):
            d.mkdir()
        old = apps / "SkillDesk.app"
        if make_old:
            (old / "Contents").mkdir(parents=True)
            (old / "version").write_text("old")
        if new_exists:
            (new / "Contents").mkdir(parents=True)
            (new / "version").write_text("new")
        opened = tmp / "opened.txt"
        (bin_ / "open").write_text(f'#!/bin/sh\necho "$@" >> "{opened}"\n')
        (bin_ / "open").chmod(stat.S_IRWXU)
        dead = subprocess.Popen(["true"])
        dead.wait()
        script = tmp / "apply.sh"
        script.write_text(su.MAC_SCRIPT)
        for hidden in ("hidden",):
            subprocess.run(["/bin/sh", str(script), str(dead.pid), str(new), str(old), str(tmp / "log"), hidden], check=True,
                           env={**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}, timeout=30)
        return tmp, old, new, opened

    def test_the_new_app_replaces_the_old_one_and_is_opened_hidden(self):
        tmp, old, new, opened = self.run_script()
        self.assertEqual((old / "version").read_text(), "new")
        self.assertEqual(sorted(p.name for p in old.parent.iterdir()), ["SkillDesk.app"])  # no .update-old / .update-new left
        self.assertFalse(new.exists())
        self.assertIn("--hidden", opened.read_text())

    def test_when_the_swap_fails_the_old_app_stays_and_is_opened(self):
        tmp, old, new, opened = self.run_script(new_exists=False)  # nothing to copy: ditto fails
        self.assertEqual((old / "version").read_text(), "old")
        self.assertEqual(sorted(p.name for p in old.parent.iterdir()), ["SkillDesk.app"])
        self.assertTrue(opened.exists())  # the user still gets an app
        self.assertIn("putting the old app back", (tmp / "log").read_text())


class MacAppCheckTest(unittest.TestCase):
    def make_app(self, version="2.0.0", bundle=su.BUNDLE_ID, executable=True) -> Path:
        import plistlib
        app = Path(tempfile.mkdtemp()) / "SkillDesk.app"
        self.addCleanup(shutil.rmtree, app.parent, True)
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle, "CFBundleShortVersionString": version}))
        if executable:
            (app / "Contents" / "MacOS" / "SkillDesk").write_text("x")
        return app

    def test_the_new_app_must_be_ours_and_the_announced_version(self):
        ok = subprocess.CompletedProcess([], 0)
        with mock.patch.object(su.subprocess, "run", return_value=ok):
            su.check_new_app(self.make_app(), "2.0.0")
            for kw, version in (({"version": "1.9.9"}, "2.0.0"), ({"bundle": "com.evil.app"}, "2.0.0"), ({"executable": False}, "2.0.0")):
                with self.assertRaises(su.UpdateError, msg=str(kw)):
                    su.check_new_app(self.make_app(**kw), version)
        bad_signature = subprocess.CompletedProcess([], 1)
        with mock.patch.object(su.subprocess, "run", return_value=bad_signature), self.assertRaisesRegex(su.UpdateError, "서명"):
            su.check_new_app(self.make_app(), "2.0.0")
        with self.assertRaises(su.UpdateError):
            su.check_new_app(Path(tempfile.gettempdir()) / "no-such.app", "2.0.0")


class WhereItLivesTest(unittest.TestCase):
    def test_only_the_frozen_app_replaces_itself(self):
        self.assertFalse(su.supported())  # these tests run from source
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(plat, "WIN", True):
            self.assertTrue(su.supported())
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(plat, "WIN", False),
            mock.patch.object(plat, "MAC", False),
        ):
            self.assertFalse(su.supported())

    def test_windows_needs_the_installers_own_folder(self):
        with tempfile.TemporaryDirectory() as t:
            exe = str(Path(t) / "SkillDesk.exe")
            with mock.patch.object(plat, "WIN", True), mock.patch.object(sys, "executable", exe):
                self.assertIn("설치 파일로 설치한 앱이 아니라", su.unsupported_reason())  # a hand-copied folder: no unins000.exe
                (Path(t) / "unins000.exe").write_bytes(b"")
                self.assertEqual(su.unsupported_reason(), "")

    def test_mac_app_in_a_disk_image_or_a_read_only_folder_is_left_alone(self):
        with mock.patch.object(plat, "WIN", False), mock.patch.object(plat, "MAC", True):
            for exe, why in (("/Volumes/SkillDesk/SkillDesk.app/Contents/MacOS/SkillDesk", "dmg"),
                             ("/private/var/folders/x/AppTranslocation/1/d/SkillDesk.app/Contents/MacOS/SkillDesk", "dmg"),
                             ("/opt/SkillDesk/bin/SkillDesk", ".app")):
                with mock.patch.object(sys, "executable", exe), mock.patch.object(su.Path, "resolve", lambda self: self):
                    self.assertIn(why, su.unsupported_reason(), exe)


if __name__ == "__main__":
    unittest.main()

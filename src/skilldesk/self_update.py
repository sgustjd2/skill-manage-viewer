"""Updates an app that was installed from the Windows installer (exe) or the macOS dmg: download, verify, replace, start again.

Git clones are updated by skill_update.py and source ZIPs only get the notice of update_check.py. Here the app is frozen
(PyInstaller), so a new release means a new installer, and this module fetches it without ever trusting the network:
  - the release answer only names the version and the assets that exist; the file names and URLs are built here from
    the tag, and only https://github.com/<official repo>/releases/download/... is fetched, with every redirect hop checked
    against GitHub's own download hosts,
  - the installer is size-capped, time-capped, saved in its own temp folder and compared with the release's SHA256SUMS.txt;
    a file that does not match is deleted and never run,
  - what runs is that one verified file (Windows) or the verified app copied out of the verified dmg (macOS), with fixed
    arguments and no shell.
This protects against damaged or tampered downloads. It cannot protect against a compromised release (the sums sit in the
same release) because the files are not code-signed; see docs/DEVELOPMENT.md.
Windows: the downloaded installer is started silently and, being the same AppId, upgrades in place and starts the app again.
macOS: a detached shell script waits for the app to quit, swaps the .app and opens it again; the old app is put back if that fails.
"""

import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import platform_support as plat
from . import skill_manager as mgr
from . import update_check as uc

API_ORIGIN = "https://api.github.com"
DOWNLOAD_ORIGIN = "https://github.com"
CDN_HOSTS = frozenset({"objects.githubusercontent.com", "release-assets.githubusercontent.com"})  # where GitHub redirects asset downloads
TEST_ORIGIN_ENV = "SKILLDESK_UPDATE_TEST_ORIGIN"  # CI only: a loopback http://127.0.0.1:PORT standing in for both GitHub hosts
_TEST_ORIGIN = re.compile(r"^http://(?:127\.0\.0\.1|localhost):\d{2,5}$")
MAX_BYTES = 200_000_000
SUMS_MAX_BYTES = 64_000
CONNECT_TIMEOUT = 20  # seconds without a byte
TOTAL_SECONDS = 600  # for one whole download
SUMS_NAME = "SHA256SUMS.txt"
BUNDLE_ID = "io.github.sgustjd2.skilldesk"  # the same as tools/build_mac.py (a test compares them)
STAGE_PREFIX = "skilldesk-update-"
SWEEP_AFTER = 3600  # seconds a left-over staging folder is kept before the next check removes it
_swept = False  # the first check of a run removes every left-over folder: none can belong to this run yet
_SUMS_LINE = re.compile(r"^([0-9a-f]{64}) [ *]([A-Za-z0-9._-]{1,200})$")


class UpdateError(Exception):
    """Anything that stops a self-update; the text is shown to the user."""


@dataclass
class Staged:
    status: str  # up-to-date | staged | notice (newer release, cannot self-update) | failed
    version: str = ""
    path: Path | None = None  # staged: the verified installer (Windows) or the verified new SkillDesk.app (macOS)
    sha256: str = ""
    detail: str = ""


# ---- what is allowed -----------------------------------------------------------------------------
def test_origin() -> str:
    """The loopback origin of CI's fake release server, or '' (always '' unless the environment variable is a loopback URL)."""
    value = os.environ.get(TEST_ORIGIN_ENV, "")
    return value if _TEST_ORIGIN.match(value) else ""


def api_url() -> str:
    return f"{test_origin() or API_ORIGIN}/repos/{uc.REPO}/releases/latest"


def asset_url(version: str, name: str) -> str:
    return f"{test_origin() or DOWNLOAD_ORIGIN}/{uc.REPO}/releases/download/v{version}/{name}"


def url_allowed(url: str, first: bool = False) -> bool:
    """The first URL must be an official release download; a redirect may also go to GitHub's download hosts. Always https
    (the loopback test origin excepted), never with a login or an odd port."""
    u = urllib.parse.urlsplit(url)
    if test_origin():
        ok = u.scheme == "http" and f"http://{u.netloc}" == test_origin()
        return ok and (not first or u.path.startswith(f"/{uc.REPO}/releases/download/"))
    try:
        port = u.port
    except ValueError:
        return False
    if u.scheme != "https" or u.username or u.password or port not in (None, 443):
        return False
    if first:
        return u.hostname == urllib.parse.urlsplit(DOWNLOAD_ORIGIN).hostname and u.path.startswith(f"/{uc.REPO}/releases/download/v")
    return u.hostname in CDN_HOSTS


def installer_name(version: str) -> str:
    """The release asset this machine installs, or '' (no installer for this system)."""
    if plat.WIN:
        return f"SkillDesk-Setup-v{version}.exe"
    if plat.MAC:
        return f"SkillDesk-v{version}-macOS-{platform.machine()}.dmg"
    return ""


def asset_names(data: object) -> set[str]:
    """The names of the assets a /releases/latest answer lists (only plain file names are kept)."""
    assets = data.get("assets") if isinstance(data, dict) else None
    names = {a.get("name") for a in assets[:100] if isinstance(a, dict)} if isinstance(assets, list) else set()
    return {n for n in names if isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,200}", n)}


def parse_sums(text: str) -> dict[str, str]:
    """{file name: sha256} from a `sha256sum` list. A name listed twice with different sums makes the whole list unusable."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _SUMS_LINE.match(line.strip())
        if not m:
            raise UpdateError("해시 목록(SHA256SUMS.txt)의 형식이 올바르지 않습니다")
        if sums.setdefault(m[2], m[1]) != m[1]:
            raise UpdateError("해시 목록에 같은 파일이 서로 다른 값으로 두 번 있습니다")
    return sums


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


# ---- download ------------------------------------------------------------------------------------
class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not url_allowed(newurl):
            raise UpdateError(f"허용되지 않은 주소로 넘어가려 해서 멈췄습니다 ({urllib.parse.urlsplit(newurl).hostname})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, dest: Path, limit: int = MAX_BYTES, timeout: float = CONNECT_TIMEOUT, total: float = TOTAL_SECONDS) -> str:
    """Save `url` to `dest` and return the SHA-256 of what was saved. Stops at `limit` bytes or `total` seconds."""
    if not url_allowed(url, first=True):
        raise UpdateError("공식 릴리스 주소가 아니라 내려받지 않습니다")
    req = urllib.request.Request(url, headers={"User-Agent": "SkillDesk"})
    h, size, end = hashlib.sha256(), 0, time.monotonic() + total
    try:
        with urllib.request.build_opener(_CheckedRedirects).open(req, timeout=timeout) as r, open(dest, "wb") as f:
            if not url_allowed(r.geturl(), first=r.geturl() == url):
                raise UpdateError("받은 곳이 허용된 주소가 아닙니다")
            declared = r.headers.get("Content-Length", "")
            if declared.isdigit() and int(declared) > limit:
                raise UpdateError("파일이 너무 커서 내려받지 않습니다")
            while chunk := r.read(1 << 16):
                size += len(chunk)
                if size > limit:
                    raise UpdateError("파일이 너무 커서 내려받기를 멈췄습니다")
                if time.monotonic() > end:
                    raise UpdateError("내려받는 데 너무 오래 걸려 멈췄습니다")
                h.update(chunk)
                f.write(chunk)
    except (OSError, ValueError) as e:  # URLError, timeouts and a full disk are OSError
        dest.unlink(missing_ok=True)
        raise UpdateError(f"내려받지 못했습니다: {e}") from e
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    return h.hexdigest()


# ---- where this install lives --------------------------------------------------------------------
def supported() -> bool:
    """True for the frozen Windows / macOS app: the only installs that replace themselves."""
    return uc.frozen() and (plat.WIN or plat.MAC)


def mac_app() -> Path:
    """.../SkillDesk.app of the running macOS app."""
    return Path(sys.executable).resolve().parents[2]


def unsupported_reason() -> str:
    """'' when this install may replace itself, else why not (the notice with the release page is shown instead)."""
    if plat.WIN:
        exe_dir = Path(sys.executable).resolve().parent
        if not (exe_dir / "unins000.exe").exists():
            return "설치 파일로 설치한 앱이 아니라 자동 업데이트하지 않습니다"
        if not os.access(exe_dir, os.W_OK):
            return "설치 폴더에 쓸 수 없어 자동 업데이트하지 않습니다"
        return ""
    app = mac_app()
    if app.suffix != ".app":
        return "SkillDesk.app 안에서 실행 중이 아니라 자동 업데이트하지 않습니다"
    if app.as_posix().startswith("/Volumes/") or "AppTranslocation" in app.parts:
        return "dmg나 다운로드 폴더에서 바로 실행 중입니다. SkillDesk를 Applications 폴더로 옮긴 뒤에 자동 업데이트됩니다"
    if not os.access(app.parent, os.W_OK):
        return f"{app.parent} 폴더에 쓸 수 없어 자동 업데이트하지 않습니다"
    return ""


# ---- the attempt marker: a failed update is not retried by itself ---------------------------------
def _marker() -> Path:
    return mgr.STORE / "update-attempt.json"


def _read_attempt() -> dict:
    try:
        data = json.loads(_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_attempt(old: str, new: str) -> None:
    try:
        mgr.STORE.mkdir(parents=True, exist_ok=True)
        _marker().write_text(json.dumps({"from": old, "to": new, "at": int(time.time())}), encoding="utf-8")
    except OSError:
        pass  # without the marker a failed update could be retried at the next check: annoying, not unsafe


def failed_before(version: str, current: str) -> bool:
    """True when the last automatic update to `version` was started from this very version and we are still on it: the
    installer did not finish, so retrying by itself would loop. A newer attempt or a success clears the note."""
    note = _read_attempt()
    if note and note.get("to") == version and note.get("from") == current:
        return True
    if note and uc.parse(str(note.get("to", ""))) and not uc.is_newer(str(note["to"]), current):  # we are at or past it: done
        _marker().unlink(missing_ok=True)
    return False


# ---- staging --------------------------------------------------------------------------------------
def sweep(older_than: float = SWEEP_AFTER) -> None:
    """Remove staging folders of earlier checks (the installer of an update that has run stays until the app's next start)."""
    now = time.time()
    for d in Path(tempfile.gettempdir()).glob(f"{STAGE_PREFIX}*"):
        try:
            # older_than 0 means all: a folder's time can be a few ms ahead of the clock, which made 'now - mtime' negative
            if d.is_dir() and (older_than <= 0 or now - d.stat().st_mtime >= older_than):
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _stage_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=STAGE_PREFIX))


def _run(*cmd: str) -> None:
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)


def _read_new_app(dmg: Path, stage: Path, version: str) -> Path:
    """macOS: copy SkillDesk.app out of the (already verified) dmg and check it is the app we were told about."""
    mount, app = stage / "mnt", stage / "SkillDesk.app"
    try:
        _run("hdiutil", "attach", str(dmg), "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount))
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError(f"dmg를 열지 못했습니다: {e}") from e
    try:
        _run("ditto", str(mount / "SkillDesk.app"), str(app))
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError(f"dmg에서 SkillDesk.app을 꺼내지 못했습니다: {e}") from e
    finally:
        if subprocess.run(["hdiutil", "detach", str(mount)], capture_output=True).returncode != 0:
            subprocess.run(["hdiutil", "detach", "-force", str(mount)], capture_output=True)
    check_new_app(app, version)
    return app


def check_new_app(app: Path, version: str) -> None:
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError) as e:
        raise UpdateError("새 앱의 Info.plist를 읽지 못했습니다") from e
    if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleShortVersionString") != version:
        raise UpdateError("새 앱의 이름이나 버전이 릴리스와 달라 쓰지 않았습니다")
    if not (app / "Contents" / "MacOS" / "SkillDesk").is_file():
        raise UpdateError("새 앱에 실행 파일이 없습니다")
    if subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True).returncode != 0:
        raise UpdateError("새 앱의 서명이 올바르지 않아 쓰지 않았습니다")


def prepare(download_it: bool = True, manual: bool = False, current: str | None = None) -> Staged:
    """Look for a newer release and, when allowed, download and verify it. Never raises; never runs what it downloaded.
    download_it=False (auto-update switched off) only reports that a newer release exists. manual=True (the user asked)
    also retries a version whose last automatic update did not finish."""
    global _swept
    sweep(older_than=SWEEP_AFTER if _swept else 0)
    _swept = True
    current = current or uc.current_version()
    data = uc.fetch_release(api_url())
    latest = uc.latest_version(data)
    if not latest or not uc.is_newer(latest, current):
        return Staged("up-to-date")
    if not download_it:
        return Staged("notice", latest)
    if reason := unsupported_reason():
        return Staged("notice", latest, detail=reason)
    name, names = installer_name(latest), asset_names(data)
    if not name or name not in names or SUMS_NAME not in names:
        return Staged("notice", latest, detail="이번 릴리스에는 자동 업데이트에 쓸 파일이 없습니다")
    if not manual and failed_before(latest, current):
        return Staged(
            "failed", latest, detail=f"이전 자동 업데이트가 끝나지 않아 {latest}은 다시 시도하지 않습니다. 위쪽 알림에서 직접 받아 주세요"
        )
    stage = _stage_dir()
    try:
        sums_file = stage / SUMS_NAME
        download(asset_url(latest, SUMS_NAME), sums_file, limit=SUMS_MAX_BYTES)
        expected = parse_sums(sums_file.read_text(encoding="utf-8", errors="strict")).get(name)
        if not expected:
            raise UpdateError(f"해시 목록에 {name}이 없어 쓰지 않았습니다")
        path = stage / name
        got = download(asset_url(latest, name), path)
        if got != expected:
            raise UpdateError("받은 파일이 릴리스의 해시 목록과 달라 지웠습니다 (손상되었거나 바뀐 파일일 수 있습니다)")
        if plat.MAC:
            path = _read_new_app(path, stage, latest)
    except (UpdateError, UnicodeDecodeError, OSError) as e:
        shutil.rmtree(stage, ignore_errors=True)
        return Staged("failed", latest, detail=f"자동 업데이트를 준비하지 못했습니다: {e}")
    return Staged("staged", latest, path, expected)


# ---- applying ------------------------------------------------------------------------------------
# macOS helper. Everything it needs comes as arguments, none of it is part of this text:
# 1 pid of the running app, 2 the new app, 3 the installed app, 4 log file, 5 hidden|shown.
MAC_SCRIPT = r"""#!/bin/sh
LOG=$4
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
i=0
while kill -0 "$1" 2> /dev/null; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then log "the app did not quit"; exit 1; fi
  sleep 1
done
new="$3.update-new"
old="$3.update-old"
rm -rf "$new" "$old"
if [ -d "$2/Contents" ] && ditto "$2" "$new" && [ -d "$new/Contents" ] && mv "$3" "$old" && mv "$new" "$3"; then
  rm -rf "$old"
  log "replaced $3"
else
  log "swap failed, putting the old app back"
  rm -rf "$new"
  if [ -d "$old" ] && [ ! -d "$3" ]; then mv "$old" "$3"; fi
fi
rm -rf "$2"
if [ "$5" = hidden ]; then open "$3" --args --hidden; else open "$3"; fi
"""


def installer_command(setup: Path, hidden: bool, log: Path) -> list[str]:
    """The silent upgrade. /RELAUNCH is read by tools/skilldesk.iss, which starts the app again when the install is done
    (a silent Setup skips the usual 'run SkillDesk' checkbox)."""
    return [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}", f"/RELAUNCH={'hidden' if hidden else 'shown'}"]


def apply(staged: Staged, hidden: bool) -> None:
    """Start the replacement and return; the caller quits the app right after. Raises UpdateError if it could not be started
    (then nothing was changed)."""
    path = staged.path
    ours = bool(path) and path.parent.parent == Path(tempfile.gettempdir()) and path.parent.name.startswith(STAGE_PREFIX)  # type: ignore[union-attr]
    if staged.status != "staged" or not path or not ours or not path.exists():
        raise UpdateError("받아 둔 업데이트 파일이 없습니다")
    if reason := unsupported_reason():
        raise UpdateError(reason)
    mgr.STORE.mkdir(parents=True, exist_ok=True)
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    try:
        if plat.WIN:
            if sha256_file(path) != staged.sha256:  # the file was changed after the check
                raise UpdateError("받아 둔 설치 파일이 바뀌어 실행하지 않았습니다")
            cmd = installer_command(path, hidden, mgr.STORE / "update-setup.log")
            extra = {"creationflags": 0x00000008 | 0x00000200}  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: outlives this app
        else:
            check_new_app(path, staged.version)
            script = path.parent / "apply-update.sh"
            script.write_text(MAC_SCRIPT, encoding="utf-8")
            script.chmod(stat.S_IRWXU)
            log = mgr.STORE / "update-apply.log"
            cmd = ["/bin/sh", str(script), str(os.getpid()), str(path), str(mac_app()), str(log), "hidden" if hidden else "shown"]
            extra = {"start_new_session": True}
        subprocess.Popen(cmd, **quiet, **extra)  # noqa: S603 - a fixed program and arguments, no shell
        _write_attempt(uc.current_version(), staged.version)
        plat.release_lock()
    except OSError as e:
        raise UpdateError(f"업데이트를 시작하지 못했습니다: {e}") from e


if __name__ == "__main__":
    print("current:", uc.current_version() or "?", "| supported:", supported(), "| reason:", unsupported_reason() if supported() else "-")

"""Keeps the installed app itself up to date: fetch, fast-forward, verify, or roll back.

The app is installed as a git clone of the official repo (see README), so an update is `git pull --ff-only` of `main`.
Because this runs code from the network without asking, it only ever acts when ALL of these hold, and otherwise
reports why it did nothing:
  - the folder is a git clone whose `origin` is the official repo (a fork, mirror or ZIP download is left alone),
  - it is on `main` with no edited tracked files (a developer's branch or unfinished work is never touched),
  - the update is a plain fast-forward (never a merge, rebase or force).
After pulling it installs any changed dependencies and starts the NEW code once (`--check`); if either fails the
folder goes back to exactly the commit it was on, so a bad release can't leave the app unable to start.

    python skill_update.py            check only: prints the state and whether an update is waiting
    python skill_update.py --apply    update now (what the app does by itself every few hours)
"""

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import platform_support as plat
import skill_manager as mgr

HERE = Path(__file__).resolve().parent
OFFICIAL = "sgustjd2/skill-manage-viewer"
BRANCH = "main"
DATA_FILES = {"skill_recommend.json"}  # rewritten daily by a workflow; the app re-reads it, so it never needs a restart


@dataclass
class Result:
    status: str  # up-to-date | available | updated | skipped | failed
    detail: str = ""
    old: str = ""
    new: str = ""
    changes: list[str] = field(default_factory=list)  # commit subjects that came in
    data_only: bool = False  # status "updated" but only DATA_FILES changed: the running code is still current, no restart needed


def slug(url: str) -> str:
    """'https://github.com/Owner/Repo.git' or 'git@github.com:Owner/Repo' -> 'owner/repo' (lower case)."""
    m = re.search(r"[:/]([^/:]+/[^/:]+?)(?:\.git)?/?$", url.strip())
    return m[1].lower() if m else url.strip().lower()


def eligible(root: Path, official: str = OFFICIAL) -> str:
    """'' if this folder may be updated, else the reason it may not (shown to the user)."""
    if not (root / ".git").exists():
        return "git으로 받은 설치가 아니라 자동 업데이트할 수 없습니다 (README의 설치 방법 참고)"
    try:
        remote = mgr.git(root, "remote", "get-url", "origin")
        branch = mgr.git(root, "rev-parse", "--abbrev-ref", "HEAD")
        dirty = mgr.git(root, "status", "--porcelain", "--untracked-files=no")
    except mgr.ManagerError as e:
        return f"git 상태를 읽지 못했습니다: {e}"
    if slug(remote) != slug(official):
        return "공식 저장소가 아닌 곳에서 받은 설치라 자동 업데이트하지 않습니다"
    if branch != BRANCH:
        return f"{branch} 브랜치에서 작업 중이라 자동 업데이트하지 않습니다"
    if dirty:
        return "수정한 파일이 있어 자동 업데이트하지 않습니다"
    return ""


def check(root: Path = HERE, official: str = OFFICIAL) -> Result:
    """Fetch and compare; changes nothing in the working tree."""
    if reason := eligible(root, official):
        return Result("skipped", reason)
    try:
        mgr.git(root, "fetch", "-q", "origin", BRANCH)
        old = mgr.git(root, "rev-parse", "--short", "HEAD")
        new = mgr.git(root, "rev-parse", "--short", f"origin/{BRANCH}")
        behind = int(mgr.git(root, "rev-list", "--count", f"HEAD..origin/{BRANCH}"))
        ahead = int(mgr.git(root, "rev-list", "--count", f"origin/{BRANCH}..HEAD"))
    except (mgr.ManagerError, ValueError) as e:
        return Result("failed", f"업데이트를 확인하지 못했습니다: {e}")
    if ahead:
        return Result("skipped", "이 폴더에 아직 올라가지 않은 커밋이 있어 자동 업데이트하지 않습니다", old, new)
    if not behind:
        return Result("up-to-date", "", old, old)
    subjects = mgr.git(root, "log", "--format=%s", f"HEAD..origin/{BRANCH}").splitlines()
    return Result("available", "", old, new, subjects)


def apply(root: Path = HERE, official: str = OFFICIAL, pip_cmd: list[str] | None = None, smoke_cmd: list[str] | None = None) -> Result:
    """Check, then update if allowed. pip_cmd / smoke_cmd are only replaced by tests."""
    found = check(root, official)
    if found.status != "available":
        return found
    old_full = mgr.git(root, "rev-parse", "HEAD")
    try:
        mgr.git(root, "merge", "--ff-only", "-q", f"origin/{BRANCH}")
        files = set(mgr.git(root, "diff", "--name-only", old_full, "HEAD").splitlines())
        if files <= DATA_FILES:  # nothing runs differently: skip the pip and start-up checks (the loader reads a broken data file as empty)
            return Result("updated", "", found.old, found.new, found.changes, data_only=True)
        if "requirements.txt" in files:
            mgr.run(pip_cmd or [sys.executable, "-m", "pip", "install", "-q", "-r", str(root / "requirements.txt")], timeout=300)
        mgr.run(smoke_cmd or [sys.executable, str(root / "skill_viewer.py"), "--check"], timeout=120)
    except mgr.ManagerError as e:
        # shortcut: only the files git tracks go back; packages pip already installed stay (harmless extras)
        rolled = _rollback(root, old_full)
        return Result(
            "failed",
            f"새 버전을 확인하는 중 실패해 이전 버전으로 되돌렸습니다{'' if rolled else ' (되돌리기도 실패)'}: {e}",
            found.old,
            found.new,
        )
    return Result("updated", "", found.old, found.new, found.changes)


def _rollback(root: Path, commit: str) -> bool:
    """Back to `commit`. Safe to hard-reset: eligible() guaranteed no edited tracked files before we moved."""
    try:
        mgr.git(root, "reset", "-q", "--hard", commit)
        return True
    except mgr.ManagerError:
        return False


def restart(hidden: bool) -> None:
    """Start a fresh copy of the app and let this one exit. The single-instance lock is released first, or the new
    copy would see the old one still running and quit."""
    plat.release_lock()
    cmd = [sys.executable, str(HERE / "skill_viewer.py"), *(["--hidden"] if hidden else [])]
    subprocess.Popen(cmd, cwd=HERE, creationflags=plat.NO_WINDOW, close_fds=True)


if __name__ == "__main__":
    r = apply() if "--apply" in sys.argv else check()
    print(r.status, r.detail, f"{r.old} -> {r.new}" if r.new else "")
    for s in r.changes:
        print(" -", s)
    sys.exit(1 if r.status == "failed" else 0)

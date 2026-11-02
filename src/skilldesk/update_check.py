"""Tells an install that git cannot update (a source ZIP; the exe / .app when self_update.py cannot act) of a newer release.

It only READS the latest release from GitHub and never downloads or runs anything: the app shows a pill and opens the
release page when the user clicks it. (The exe / dmg download the new installer themselves: self_update.py.) A git clone
keeps skill_update.py's own update and is never asked here, so nobody sees two notices.
Everything the network returns is checked before use: only a `vX.Y.Z` tag is taken from the answer, and
the page to open is built from that tag, never read from the response.

    PYTHONPATH=src python -m skilldesk.update_check        print the current and the latest version
"""

import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from . import skill_manager as mgr
from . import skill_update as upd

REPO = upd.OFFICIAL
API = f"https://api.github.com/repos/{REPO}/releases/latest"
TAG = re.compile(r"^v(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
_logged = False  # one log line per run, however often the 6-hourly check fails


def frozen() -> bool:
    """True inside the PyInstaller bundle (the Windows exe or the macOS .app)."""
    return bool(getattr(sys, "frozen", False))


def current_version() -> str:
    """The VERSION file: next to the packages in the exe / .app (PyInstaller's data folder), at the repo top in a source install.
    '' when it is missing or not X.Y.Z."""
    base = Path(getattr(sys, "_MEIPASS", "") or Path(__file__).resolve().parents[2])
    try:
        text = (base / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text if re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", text) else ""


def parse(version: str) -> tuple[int, int, int] | None:
    """'1.10.0' or 'v1.10.0' -> (1, 10, 0), compared as numbers (1.10.0 is newer than 1.9.0); None if it is not that shape."""
    m = TAG.match(version if version.startswith("v") else "v" + version)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def release_page(version: str) -> str:
    return f"https://github.com/{REPO}/releases/tag/v{version}"


def applies() -> bool:
    """Whether git cannot update this install (the exe or .app, or a folder without .git), so the notice is the way to learn of news."""
    return frozen() or not (upd.HERE / ".git").exists()


def latest_version(data: object) -> str | None:
    """X.Y.Z from a /releases/latest answer, or None when it is not a published release with a proper tag."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    return tag[1:] if isinstance(tag, str) and TAG.match(tag) else None


def _log(problem: str) -> None:
    global _logged
    if _logged:
        return
    _logged = True
    try:
        mgr.STORE.mkdir(parents=True, exist_ok=True)
        (mgr.STORE / "update-check.log").write_text(f"{datetime.now():%Y-%m-%d %H:%M} {problem}\n", encoding="utf-8")
    except OSError:
        pass


def fetch_release(url: str = API, timeout: float = 10) -> dict | None:
    """The /releases/latest answer, or None when offline, rate limited or it is not a published release with a proper tag.
    `url` is only replaced by self_update.py (its test hook)."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "SkillDesk"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - a constant https URL (or the loopback test hook)
            data = json.loads(r.read(1_000_000))
    except (OSError, ValueError) as e:  # URLError / timeout are OSError; bad JSON is ValueError
        _log(f"{type(e).__name__}: {e}")
        return None
    if latest_version(data) is None:
        _log("unexpected answer from the release API")
        return None
    return data


def fetch_latest(timeout: float = 10) -> str | None:
    """The newest published version ('1.2.0'), or None when offline, rate limited or the answer is not what we expect."""
    return latest_version(fetch_release(timeout=timeout))


def newer_release(current: str | None = None) -> str | None:
    """The version to tell the user about, or None (up to date, offline, or anything unexpected)."""
    current = current or current_version()
    latest = fetch_latest()
    return latest if latest and is_newer(latest, current) else None


if __name__ == "__main__":
    print("current:", current_version() or "?", "| latest:", fetch_latest() or "?")

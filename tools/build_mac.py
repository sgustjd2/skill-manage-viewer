"""Builds the macOS installer: PyInstaller makes dist/SkillDesk.app, hdiutil wraps it in dist/SkillDesk-vX.Y.Z-macOS-<cpu>.dmg.

    python3 -m pip install -r requirements.txt pyinstaller==6.11.1
    python3 tools/build_mac.py                       build both (CI does this; see .github/workflows/release.yml)
    python3 tools/build_mac.py --app-only            only dist/SkillDesk.app, no dmg

macOS only. The app is built for the CPU of the Python that runs this (arm64 on GitHub's macos-latest), not universal2:
a universal2 app needs every binary wheel (Pillow, pyobjc) in both architectures. The dmg name carries the CPU.
The app is signed ad hoc (`codesign -s -`): no Apple Developer ID, no notarization, so Gatekeeper warns on the first start.
The app is built from the repo files, so build from the commit the release tag points at.
"""

import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = DIST / "SkillDesk.app"
BUNDLE_ID = "io.github.sgustjd2.skilldesk"  # not the source install's id (install.py), so the two never get mixed up


def run(*cmd: str) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


def pyinstaller() -> None:
    run(sys.executable, "skill_viewer.py", "--make-icns")  # skill_viewer.icns is drawn, not tracked
    # skilldesk/data and skilldesk/assets sit where the code looks for them (next to the module); VERSION is read from the bundle root
    run(
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--name", "SkillDesk",
        "--icon", "skill_viewer.icns", "--osx-bundle-identifier", BUNDLE_ID, "--paths", "src",
        "--add-data", "VERSION:.",
        "--add-data", "src/skilldesk/data:skilldesk/data",
        "--add-data", "src/skilldesk/assets:skilldesk/assets",
        "--hidden-import", "pystray._darwin",  # pystray picks its backend by name at run time
        "skill_viewer.py",
    )


def finish_bundle(version: str) -> None:
    """The version shown in Finder, then the ad-hoc signature (it must come last: it seals Info.plist)."""
    info = APP / "Contents" / "Info.plist"
    plist = plistlib.loads(info.read_bytes())
    plist.update({"CFBundleShortVersionString": version, "CFBundleVersion": version, "LSMinimumSystemVersion": "10.15",
                  "NSHighResolutionCapable": True})
    info.write_bytes(plistlib.dumps(plist))
    run("codesign", "--force", "--deep", "-s", "-", str(APP))
    run("codesign", "--verify", "--deep", "--strict", str(APP))


def dmg(version: str) -> Path:
    out = DIST / f"SkillDesk-v{version}-macOS-{platform.machine()}.dmg"
    stage = DIST / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    run("ditto", str(APP), str(stage / "SkillDesk.app"))  # ditto keeps the symlinks and signature inside the bundle
    (stage / "Applications").symlink_to("/Applications")  # drag the app onto it
    out.unlink(missing_ok=True)
    run("hdiutil", "create", "-volname", "SkillDesk", "-srcfolder", str(stage), "-format", "UDZO", "-ov", str(out))
    shutil.rmtree(stage)
    return out


def main(argv: list[str]) -> int:
    if sys.platform != "darwin":
        sys.exit("the macOS app is built on macOS only")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyinstaller()
    finish_bundle(version)
    print("built", APP, "for", platform.machine())
    if "--app-only" not in argv:
        print("built", dmg(version))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

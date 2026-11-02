"""Put the app where the OS can find it by name, so you can start it from the Start menu / Spotlight.

  python install.py              Windows: Start menu shortcut      macOS: ~/Applications/<name>.app
  python install.py --startup    also start (tray only) at login   Windows: Startup folder  macOS: LaunchAgent
  python install.py --uninstall  remove what this script created
  python install.py --dest DIR   write the shortcut / app into DIR instead (for testing)

Search for "SkillDesk". Nothing is copied: the entries point at this folder, so keep it where it is.
"""

import argparse
import base64
import contextlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import platform_support as plat

HERE = Path(__file__).resolve().parent
APP_NAME = "SkillDesk"
BUNDLE_ID = "io.github.sgustjd2.skill-manage-viewer"
MARKER = "# created by skill-manage-viewer install.py"  # only bundles carrying this are ever removed


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def make_shortcut(lnk: Path, python: Path, args: str) -> None:
    """Windows .lnk via the WScript.Shell COM object (no extra packages).

    WScript.Shell saves through the ANSI code page, so a path with characters outside it (a Korean user name on an
    English Windows) fails. It writes an ASCII-named file next to the target, which Python then renames.
    """
    ascii_lnk = lnk.with_name(f"skilldesk-{os.getpid()}.lnk")
    script = "\n".join(
        [
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(ascii_lnk))})",
            f"$s.TargetPath = {_ps_quote(str(python))}",
            f"$s.Arguments = {_ps_quote(args)}",
            f"$s.WorkingDirectory = {_ps_quote(str(HERE))}",
            f"$s.IconLocation = {_ps_quote(str(HERE / 'skill_viewer.ico'))}",
            "$s.Description = 'SkillDesk'",
            "$s.Save()",
        ]
    )
    lnk.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()],
        capture_output=True,
        text=True,
        creationflags=plat.NO_WINDOW,
    )
    if r.returncode or not ascii_lnk.exists():
        raise RuntimeError(f"바로 가기를 만들지 못했습니다: {lnk}\n{(r.stderr or r.stdout).strip()}")
    os.replace(ascii_lnk, lnk)


def windowless(python: Path) -> Path:
    w = python.with_name("pythonw.exe")
    return w if w.exists() else python  # pythonw has no console window


def install_windows(programs: Path, startup: bool, python: Path) -> list[Path]:
    exe, script = windowless(python), HERE / "skill_viewer.py"
    made = [programs / f"{APP_NAME}.lnk"]
    make_shortcut(made[0], exe, f'"{script}"')
    if startup:
        made.append(programs / "Startup" / f"{APP_NAME}.lnk")
        make_shortcut(made[1], exe, f'"{script}" --hidden')
    return made


def install_mac(apps: Path, launch_agents: Path | None, startup: bool, python: Path) -> list[Path]:
    """A minimal .app whose launcher runs this folder's script; Spotlight indexes ~/Applications."""
    app = apps / f"{APP_NAME}.app"
    (app / "Contents/MacOS").mkdir(parents=True, exist_ok=True)
    (app / "Contents/Resources").mkdir(parents=True, exist_ok=True)
    plist = {
        "CFBundleName": "SkillDesk",
        "CFBundleDisplayName": "SkillDesk",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "launcher",
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "CFBundleShortVersionString": "1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.15",
    }
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(plist))
    launcher = app / "Contents/MacOS/launcher"
    launcher.write_text(f'#!/bin/bash\n{MARKER}\ncd "{HERE}"\nexec "{python}" skill_viewer.py "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    from skill_viewer import make_icns

    make_icns(app / "Contents/Resources/AppIcon.icns")
    made = [app]
    if startup and launch_agents is not None:
        agent = launch_agents / f"{BUNDLE_ID}.plist"
        launch_agents.mkdir(parents=True, exist_ok=True)
        agent.write_bytes(plistlib.dumps({"Label": BUNDLE_ID, "ProgramArguments": [str(launcher), "--hidden"], "RunAtLoad": True}))
        made.append(agent)  # takes effect at the next login
    return made


def remove(paths: list[Path]) -> list[Path]:
    gone = []
    for p in paths:
        if p.suffix == ".app":
            launcher = p / "Contents/MacOS/launcher"
            if not (launcher.is_file() and MARKER in launcher.read_text(encoding="utf-8")):
                continue  # not ours: leave it alone
            shutil.rmtree(p)
            gone.append(p)
        elif p.exists():
            p.unlink()
            gone.append(p)
    return gone


def targets(dest: Path | None) -> list[Path]:
    """Every file this script can create, for --uninstall."""
    if plat.WIN:
        programs = dest or Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs"
        return [programs / f"{APP_NAME}.lnk", programs / "Startup" / f"{APP_NAME}.lnk"]
    apps = dest or Path.home() / "Applications"
    return [apps / f"{APP_NAME}.app", (dest or Path.home() / "Library/LaunchAgents") / f"{BUNDLE_ID}.plist"]


def main(argv: list[str]) -> int:
    # an English Windows console (cp1252) can't print Korean; show '?' instead of crashing after the work is done
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--startup", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dest", type=Path)
    a = ap.parse_args(argv)
    if not (plat.WIN or plat.MAC):
        print("Windows and macOS only")
        return 1
    if a.uninstall:
        for p in remove(targets(a.dest)):
            print("removed", p)
        return 0
    python = Path(sys.executable)
    if plat.WIN:
        made = install_windows(a.dest or targets(None)[0].parent, a.startup, python)
    else:
        made = install_mac(
            a.dest or Path.home() / "Applications",
            (a.dest or Path.home() / "Library/LaunchAgents") if a.startup else None,
            a.startup,
            python,
        )
    for p in made:
        print("created", p)
    print('이제 검색창(Windows 시작 / macOS Spotlight)에 "SkillDesk"를 입력하세요.')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

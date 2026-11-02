"""Builds the Windows installer: PyInstaller makes dist/SkillDesk/, Inno Setup wraps it in dist/SkillDesk-Setup-vX.Y.Z.exe.

    python -m pip install pyinstaller==6.11.1        (and Inno Setup 6: winget install JRSoftware.InnoSetup)
    python tools/build_exe.py                        build both (CI does this; see .github/workflows/release.yml)
    python tools/build_exe.py --app-only             only dist/SkillDesk/SkillDesk.exe, no installer

Windows only. The exe is built from the repo files, so build from the commit the release tag points at.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEP = ";"  # PyInstaller's --add-data separator on Windows
_PF = [os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", ""), os.environ.get("LOCALAPPDATA", "") + "/Programs"]
INNO = [Path(p) / "Inno Setup 6" / "ISCC.exe" for p in _PF]


def pyinstaller(version: str) -> None:
    # skilldesk/data and skilldesk/assets sit where the code looks for them (next to the module); VERSION is read from the bundle root
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--name", "SkillDesk",
        "--icon", "skill_viewer.ico", "--paths", "src",
        "--add-data", f"VERSION{SEP}.",
        "--add-data", f"src/skilldesk/data{SEP}skilldesk/data",
        "--add-data", f"src/skilldesk/assets{SEP}skilldesk/assets",
        "--hidden-import", "pystray._win32",  # pystray picks its backend by name at run time
        "skill_viewer.py",
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)
    print("built dist/SkillDesk for", version)


def inno(version: str) -> Path:
    iscc = shutil.which("iscc") or next((str(p) for p in INNO if p.exists()), None)
    if not iscc:
        sys.exit("Inno Setup 6 (ISCC.exe) not found: winget install JRSoftware.InnoSetup")
    subprocess.run([iscc, "/Qp", str(ROOT / "tools" / "skilldesk.iss")], check=True, env={**os.environ, "SKILLDESK_VERSION": version})
    return ROOT / "dist" / f"SkillDesk-Setup-v{version}.exe"


def main(argv: list[str]) -> int:
    if sys.platform != "win32":
        sys.exit("the installer is built on Windows only")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyinstaller(version)
    if "--app-only" not in argv:
        print("built", inno(version))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

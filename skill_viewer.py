"""Launcher kept at the top of the repo: the Start menu shortcut, the macOS app, start.bat / start.command and the
auto-update of installs made before the code moved to src/skilldesk all run THIS path, so it must stay.
Takes the same arguments as always (--hidden, --check, --tray-child, --make-icon, --make-icns)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from skilldesk.skill_viewer import main  # noqa: E402

main()

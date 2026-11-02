"""Launcher kept at the top of the repo so `python install.py` keeps working (see src/skilldesk/install.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from skilldesk.install import main  # noqa: E402

sys.exit(main(sys.argv[1:]))

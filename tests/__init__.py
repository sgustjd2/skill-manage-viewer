"""Run from the repo folder: `python -m unittest tests.test_manager`. The app code lives in src/, which is put on the path here."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from skilldesk import platform_support  # noqa: E402

platform_support.hide_test_desktop()  # Windows: windows open on an invisible desktop, not over the user's work

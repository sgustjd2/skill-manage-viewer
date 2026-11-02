"""Run from the repo folder: `python -m unittest tests.test_manager`. The app code lives in src/, which is put on the path here."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

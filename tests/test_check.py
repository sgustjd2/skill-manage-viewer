"""python -m unittest test_check -v   (tools/check.py: what it runs and how much it prints)"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import check  # noqa: E402  # pyright: ignore[reportMissingImports]

SAMPLE = """..F.E
======================================================================
FAIL: test_a (test_x.T.test_a)
----------------------------------------------------------------------
Traceback (most recent call last):
AssertionError: 1 != 2

======================================================================
ERROR: test_b (test_x.T.test_b)
----------------------------------------------------------------------
Traceback (most recent call last):
KeyError: 'k'

----------------------------------------------------------------------
Ran 5 tests in 0.01s

FAILED (failures=1, errors=1)
"""


class CheckTest(unittest.TestCase):
    def test_fast_mode_skips_only_the_window_tests(self):
        classes = check.gui_free_test_classes()
        self.assertNotIn("test_viewer.PanelTest", classes)
        self.assertIn("test_viewer.CategoryTest", classes)

    def test_every_test_module_is_run_and_fast_mode_only_trims_test_viewer(self):
        every = sorted(p.stem for p in Path(check.ROOT).glob("test_*.py"))
        self.assertEqual(check.test_targets(False), every)
        fast = check.test_targets(True)
        self.assertNotIn("test_viewer", fast)
        self.assertTrue(all(m in fast for m in every if m != "test_viewer"))
        self.assertNotIn("test_viewer.PanelTest", fast)

    def test_only_failure_blocks_are_printed(self):
        out = check.failures(SAMPLE)
        self.assertIn("FAIL: test_a", out)
        self.assertIn("KeyError: 'k'", out)
        self.assertNotIn("..F.E", out)  # progress dots and the summary are noise
        self.assertNotIn("Ran 5 tests", out)

    def test_output_is_capped(self):
        noisy = "FAIL: t\n" + "\n".join(f"line {i}" for i in range(500))
        self.assertLessEqual(len(check.failures(noisy).splitlines()), check.MAX_LINES)
        self.assertLessEqual(len(check.failures("no blocks\n" * 500).splitlines()), check.MAX_LINES)

    def test_unprintable_characters_do_not_crash_the_failure_report(self):
        console = io.TextIOWrapper(io.BytesIO(), encoding="cp949")  # a Korean Windows console
        old, sys.stdout = sys.stdout, console
        try:
            check.safe_output()
            print("broken \ufffd text")
            console.flush()
        finally:
            sys.stdout = old


if __name__ == "__main__":
    unittest.main()

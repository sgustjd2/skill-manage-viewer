"""Everything a change needs before a PR, in one command, printing only what failed.

    python tools/check.py          lint + type check + every test (before pushing)
    python tools/check.py fast     lint + the tests that open no window (about 30 s; use while editing)

Long passing output costs an AI agent tokens to read, so a passing step is one line and a failing step shows only its
failures.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_LINES = 40  # of a failing step's output


def gui_free_test_classes() -> list[str]:
    """test_viewer classes except PanelTest (the one that opens windows), found without importing Tk."""
    text = (ROOT / "test_viewer.py").read_text(encoding="utf-8")
    return [f"test_viewer.{c}" for c in re.findall(r"^class (\w+)\(unittest\.TestCase\)", text, re.M) if c != "PanelTest"]


def test_targets(fast: bool) -> list[str]:
    """Every test_*.py module (so a new test file is never forgotten); in fast mode test_viewer is cut down to the
    classes that open no window."""
    modules = sorted(p.stem for p in ROOT.glob("test_*.py"))
    if not fast:
        return modules
    return [m for m in modules if m != "test_viewer"] + gui_free_test_classes()


def failures(output: str) -> str:
    """The FAIL/ERROR blocks of unittest output (or its tail when there are none), at most MAX_LINES lines."""
    blocks = re.findall(r"^(?:FAIL|ERROR): .*?(?=^(?:FAIL|ERROR): |^-{20,}\nRan |\Z)", output, re.M | re.S)
    lines = ("".join(blocks) if blocks else output).strip().splitlines()
    return "\n".join(lines[-MAX_LINES:] if not blocks else lines[:MAX_LINES])


def step(name: str, cmd: list[str], summarize=None) -> bool:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0:
        ran = re.search(r"^Ran (\d+) tests?", out, re.M)
        print(f"OK    {name}" + (f" ({ran[1]} tests)" if ran else ""))
        return True
    print(f"FAIL  {name}")
    print(summarize(out) if summarize else "\n".join(out.strip().splitlines()[:MAX_LINES]))
    return False


def safe_output() -> None:
    """A failing step can print characters the console's code page lacks (cp949 on Korean Windows): show '?', don't crash."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")


def main(argv: list[str]) -> int:
    safe_output()
    fast = "fast" in argv
    py = sys.executable
    ok = step(
        "ruff",
        [py, "-m", "ruff", "check", ".", "--line-length", "140", "--select", "E,F,W,B,UP,SIM", "--output-format", "concise", "--quiet"],
    )
    if not fast:
        if shutil.which("pyright") or subprocess.run([py, "-m", "pyright", "--version"], capture_output=True).returncode == 0:
            ok &= step("pyright", [py, "-m", "pyright", "skill_manager.py", "skill_viewer.py", "platform_support.py", "install.py"])
        else:
            print("SKIP  pyright (not installed: pip install pyright)")
    ok &= step("tests" + (" (no windows)" if fast else ""), [py, "-m", "unittest", "-q", *test_targets(fast)], failures)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

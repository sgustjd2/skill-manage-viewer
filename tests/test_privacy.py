"""python -m unittest test_privacy -v   (nothing of whoever runs this may end up in the repo)

SkillDesk reads the skills and plugins installed on each person's own PC, so the repo must hold no one's machine:
no home folder path, no account name, and only skills anyone can install in the bundled lists.
"""

import getpass
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
BINARY = {".png", ".ico", ".icns", ".otf", ".ttf", ".woff2"}


def tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return [ROOT / n for n in out.stdout.split("\0") if n and (ROOT / n).suffix.lower() not in BINARY and (ROOT / n).is_file()]


class PrivacyTest(unittest.TestCase):
    def test_no_home_folder_or_account_name_in_tracked_files(self):
        home = str(Path.home())
        user = getpass.getuser()
        needles = [home, home.replace("\\", "/")] + ([user] if len(user) >= 4 else [])  # a short name would match ordinary words
        hits = []
        for f in tracked_text_files():
            text = f.read_text(encoding="utf-8", errors="replace").lower()
            hits += [f"{f.name}: {n}" for n in needles if n.lower() in text]
        self.assertEqual(hits, [], "remove your own paths / account name before committing")

    def test_no_real_looking_user_folder_paths(self):
        # `C:\Users\이름` and `/Users/이름` in the docs are placeholders; a real account name would be a leak
        real = re.compile(r"(?:[A-Za-z]:\\?Users\\?|/Users/|/home/)(?!이름|name|<|\$|\{|\*|%|username|you\b|user\b)[\w.-]{3,}", re.I)
        hits = [
            f"{f.name}: {m.group(0)}"
            for f in tracked_text_files()
            if f.suffix != ".py"
            for m in real.finditer(f.read_text(encoding="utf-8", errors="replace"))
        ]
        self.assertEqual(hits, [])

    def test_bundled_skill_lists_hold_only_skills_anyone_can_install(self):
        """Names that exist only on one person's PC (their own or their employer's skills) don't belong in the
        shared lists: the app classifies unknown skills by keyword and shows each skill's own description."""
        private = {"company-commit-pr", "gitlab-guide-check", "ui-audit", "ui-design", "ui-polish", "ui-qa", "ui-work"}
        notes = set(json.loads((ROOT / "skill_notes.json").read_text(encoding="utf-8")))
        listed = {s for c in json.loads((ROOT / "skill_categories.json").read_text(encoding="utf-8")) for s in c["skills"]}
        self.assertEqual(sorted((notes | listed) & private), [])


if __name__ == "__main__":
    unittest.main()

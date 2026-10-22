"""python -m unittest test_release -v   (tools/release.py: which commits make a release, and what the notes say)"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import release  # noqa: E402  # pyright: ignore[reportMissingImports]


def git_in(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


class ReleaseTest(unittest.TestCase):
    def test_the_commit_prefix_decides_the_step(self):
        self.assertEqual(release.bump_kind(["Feat: 새 탭", "Fix: 버그"]), "minor")
        self.assertEqual(release.bump_kind(["Fix: 버그", "Refactor: 정리"]), "patch")
        self.assertIsNone(release.bump_kind(["Docs: 문서", "Test: 시험", "Chore: 정리", "메시지 없는 커밋"]))
        self.assertEqual(release.bump("1.2.3", "minor"), "1.3.0")
        self.assertEqual(release.bump("1.2.3", "patch"), "1.2.4")

    def test_notes_group_by_kind_and_leave_out_our_version_commit(self):
        text = release.notes_md("1.3.0", "v1.2.3", [
            ("a1", "Feat: 새 탭"), ("b2", "Fix: 버그"), ("c3", "Chore: 버전 1.3.0 올림"), ("d4", "Docs: 문서"), ("e5", "그냥 메시지"),
        ])
        self.assertIn("### 새 기능\n- 새 탭 (`a1`)", text)
        self.assertIn("### 고친 것\n- 버그 (`b2`)", text)
        self.assertIn("### 기타\n- 문서 (`d4`)\n- 그냥 메시지 (`e5`)", text)
        self.assertNotIn("버전 1.3.0", text)
        self.assertIn("[v1.2.3과 비교](https://github.com/sgustjd2/skill-manage-viewer/compare/v1.2.3...v1.3.0)", text)

    def test_the_first_release_has_no_list(self):
        self.assertIn("처음으로 버전을 붙인", release.notes_md("1.0.0", None, []))

    def test_a_feature_bumps_once_and_nothing_new_releases_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            git_in(root, "init", "-q")
            git_in(root, "config", "user.name", "t")
            git_in(root, "config", "user.email", "t@t")
            (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (root / "a.txt").write_text("1", encoding="utf-8")
            git_in(root, "add", "-A")
            git_in(root, "commit", "-qm", "Feat: 시작")
            notes = root / "notes.md"
            with mock.patch.object(release, "ROOT", root), mock.patch.object(release, "VERSION_FILE", root / "VERSION"):
                self.assertEqual(release.main([str(notes)]), 0)  # first release: VERSION as it is
                self.assertEqual(git_in(root, "tag").split(), ["v1.0.0"])
                (root / "a.txt").write_text("2", encoding="utf-8")
                git_in(root, "commit", "-qam", "Feat: 두 번째")
                self.assertEqual(release.main([str(notes)]), 0)
                self.assertEqual((root / "VERSION").read_text(encoding="utf-8").strip(), "1.1.0")
                self.assertEqual(git_in(root, "log", "-1", "--format=%s"), "Chore: 버전 1.1.0 올림")
                self.assertIn("두 번째", notes.read_text(encoding="utf-8"))
                head = git_in(root, "rev-parse", "HEAD")
                self.assertEqual(release.main([str(notes)]), 0)  # nothing new since v1.1.0: no commit, no tag
                self.assertEqual(git_in(root, "rev-parse", "HEAD"), head)
                self.assertEqual(sorted(git_in(root, "tag").split()), ["v1.0.0", "v1.1.0"])


if __name__ == "__main__":
    unittest.main()

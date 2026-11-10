"""Unseen skills must land in a sensible category from their name and (usually English) description alone."""
import unittest

from skilldesk.skill_viewer import OTHER, categorize

# (name, description written for this test, expected). None of the names is in skill_categories.json.
SAMPLES = [
    ("unslop", "Use when building or reviewing a user interface and the result looks generic or AI-generated. "
               "Improves layout, typography, color and spacing so pages read as designed by a person.", "UI·디자인"),
    ("react-best-practices", "React and Next.js performance guidelines. Use when writing or refactoring components, "
                             "data fetching or bundle size.", "구현·테스트"),
    ("web-design-guidelines", "Review UI code for accessibility and interface guidelines compliance.", "UI·디자인"),
    ("pdf-forms", "Fill, merge and split PDF files, and extract text and tables from them.", "문서·자료"),
    ("excel-helper", "Create and edit xlsx spreadsheets with formulas, charts and formatting.", "문서·자료"),
    ("playwright-e2e", "Drive a real browser with Playwright to click through pages and take screenshots.", "자동화·브라우저"),
    ("cron-helper", "Schedule recurring jobs that run on a timer and report back.", "자동화·브라우저"),
    ("pr-writer", "Write pull request titles and descriptions from the diff and the commit history.", "Git·협업"),
    ("branch-cleanup", "Delete merged branches and prune stale worktrees in a git repository.", "Git·협업"),
    ("root-cause", "Find the root cause of a failing build or a crash by reproducing it and bisecting.", "디버깅·검증"),
    ("prove-it", "Check that the change really works before you say it is done: run the app and read the output.", "디버깅·검증"),
    ("code-critic", "Review a diff for bugs, security problems and style, and report findings by severity.", "리뷰·품질"),
    ("write-tests-first", "Write a failing test, make it pass, then refactor. Red green refactor.", "구현·테스트"),
    ("roadmap-planner", "Turn a rough idea into a plan: ask questions, list requirements and decide the architecture "
                        "before any code is written.", "기획·설계"),
    ("skill-forge", "Create a new skill or improve an existing one, with a SKILL.md and evals.", "에이전트·스킬 설정"),
    ("mcp-server-kit", "Build an MCP server so Claude can call external APIs.", "에이전트·스킬 설정"),
    ("logo-maker", "Generate logos, banners and brand assets in many styles.", "UI·디자인"),
    ("slide-deck", "Make a presentation with slides, speaker notes and charts.", "문서·자료"),
    ("tailwind-ui", "Style components with Tailwind CSS: responsive layouts, dark mode and accessible widgets.", "UI·디자인"),
    ("deep-dive-research", "Research a topic across many sources and write a cited report.", "문서·자료"),
    ("zen-quotes", "Tell the user a calming quote once a day.", OTHER),
]


class CategorizeTest(unittest.TestCase):
    def test_unseen_skills(self):
        wrong = [(n, categorize(n, d), want) for n, d, want in SAMPLES if categorize(n, d) != want]
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()

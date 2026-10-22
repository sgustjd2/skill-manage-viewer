"""Release bookkeeping for main: bump VERSION, tag it and write the release notes. CI runs it on every push to main.

    python tools/release.py NOTES.md   (writes NOTES.md and prints "released vX.Y.Z"; prints nothing when there is nothing to release)

The commit subjects since the last tag decide the step, using the prefixes this repo already uses: a `Feat:` is a minor step,
`Fix:`/`Refactor:`/`Style:` a patch, and `Docs:`/`Test:`/`Chore:` alone release nothing. A major step, or any other number,
is a hand edit of VERSION: a VERSION already ahead of the last tag is released as it is.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
REPO = os.environ.get("GITHUB_REPOSITORY", "sgustjd2/skill-manage-viewer")
SUBJECT = re.compile(r"^(Feat|Fix|Refactor|Style|Docs|Test|Chore)\s*:\s*(.+)$")
BUMP_COMMIT = re.compile(r"^Chore: 버전 ")  # our own version commit: never listed in the notes
SECTIONS = {"Feat": "새 기능", "Fix": "고친 것", "Refactor": "정리", "Style": "화면", "기타": "기타"}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def last_tag() -> str | None:
    try:
        return git("describe", "--tags", "--abbrev=0", "--match", "v*")
    except subprocess.CalledProcessError:  # no tag yet
        return None


def commits_since(tag: str) -> list[tuple[str, str]]:
    items = []
    for line in git("log", "--no-merges", "--format=%h %s", f"{tag}..HEAD").splitlines():
        short, _, subject = line.partition(" ")
        items.append((short, subject))
    return items


def bump_kind(subjects: list[str]) -> str | None:
    kinds = {m[1] for s in subjects if (m := SUBJECT.match(s))}
    if "Feat" in kinds:
        return "minor"
    if kinds & {"Fix", "Refactor", "Style"}:
        return "patch"
    return None


def bump(version: str, kind: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    return f"{major}.{minor + 1}.0" if kind == "minor" else f"{major}.{minor}.{patch + 1}"


def notes_md(version: str, prev: str | None, items: list[tuple[str, str]]) -> str:
    if prev is None:
        return f"## v{version}\n\n처음으로 버전을 붙인 릴리스입니다. 이전 변경은 커밋 기록에서 볼 수 있습니다.\n"
    groups: dict[str, list[str]] = {}
    for short, subject in items:
        if BUMP_COMMIT.match(subject):
            continue
        m = SUBJECT.match(subject)
        kind, text = (m[1], m[2]) if m else ("기타", subject)
        groups.setdefault(kind if kind in SECTIONS else "기타", []).append(f"- {text} (`{short}`)")
    lines = [f"## v{version}", ""]
    for kind, title in SECTIONS.items():
        if groups.get(kind):
            lines += [f"### {title}", *groups[kind], ""]
    lines.append(f"[{prev}과 비교](https://github.com/{REPO}/compare/{prev}...v{version})")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    current = VERSION_FILE.read_text(encoding="utf-8").strip()
    prev = last_tag()
    if prev is None:  # first release: VERSION as it is
        version, items = current, []
    else:
        items = commits_since(prev)
        if current != prev.removeprefix("v"):  # edited by hand (a major step, say): that number is the release
            version = current
        else:
            kind = bump_kind([s for _, s in items])
            if kind is None:
                return 0
            version = bump(current, kind)
            VERSION_FILE.write_text(version + "\n", encoding="utf-8")
            git("-c", "user.name=SkillDesk release", "-c", "user.email=release@skilldesk.invalid",
                "commit", "-q", "-m", f"Chore: 버전 {version} 올림", "--", "VERSION")
    git("tag", f"v{version}")
    Path(argv[0]).write_text(notes_md(version, prev, items), encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):  # the workflow reads the version from here
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"version={version}\n")
    print(f"released v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

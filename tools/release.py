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
INSTALL = """
---

### 설치

**Windows: 설치 파일**

Python과 Git 없이 쓸 수 있습니다. [SkillDesk-Setup-v{version}.exe][exe]를 받아 실행하세요.

- 서명하지 않은 파일이라 Windows SmartScreen이 "PC를 보호했습니다"라고 할 수 있습니다. **추가 정보 → 실행**을 누르세요.
- 새 버전이 나오면 앱 위쪽에 알림이 뜹니다. 눌러서 새 설치 파일을 받아 다시 실행하면 됩니다.

**macOS: 설치 파일** (Apple 칩 Mac, M1 이후)

Python과 Git 없이 쓸 수 있습니다. [SkillDesk-v{version}-macOS-arm64.dmg][mac]를 받아 열고, SkillDesk를 Applications 폴더로 끌어다 놓으세요.

- Intel Mac용 설치 파일은 없습니다. Intel Mac은 아래 "소스로 설치"를 쓰세요.
- 서명·공증하지 않은 앱이라 처음 열 때 "확인되지 않은 개발자" 경고가 뜹니다.
  macOS 14 이하는 Finder에서 SkillDesk를 **우클릭 → 열기**,
  macOS 15 이상은 시스템 설정 → 개인정보 보호 및 보안에서 **그래도 열기**를 누르세요.
  그래도 안 열리면 터미널에서 `xattr -dr com.apple.quarantine /Applications/SkillDesk.app`을 실행하세요.
- 새 버전 알림은 Windows와 같습니다. 눌러서 새 dmg를 받아 덮어 넣으면 됩니다.

**소스로 설치** (Intel Mac, 또는 직접 받고 싶을 때): Python 3.12 이상과 Git이 필요합니다. 자세한 안내는 [README][readme]를 보세요.

```
git clone https://github.com/{repo}.git
cd skill-manage-viewer
.\\start.bat          # macOS: ./start.command
python install.py      # macOS: python3 install.py
```

`git clone`으로 받아야 새 버전을 자동으로 받습니다. 소스 ZIP으로 받은 설치는 자동 업데이트가 되지 않습니다.

[exe]: https://github.com/{repo}/releases/download/v{version}/SkillDesk-Setup-v{version}.exe
[mac]: https://github.com/{repo}/releases/download/v{version}/SkillDesk-v{version}-macOS-arm64.dmg
[readme]: https://github.com/{repo}#빠른-시작-5분
"""


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
        intro = f"## v{version}\n\n처음으로 버전을 붙인 릴리스입니다. 이전 변경은 커밋 기록에서 볼 수 있습니다.\n"
        return intro + INSTALL.format(repo=REPO, version=version)
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
    return "\n".join(lines) + "\n" + INSTALL.format(repo=REPO, version=version)


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

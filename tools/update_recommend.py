"""Rebuild skill_recommend.json, the snapshot the app's "추천" tab shows. Run by hand; the app never imports this.

    python tools/update_recommend.py

Reads two public pages of skills.sh, one request each: the all-time list (/) and the 24h list (/trending). Both embed
their list as the `initialSkills` JSON that Next.js ships inside the page, so that JSON is parsed instead of the markup.
/api/ and /search are never called (robots.txt disallows them). If a page can't be fetched or no longer has that JSON,
the old file is left as it was and the exit code is 1.
"""

import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "skill_recommend.json"
PAGES = {"popular": "https://www.skills.sh/", "trending": "https://www.skills.sh/trending"}
USER_AGENT = "SkillDesk-recommend-updater/1.0 (+https://github.com/sgustjd2/skill-manage-viewer)"

SIZE = 30  # entries per list
PER_REPO = 2  # 15 of the all-time top 20 were two repos' skills; opening a repo in the app shows the rest of its skills
MIN_INSTALLS = 1000  # a floor against one-off noise; today's top 30 sit far above it
# skills.sh also lists sources that are not GitHub repos (a domain like 'uizze.sh'); git can't clone those. Owners have no dots.
GITHUB = re.compile(r"[A-Za-z0-9-]+/[\w.-]+")
SKILL = re.compile(r"[A-Za-z0-9][\w.-]{0,63}")
PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')  # a JS string literal holding one chunk of the page data


def parse_skills(html: str) -> list[dict]:
    """The `initialSkills` list of a skills.sh page; ValueError when the page doesn't have one (the format changed)."""
    for chunk in PUSH.findall(html):
        if "initialSkills" not in chunk:
            continue
        text = json.loads(f'"{chunk}"')  # undo the string literal's escaping
        start = text.find('"initialSkills":')
        if start < 0:
            continue
        skills, _ = json.JSONDecoder().raw_decode(text, start + len('"initialSkills":'))
        if isinstance(skills, list):
            return [s for s in skills if is_skill(s)]
    raise ValueError("no initialSkills in the page: skills.sh probably changed its format")


def is_skill(s: object) -> bool:
    return (isinstance(s, dict) and isinstance(s.get("source"), str) and isinstance(s.get("skillId"), str)
            and isinstance(s.get("installs"), int) and not isinstance(s["installs"], bool))


def pick(skills: list[dict]) -> list[dict]:
    """The most installed GitHub skills: at most PER_REPO from one repo, one per skill name (14 of the 24h list's top 30 were the
    same four skills copied into different repos, with counts within 1% of each other), none below MIN_INSTALLS."""
    out, per_repo, names = [], Counter(), set()
    for s in sorted(skills, key=lambda s: -s["installs"]):
        repo, name = s["source"].lower(), s["skillId"].lower()
        if (s["installs"] < MIN_INSTALLS or not GITHUB.fullmatch(s["source"]) or not SKILL.fullmatch(s["skillId"])
                or per_repo[repo] >= PER_REPO or name in names):
            continue
        per_repo[repo] += 1
        names.add(name)
        out.append({"source": s["source"], "skill": s["skillId"], "installs": s["installs"]})
        if len(out) == SIZE:
            break
    if not out:
        raise ValueError("nothing left after filtering")
    return out


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(8_000_000).decode("utf-8")


def dump(lists: dict[str, list[dict]], updated: str) -> str:
    """JSON with one entry per line, so a refresh reads as a short diff."""
    body = ",\n".join(f' "{k}": [\n' + ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in items) + "\n ]"
                      for k, items in lists.items())
    return f'{{\n "updated": "{updated}",\n{body}\n}}\n'


def main() -> int:
    try:
        lists = {key: pick(parse_skills(fetch(url))) for key, url in PAGES.items()}
    except (OSError, ValueError) as e:  # network, HTTP, decoding and parsing failures
        print(f"{OUT.name} not updated: {e}", file=sys.stderr)
        return 1
    OUT.write_text(dump(lists, date.today().isoformat()), encoding="utf-8", newline="\n")  # LF on Windows too, like the rest of the repo
    print(f"{OUT.name}: " + ", ".join(f"{k} {len(v)}" for k, v in lists.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

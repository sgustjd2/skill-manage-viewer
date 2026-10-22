"""Rebuild skill_recommend.json, the snapshot the app's "추천" tab shows. Run daily by .github/workflows/recommend.yml (or by
hand); the app never imports this.

    python tools/update_recommend.py

Reads two public pages of skills.sh, one request each: the all-time list (/) and the 24h list (/trending). Both embed
their list as the `initialSkills` JSON that Next.js ships inside the page, so that JSON is parsed instead of the markup.
/api/ and /search are never called (robots.txt disallows them).

The "어제 급등" list comes from GitHub: the star count of each repo in those two lists is stored per day under `stars`, and
a repo's gain is today's count minus the count in the latest earlier day. The first run has nothing to compare with, so the
list is empty then. GITHUB_TOKEN is used when set (the 60 unauthenticated requests per hour barely cover one run).

If a page or the GitHub API can't be fetched, or a page no longer has that JSON, the old file is left as it was and the
exit code is 1.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "skill_recommend.json"
PAGES = {"popular": "https://www.skills.sh/", "trending": "https://www.skills.sh/trending"}
USER_AGENT = "SkillDesk-recommend-updater/1.0 (+https://github.com/sgustjd2/skill-manage-viewer)"

STARS_API = "https://api.github.com/repos/{}"
KST = timezone(timedelta(hours=9))  # the day the app's users wake up in; the workflow runs at 07:00 KST
KEEP_DAYS = 3  # days of star counts kept; only the latest earlier one is compared, the rest cover a missed run
SURGE_SIZE = 20
MIN_GAIN = 10  # stars; a floor against a handful of stars on a small repo
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

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


def dump(data: dict) -> str:
    """JSON with one entry per line (one day per line for `stars`), so a refresh reads as a short diff."""
    parts = [f' "updated": {json.dumps(data["updated"])}', f' "surge_since": {json.dumps(data["surge_since"])}']
    for k in ("popular", "trending", "surge"):
        rows = ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in data[k])
        parts.append(f' "{k}": [\n{rows}\n ]' if rows else f' "{k}": []')
    days = ",\n".join(f'  "{d}": ' + json.dumps(m, sort_keys=True) for d, m in sorted(data["stars"].items()))
    parts.append(f' "stars": {{\n{days}\n }}' if days else ' "stars": {}')
    return "{\n" + ",\n".join(parts) + "\n}\n"


def fetch_stars(repo: str) -> int | None:
    """Stars of a GitHub repo; None when it is gone (404). Other failures (rate limit, network) raise OSError."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(STARS_API.format(repo), headers=headers), timeout=30) as r:
            n = json.loads(r.read(2_000_000))["stargazers_count"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except KeyError as e:
        raise ValueError(f"no stargazers_count for {repo}") from e
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError(f"odd stargazers_count for {repo}")
    return n


def surge(lists: dict[str, list[dict]], stars: dict[str, int], before: dict[str, int]) -> list[dict]:
    """Repos of the two lists that gained at least MIN_GAIN stars since `before`, biggest gain first. A repo with no earlier
    count (new to the lists) can't be compared and is left out. `skill` is its first listed skill, which a tap opens."""
    skill_of: dict[str, tuple[str, str]] = {}
    for items in lists.values():
        for e in items:
            skill_of.setdefault(e["source"].lower(), (e["source"], e["skill"]))
    gains = [(stars[r] - before[r], r) for r in skill_of if r in stars and r in before]
    return [{"source": skill_of[r][0], "skill": skill_of[r][1], "stars": stars[r], "gain": g}
            for g, r in sorted(gains, key=lambda t: -t[0])[:SURGE_SIZE] if g >= MIN_GAIN]


def today() -> str:
    return datetime.now(KST).date().isoformat()


def read_history() -> dict[str, dict[str, int]]:
    """The `stars` of the current file ({day: {repo: count}}); anything odd, or a missing/broken file, counts as nothing."""
    try:
        days = json.loads(OUT.read_text(encoding="utf-8")).get("stars")
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(days, dict):
        return {}
    return {d: {r: n for r, n in m.items() if isinstance(n, int) and not isinstance(n, bool)}
            for d, m in days.items() if DATE.fullmatch(d) and isinstance(m, dict)}


def main() -> int:
    day = today()
    history = read_history()
    try:
        lists = {key: pick(parse_skills(fetch(url))) for key, url in PAGES.items()}
        repos = list({e["source"].lower(): e["source"] for items in lists.values() for e in items}.values())
        found = {r: fetch_stars(r) for r in repos}
    except (OSError, ValueError) as e:  # network, HTTP, decoding and parsing failures
        print(f"{OUT.name} not updated: {e}", file=sys.stderr)
        return 1
    stars = {r.lower(): n for r, n in found.items() if n is not None}
    since = max((d for d in history if d < day), default="")
    history[day] = stars
    history = dict(sorted(history.items())[-KEEP_DAYS:])
    data = {"updated": day, "surge_since": since, **lists, "surge": surge(lists, stars, history.get(since, {})), "stars": history}
    OUT.write_text(dump(data), encoding="utf-8", newline="\n")  # LF on Windows too, like the rest of the repo
    print(f"{OUT.name}: " + ", ".join(f"{k} {len(data[k])}" for k in ("popular", "trending", "surge")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

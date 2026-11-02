"""How often Claude Code used each skill, per month, counted from its conversation logs.

Claude Code keeps every conversation as JSON lines under ~/.claude/projects/**/*.jsonl. A skill shows up there in
two ways: the agent calling it (a `Skill` tool call) and the user typing `/name` (a `<command-name>` tag). The logs
are large, so each file is read once and its events are cached by size and modification time; afterwards only new or
grown files are read again.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

COMMAND = re.compile(r"<command-name>/([^<\s]+)</command-name>")
ALL = "전체"  # key for the sum over every month


def month_of(timestamp: object) -> str | None:
    """'2026-10-19T04:18:40Z' -> '2026-10' in this PC's time zone (a late-night call belongs to the local month)."""
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().strftime("%Y-%m")
    except ValueError:
        return None


def scan_file(path: Path) -> list[list[str]]:
    """[event id, month, skill] for every skill call in one log file. Unreadable lines are skipped."""
    events = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"Skill"' not in line and "<command-name>" not in line:
                continue  # cheap filter (spacing-agnostic: another tool may write `"name": "Skill"`); most lines are something else
            try:
                d = json.loads(line)
            except ValueError:
                continue
            month = month_of(d.get("timestamp"))
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            content = msg.get("content")
            if not month:
                continue
            blocks = content if isinstance(content, list) else []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                    skill = (b.get("input") or {}).get("skill")
                    if isinstance(skill, str) and skill:
                        events.append([str(b.get("id") or d.get("uuid")), month, skill])
            if d.get("type") == "user":
                text = (
                    content
                    if isinstance(content, str)
                    else " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str))
                )
                for name in COMMAND.findall(text):
                    events.append([f"{d.get('uuid')}:{name}", month, name])
    return events


def monthly_counts(projects: Path, cache_file: Path) -> dict[str, Counter]:
    """{'2026-10': Counter({'tdd': 3, ...}), ..., ALL: Counter(...)}. Built-in commands like /clear are counted too;
    the caller keeps only names that are skills."""
    return scan_logs(projects, cache_file)[0]


def scan_logs(projects: Path, cache_file: Path) -> tuple[dict[str, Counter], datetime | None]:
    """monthly_counts, plus how far back the logs are known to reach: the oldest modification time of a log file
    (None without logs). A file is touched after its first line, so this errs towards 'the logs start later than they do'."""
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    fresh = {}
    for f in sorted(projects.rglob("*.jsonl")) if projects.is_dir() else []:
        try:
            st = f.stat()
            entry = cache.get(str(f))
            if not (isinstance(entry, dict) and entry.get("size") == st.st_size and entry.get("mtime") == st.st_mtime):
                entry = {"size": st.st_size, "mtime": st.st_mtime, "events": scan_file(f)}
        except OSError:
            continue  # a log being written or removed right now: count it next time
        fresh[str(f)] = entry
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(fresh), encoding="utf-8")
    except OSError:
        pass  # counting still works, it is only slower next time
    counts: dict[str, Counter] = defaultdict(Counter)
    seen: set[str] = set()
    for entry in fresh.values():
        for event_id, month, skill in entry["events"]:
            if event_id in seen:  # a resumed conversation can repeat earlier lines in a new file
                continue
            seen.add(event_id)
            counts[month][skill] += 1
            counts[ALL][skill] += 1
    oldest = min((e["mtime"] for e in fresh.values()), default=None)
    return dict(counts), datetime.fromtimestamp(oldest) if oldest is not None else None


def count_for(counter: Counter, name: str) -> int:
    """Calls of an installed skill. The log may name it with a plugin prefix the list doesn't show
    ('anthropic-skills:pdf' for 'pdf') or without one ('brainstorming' for 'superpowers:brainstorming')."""
    short = name.split(":")[-1]
    return sum(n for logged, n in counter.items() if logged == name or logged.split(":")[-1] == short)


IDLE_MONTHS = 2  # cleanup candidates: skills not called this month or last month (counted in whole months, like the rest of this file)


def recent_months(now: datetime, n: int = IDLE_MONTHS) -> list[str]:
    """['2026-10', '2026-09']: the current month and the ones before it, newest first."""
    first = now.year * 12 + now.month - 1
    return [f"{(first - i) // 12}-{(first - i) % 12 + 1:02d}" for i in range(n)]


def last_used(counts: dict[str, Counter], name: str) -> str | None:
    """The latest 'YYYY-MM' in which the logs show a call of this skill, or None if they never do."""
    return max((m for m, c in counts.items() if m != ALL and count_for(c, name)), default=None)


def idle_since(counts: dict[str, Counter], names: list[str], now: datetime, n: int = IDLE_MONTHS) -> dict[str, str | None]:
    """{skill: its last month in the logs, or None} for every name not called in the last n months. Empty when the logs
    hold no skill call at all: then every skill would look idle and the list would only be a trap."""
    if not any(m != ALL for m in counts):
        return {}
    oldest = recent_months(now, n)[-1]
    last = {name: last_used(counts, name) for name in names}
    return {name: m for name, m in last.items() if m is None or m < oldest}

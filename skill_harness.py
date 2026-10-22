"""Harness sets: a named bundle of skills that can be linked into one project at a time. Pure logic (no Tk).

A *set* is a name plus a list of skill names. A *project* is a folder the user registered. Applying a set links each
skill into the project's own skills folder (the agent reads it only while working in that project):
  Claude Code            <project>/.claude/skills/<name>     (Claude Code docs)
  Codex · Antigravity    <project>/.agents/skills/<name>     (both read the same folder; OpenAI Codex docs, Google
                                                              Antigravity codelab)
Everything lives in ~/.skill-manager/harness.json, never in the repo or the project:
  {"version": 1,
   "sets": {name: {"skills": [...]}},        # a set is a dict so later versions can add keys (files, hooks, MCP)
   "projects": {path: {"set": name|None, "targets": [...],
                       "links": [{"dir": ".claude/skills", "name": ..., "target": ...}],   # the manifest: what *we* made
                       "exclude": [lines added to .git/info/exclude]}}}
Release only ever undoes the manifest, so a folder the user made by hand is never touched.
"""
import json
from pathlib import Path

import platform_support as plat
import skill_manager as mgr
from skill_manager import ManagerError, Result

VERSION = 1
# label shown to the user -> folder inside the project
PROJECT_DIRS = {"Claude Code": ".claude/skills", "Codex·Antigravity": ".agents/skills"}
MAX_NAME = 40
# What a project's agent setup is made of (the "guide" tree). Only the skills folders are managed here; the rest is shown
# so people can see where each piece lives. Paths and roles are from the Claude Code and Codex docs.
STRUCTURE = [
    ("CLAUDE.md", "Claude Code가 이 프로젝트에서 시작할 때마다 읽는 지침"),
    ("AGENTS.md", "Codex가 읽는 지침 (여러 에이전트가 함께 쓰는 파일)"),
    (".mcp.json", "Claude Code 프로젝트 전용 MCP 서버 설정"),
    (".claude", "Claude Code의 프로젝트 설정 폴더"),
    (".claude/settings.json", "훅·권한 설정"),
    (".claude/skills", "프로젝트 전용 스킬 폴더 ← 세트를 적용하면 여기에 연결됩니다"),
    (".agents", "Codex·Antigravity의 프로젝트 설정 폴더"),
    (".agents/skills", "프로젝트 전용 스킬 폴더 ← 세트를 적용하면 여기에 연결됩니다"),
]
MAX_SHOWN = 50  # skills listed per folder in the guide


def _file() -> Path:
    return mgr.STORE / "harness.json"


def load() -> dict:
    """-> {"version", "sets", "projects"}. A missing or broken file, or an odd entry, counts as nothing."""
    try:
        raw = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    raw = raw if isinstance(raw, dict) else {}
    sets, projects = {}, {}
    for name, v in (raw.get("sets") if isinstance(raw.get("sets"), dict) else {}).items():
        if isinstance(v, dict):
            skills = v.get("skills")
            sets[name] = {**v, "skills": [s for s in (skills if isinstance(skills, list) else [])
                                          if isinstance(s, str) and mgr.NAME_RE.fullmatch(s)]}
    for path, v in (raw.get("projects") if isinstance(raw.get("projects"), dict) else {}).items():
        if not isinstance(v, dict):
            continue
        links = [e for e in v.get("links", []) if isinstance(e, dict) and e.get("dir") in PROJECT_DIRS.values()
                 and isinstance(e.get("name"), str) and mgr.NAME_RE.fullmatch(e["name"]) and isinstance(e.get("target"), str)]
        targets = [t for t in v.get("targets", []) if t in PROJECT_DIRS]
        exclude = [x for x in v.get("exclude", []) if isinstance(x, str)]
        chosen = v.get("set") if v.get("set") in sets else None
        projects[path] = {**v, "set": chosen, "targets": targets or list(PROJECT_DIRS)[:1], "links": links, "exclude": exclude}
    return {"version": VERSION, "sets": sets, "projects": projects}


def save(data: dict) -> None:
    mgr.STORE.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps({**data, "version": VERSION}, ensure_ascii=False, indent=1), encoding="utf-8")


# ---- sets ----------------------------------------------------------------------------------------
def save_set(name: str, skills: list[str]) -> None:
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME:
        raise ManagerError(f"세트 이름은 1~{MAX_NAME}자로 적어 주세요")
    bad = [s for s in skills if not mgr.NAME_RE.fullmatch(s)]
    if bad:
        raise ManagerError(f"스킬 이름으로 쓸 수 없습니다: {bad[0]}")
    data = load()
    data["sets"][name] = {**data["sets"].get(name, {}), "skills": list(dict.fromkeys(skills))}
    save(data)


def delete_set(name: str) -> None:
    """Forget a set. Projects that used it keep their links (release them first if they should go) but lose the choice."""
    data = load()
    data["sets"].pop(name, None)
    for p in data["projects"].values():
        if p["set"] == name:
            p["set"] = None
    save(data)


# ---- projects ------------------------------------------------------------------------------------
def _project_path(path: str | Path) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise ManagerError(f"폴더를 찾을 수 없습니다: {p}")
    return p


def add_project(path: str | Path) -> str:
    """Register a folder. -> the key it is stored under."""
    p = _project_path(path)
    globals_ = {d.resolve() for d in mgr.AGENT_DIRS.values()}
    if any((p / rel).resolve() in globals_ for rel in PROJECT_DIRS.values()):
        raise ManagerError("에이전트의 전역 스킬 폴더가 들어 있는 곳(홈 폴더 등)은 프로젝트로 쓸 수 없습니다")
    data = load()
    data["projects"].setdefault(str(p), {"set": None, "targets": list(PROJECT_DIRS)[:1], "links": [], "exclude": []})
    save(data)
    return str(p)


def configure_project(path: str, set_name: str | None, targets: list[str]) -> None:
    data = load()
    if path not in data["projects"]:
        raise ManagerError("등록되지 않은 프로젝트입니다")
    if set_name is not None and set_name not in data["sets"]:
        raise ManagerError(f"없는 세트입니다: {set_name}")
    data["projects"][path].update({"set": set_name, "targets": [t for t in targets if t in PROJECT_DIRS]})
    save(data)


def remove_project(path: str) -> list[Result]:
    """Release what we linked there, then forget the project. The folder itself is not touched."""
    out = release(path) if Path(path).is_dir() else []
    data = load()
    data["projects"].pop(path, None)
    save(data)
    return out


# ---- apply / release -----------------------------------------------------------------------------
def find_source(name: str) -> Path | None:
    """The real folder of an installed skill (the first agent folder that has it), or None."""
    if not mgr.NAME_RE.fullmatch(name):
        return None
    for d in mgr.AGENT_DIRS.values():
        if (d / name / "SKILL.md").is_file():
            return mgr.target_of(d / name)
    return None


def is_git_repo(path: str | Path) -> bool:
    return (Path(path) / ".git").exists()


def _exclude_file(project: Path) -> Path | None:
    try:
        p = Path(mgr.git(project, "rev-parse", "--git-path", "info/exclude"))
    except ManagerError:
        return None
    return p if p.is_absolute() else project / p


def _edit_exclude(project: Path, add: list[str] | tuple = (), drop: list[str] | tuple = ()) -> list[str]:
    """Add lines to the project's .git/info/exclude (a local file git never commits). -> the lines actually added."""
    f = _exclude_file(project)
    if f is None:
        return []
    lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
    added = [x for x in dict.fromkeys(add) if x not in lines]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join([x for x in lines if x not in drop] + added) + "\n", encoding="utf-8")
    return added


def _ours(project: Path, entry: dict) -> Path | None:
    """The link of a manifest entry if it is still our link (it exists, is a link and points where we pointed)."""
    link = project / entry["dir"] / entry["name"]
    if mgr.is_link(link) and link.exists() and mgr.target_of(link) == Path(entry["target"]):
        return link
    return None


def apply(path: str, git_exclude: bool = False) -> list[Result]:
    """Make the project's links match its chosen set. Links we made earlier that the set no longer wants are removed;
    a same-named real folder (or someone else's link) is skipped and reported as 'conflict'. Nothing else is touched."""
    project = _project_path(path)
    data = load()
    entry = data["projects"].get(path)
    if entry is None:
        raise ManagerError("등록되지 않은 프로젝트입니다")
    if entry["set"] is None:
        raise ManagerError("적용할 세트를 먼저 고르세요")
    wanted = data["sets"][entry["set"]]["skills"]
    out: list[Result] = []
    keep, mine = [], {(e["dir"], e["name"]): e for e in entry["links"]}
    for label in entry["targets"]:
        rel = PROJECT_DIRS[label]
        for name in wanted:
            src, link, old = find_source(name), project / rel / name, mine.get((rel, name))
            if src is None:
                out.append(Result(label, name, "missing"))
                continue
            if old and _ours(project, old):
                if Path(old["target"]) == src:
                    keep.append(old)
                    out.append(Result(label, name, "exists"))
                    continue
                plat.unlink_dir(link)  # our link, but the skill has moved: point it at the new place
            elif mgr.is_link(link) and not link.exists():  # dangling link: nothing of the user's to protect
                plat.unlink_dir(link)
            if link.exists() or mgr.is_link(link):  # not ours: never recorded, never removed
                same = mgr.is_link(link) and mgr.target_of(link) == src
                out.append(Result(label, name, "exists" if same else "conflict"))
            else:
                keep.append(_make(link, src, rel, name, out, label))
    stale = [e for k, e in mine.items() if e not in keep]
    for e in stale:
        if link := _ours(project, e):
            plat.unlink_dir(link)
    entry["links"] = keep
    if git_exclude and is_git_repo(project):
        lines = [f"/{e['dir']}/{e['name']}" for e in entry["links"]]
        entry["exclude"] = list(dict.fromkeys(entry["exclude"] + _edit_exclude(project, lines)))
    save(data)
    return out


def _make(link: Path, src: Path, rel: str, name: str, out: list[Result], label: str) -> dict:
    link.parent.mkdir(parents=True, exist_ok=True)
    mgr.make_link(link, src)
    out.append(Result(label, name, "installed"))
    return {"dir": rel, "name": name, "target": str(src)}


def release(path: str) -> list[Result]:
    """Unlink what we linked into the project (originals stay) and take back the .git/info/exclude lines we added.
    A link the user has since replaced or re-pointed is left alone and reported as 'conflict'."""
    project = _project_path(path)
    data = load()
    entry = data["projects"].get(path)
    if entry is None:
        raise ManagerError("등록되지 않은 프로젝트입니다")
    out = []
    for e in entry["links"]:
        label = next(k for k, v in PROJECT_DIRS.items() if v == e["dir"])
        if link := _ours(project, e):
            plat.unlink_dir(link)
            out.append(Result(label, e["name"], "removed"))
        elif (project / e["dir"] / e["name"]).exists() or mgr.is_link(project / e["dir"] / e["name"]):
            out.append(Result(label, e["name"], "conflict"))
        else:
            out.append(Result(label, e["name"], "gone"))
    if entry["exclude"]:
        _edit_exclude(project, drop=entry["exclude"])
    entry["links"], entry["exclude"] = [], []
    save(data)
    return out


def applied(path: str) -> list[str]:
    """Skill names currently linked by us into the project (a folder that is gone has none)."""
    entry = load()["projects"].get(path)
    if entry is None or not Path(path).is_dir():
        return []
    return sorted({e["name"] for e in entry["links"] if _ours(Path(path), e)})


def guide_tree(path: str) -> list[dict]:
    """The project's agent setup as tree rows [{prefix, name, note, exists, owner}] (read only; nothing is created).
    owner is 'harness' for a skill link made here, 'user' for any other skill, '' otherwise."""
    project = _project_path(path)
    entry = load()["projects"].get(path) or {"links": []}
    ours = {(e["dir"], e["name"]) for e in entry["links"] if _ours(project, e)}
    kids: dict[str, list[dict]] = {}

    def add(rel: str, note: str, exists: bool, owner: str = "") -> None:
        parent, _, name = rel.rpartition("/")
        kids.setdefault(parent, []).append({"rel": rel, "name": name, "note": note, "exists": exists, "owner": owner})
    for rel, note in STRUCTURE:
        add(rel, note, (project / rel).exists())
    for rel in PROJECT_DIRS.values():
        d = project / rel
        for p in (sorted(d.iterdir(), key=lambda x: x.name.lower())[:MAX_SHOWN] if d.is_dir() else []):
            if p.is_dir() or mgr.is_link(p):
                mine = (rel, p.name) in ours
                add(f"{rel}/{p.name}", "이 앱이 연결한 스킬" if mine else "직접 만들었거나 다른 곳에서 가져온 스킬 (건드리지 않음)",
                    True, "harness" if mine else "user")
    rows: list[dict] = []

    def walk(parent: str, prefix: str) -> None:
        for i, k in enumerate(kids.get(parent, [])):
            last = i == len(kids[parent]) - 1
            rows.append({**k, "prefix": prefix + ("└─ " if last else "├─ ")})
            walk(k["rel"], prefix + ("   " if last else "│  "))
    walk("", "")
    return rows

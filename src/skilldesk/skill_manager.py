"""Install, update, remove and create agent skills. Pure logic (no Tk) so it can be tested on its own.

Layout on disk:
  ~/.skill-manager/sources/<host>__<owner>__<repo>[@ref]/   git clones made from links
  ~/.skill-manager/skills/<name>/                           skills created here
  ~/.skill-manager/trash/                                   anything "deleted" lands here, never hard-deleted
  ~/.skill-manager/notes.json                               user-edited one-line purposes
Each agent folder holds a junction (Windows directory link) to the real skill folder, so one copy serves
Claude Code, Codex and Antigravity and a `git pull` updates all of them at once.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from urllib.parse import urlparse, urlsplit

from . import platform_support as plat

HOME = Path.home()
STORE = HOME / ".skill-manager"
# Where each agent keeps its config; Claude Code and Codex honour these env vars, so follow them on other people's PCs.
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
AGENT_DIRS = {
    "Claude Code": CLAUDE_HOME / "skills",
    "Codex": CODEX_HOME / "skills",
    "Antigravity": HOME / ".gemini/config/skills",
}
# The login-time updater written earlier for mattpocock/skills re-links every name in its `$names` list.
UPDATER = HOME / ".mattpocock-skills-update.ps1"

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class ManagerError(Exception):
    """A failure whose message is fit to show the user as-is."""


NO_GIT = ("Git이 설치돼 있지 않습니다. https://git-scm.com 에서 설치한 뒤 SkillDesk를 다시 켜 주세요. "
          "(github.com 링크는 Git 없이도 설치할 수 있습니다)")


# ---- small helpers ------------------------------------------------------------------------------
def run(cmd: list[str], timeout: int = 180) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=plat.NO_WINDOW, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        raise ManagerError(f"시간 초과: {' '.join(cmd[:4])}…") from None
    except FileNotFoundError:
        raise ManagerError(NO_GIT) from None
    if r.returncode:
        raise ManagerError(explain_git((r.stderr or r.stdout).strip()) or f"실패: {' '.join(cmd[:4])}")
    return r.stdout.strip()


def explain_git(msg: str) -> str:
    """git's English complaint -> a Korean sentence saying what is wrong, followed by git's own last line."""
    low, last = msg.lower(), msg.splitlines()[-1] if msg else ""
    if "remote branch" in low:
        return f"그 브랜치(태그)를 찾지 못했습니다. ({last})"
    if "not found" in low or "could not read username" in low or "authentication failed" in low:
        return f"저장소를 찾지 못했습니다. 주소가 틀렸거나 비공개 저장소일 수 있습니다. ({last})"
    if "could not resolve host" in low or "unable to access" in low:
        return f"네트워크에 연결하지 못했습니다. 인터넷 연결을 확인하세요. ({last})"
    return msg


def git(repo: Path, *args: str) -> str:
    return run(["git", "-C", str(repo), *args])


def is_link(p: Path) -> bool:
    return p.is_symlink() or os.path.isjunction(p)


def target_of(p: Path) -> Path:
    return Path(os.path.realpath(p))


def installed_on(p: Path) -> datetime | None:
    """When this skill folder, or the link to it, was created here (None if the file system can't say).
    A link is not followed: its own date is the day the skill was installed, not the day the original was written."""
    try:
        return datetime.fromtimestamp(os.lstat(p).st_birthtime)
    except (OSError, AttributeError):
        return None


def parse(path: Path) -> dict:
    """Front matter of a SKILL.md -> {name, desc, manual}."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict[str, str] = {}
    m = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    key = None
    for line in (m.group(1).splitlines() if m else []):
        if re.match(r"^[A-Za-z_-]+:", line):
            k, v = line.split(":", 1)
            key, meta[k.strip()] = k.strip(), v.strip()
        elif key and line.startswith((" ", "\t")):
            meta[key] += " " + line.strip()
    desc = re.sub(r"^[|>][+-]?\s*", "", meta.get("description", "")).strip().strip("\"'")
    return {"name": meta.get("name", "").strip("\"'") or path.parent.name, "desc": desc,
            "manual": meta.get("disable-model-invocation", "").lower() == "true"}


def discover(root: Path) -> list[Path]:
    """Every skill folder (one that holds a SKILL.md) under root, not descending into a skill."""
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "node_modules" and not d.startswith(".")]
        if "SKILL.md" in files:
            out.append(Path(dirpath))
            dirs.clear()
    return sorted(out)


# ---- links -> clone location ---------------------------------------------------------------------
_SHORT = re.compile(r"([\w.-]+)/([\w.-]+)(?:/(.+))?")
_URL_PATH = re.compile(r"/(?P<repo>[^/]+/[^/]+?)(?:\.git)?(?:/(?:-/)?(?P<kind>tree|blob)/(?P<ref>[^/]+)/(?P<path>.+?))?/?")
_REF = re.compile(r"[\w][\w./-]*")


def parse_link(text: str) -> tuple[str, str | None, str | None]:
    """-> (clone url, ref, subpath). Accepts owner/repo[/path] (GitHub), or an https repo/tree/blob link.

    shortcut: a ref containing '/' (feature/x) is read as ref 'feature' + path 'x/...'; use the repo root link for those.
    """
    t = text.strip()
    if not t:
        raise ManagerError("링크를 입력하세요")
    if "://" not in t:
        m = _SHORT.fullmatch(t)
        if not m or ":" in t or t.startswith((".", "/", "\\")):
            raise ManagerError("owner/repo 또는 https 링크 형식이 아닙니다")
        return f"https://github.com/{m[1]}/{m[2]}", None, m[3]
    u = urlparse(t)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise ManagerError("https 링크만 지원합니다 (계정 정보가 들어간 링크도 불가)")
    m = _URL_PATH.fullmatch(u.path)
    if not m:
        raise ManagerError("저장소 링크를 인식하지 못했습니다 (…/owner/repo 또는 …/tree/브랜치/경로)")
    path = m["path"]
    if m["kind"] == "blob" and path:  # a file link: install the folder that holds it
        path = path.rpartition("/")[0] or None
    if (m["ref"] and not _REF.fullmatch(m["ref"])) or (path and ".." in path.split("/")):
        raise ManagerError("브랜치/경로에 사용할 수 없는 문자가 있습니다")
    return f"https://{u.netloc}/{m['repo']}", m["ref"], path


ZIP_HOSTS = frozenset({"github.com", "codeload.github.com"})  # the only hosts a GitHub zip download may touch
ZIP_MAX_BYTES = 100_000_000  # the zip itself and, separately, what it unpacks to
ZIP_MAX_FILES = 20_000
ZIP_SECONDS = 300


class _ZipRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urlsplit(newurl)
        if u.scheme != "https" or u.hostname not in ZIP_HOSTS:
            raise ManagerError(f"허용되지 않은 주소로 넘어가려 해서 멈췄습니다 ({u.hostname})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_zip(url: str, dest: Path) -> None:
    """Save a GitHub archive to dest (https, GitHub hosts only, size and time limited)."""
    req = urllib.request.Request(url, headers={"User-Agent": "SkillDesk"})
    size, end = 0, time.monotonic() + ZIP_SECONDS
    try:
        with urllib.request.build_opener(_ZipRedirects).open(req, timeout=20) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 16):
                size += len(chunk)
                if size > ZIP_MAX_BYTES:
                    raise ManagerError("저장소가 너무 커서(100MB 초과) 내려받기를 멈췄습니다")
                if time.monotonic() > end:
                    raise ManagerError("내려받는 데 너무 오래 걸려 멈췄습니다")
                f.write(chunk)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise ManagerError("저장소 또는 브랜치를 찾지 못했습니다. 주소가 틀렸거나 비공개 저장소일 수 있습니다 "
                               "(비공개 저장소는 Git이 필요합니다)") from e
        raise ManagerError(f"GitHub에서 내려받지 못했습니다 (HTTP {e.code})") from e
    except OSError as e:  # URLError, timeouts, a full disk
        raise ManagerError(f"내려받지 못했습니다. 인터넷 연결을 확인하세요. ({e})") from e


def unzip_safely(zpath: Path, out: Path) -> Path:
    """Unpack a GitHub archive into the empty folder `out` -> its single top folder. Refuses paths that
    leave `out`, links, too many files and too much data."""
    try:
        z = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile:
        raise ManagerError("받은 파일이 zip이 아닙니다") from None
    with z:
        infos = z.infolist()
        if len(infos) > ZIP_MAX_FILES or sum(i.file_size for i in infos) > ZIP_MAX_BYTES:
            raise ManagerError("저장소가 너무 커서 풀지 않았습니다")
        root = out.resolve()
        for i in infos:
            if stat.S_ISLNK(i.external_attr >> 16):
                raise ManagerError(f"링크가 들어 있는 압축 파일이라 풀지 않았습니다: {i.filename}")
            if i.filename.startswith(("/", "\\")) or not (root / i.filename).resolve().is_relative_to(root):
                raise ManagerError(f"안전하지 않은 경로가 들어 있어 풀지 않았습니다: {i.filename}")
        left = ZIP_MAX_BYTES  # the sizes a zip declares can lie, so count what really comes out
        for i in infos:
            target = root / i.filename
            if i.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, open(target, "wb") as dst:
                while chunk := src.read(1 << 16):
                    left -= len(chunk)
                    if left < 0:
                        raise ManagerError("저장소가 너무 커서 풀지 않았습니다")
                    dst.write(chunk)
    tops = [d for d in out.iterdir() if d.is_dir()]
    if len(tops) != 1:
        raise ManagerError("받은 압축 파일의 구조를 알 수 없습니다")
    return tops[0]


def fetch_zip(url: str, ref: str | None, dest: Path) -> None:
    """No Git: take a public GitHub repo as a zip into `dest` (replacing an earlier copy of the same link)."""
    if urlsplit(url).hostname != "github.com":
        raise ManagerError("Git이 설치돼 있지 않습니다. github.com이 아닌 주소는 https://git-scm.com 에서 Git을 설치해야 받을 수 있습니다")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".part-") as tmp:
        zpath, out = Path(tmp) / "src.zip", Path(tmp) / "out"
        out.mkdir()
        download_zip(f"{url}/archive/{ref or 'HEAD'}.zip", zpath)
        top = unzip_safely(zpath, out)
        if dest.exists():
            shutil.rmtree(dest)  # a copy this function made earlier (a git clone has .git and never gets here)
        shutil.move(str(top), dest)


def fetch(text: str) -> tuple[Path, Path]:
    """Clone (or freshen) the repo behind a link -> (clone dir, folder to look for skills in). Without Git a github.com
    repo comes as a zip instead (no history, so no update check: opening the same link again downloads it afresh)."""
    url, ref, sub = parse_link(text)
    p = urlparse(url)
    key = re.sub(r"[^\w.-]+", "__", f"{p.netloc}/{p.path.strip('/')}") + (f"@{ref}" if ref else "")
    dest = STORE / "sources" / key
    try:
        if (dest / ".git").exists():
            git(dest, "pull", "--ff-only", "-q")
        elif shutil.which("git") is None:
            fetch_zip(url, ref, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            cmd = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "clone", "--depth", "1", "-q"]
            try:
                run(cmd + (["--branch", ref] if ref else []) + ["--", url, str(dest)])
            except ManagerError:
                shutil.rmtree(dest, ignore_errors=True)  # only ever a partial clone we just started
                raise
    except OSError as e:
        raise ManagerError(f"저장소를 저장하지 못했습니다: {e}") from e
    root = dest / sub if sub else dest
    if not root.resolve().is_relative_to(dest.resolve()) or not root.is_dir():
        raise ManagerError(f"저장소 안에 '{sub}' 경로가 없습니다")
    return dest, root


# ---- origin / version ----------------------------------------------------------------------------
def git_root(p: Path) -> Path | None:
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def origin(skill_dir: Path) -> tuple[str, Path | None]:
    """('git', repo) | ('own', None) | ('link', None) | ('local', None)."""
    if not is_link(skill_dir):
        return "local", None
    t = target_of(skill_dir)
    if STORE.resolve() / "skills" in t.parents:
        return "own", None
    root = git_root(t)
    return ("git", root) if root else ("link", None)


@cache
def version(repo: Path) -> str:
    """'49dd158 · 2026-10-09'. Cached; call version.cache_clear() after anything that moves HEAD."""
    try:
        return git(repo, "log", "-1", "--format=%h · %cs")
    except ManagerError:
        return "?"


def repo_name(repo: Path) -> str:
    try:
        return urlparse(git(repo, "remote", "get-url", "origin")).path.strip("/").removesuffix(".git")
    except ManagerError:
        return repo.name


# ---- install / remove ----------------------------------------------------------------------------
@dataclass
class Result:
    agent: str
    name: str
    status: str  # installed | replaced | exists | conflict


def make_link(link: Path, target: Path) -> None:
    try:
        plat.make_dir_link(link, target)
    except OSError as e:
        raise ManagerError(f"링크를 만들지 못했습니다: {link} ({e})") from e


def trash(p: Path) -> Path:
    dest = STORE / "trash" / f"{time.strftime('%Y%m%d-%H%M%S')}_{p.parent.name}_{p.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), dest)
    return dest


def drop(p: Path) -> None:
    """Remove a skill from an agent folder: a link is just unlinked, a real folder goes to the trash."""
    if is_link(p):
        plat.unlink_dir(p)
    else:
        trash(p)


def install(skill_dirs: list[Path], agents: list[str], replace: bool = False) -> list[Result]:
    out = []
    for src in skill_dirs:
        if not NAME_RE.fullmatch(src.name):
            raise ManagerError(f"폴더 이름을 스킬 이름으로 쓸 수 없습니다: {src.name}")
        for agent in agents:
            link = AGENT_DIRS[agent] / src.name
            status = "installed"
            if is_link(link) and not link.exists():  # dangling link: nothing of the user's to protect
                plat.unlink_dir(link)
            elif link.exists():
                if is_link(link) and target_of(link) == src.resolve():
                    out.append(Result(agent, src.name, "exists"))
                    continue
                if not replace:
                    out.append(Result(agent, src.name, "conflict"))
                    continue
                drop(link)
                status = "replaced"
            try:
                link.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ManagerError(f"{agent} 스킬 폴더를 만들지 못했습니다: {link.parent} ({e})") from e
            make_link(link, src)
            out.append(Result(agent, src.name, status))
    return out


def forget_in_updater(name: str, path: Path | None = None) -> bool:
    """Stop the login updater from re-linking a skill the user just deleted."""
    p = path or UPDATER
    if not p.is_file():
        return False
    text = p.read_text(encoding="utf-8")
    m = re.search(r"^(\$names\s*=\s*)(.+)$", text, re.M)
    items = re.findall(r"'([^']+)'", m[2]) if m else []
    if not m or name not in items:
        return False
    items.remove(name)
    p.write_text(text[:m.start(2)] + ",".join(f"'{i}'" for i in items) + text[m.end(2):], encoding="utf-8")
    return True


def remove(agent: str, name: str) -> Path | None:
    """Delete a skill from one agent. -> where a real folder was moved, or None for a plain unlink."""
    if not NAME_RE.fullmatch(name):
        raise ManagerError(f"잘못된 스킬 이름: {name}")
    p = AGENT_DIRS[agent] / name
    if not (p.exists() or is_link(p)):
        raise ManagerError(f"{agent}에 '{name}' 스킬이 없습니다")
    kind, _ = origin(p)
    t = target_of(p)
    if is_link(p):
        plat.unlink_dir(p)
        moved = None
    else:
        moved = trash(p)
    if kind == "own" and not any(target_of(q) == t for d in AGENT_DIRS.values() if d.is_dir()
                                 for q in d.iterdir() if is_link(q)):
        moved = trash(t)  # our own skill, no agent uses it any more
    forget_in_updater(name)
    return moved


def create(name: str, description: str, agents: list[str]) -> tuple[Path, list[Result]]:
    if not NAME_RE.fullmatch(name):
        raise ManagerError("이름은 영문·숫자·-_. 만 쓸 수 있습니다 (예: my-skill)")
    d = STORE / "skills" / name
    if d.exists():
        raise ManagerError(f"이미 만든 스킬입니다: {name}")
    desc = " ".join(description.replace('"', "'").split()) or "이 스킬을 언제 쓰는지 한 줄로 적으세요"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{desc}"\n---\n\n# {name}\n\n이 스킬이 할 일과 따를 단계를 여기에 적으세요.\n',
        encoding="utf-8")
    results = install([d], agents)
    if not any(r.status in ("installed", "replaced") for r in results):
        shutil.rmtree(d)  # nothing links to it, so it is only the empty template we just wrote
        raise ManagerError(f"선택한 에이전트에 같은 이름의 스킬이 이미 있어 만들지 않았습니다: {name}")
    return d, results


# ---- updates -------------------------------------------------------------------------------------
def has_update(repo: Path) -> bool:
    git(repo, "fetch", "-q")
    return git(repo, "rev-parse", "HEAD") != git(repo, "rev-parse", "@{u}")


def relink(repo: Path) -> list[str]:
    """After a pull, re-point links whose skill folder moved inside the repo. -> names that vanished upstream."""
    gone = []
    by_name = {d.name: d for d in discover(repo)}
    root = repo.resolve()
    for d in AGENT_DIRS.values():
        for link in (d.iterdir() if d.is_dir() else []):
            if not (is_link(link) and not link.exists() and root in target_of(link).parents):
                continue
            plat.unlink_dir(link)
            if link.name in by_name:
                make_link(link, by_name[link.name])
            else:
                gone.append(link.name)
    return gone


def update(repo: Path) -> dict:
    old = git(repo, "rev-parse", "--short", "HEAD")
    git(repo, "pull", "--ff-only", "-q")
    version.cache_clear()
    return {"old": old, "new": git(repo, "rev-parse", "--short", "HEAD"), "gone": relink(repo)}


# ---- user notes / editor -------------------------------------------------------------------------
def load_notes() -> dict[str, str]:
    try:
        return json.loads((STORE / "notes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_note(name: str, text: str) -> None:
    notes = load_notes()
    if text.strip():
        notes[name] = text.strip()
    else:
        notes.pop(name, None)  # empty = back to the built-in / generated description
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")


def load_settings() -> dict:
    try:
        return json.loads((STORE / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_setting(key: str, value: object) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / "settings.json").write_text(json.dumps({**load_settings(), key: value}, indent=1), encoding="utf-8")


def list_plugins() -> list[dict]:
    """Claude Code plugins in plugins/installed_plugins.json, by name: {name, key, version, enabled, path, skills}.
    Empty if the file is missing or malformed; a malformed entry is skipped."""
    try:
        installed = json.loads((CLAUDE_HOME / "plugins/installed_plugins.json").read_text(encoding="utf-8"))["plugins"]
    except (OSError, KeyError, ValueError, TypeError):
        return []
    try:
        enabled = json.loads((CLAUDE_HOME / "settings.json").read_text(encoding="utf-8")).get("enabledPlugins", {})
    except (OSError, ValueError, AttributeError):
        enabled = {}
    if not isinstance(installed, dict):
        return []
    if not isinstance(enabled, dict):
        enabled = {}
    out = []
    for key, entries in installed.items():
        try:
            path = Path(entries[0]["installPath"])
            skills = sum(1 for _ in (path / "skills").rglob("SKILL.md"))
            out.append({"name": key.split("@")[0], "key": key, "version": str(entries[0].get("version") or "?"),
                        "enabled": bool(enabled.get(key)), "path": path, "skills": skills})
        except (KeyError, IndexError, TypeError, AttributeError, OSError):
            continue
    return sorted(out, key=lambda p: p["name"].lower())


RECOMMENDED = Path(__file__).parent / "data" / "skill_recommend.json"  # a snapshot of skills.sh, rebuilt by tools/update_recommend.py
_SOURCE = re.compile(r"[A-Za-z0-9-]+/[\w.-]+")  # owner/repo on GitHub


def load_recommended() -> dict:
    """The bundled snapshot -> {"updated": 'YYYY-MM-DD', "popular": [...], "trending": [...], "surge": [...], "surge_since": 'YYYY-MM-DD'},
    each popular/trending entry {"source": 'owner/repo', "skill": name, "installs": n}, each surge entry the same with
    "stars" and "gain" instead of "installs" (surge_since is '' when there was no earlier day to compare with).
    A missing or broken file, or an odd entry, counts as nothing."""
    try:
        raw = json.loads(RECOMMENDED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    raw = raw if isinstance(raw, dict) else {}

    def entries(key: str, *counts: str) -> list[dict]:
        items = raw.get(key)
        return [{"source": e["source"], "skill": e["skill"], **{k: e[k] for k in counts}}
                for e in (items if isinstance(items, list) else [])
                if isinstance(e, dict) and isinstance(e.get("source"), str) and _SOURCE.fullmatch(e["source"])
                and isinstance(e.get("skill"), str) and NAME_RE.fullmatch(e["skill"])
                and all(isinstance(e.get(k), int) and not isinstance(e[k], bool) for k in counts)]

    def text(key: str) -> str:
        return raw[key] if isinstance(raw.get(key), str) else ""
    return {"updated": text("updated"), "surge_since": text("surge_since"), "popular": entries("popular", "installs"),
            "trending": entries("trending", "installs"), "surge": entries("surge", "stars", "gain")}


def detected_agents() -> list[str]:
    """Agents whose config folder exists on this PC (the skills folder inside it may not yet)."""
    return [a for a, d in AGENT_DIRS.items() if d.parent.is_dir()]


def chosen_agents() -> list[str]:
    """What the panel shows: the user's pick if saved, else what is installed, else everything (never an empty panel)."""
    saved = load_settings().get("agents")
    picked = [a for a in AGENT_DIRS if isinstance(saved, list) and a in saved]
    return picked or detected_agents() or list(AGENT_DIRS)


def open_in_editor(path: Path) -> None:
    plat.open_path(path)


# ---- editing SKILL.md ----------------------------------------------------------------------------
def split_skill_md(text: str) -> tuple[str, str]:
    """(front matter without its --- lines, body). ('', text) when the file has no front matter."""
    m = re.match(r"---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    return (m[1], text[m.end():]) if m else ("", text)


def _description_lines(description: str) -> list[str]:
    one_line = " ".join(description.split())
    if re.search(r'["\]|^[>|&*!%@`#-]', one_line):  # YAML would need escapes: use a folded block instead
        return ["description: >-", f"  {one_line}"]
    return [f'description: "{one_line}"']


def set_front_values(front: str, description: str, manual: bool) -> str:
    """Front matter text with `description` and `disable-model-invocation` replaced; every other line stays exactly as it was."""
    out: list[str] = []
    lines = front.split("\n") if front else []
    i, seen_desc, seen_manual = 0, False, False
    while i < len(lines):
        line = lines[i]
        i += 1
        if re.match(r"description\s*:", line):
            seen_desc = True
            while i < len(lines) and lines[i].startswith((" ", "\t")):  # the old value's continuation lines
                i += 1
            out += _description_lines(description)
        elif re.match(r"disable-model-invocation\s*:", line):
            seen_manual = True
            if manual:
                out.append("disable-model-invocation: true")
        else:
            out.append(line)
    if not seen_desc:
        out += _description_lines(description)
    if manual and not seen_manual:
        out.append("disable-model-invocation: true")
    return "\n".join(out)


def read_skill_md(skill_dir: Path) -> tuple[str, bool, str]:
    """(description, manual-only, body) as the edit form shows them."""
    path = skill_dir / "SKILL.md"
    info = parse(path)
    return info["desc"], info["manual"], split_skill_md(path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n"))[1]


def save_skill_md(skill_dir: Path, description: str, manual: bool, body: str) -> Path | None:
    """Write the edited SKILL.md. The old file is first copied into the trash folder, so nothing is ever lost.
    -> that backup, or None when nothing changed."""
    description = " ".join(description.split())
    if not description:
        raise ManagerError("설명은 비워 둘 수 없습니다 (에이전트가 이 스킬을 언제 쓸지 판단하는 문장입니다)")
    path = skill_dir / "SKILL.md"
    raw = path.read_bytes().decode("utf-8", errors="replace") if path.exists() else ""
    eol = "\r\n" if "\r\n" in raw else "\n"  # keep the file's own line endings: a LF file must not turn CRLF in git
    front, _ = split_skill_md(raw.replace("\r\n", "\n"))
    new_front = set_front_values(front or f"name: {skill_dir.name}", description, manual)
    text = f"---\n{new_front}\n---\n{body.rstrip(chr(10))}\n".replace("\n", eol)
    if len(text) > 1_000_000:
        raise ManagerError("SKILL.md가 너무 큽니다 (1MB 이하)")
    if text == raw:
        return None
    backup = None
    if raw:
        backup = STORE / "trash" / f"{time.strftime('%Y%m%d-%H%M%S')}_{skill_dir.parent.name}_{skill_dir.name}_SKILL.md"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, backup)
    tmp = path.with_name("SKILL.md.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)  # a crash can't leave half a file
    return backup

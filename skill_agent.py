"""Let a coding agent write a skill for the user. Pure logic (no Tk), so it can be tested with a fake agent.

Only Claude Code is supported: it is the one whose non-interactive mode (`claude -p`) was checked. Codex and Antigravity are
left out until their equivalents are checked the same way.

The agent only ever works inside a throw-away folder under the system temp folder. What it wrote is shown to the user
as a Draft, and only `install_draft` (called when the user presses 설치) copies it into the skill store and links it.
Nothing here touches the user's skills folders before that.
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import platform_support as plat
import skill_manager as mgr

SUPPORTED = "Claude Code"
UNSUPPORTED = ("Codex", "Antigravity")  # no checked way to run them non-interactively yet
TIMEOUT = 600  # seconds; writing a skill takes a minute or two
MAX_BYTES = 5_000_000  # a draft bigger than this is not a skill
# Flags below are all from `claude --help`: --restricted drops the shell/web tools, ignores the user's settings files and keeps
# file tools inside the working folder; --tools lists the only tools left; --permission-prompts none denies anything that
# would still ask, so a stray write outside the folder fails instead of waiting for a click.
FLAGS = ["-p", "--restricted", "--tools", "Read,Write,Edit,Glob,Grep", "--permission-mode", "acceptEdits",
         "--permission-prompts", "none", "--no-session-persistence"]
PROMPT = """다음 요청에 맞는 에이전트 스킬을 만들어 주세요.

요청: {request}

규칙:
- 현재 폴더 안에 스킬 이름(영문 소문자·숫자·하이픈, 예: my-skill)과 같은 폴더를 하나만 만들고, 그 안에 SKILL.md를 쓰세요.
- SKILL.md 맨 위에 YAML 머리말로 name(폴더 이름과 같게)과 description(언제 이 스킬을 쓰는지)을 적으세요.
- 보조 파일이 꼭 필요하면 같은 스킬 폴더 안에만 두세요.
- 현재 폴더 밖은 읽지도 쓰지도 마세요. 명령은 실행하지 마세요.
- 다 만들면 한 줄로 무엇을 만들었는지만 답하세요.
"""


class Cancelled(Exception):
    """The user pressed 취소."""


@dataclass
class Draft:
    root: Path  # the temp folder the agent worked in
    skill_dir: Path
    name: str
    skill_md: str
    files: list[str]  # relative to skill_dir


def agent_status() -> tuple[Path | None, str]:
    """-> (the claude command or None, a line for the user)"""
    exe = plat.find_claude()
    if exe:
        return exe, f"{SUPPORTED}로 만들 수 있습니다."
    return None, (f"{SUPPORTED} 명령을 이 PC에서 찾지 못했습니다. 설치하면 여기서 만들 수 있습니다. "
                  f"{', '.join(UNSUPPORTED)}는 아직 지원하지 않습니다. 지금은 '새로 만들기' 탭의 템플릿을 쓰세요.")


def discard(draft: Draft) -> None:
    shutil.rmtree(draft.root, ignore_errors=True)  # our own temp folder, never the user's files


def _kill(proc: subprocess.Popen) -> None:
    if plat.WIN:  # also the helpers the agent started
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, creationflags=plat.NO_WINDOW)
    proc.kill()
    proc.communicate()


def _run(cmd: list[str], request: str, cwd: Path, cancel: threading.Event, timeout: float) -> None:
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=plat.NO_WINDOW)
    stdin, end = PROMPT.format(request=request), time.monotonic() + timeout
    while True:
        try:
            out, err = proc.communicate(stdin, timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            stdin = None  # sent on the first call
            if cancel.is_set():
                _kill(proc)
                raise Cancelled from None
            if time.monotonic() > end:
                _kill(proc)
                raise mgr.ManagerError(f"{int(timeout)}초 안에 끝나지 않아 멈췄습니다. 요청을 더 작게 나눠 다시 해 보세요.") from None
    if proc.returncode:
        raise mgr.ManagerError(f"에이전트가 실패했습니다 (종료 코드 {proc.returncode}):\n{(err or out).strip()[-600:]}")


def _collect(root: Path) -> Draft:
    """Check what the agent left in `root` and turn it into a Draft."""
    skills = [d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()]
    if len(skills) != 1:
        raise mgr.ManagerError("에이전트가 스킬 폴더(SKILL.md가 든 폴더) 하나를 만들지 못했습니다." if not skills
                               else f"스킬 폴더가 {len(skills)}개 만들어졌습니다. 하나씩 요청해 주세요.")
    d, total, files = skills[0], 0, []
    if not mgr.NAME_RE.fullmatch(d.name):
        raise mgr.ManagerError(f"만들어진 폴더 이름을 스킬 이름으로 쓸 수 없습니다: {d.name}")
    for dirpath, dirs, names in os.walk(d):
        for n in dirs + names:
            p = Path(dirpath, n)
            if mgr.is_link(p) or p.is_symlink():
                raise mgr.ManagerError(f"링크가 들어 있어 쓰지 않습니다: {p.relative_to(d)}")
        for n in names:
            total += (Path(dirpath) / n).stat().st_size
            files.append((Path(dirpath) / n).relative_to(d).as_posix())
    if total > MAX_BYTES:
        raise mgr.ManagerError("만들어진 파일이 너무 큽니다")
    if not mgr.parse(d / "SKILL.md")["desc"]:
        raise mgr.ManagerError("SKILL.md 머리말에 description이 없습니다. 다시 만들어 보세요.")
    return Draft(root, d, d.name, (d / "SKILL.md").read_text(encoding="utf-8", errors="replace"), sorted(files))


def generate(request: str, cancel: threading.Event, base_cmd: list[str] | None = None, timeout: float = TIMEOUT) -> Draft:
    """Ask the agent for a skill. Slow: call it from a worker thread. -> a Draft to show the user (nothing is installed).
    Only `request` is sent to the agent; `base_cmd` is for tests."""
    request = " ".join(request.split())
    if not request:
        raise mgr.ManagerError("어떤 스킬이 필요한지 적어 주세요")
    if base_cmd is None:
        exe = plat.find_claude()
        if not exe:
            raise mgr.ManagerError(agent_status()[1])
        base_cmd = [str(exe)]
    root = Path(tempfile.mkdtemp(prefix="skilldesk-agent-"))
    try:
        _run([*base_cmd, *FLAGS], request, root, cancel, timeout)
        return _collect(root)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def install_draft(draft: Draft, agents: list[str]) -> tuple[Path, list[mgr.Result]]:
    """The only step that touches the user's folders: copy the previewed skill into the store and link it, like `mgr.create`."""
    dest = mgr.STORE / "skills" / draft.name
    if dest.exists():
        raise mgr.ManagerError(f"이미 만든 스킬입니다: {draft.name}")
    shutil.copytree(draft.skill_dir, dest)
    results = mgr.install([dest], agents)
    if not any(r.status in ("installed", "replaced") for r in results):
        shutil.rmtree(dest)  # only the copy we just made
        raise mgr.ManagerError(f"선택한 에이전트에 같은 이름의 스킬이 이미 있어 설치하지 않았습니다: {draft.name}")
    discard(draft)
    return dest, results

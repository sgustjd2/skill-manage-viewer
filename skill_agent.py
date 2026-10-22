"""Has an agent CLI write a skill for the user, then hands the result over for review. No Tk here (see AGENTS.md).

Only Claude Code is supported. The command line below was checked against `claude --help` (2.1.302) and a real run:
  -p                          one answer, no interactive session; the prompt arrives on stdin (no quoting problems on Windows .cmd shims)
  --restricted                no Bash/PowerShell/WebFetch, user/project settings ignored, file tools confined to the working folder
                              (a plain allow-list is not enough: `--allowedTools Write` alone let a write escape to a sibling folder)
  --tools / --allowedTools    only Read, Write, Edit, Glob, Grep
  --permission-mode dontAsk + --permission-prompts none    anything not allowed is denied, nobody is asked
  --no-session-persistence    leave no transcript behind        --max-budget-usd    cap on what one run may cost
  --output-format json        one JSON object with `result` and `is_error`
Because the user's settings are ignored, their own skill-creator skill is not available: the prompt carries its essentials.

The agent works in a scratch folder. What it wrote is a Draft: nothing is in the skills folders until install_draft(), which
refuses a draft whose preview() was never produced.
"""

import json
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

AGENT = "Claude Code"  # the only agent that can create skills this way
NOT_YET = ("Codex", "Antigravity")  # their non-interactive mode and skill-creator are not verified yet
TIMEOUT = 600  # seconds
MAX_REQUEST = 2000  # characters
TOOLS = "Read,Write,Edit,Glob,Grep"
BUDGET_USD = "2"
PREVIEW_CHARS = 20000
PREVIEW_FILES = 60

PROMPT = """너는 에이전트 스킬 작성자다. 아래 [요청]에 맞는 스킬 폴더 하나를 현재 작업 폴더 안에 만든다.
- 폴더 이름은 영문 소문자·숫자·하이픈(예: pdf-summary). 그 안에 SKILL.md를 둔다.
- SKILL.md 맨 위는 ---로 감싼 앞부분이고, name(폴더 이름과 같게)과 description을 적는다.
  description에는 무엇을 하는 스킬인지와 언제 쓰는지(사용자가 할 법한 말)를 함께 적는다.
- 본문은 에이전트에게 주는 지시문이다. 명령형으로, 단계별로, 짧고 구체적으로 쓴다.
  길어지는 참고 자료만 하위 폴더(references/ 등)로 나눈다. 스크립트는 꼭 필요할 때만 만들고, SKILL.md에 용도를 적는다.
- 스킬 글은 [요청]과 같은 언어로 쓴다.
- 현재 작업 폴더 밖은 읽거나 쓰지 않는다. 스킬 폴더는 하나만 만든다.
- 끝나면 만든 폴더 이름과 한 줄 요약만 답한다.

[요청]
"""


class Cancelled(mgr.ManagerError):
    pass


def known_paths() -> list[Path]:
    """Where Claude Code's installers put `claude` when PATH does not list it."""
    exe = "claude.exe" if plat.WIN else "claude"
    out = [Path.home() / ".local" / "bin" / exe, mgr.CLAUDE_HOME / "local" / exe]  # native installer, older local install
    if plat.WIN:
        out.append(Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "npm" / "claude.cmd")
    else:
        out += [Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
    return out


def find_cli() -> Path | None:
    return plat.find_executable("claude", known_paths())


def availability() -> tuple[Path | None, str]:
    """(the claude program or None, what to tell the user about it)."""
    cli = find_cli()
    if cli:
        return cli, f"{AGENT}를 찾았습니다. {'·'.join(NOT_YET)}는 아직 지원하지 않습니다."
    return None, (f"{AGENT}(claude 명령)를 이 PC에서 찾지 못했습니다. 설치하고 로그인하면 여기서 에이전트로 스킬을 만들 수 있습니다. "
                  f"지금은 '새로 만들기' 탭의 템플릿을 쓰세요. ({'·'.join(NOT_YET)}는 아직 지원하지 않습니다)")


@dataclass
class Draft:
    root: Path  # the scratch folder: everything the agent wrote is under it
    dir: Path  # the skill folder it made
    note: str  # the agent's closing words
    seen: bool = False

    @property
    def name(self) -> str:
        return self.dir.name

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.dir).as_posix() for p in self.dir.rglob("*") if p.is_file())

    def preview(self) -> str:
        """SKILL.md and the file list, for the user to read before installing. Producing it is what unlocks install_draft()."""
        text = (self.dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        files = self.files()
        shown = files[:PREVIEW_FILES] + ([f"… 외 {len(files) - PREVIEW_FILES}개"] if len(files) > PREVIEW_FILES else [])
        cut = "\n… (이후 생략)" if len(text) > PREVIEW_CHARS else ""
        self.seen = True
        return f"{text[:PREVIEW_CHARS]}{cut}\n\n──────────\n파일 {len(files)}개\n" + "\n".join(shown)

    def discard(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)  # our own scratch folder, never anything of the user's


class Job:
    """Lets another thread stop a running agent."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        self.stop.set()
        if self.proc:
            _kill(self.proc)


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if plat.WIN:  # /T: the agent's own child processes too (a .cmd shim starts node)
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, creationflags=plat.NO_WINDOW)
    else:
        proc.kill()


def _why(text: str) -> str:
    text = text.strip()[-400:] or "이유를 알 수 없습니다"
    hint = "\nClaude Code가 오래된 것 같습니다. 최신 버전으로 업데이트하세요." if "unknown option" in text.lower() else ""
    return f"에이전트가 오류로 끝났습니다: {text}{hint}"


def _run(cli: list[str], work: Path, request: str, job: Job, timeout: int) -> str:
    args = [*cli, "-p", "--restricted", "--permission-mode", "dontAsk", "--permission-prompts", "none", "--no-session-persistence",
            "--output-format", "json", "--max-budget-usd", BUDGET_USD, "--tools", TOOLS, "--allowedTools", TOOLS]
    try:
        proc = subprocess.Popen(args, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", creationflags=plat.NO_WINDOW)
    except OSError as e:
        raise mgr.ManagerError(f"에이전트를 실행하지 못했습니다: {e}") from e
    with proc:
        job.proc = proc
        deadline, data = time.monotonic() + timeout, PROMPT + request
        while True:
            if job.stop.is_set():
                _kill(proc)
                raise Cancelled("취소했습니다")
            if time.monotonic() > deadline:
                _kill(proc)
                raise mgr.ManagerError(f"시간 초과: {timeout // 60}분 안에 끝나지 않아 멈췄습니다")
            try:
                out, err = proc.communicate(data, timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                data = None  # already sent; a retry may not send input again
    if job.stop.is_set():  # cancelled just as it was finishing
        raise Cancelled("취소했습니다")
    if proc.returncode:
        raise mgr.ManagerError(_why(err or out))
    return out


def generate(request: str, cli: list[str], job: Job | None = None, timeout: int = TIMEOUT) -> Draft:
    """Run the agent in a scratch folder and return what it made. `request` is the only thing sent to it. Blocks: call from a thread."""
    request = request.strip()
    if not request:
        raise mgr.ManagerError("만들고 싶은 스킬을 적어 주세요")
    if len(request) > MAX_REQUEST:
        raise mgr.ManagerError(f"요청이 너무 깁니다 ({len(request)}자, 최대 {MAX_REQUEST}자)")
    root = Path(tempfile.mkdtemp(prefix="skilldesk-agent-")).resolve()
    work = root / "work"
    work.mkdir()
    try:
        out = _run(cli, work, request, job or Job(), timeout)
        try:
            res = json.loads(out)
        except ValueError:
            res = {"result": out}
        note = str(res.get("result") or "").strip()
        if res.get("is_error"):
            raise mgr.ManagerError(_why(note))
        found = mgr.discover(work)
        if len(found) != 1 or found[0].parent != work:
            hint = f"\n에이전트의 말: {note[:300]}" if note else ""
            raise mgr.ManagerError(f"스킬 폴더 하나를 기대했지만 {len(found)}개가 만들어졌습니다.{hint}")
        d = found[0]
        if not mgr.NAME_RE.fullmatch(d.name):
            raise mgr.ManagerError(f"에이전트가 정한 폴더 이름을 쓸 수 없습니다: {d.name}\n이름은 영문·숫자·-_. 만 쓸 수 있습니다")
        if any(mgr.is_link(p) for p in d.rglob("*")):
            raise mgr.ManagerError("만들어진 폴더에 바로가기(링크)가 들어 있어 쓰지 않습니다")
        return Draft(root, d, note)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def install_draft(draft: Draft, agents: list[str]) -> list[mgr.Result]:
    """Move the reviewed draft into the app's own skills folder and link it into the agents, like a skill made in the 새로 만들기 tab."""
    if not draft.seen:
        raise mgr.ManagerError("미리보기로 내용을 확인한 뒤에만 설치할 수 있습니다")
    target = mgr.STORE / "skills" / draft.name
    if target.exists():
        raise mgr.ManagerError(f"이미 만든 스킬입니다: {draft.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(draft.dir), target)
    results = mgr.install([target], agents)
    if not any(r.status in ("installed", "replaced") for r in results):
        shutil.move(str(target), str(draft.dir))  # keep the draft so the user can still read it
        raise mgr.ManagerError(f"선택한 에이전트에 같은 이름의 스킬이 이미 있어 설치하지 않았습니다: {draft.name}")
    draft.discard()
    return results

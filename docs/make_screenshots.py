"""Regenerate the README screenshots in docs/images/ (Windows only: uses the screen grabber).

    python docs/make_screenshots.py

Runs the real app against a made-up set of public skills in a temp folder, so no one's own skills, paths or
settings end up in the pictures. Nothing outside the temp folder is written.
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import platform_support as plat  # noqa: E402
import skill_manager as mgr  # noqa: E402
import skill_viewer as v  # noqa: E402

OUT = ROOT / "docs" / "images"
MANUAL = {"grill-me", "grill-with-docs", "improve-codebase-architecture", "theme-factory"}
GIT_SKILLS = ["grilling", "grill-me", "grill-with-docs", "domain-modeling", "codebase-design", "improve-codebase-architecture", "tdd"]
LOCAL_SKILLS = [
    "frontend-design", "theme-factory", "web-design-guidelines", "pdf", "docx", "xlsx", "pptx", "deep-research", "built-in-browser",
]
SYNCED_SKILLS = ["skill-creator", "canvas-design", "theme-factory"]
PLUGIN_SKILLS = [
    "brainstorming",
    "systematic-debugging",
    "writing-plans",
    "verification-before-completion",
    "requesting-code-review",
    "using-git-worktrees",
    "executing-plans",
]


def write_skill(d: Path, desc: str | None = None) -> None:
    d.mkdir(parents=True, exist_ok=True)
    extra = "disable-model-invocation: true\n" if d.name in MANUAL else ""
    (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: {desc or 'Demo skill ' + d.name}\n{extra}---\n", encoding="utf-8")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=demo", "-c", "user.email=demo@example.com", *args], cwd=cwd, check=True, capture_output=True)


def demo_world(t: Path) -> Path:
    """Agent folders full of public example skills. -> the git repo some of them come from."""
    agents = {"Claude Code": t / "claude/skills", "Codex": t / "codex/skills", "Antigravity": t / "gemini/skills"}
    mgr.AGENT_DIRS.clear()
    mgr.AGENT_DIRS.update(agents)
    repo = t / "store/sources/github.com__example__skills"
    for n in GIT_SKILLS:
        write_skill(repo / "skills" / n)
    git(t, "init", "-q", str(repo))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "skills")
    mgr.install([repo / "skills" / n for n in GIT_SKILLS], ["Claude Code"])
    mgr.install([repo / "skills" / n for n in ("grilling", "tdd", "domain-modeling")], ["Codex", "Antigravity"])
    for n in LOCAL_SKILLS:
        write_skill(agents["Claude Code"] / n)
    for n in SYNCED_SKILLS:
        write_skill(agents["Claude Code"] / "synced/app" / n)
    plugin = t / "claude/plugins/cache/demo/superpowers/5.0.0"
    for n in PLUGIN_SKILLS:
        write_skill(plugin / "skills" / n)
    (t / "claude/plugins").mkdir(parents=True, exist_ok=True)
    (t / "claude/plugins/installed_plugins.json").write_text(
        json.dumps({"plugins": {"superpowers@demo": [{"installPath": str(plugin)}]}}), encoding="utf-8"
    )
    (t / "claude/settings.json").write_text(json.dumps({"enabledPlugins": {"superpowers@demo": True}}), encoding="utf-8")
    for n in ("skill-installer", "openai-docs"):
        write_skill(agents["Codex"] / ".system" / n)
    for n in ("design-system", "pdf"):
        write_skill(agents["Codex"] / n)
    log = t / "claude/projects/demo"
    log.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone().isoformat()
    uses = {"tdd": 14, "grilling": 9, "brainstorming": 7, "systematic-debugging": 5, "pdf": 3, "writing-plans": 2}
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "timestamp": now,
                "message": {"content": [{"type": "tool_use", "id": f"{n}-{i}", "name": "Skill", "input": {"skill": n}}]},
            }
        )
        for n, c in uses.items()
        for i in range(c)
    ]
    (log / "demo.jsonl").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    older = [  # a few skills were last used months ago (they show up in the cleanup list), and the logs reach back five months
        json.dumps(
            {
                "type": "assistant",
                "timestamp": (datetime.now().astimezone() - timedelta(days=days)).isoformat(),
                "message": {"content": [{"type": "tool_use", "id": f"old-{n}", "name": "Skill", "input": {"skill": n}}]},
            }
        )
        for n, days in (("docx", 95), ("executing-plans", 100), ("xlsx", 130))
    ]
    (log / "older.jsonl").write_text("".join(line + "\n" for line in older), encoding="utf-8")
    five_months_ago = time.time() - 150 * 86400
    os.utime(log / "older.jsonl", (five_months_ago, five_months_ago))
    mgr.create("meeting-notes", "Summarize meeting notes into a short report", ["Claude Code", "Codex"])
    mgr.save_skill_md(
        mgr.STORE / "skills/meeting-notes",
        "Summarize meeting notes into a short report",
        False,
        "# meeting-notes\n\n회의 메모를 받으면 **한 쪽짜리 보고서**로 정리합니다.\n\n## 순서\n\n"
        "- 참석자와 날짜를 먼저 적는다\n- 결정 사항과 할 일을 나눠 `표`로 정리한다\n- 끝에 다음 회의 안건을 붙인다\n\n"
        "## 출력 형식\n\n```\n결정: ...\n할 일: 담당자 / 기한\n```\n",
    )
    mgr.set_note("meeting-notes", "회의록을 요약 보고서로 정리 (직접 만든 스킬 예시)")
    return repo


def bounds(win) -> tuple[int, int, int, int]:
    """Window rectangle including the title bar, as Windows draws it."""

    class RECT(ctypes.Structure):
        _fields_ = [(n, ctypes.c_long) for n in ("left", "top", "right", "bottom")]

    r = RECT()
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))  # DWMWA_EXTENDED_FRAME_BOUNDS
    return r.left, r.top, r.right, r.bottom


def shot(win, name: str) -> None:
    from PIL import ImageGrab

    win.attributes("-topmost", True)  # the app no longer stays on top by itself; nothing may cover it while it is grabbed
    for _ in range(3):
        win.update()
        time.sleep(0.4)  # let the window finish fading in
    ImageGrab.grab(bbox=bounds(win)).save(OUT / f"{name}.png", optimize=True)
    print("saved", OUT / f"{name}.png")


def main() -> None:
    if not plat.WIN:
        sys.exit("Windows only")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        with (
            mock.patch.object(mgr, "STORE", t / "store"),
            mock.patch.object(mgr, "CLAUDE_HOME", t / "claude"),
            mock.patch.object(mgr, "UPDATER", t / "none.ps1"),
            mock.patch.object(mgr, "open_in_editor"),
            mock.patch.object(mgr, "AGENT_DIRS", dict(mgr.AGENT_DIRS)),
            mock.patch.object(v, "HOME", t),
            # every demo skill was just created here, which would make all of them "new"; only the one made last stays new
            mock.patch.object(
                mgr, "installed_on", side_effect=lambda p: datetime.now() - timedelta(days=0 if Path(p).name == "meeting-notes" else 240)
            ),
        ):
            repo = demo_world(t)
            app = v.App(with_tray=False, auto_update=False)
            app.load()
            shot(app.root, "main")

            app.updatable = {repo.resolve()}
            app.pick("기획·설계")
            shot(app.root, "category")
            app.updatable = set()

            app.pick(None)
            app.query.set("검증")
            shot(app.root, "search")
            app.query.set("")

            app.toggle_theme()
            shot(app.root, "dark")
            app.toggle_theme()

            app.add_dialog()
            dlg = app.dialog
            dlg.link.set("example/skills")
            dlg._show_found(mgr.discover(repo))
            for d in list(dlg.picks)[:3]:
                dlg.picks[d].set(True)
            shot(dlg.win, "add-link")
            dlg.show("새로 만들기")
            dlg.name.set("release-notes")
            dlg.desc.set("Write release notes from merged pull requests")
            shot(dlg.win, "add-new")
            dlg.show("추천")  # the bundled skills.sh snapshot; the demo skills above that it lists show as installed
            shot(dlg.win, "add-recommended")
            dlg.close()
            app.toggle_theme()
            app.add_dialog()
            app.dialog.show("추천")
            shot(app.dialog.win, "add-recommended-dark")
            app.dialog.close()
            app.toggle_theme()

            grilling = next(x for x in app.data["Claude Code"] if x["name"] == "grilling")
            app.detail(grilling)
            shot(app.detail_win.win, "detail")
            app.detail_win.close()
            mine = next(x for x in app.data["Claude Code"] if x["name"] == "meeting-notes")  # hand-made: the only editable kind
            app.detail(mine, page="SKILL.md")
            shot(app.detail_win.win, "skill-md-view")
            app.detail_win.close()
            app.detail(mine, page="SKILL.md", edit=True)
            shot(app.detail_win.win, "skill-md-edit")
            app.detail_win.editing = False  # nothing was changed; close without asking
            app.detail_win.close()
            app.row_menu(grilling, app.root.winfo_rootx() + app.px(600), app.root.winfo_rooty() + app.px(330))
            app.popup.win.unbind("<FocusOut>")  # keep it open while the picture is taken
            shot(app.popup.win, "menu")
            app.close_popup()

            app.set_agent("Codex")
            shot(app.root, "codex")

            app.settings_dialog()
            shot(app.dialog.win, "settings")
            app.dialog.close()

            app.set_agent("Claude Code")
            app.pick(v.USAGE)
            end = time.time() + 15
            while app.usage is None and time.time() < end:  # the counting runs on a worker thread
                app.root.update()
                app._pump()
                time.sleep(0.05)
            shot(app.root, "usage")
            app.pick(v.IDLE)
            deletable = [s["name"] for s in app.shown if s["managed"]]
            app.set_checked(set(deletable[:3]))  # three ticked, as when someone is about to delete them
            shot(app.root, "cleanup")
            app.toggle_theme()
            shot(app.root, "cleanup-dark")
            app.toggle_theme()
            app.pick(None)
            app.set_agent("Claude Code")  # last, so the stars do not show up in the pictures above
            by_name = {s["name"].split(":")[-1]: s for s in app.data["Claude Code"]}
            for n in ("tdd", "grilling", "systematic-debugging", "pdf"):
                app.toggle_pin(by_name[n])
            app.pick(v.STAR)
            shot(app.root, "favorites")
            app.quit()
            for d in mgr.AGENT_DIRS.values():  # unlink before the temp folder is removed
                for p in d.iterdir() if d.is_dir() else []:
                    if mgr.is_link(p):
                        plat.unlink_dir(p)


if __name__ == "__main__":
    main()

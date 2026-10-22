"""SkillDesk: lists the skills each agent (Claude Code / Codex / Antigravity) can use, grouped by
category (category rail on the left, skills on the right), and installs / updates / edits / deletes them.
Lives in a window centered on the screen that is opened and closed from a tray icon.

  pythonw skill_viewer.py            start (window visible; minimize or close hides it to the tray)
  pythonw skill_viewer.py --hidden   start in the tray only
  python  skill_viewer.py --check    print counts per agent/category, no GUI
  python  skill_viewer.py --make-icon  rewrite skill_viewer.ico

Needs: pip install pystray pillow     (the file operations themselves live in skill_manager.py)
Font: Pretendard is loaded from ./fonts/*.otf when present (private to this process); otherwise Malgun Gothic.
"""

import contextlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import Counter
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, simpledialog

import platform_support as plat
import skill_agent
import skill_harness as hn
import skill_manager as mgr
import skill_usage as usage
import skill_update as upd

HERE = Path(__file__).parent
HOME = Path.home()
NOTES = json.loads((HERE / "skill_notes.json").read_text(encoding="utf-8"))

# Categories are mutually exclusive: every known skill is listed under exactly one of them in
# skill_categories.json (checked by tests). The keywords there only place skills that file doesn't list yet,
# trying the categories in file order, the skill name before its description.
CATALOG = json.loads((HERE / "skill_categories.json").read_text(encoding="utf-8"))
CATEGORIES = [(c["name"], c["keywords"]) for c in CATALOG]
CATEGORY_OF = {skill: c["name"] for c in CATALOG for skill in c["skills"]}
OTHER = "기타"
STAR = "★ 자주 쓰는 스킬"  # rail entry listing what the user starred, separately for each agent
USAGE = "많이 쓴 스킬"  # Claude Code only: skills ranked by how often they were called in a month
IDLE = "정리 후보"  # Claude Code only, last in the rail: skills not called for two months, to tick and delete
ABOUT = {c["name"]: c["about"] for c in CATALOG} | {
    OTHER: "어느 카테고리에도 맞지 않는 스킬",
    STAR: "☆를 눌러 지정한 스킬만 모아 봅니다",
    USAGE: "Claude Code 대화 기록에서 스킬을 부른 횟수, 많은 순",
    IDLE: "최근 2개월 동안 한 번도 쓰지 않은 스킬, 오래 안 쓴 순",
}
AGENTS = list(mgr.AGENT_DIRS)

# Every category gets its own hue: (text on light, tint on light, text on dark, tint on dark).
CAT_COLORS = {
    "기획·설계": ("#945A06", "#FEF3C7", "#F5C451", "#463813"),
    "구현·테스트": ("#0E6E67", "#D5F3EF", "#4FD1C5", "#173E3B"),
    "디버깅·검증": ("#137538", "#DCF5E3", "#5FD38A", "#1B3D2A"),
    "리뷰·품질": ("#C0261D", "#FDE7E4", "#FF8A80", "#4A2220"),
    "Git·협업": ("#B23B0B", "#FFEBDD", "#FFA566", "#4A2D1A"),
    "UI·디자인": ("#1D4ED8", "#E0E9FF", "#7FA8FF", "#1F3158"),
    "문서·자료": ("#4338CA", "#E5E4FF", "#A5A0FF", "#2B2A5C"),
    "자동화·브라우저": ("#7E22CE", "#F1E3FD", "#C99BFF", "#3B2358"),
    "에이전트·스킬 설정": ("#BE185D", "#FCE4EF", "#FF8FBF", "#4D1F37"),
    OTHER: ("#52525B", "#ECECEF", "#C4C4CC", "#33333A"),
}
# The star entry is not a category, so its colors stay out of CAT_COLORS (a test pairs that with skill_categories.json).
STAR_COLORS = ("#854F0B", "#FAEEDA", "#EF9F27", "#3F2A0A")
USAGE_COLORS = ("#0B6380", "#DCF0F7", "#67C7EB", "#123845")
IDLE_COLORS = ("#475569", "#E8ECF1", "#B6C2D2", "#2C3542")  # slate: neutral, nothing to be alarmed about until the user ticks something

LIGHT = dict(bg="#F4F6F9", surface="#FFFFFF", rail="#EBEFF5", line="#CBD3DE", text="#0F172A", muted="#475569",
             accent="#1D5FD0", on_accent="#FFFFFF", soft="#E3ECFC", warn_bg="#FFF1D6", warn_fg="#7A4A00",
             danger="#B42318", danger_bg="#FEECEB")
DARK = dict(bg="#161B22", surface="#1F2630", rail="#1A2029", line="#3A4452", text="#F3F6FA", muted="#C2CBD6",
            accent="#7DB0FF", on_accent="#0B1220", soft="#2A3B55", warn_bg="#4A3A14", warn_fg="#FFD98A",
            danger="#FF8A80", danger_bg="#4A2220")
FONT, FALLBACK_FONT = "Pretendard", plat.FALLBACK_FONT
TRAY_COMMANDS = {  # what the tray may ask the app to do
    "toggle", "add_dialog", "check_updates", "update_app", "toggle_auto_update", "load", "quit",
}
TRAY_NAME = "메뉴 막대" if plat.MAC else "트레이"
DEFAULT_HINT = f"클릭: 이름 복사 · ☆: 자주 쓰는 스킬 지정 · ⋯ 또는 우클릭: 수정·삭제 · {TRAY_NAME} 아이콘으로 열기/닫기"


# ---- data -------------------------------------------------------------------------------------
def tilde(p: Path) -> str:
    """A path under the home folder as ~/...: shorter, and screenshots don't show the account name."""
    text, home = str(p), str(HOME)
    return "~" + text[len(home):] if text.lower().startswith(home.lower()) else text


def categorize(name: str, desc: str) -> str:
    if cat := CATEGORY_OF.get(name) or CATEGORY_OF.get(name.split(":")[-1]):
        return cat
    for text in (name.lower(), desc.lower()):
        for cat, rx in CATEGORIES:
            if re.search(rx, text):
                return cat
    return OTHER


def brief(name: str, desc: str, user_notes: dict[str, str] | None = None) -> str:
    """Korean one-liner of what a skill is for: the user's own note, then the bundled one, then the
    first sentence of its description (the only source for skills that came from somewhere else)."""
    for src in (user_notes or {}, NOTES):
        if n := src.get(name) or src.get(name.split(":")[-1]):
            return n
    first = re.split(r"(?<=[.!?。])\s", desc, maxsplit=1)[0]
    return first if len(first) <= 80 else first[:77] + "…"


def scan(root: Path, prefix: str = "") -> list[dict]:
    out = []
    if not root.is_dir():
        return out
    seen: set[str] = set()
    # follow links: on macOS installed skills are symlinks, which os.walk would otherwise list but never enter
    for dirpath, dirs, files in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:  # a link pointing back up the tree would make the walk endless
            dirs.clear()
            continue
        seen.add(real)
        if "SKILL.md" in files:
            try:
                s = mgr.parse(Path(dirpath) / "SKILL.md")
            except OSError:  # unreadable file: skip that one skill rather than fail the whole list
                dirs.clear()
                continue
            s["name"] = prefix + s["name"]
            s["path"] = Path(dirpath)
            out.append(s)
            dirs.clear()  # don't descend into a skill's own folders
    return out


def claude_plugin_roots() -> list[tuple[Path, str]]:
    """Skill folders of the enabled Claude Code plugins, as (folder, 'plugin:' prefix). Empty if there are none."""
    try:
        installed = json.loads((mgr.CLAUDE_HOME / "plugins/installed_plugins.json").read_text(encoding="utf-8"))["plugins"]
        enabled = json.loads((mgr.CLAUDE_HOME / "settings.json").read_text(encoding="utf-8")).get("enabledPlugins", {})
    except (OSError, KeyError, ValueError, TypeError):
        return []
    roots = []
    for key, entries in installed.items():
        with contextlib.suppress(KeyError, IndexError, TypeError):
            if enabled.get(key):
                roots.append((Path(entries[0]["installPath"]) / "skills", key.split("@")[0] + ":"))
    return roots


def describe_origin(s: dict, kind: str, root: Path) -> None:
    """Fills s['origin'] (short label), s['repo'] (git clone it follows) and s['managed'] (may be edited/deleted here)."""
    p = s["path"]
    s["repo"], s["managed"] = None, False
    if kind == "plugin":
        ver = root.parent.name
        s["origin"] = "플러그인" if re.fullmatch(r"[0-9a-f]{12}", ver) else f"플러그인 v{ver}"
    elif kind == "builtin":
        s["origin"] = "내장"
    elif ".system" in p.relative_to(root).parts:
        s["origin"] = "시스템"
    elif p.parent != root:
        s["origin"] = "앱 동기화"
    else:
        s["managed"] = True
        how, repo = mgr.origin(p)
        s["repo"] = repo
        s["origin"] = {"git": "git · " + (mgr.version(repo).split(" · ")[0] if repo else ""),
                       "own": "내 스킬", "link": "링크", "local": "로컬"}[how]


def collect(agent: str) -> list[dict]:
    roots = [(mgr.AGENT_DIRS[agent], "", "user")]
    if agent == "Claude Code":
        roots += [(r, prefix, "plugin") for r, prefix in claude_plugin_roots()]
    elif agent == "Antigravity":
        roots.append((HOME / ".gemini/antigravity-cli/builtin/skills", "", "builtin"))
    notes = mgr.load_notes()
    seen: dict[str, dict] = {}
    for root, prefix, kind in roots:
        for s in scan(root, prefix):
            if s["name"] not in seen:
                describe_origin(s, kind, root)
                seen[s["name"]] = s | {"agent": agent}
    skills = sorted(seen.values(), key=lambda s: s["name"].lower())
    for s in skills:
        s["cat"] = categorize(s["name"], s["desc"])
        s["note"] = brief(s["name"], s["desc"], notes)
    return skills


def month_label(month: str) -> str:
    """'2026-10' -> '10월' this year, '2025.12' otherwise."""
    return f"{int(month[5:])}월" if month[:4] == datetime.now().strftime("%Y") else f"{month[:4]}.{month[5:]}"


def idle_label(last: str | None, now: datetime) -> str:
    """'기록 없음' or '3개월 전 사용' for a skill whose latest call was in the month `last` ('YYYY-MM')."""
    if last is None:
        return "기록 없음"
    return f"{(now.year - int(last[:4])) * 12 + now.month - int(last[5:])}개월 전 사용"


def load_pins() -> dict[str, set[str]]:
    """Starred skill names per agent, from settings.json. Anything malformed counts as 'nothing starred'."""
    raw = mgr.load_settings().get("pins")
    raw = raw if isinstance(raw, dict) else {}
    return {a: {n for n in raw[a] if isinstance(n, str)} if isinstance(raw.get(a), list) else set() for a in AGENTS}


def check() -> None:
    for a in AGENTS:
        skills = collect(a)
        assert all(s["name"] and s["cat"] and s["note"] and s["origin"] for s in skills)
        print(f"{a}: {len(skills)}", dict(Counter(s["cat"] for s in skills)), "| folder:", mgr.AGENT_DIRS[a])
        print("   origin:", dict(Counter(s["origin"].split(" ·")[0] for s in skills)))
        print("   기타:", [s["name"] for s in skills if s["cat"] == OTHER])


# ---- windows helpers ---------------------------------------------------------------------------
def load_bundled_fonts() -> int:
    """Make ./fonts/*.otf|ttf usable by this process only (nothing is installed system-wide)."""
    return sum(plat.register_font(p) for p in sorted((HERE / "fonts").glob("*.[ot]tf")))


def icon_image(size: int = 256):
    """App icon: a 2x2 catalog grid on a blue tile, one cell highlighted. Drawn at 4x and downsampled."""
    from PIL import Image, ImageDraw
    s = 1024
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((32, 32, s - 32, s - 32), radius=232, fill="#1D5FD0")
    cell, gap, x0 = 252, 68, 220
    colors = ["#FFFFFF", "#C7DAFB", "#C7DAFB", "#4DE0B5"]
    for i, color in enumerate(colors):
        x, y = x0 + (i % 2) * (cell + gap), x0 + (i // 2) * (cell + gap)
        d.rounded_rectangle((x, y, x + cell, y + cell), radius=64, fill=color)
    return im.resize((size, size), Image.Resampling.LANCZOS)


def make_ico(path: Path) -> None:
    icon_image(256).save(path, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])


def tray_menu(put):
    import pystray
    return pystray.Menu(
        pystray.MenuItem("열기 / 닫기", lambda i, m: put("toggle"), default=True),
        pystray.MenuItem("스킬 추가…", lambda i, m: put("add_dialog")),
        pystray.MenuItem("스킬 업데이트 확인", lambda i, m: put("check_updates")),
        pystray.MenuItem("SkillDesk 업데이트 확인", lambda i, m: put("update_app")),
        pystray.MenuItem("SkillDesk 자동 업데이트 켜기/끄기", lambda i, m: put("toggle_auto_update")),
        pystray.MenuItem("새로고침", lambda i, m: put("load")),
        pystray.MenuItem("종료", lambda i, m: put("quit")),
    )


def tray_child() -> None:
    """macOS only: show the menu-bar icon and print the command of each click; exit when the app closes our stdin."""
    import pystray
    icon = pystray.Icon("skill_viewer", icon_image(64), "SkillDesk", tray_menu(lambda cmd: print(cmd, flush=True)))
    threading.Thread(target=lambda: (sys.stdin.read(), icon.stop()), daemon=True).start()
    icon.run()


def make_icns(path: Path) -> None:
    icon_image(1024).save(path, format="ICNS")


def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Scroll:
    """A vertically scrollable frame: pack content into .body, place .wrap."""

    def __init__(self, parent, c: dict):
        self.wrap = tk.Frame(parent, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"])
        self.canvas = tk.Canvas(self.wrap, bg=c["surface"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.scroller = self  # type: ignore[attr-defined]  # lets the wheel handler find us
        self.body = tk.Frame(self.canvas, bg=c["surface"])
        win = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))

    def clear(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()


class App:
    W, H, RAIL = 900, 784, 230  # tall enough at 100% scaling for the whole rail: three fixed entries, ten categories, and 정리 후보 last
    INSTANCE = plat.INSTANCE  # whose "show yourself" notes this copy reads; tests use their own so they never touch a real app

    def __init__(self, with_tray: bool = True, auto_update: bool = True):
        plat.dpi_aware()
        load_bundled_fonts()  # before Tk starts listing fonts
        self.root = root = tk.Tk()
        root.title("SkillDesk")
        from PIL import ImageTk
        self._icon = ImageTk.PhotoImage(icon_image(64))  # keep a reference or Tk drops it
        root.iconphoto(True, self._icon)  # pyright: ignore[reportArgumentType]
        root.resizable(False, True)  # no maximize; minimize and close stay
        self.family = FONT if FONT in tkfont.families(root) else FALLBACK_FONT
        self.scale = plat.pixel_scale(root)
        self.name_font = tkfont.Font(family=self.family, size=round(11 * plat.FONT_SCALE), weight="bold", slant="roman")
        self.tag_font = tkfont.Font(family=self.family, size=round(9 * plat.FONT_SCALE), slant="roman")
        self.theme = mgr.load_settings().get("theme", "light")  # light unless the user switched
        self.c = DARK if self.theme == "dark" else LIGHT
        root.configure(bg=self.c["bg"])
        self.visible = True
        self.cat: str | None = None  # category picked in the rail; None = all
        self.shown: list[dict] = []
        self.sections: dict[str, tk.Frame] = {}  # built category groups of the list; see render()
        self.pins = load_pins()  # starred skill names per agent
        self.usage: dict[str, Counter] | None = None  # Claude Code calls per month; None until counted
        self.usage_month = datetime.now().strftime("%Y-%m")
        self.calls: dict[str, int] = {}  # calls of each listed skill in usage_month
        self.log_start: datetime | None = None  # oldest Claude Code log file: how far back the idle list can see
        self.idle_last: dict[str, str | None] = {}  # idle skill -> month of its last call (None: never in the logs)
        self._idle_key: tuple | None = None  # what _idle() last worked from, and its answer
        self._idle_items: list[dict] = []
        self.hide_new = tk.BooleanVar(value=True)  # idle list: leave out skills installed inside the window
        self.hide_new.trace_add("write", lambda *_: self._idle_filter_changed())
        self.checked: set[str] = set()  # skills ticked in the idle list
        self.checks: dict[str, list[tk.Label]] = {}  # the ☐/☑ labels on screen for each skill name
        self.icons: dict[str, list[tk.Label]] = {}  # the ☆/★ labels on screen for each skill name (it can be in two groups)
        self.updatable: set[Path] = set()
        self.data: dict[str, list[dict]] = {}
        self.agents = mgr.chosen_agents()  # tabs shown; set in the settings dialog
        self.agent = tk.StringVar(value=self.agents[0])
        self.query = tk.StringVar()
        self.events: queue.Queue = queue.Queue()
        self.tray = None
        self.tray_proc: subprocess.Popen | None = None
        self.dialog: AddDialog | SettingsDialog | HarnessWindow | None = None
        self.detail_win: DetailDialog | None = None
        self.popup: Popup | None = None
        self._last_toggle = 0.0
        self.pending_restart: upd.Result | None = None  # an update was installed; the new code runs after a restart
        self.restart_pill: tk.Label | None = None
        self._failed_update = ""  # the version whose update failed, so it is reported once, not every few hours
        self._say_job = None
        self._build()
        self.query.trace_add("write", lambda *_: self.render(top=True))
        root.protocol("WM_DELETE_WINDOW", self.hide if with_tray else self.quit)
        root.bind("<Unmap>", self._on_unmap)
        root.bind_all("<MouseWheel>", self._wheel)
        self.load()
        self.place()
        self._dark_titlebar()
        if with_tray:
            self.start_tray()
        root.after(100, self._pump)
        root.after(60_000, self._tick)
        if auto_update:  # tests and screenshots turn this off: they must not reach out to GitHub
            root.after(15_000, self.auto_update)

    # ---- widgets ----------------------------------------------------------------------------
    def px(self, n: float) -> int:
        return int(n * self.scale)

    def f(self, size: int = 11, bold: bool = False) -> tuple:
        """Font spec. Always upright ('roman'); nothing in this app is ever italic."""
        return (self.family, round(size * plat.FONT_SCALE), "bold" if bold else "normal", "roman")

    def cat_colors(self, cat: str | None) -> tuple[str, str]:
        """(text color, tint) for a category; None (= all) uses the app accent."""
        if cat is None:
            return self.c["accent"], self.c["soft"]
        special = {STAR: STAR_COLORS, USAGE: USAGE_COLORS, IDLE: IDLE_COLORS}
        fg_l, soft_l, fg_d, soft_d = special.get(cat) or CAT_COLORS[cat]
        return (fg_d, soft_d) if self.theme == "dark" else (fg_l, soft_l)

    def _label(self, parent, text="", size=11, bold=False, fg=None, bg=None, **kw):
        return tk.Label(parent, text=text, font=self.f(size, bold), fg=fg or self.c["text"],
                        bg=bg or self.c["surface"], **kw)

    def _icon_button(self, parent, text, command, hint=""):
        b = self._label(parent, text, 11, fg=self.c["muted"], cursor="hand2", padx=9, pady=3)
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: (b.config(bg=self.c["soft"], fg=self.c["accent"]), self.footer.config(text=hint)))
        b.bind("<Leave>", lambda e: (b.config(bg=self.c["surface"], fg=self.c["muted"]),
                                     self.footer.config(text=DEFAULT_HINT, fg=self.c["muted"])))
        return b

    def _button(self, parent, text, command, primary=False, danger=False):
        """A Label dressed as a button: Tk on macOS draws native buttons and ignores their colors."""
        c = self.c
        base = c["accent"] if primary else c["surface"]
        fg = c["on_accent"] if primary else c["danger"] if danger else c["text"]
        b = tk.Label(parent, text=text, font=self.f(11, primary), padx=16, pady=7, cursor="hand2", bg=base, fg=fg,
                     disabledforeground=c["muted"], highlightthickness=0 if primary else 1, highlightbackground=c["line"])

        def on() -> bool:
            return str(b.cget("state")) != "disabled"
        b.bind("<Button-1>", lambda e: command() if on() else None)
        if not primary:
            b.bind("<Enter>", lambda e: b.config(bg=c["danger_bg"] if danger else c["soft"]) if on() else None)
            b.bind("<Leave>", lambda e: b.config(bg=base))
        return b

    def _enable(self, button: tk.Label, on: bool) -> None:
        """A disabled button gets a grey background too, so its grey text never sits on the accent color."""
        c = self.c
        button.config(state="normal" if on else "disabled", bg=c["accent"] if on else c["line"],
                      cursor="hand2" if on else "arrow")

    def _check(self, parent, text, var, bg=None):
        """A checkbox drawn in the app's colors (the system one ignores them). Click or Space toggles `var`."""
        c = self.c
        bg = bg or c["bg"]
        f = tk.Frame(parent, bg=bg, cursor="hand2", takefocus=1, highlightthickness=1, highlightbackground=bg,
                     highlightcolor=c["accent"])
        size = self.px(18)
        box = tk.Canvas(f, width=size, height=size, bg=bg, highlightthickness=0, cursor="hand2")
        box.pack(side="left", padx=(2, self.px(8)), pady=2)
        lab = self._label(f, text, 11, bg=bg, cursor="hand2")
        lab.pack(side="left", padx=(0, 2))
        hover = [False]

        def draw(*_):
            on, s = var.get(), size - 2
            box.delete("all")
            # unchecked border uses `muted`, not `line`: a control's outline needs 3:1 against its background
            rounded_rect(box, 1, 1, s, s, self.px(5), fill=c["accent"] if on else c["surface"],
                         outline=c["accent"] if on or hover[0] else c["muted"], width=max(1, self.px(1.5)))
            if on:
                tick = [round(size * k) for k in (0.27, 0.52, 0.43, 0.68, 0.74, 0.33)]
                box.create_line(*tick, fill=c["on_accent"], width=max(2, self.px(2)), capstyle=tk.ROUND, joinstyle=tk.ROUND)

        def set_hover(v):
            hover[0] = v
            draw()
        for w in (f, box, lab):
            w.bind("<Button-1>", lambda e: (var.set(not var.get()), f.focus_set()))
            w.bind("<Enter>", lambda e: set_hover(True))
            w.bind("<Leave>", lambda e: set_hover(False))
        f.bind("<space>", lambda e: var.set(not var.get()))
        trace = var.trace_add("write", draw)
        # the variable outlives this widget (tabs rebuild it), so stop redrawing a destroyed canvas
        f.bind("<Destroy>", lambda e: var.trace_remove("write", trace) if e.widget is f else None)
        draw()
        f.box = box  # type: ignore[attr-defined]  # for tests
        return f

    def _entry(self, parent, var):
        c = self.c
        box = tk.Frame(parent, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"],
                       highlightcolor=c["accent"])
        e = tk.Entry(box, textvariable=var, relief="flat", bg=c["surface"], fg=c["text"],
                     insertbackground=c["text"], font=self.f(11))
        e.pack(fill="x", ipady=7, padx=10)
        return box, e

    def _tab_bar(self, parent, names, on_pick):
        """Underlined tabs. -> (frame, restyle(selected, texts=None))."""
        c = self.c
        bar = tk.Frame(parent, bg=c["surface"])
        cells = {}
        for n in names:
            cell = tk.Frame(bar, bg=c["surface"], cursor="hand2")
            cell.pack(side="left", expand=True, fill="x")
            lab = self._label(cell, n, 11, pady=10)
            lab.pack(fill="x")
            underline = tk.Frame(cell, height=3, bg=c["surface"])
            underline.pack(fill="x")
            for w in (cell, lab):
                w.bind("<Button-1>", lambda e, n=n: on_pick(n))
            cells[n] = (lab, underline)

        def restyle(selected, texts=None):
            for n, (lab, underline) in cells.items():
                on = n == selected
                lab.config(text=(texts or {}).get(n, n), fg=c["accent"] if on else c["muted"], font=self.f(11, on))
                underline.config(bg=c["accent"] if on else c["surface"])
        return bar, restyle

    def _build(self) -> None:
        c = self.c
        header = tk.Frame(self.root, bg=c["surface"])
        header.pack(fill="x")
        titles = tk.Frame(header, bg=c["surface"])
        titles.pack(side="left", padx=(18, 0), pady=(14, 12))
        self._label(titles, "SkillDesk", 16, bold=True).pack(anchor="w")
        self.subtitle = self._label(titles, "", 10, fg=c["muted"])
        self.subtitle.pack(anchor="w")
        actions = self.actions = tk.Frame(header, bg=c["surface"])
        actions.pack(side="right", padx=(0, 8))
        # created before self.footer exists; the hint handlers only touch it when the pointer is over a button
        for text, cmd, hint in (("＋ 추가", self.add_dialog, "스킬 추가 (링크로 설치 · 새로 만들기 · 추천)"),
                                ("⇧ 업데이트", lambda: self.check_updates(), "git으로 받은 스킬의 업데이트 확인"),
                                ("⟳", self.load, "목록 새로고침"),
                                ("⚙", self.settings_dialog, "설정 (표시할 에이전트)"),
                                ("프로젝트", self.harness_dialog, "프로젝트별 스킬 세트 (하네스) 관리"),
                                ("라이트" if self.theme == "dark" else "다크", self.toggle_theme, "밝은/어두운 화면 전환"),
                                ("–", self.hide, "트레이로 숨기기")):
            self._icon_button(actions, text, cmd, hint).pack(side="left")

        self.tabs, self._restyle_tabs = self._tab_bar(self.root, self.agents, self.set_agent)
        self.tabs.pack(fill="x")
        tk.Frame(self.root, height=1, bg=c["line"]).pack(fill="x")

        self.footer = self._label(self.root, DEFAULT_HINT, 10, fg=c["muted"], bg=c["bg"], anchor="w", padx=16, pady=7)
        self.footer.pack(side="bottom", fill="x")
        tk.Frame(self.root, height=1, bg=c["line"]).pack(side="bottom", fill="x")

        body = tk.Frame(self.root, bg=c["bg"])
        body.pack(fill="both", expand=True)
        self.rail = tk.Frame(body, bg=c["rail"], width=self.px(self.RAIL))
        self.rail.pack(side="left", fill="y")
        self.rail.pack_propagate(False)
        tk.Frame(body, width=1, bg=c["line"]).pack(side="left", fill="y")
        main = tk.Frame(body, bg=c["bg"])
        main.pack(side="left", fill="both", expand=True)

        search = tk.Frame(main, bg=c["bg"])
        search.pack(fill="x", padx=14, pady=(12, 4))
        box = tk.Frame(search, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"],
                       highlightcolor=c["accent"])
        box.pack(fill="x")
        self._label(box, "검색", 10, fg=c["muted"], padx=10).pack(side="left")
        self.entry = tk.Entry(box, textvariable=self.query, relief="flat", bg=c["surface"], fg=c["text"],
                              insertbackground=c["text"], font=self.f(11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 10))
        self.count = self._label(main, "", 10, fg=c["muted"], bg=c["bg"], anchor="w")
        self.count.pack(fill="x", padx=16, pady=(2, 4))

        self.list = Scroll(main, c)
        self.list.wrap.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.idle_bar = tk.Frame(main, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"])  # packed by _draw_idle_bar
        self.canvas, self.body = self.list.canvas, self.list.body
        self._show_restart_pill()  # a theme switch rebuilds the header; keep the pill if an update is waiting

    def _wheel(self, e) -> None:
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except KeyError:  # pointer is over a popup menu
            return
        scroller = None
        while w is not None and scroller is None:
            scroller, w = getattr(w, "scroller", None), w.master
        if scroller and scroller.canvas.yview() != (0.0, 1.0):
            scroller.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def _dark_titlebar(self, win=None) -> None:
        plat.dark_titlebar(win or self.root, self.theme == "dark")

    def toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        self.c = DARK if self.theme == "dark" else LIGHT
        mgr.save_setting("theme", self.theme)
        if self.dialog and self.dialog.win.winfo_exists():
            self.dialog.close()
        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg=self.c["bg"])
        self._build()
        self._dark_titlebar()
        self.render()

    def say(self, text: str, warn: bool = False, ms: int = 4000) -> None:
        """Footer message that falls back to the default hint."""
        if self._say_job:
            self.root.after_cancel(self._say_job)
        self.footer.config(text=text, fg=self.c["warn_fg"] if warn else self.c["accent"])
        self._say_job = self.root.after(ms, lambda: self.footer.config(text=DEFAULT_HINT, fg=self.c["muted"]))

    def bg(self, fn, done) -> None:
        """Run fn on a worker thread, then done(result or exception) on the Tk thread."""
        def run():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 - surfaced to the user by `done`
                res = e
            self.events.put(lambda: done(res))
        threading.Thread(target=run, daemon=True).start()

    # ---- list -------------------------------------------------------------------------------
    def load(self) -> None:
        mgr.version.cache_clear()
        data = {a: collect(a) for a in self.agents}
        if data != self.data:  # rescanning is cheap; redrawing is not, and would also reset what the user is reading
            self.data = data
            self.render()

    def set_agent(self, name: str) -> None:
        self.agent.set(name)
        self.cat = None
        self.render(top=True)

    def pick(self, cat: str | None) -> None:
        self.cat = cat
        if cat in (USAGE, IDLE):
            self.load_usage()
        self.render(top=True, rebuild=False)

    def load_usage(self) -> None:
        """Count skill calls in Claude Code's logs on a worker thread (cached, so only new log lines are read)."""
        projects = mgr.CLAUDE_HOME / "projects"
        work = lambda: usage.scan_logs(projects, mgr.STORE / "usage_cache.json")  # noqa: E731
        self.bg(work, self._usage_loaded)

    def _usage_loaded(self, res) -> None:
        if isinstance(res, Exception):
            self.say(f"사용 기록을 읽지 못했습니다: {res}", warn=True)
            res = ({}, None)
        if res != (self.usage, self.log_start):
            self.usage, self.log_start = res
            self._drop_usage_group()
            self.render(rebuild=False)

    def set_usage_month(self, month: str) -> None:
        self.usage_month = month
        self._drop_usage_group()
        self.render(top=True, rebuild=False)

    def _drop_usage_group(self) -> None:
        for cat in (USAGE, IDLE):
            if g := self.sections.pop(cat, None):  # its rows depend on the counts (and the month)
                g.destroy()
        self.checks = {}  # the tick boxes lived in the idle group

    def _idle_filter_changed(self) -> None:
        self._drop_usage_group()
        self.render(rebuild=False)

    def _used(self, found: list[dict]) -> list[dict]:
        """The listed skills called in usage_month, most called first. Sets self.calls."""
        counter = (self.usage or {}).get(self.usage_month, Counter())
        self.calls = {s["name"]: usage.count_for(counter, s["name"]) for s in found}
        return sorted((s for s in found if self.calls[s["name"]]), key=lambda s: (-self.calls[s["name"]], s["name"]))

    def _idle(self, skills: list[dict]) -> list[dict]:
        """The given skills not called in the last two months, longest unused first. Sets self.idle_last.
        Empty until the logs are counted, and when they hold no skill call at all."""
        now = datetime.now()
        key = (self.usage, tuple(skills), self.hide_new.get(), now.strftime("%Y-%m"))
        if key != self._idle_key:  # counting is the slow part (every category click renders); the answer only changes with these
            window = usage.recent_months(now)[-1]
            last = self.idle_last = usage.idle_since(self.usage or {}, [s["name"] for s in skills], now)

            def too_new(s: dict) -> bool:  # installed inside the window: it has not had the whole window to be used
                born = mgr.installed_on(s["path"])
                return born is not None and born.strftime("%Y-%m") >= window
            items = [s for s in skills if s["name"] in last and not (self.hide_new.get() and too_new(s))]
            self._idle_key = key
            self._idle_items = sorted(items, key=lambda s: (last[s["name"]] is not None, last[s["name"]] or "", s["name"]))
        return self._idle_items

    def render(self, top: bool = False, rebuild: bool = True) -> None:
        """rebuild=False (a category click) keeps the groups already built and only shows the picked ones."""
        c, agent = self.c, self.agent.get()
        q = self.query.get().strip().lower()
        everything = self.data.get(agent, [])
        found = [s for s in everything if q in " ".join((s["name"], s["note"], s["desc"], s["origin"])).lower()]
        counts = Counter(s["cat"] for s in found)
        starred = [s for s in found if s["name"] in self.pins[agent]]
        claude = agent == "Claude Code"  # only Claude Code's logs are read
        used = self._used(found) if claude else []
        idle_all = self._idle(everything) if claude else []  # not narrowed by the search: what is ticked must outlive a search
        shown_names = {s["name"] for s in found}
        idle = [s for s in idle_all if s["name"] in shown_names]
        if self.cat not in counts and self.cat not in (STAR, USAGE, IDLE) or (self.cat in (USAGE, IDLE) and not claude):
            self.cat = None  # the picked category vanished (search, refresh or another agent)
        self.shown = starred if self.cat == STAR else used if self.cat == USAGE else idle if self.cat == IDLE else [
            s for s in found if self.cat in (None, s["cat"])]
        self.checked &= {s["name"] for s in idle_all if s["managed"]}  # ticked, but gone or no longer idle: not ticked any more
        self._restyle_tabs(agent, {a: f"{a}  {len(self.data.get(a, []))}" for a in self.agents})
        self.subtitle.config(text=f"{agent}에서 사용할 수 있는 스킬")
        idle_count = None if not claude else "–" if self.usage is None else len(idle)  # "–": the logs are counted on the first visit
        self._draw_rail(counts, len(found), len(starred), len(used) if claude else None, idle_count)
        self.count.config(text=f"{len(self.shown)}개 스킬" + (f" · '{q}' 검색 결과" if q else ""))
        if rebuild or not self.shown:  # data, agent, search or theme changed (or only the empty note is up): groups are stale
            self.list.clear()
            self.sections, self.icons, self.checks = {}, {}, {}
        groups = [(STAR, self.shown)] if self.cat == STAR else [(USAGE, self.shown)] if self.cat == USAGE else [
            (IDLE, self.shown)] if self.cat == IDLE else [
            (cat, [s for s in self.shown if s["cat"] == cat]) for cat in [n for n, _ in CATEGORIES] + [OTHER]
        ]
        wanted = [self._group(cat, items) for cat, items in groups if items]
        for w in self.body.pack_slaves():
            if w not in wanted:
                w.pack_forget()
        packed = [g for g in wanted if g.winfo_manager()]  # groups already on screen stay put: laying the list out again is the slow part
        prev = None
        for g in wanted:
            if not g.winfo_manager():
                if prev:
                    g.pack(fill="x", after=prev)
                elif packed:
                    g.pack(fill="x", before=packed[0])
                else:
                    g.pack(fill="x")
            prev = g
        if not self.shown:
            msg = ("일치하는 스킬이 없습니다" if everything else
                   f"{agent}에 설치된 스킬이 없습니다.\n\n{mgr.AGENT_DIRS[agent]}\n\n위의 ＋ 추가에서 설치할 수 있습니다.")
            if self.cat == STAR and everything and not q:
                msg = "아직 지정한 스킬이 없습니다\n\n스킬 이름 앞의 ☆를 눌러\n자주 쓰는 스킬로 지정하세요."
            if self.cat == USAGE and everything:
                msg = ("Claude Code 대화 기록에서 스킬 호출을 세는 중입니다…" if self.usage is None else
                       f"{month_label(self.usage_month)}에 기록된 스킬 호출이 없습니다")
                self._month_chips(self.body)
            if self.cat == IDLE and everything:
                if self.usage is None:
                    msg = "Claude Code 대화 기록에서 스킬 호출을 세는 중입니다…"
                elif not any(m != usage.ALL for m in self.usage):
                    msg = ("대화 기록에서 스킬 호출을 하나도 찾지 못했습니다\n\n기록이 없으면 모든 스킬이 쓰지 않은 것처럼 보이므로\n"
                           "정리 후보를 만들지 않았습니다.")
                else:
                    msg = "정리할 스킬이 없습니다\n\n최근 2개월 안에 모든 스킬을 한 번 이상 썼습니다."
                    self._idle_head(self.body)
            self._label(self.body, msg, 11, fg=c["muted"], pady=40, padx=20, justify="center").pack(fill="x")
        self._draw_idle_bar()
        if top:
            self.canvas.yview_moveto(0)

    def _draw_rail(self, counts: Counter, total: int, starred: int, used: int | None = None, idle: int | str | None = None) -> None:
        c = self.c
        for w in self.rail.winfo_children():
            w.destroy()
        self._label(self.rail, "카테고리", 10, fg=c["muted"], bg=c["rail"], anchor="w", padx=16, pady=10).pack(fill="x")
        # the star entry is always there, even at 0, so the feature can be found
        top = [(STAR, starred)] + ([(USAGE, used)] if used is not None else []) + [(None, total)]
        bottom = [(IDLE, idle)] if idle is not None else []  # the cleanup entry is the last one, after every category
        for cat, n in top + [(k, counts[k]) for k in [n for n, _ in CATEGORIES] + [OTHER] if counts[k]] + bottom:
            fg, soft = self.cat_colors(cat)
            on = cat == self.cat
            row = tk.Frame(self.rail, bg=soft if on else c["rail"], cursor="hand2")
            row.pack(fill="x", pady=1)
            bar = tk.Frame(row, width=self.px(6), bg=fg)
            bar.pack(side="left", fill="y")
            name = self._label(row, cat or "전체", 11, bold=on, fg=fg if on else c["text"], bg=row["bg"], pady=9)
            name.pack(side="left", padx=(12, 4))
            chip = self._label(row, str(n), 10, bold=True, fg=fg, bg=c["surface"] if on else soft, padx=8)
            chip.pack(side="right", padx=12)
            about = ABOUT.get(cat, "모든 카테고리의 스킬") if cat else "모든 카테고리의 스킬"
            for w in (row, bar, name, chip):
                w.bind("<Button-1>", lambda e, cat=cat: self.pick(cat))
                w.bind("<Enter>", lambda e, t=about: self.footer.config(text=t, fg=c["text"]))
                w.bind("<Leave>", lambda e: self.footer.config(text=DEFAULT_HINT, fg=c["muted"]))

    def _group(self, cat: str, items: list[dict]) -> tk.Frame:
        """One category's header and rows: built the first time it is shown, afterwards only packed or hidden."""
        if cat not in self.sections:
            g = self.sections[cat] = tk.Frame(self.body, bg=self.c["surface"])
            self._section(g, cat, len(items))
            if cat == USAGE:
                self._month_chips(g)
            if cat == IDLE:
                self._idle_head(g)
            now = datetime.now()
            for s in items:
                self._row(g, s, show_cat=cat in (STAR, USAGE, IDLE), calls=self.calls.get(s["name"]) if cat == USAGE else None,
                          idle=idle_label(self.idle_last.get(s["name"]), now) if cat == IDLE else None)
        return self.sections[cat]

    def _section(self, parent, cat: str, n: int) -> None:
        fg, soft = self.cat_colors(cat)
        f = tk.Frame(parent, bg=soft)
        f.pack(fill="x")
        tk.Frame(f, width=self.px(6), bg=fg).pack(side="left", fill="y")
        self._label(f, cat, 12, bold=True, fg=fg, bg=soft, pady=8, padx=12).pack(side="left")
        self._label(f, f"{n}개", 10, bold=True, fg=fg, bg=soft, padx=10).pack(side="right")
        self._label(f, ABOUT[cat], 10, fg=self.c["text"], bg=soft).pack(side="left")  # what belongs here

    def _month_chips(self, parent) -> None:
        """Months that have calls (newest first, up to six) plus the whole period; the picked one is filled."""
        c = self.c
        months = sorted({m for m in (self.usage or {}) if m != usage.ALL} | {self.usage_month}, reverse=True)[:6]
        bar = tk.Frame(parent, bg=c["surface"])
        bar.pack(fill="x", padx=12, pady=8)
        for m in [*months, usage.ALL]:
            on = m == self.usage_month
            chip = self._label(bar, "전체 기간" if m == usage.ALL else month_label(m), 10, bold=on, cursor="hand2", padx=10, pady=3,
                               fg=c["on_accent"] if on else c["text"], bg=c["accent"] if on else c["rail"])
            chip.pack(side="left", padx=(0, 6))
            chip.bind("<Button-1>", lambda e, m=m: self.set_usage_month(m))

    def _idle_head(self, parent) -> None:
        """Top of the idle list: the switch for new skills, select-all, and a note when the logs are shorter than the window."""
        c = self.c
        bar = tk.Frame(parent, bg=c["surface"])
        bar.pack(fill="x", padx=12, pady=(8, 2))
        self._check(bar, "설치 2개월 미만 제외", self.hide_new, bg=c["surface"]).pack(side="left")
        for text, command in (("선택 해제", self.clear_checks), ("전체 선택", self.select_all)):
            link = self._label(bar, text, 10, fg=c["accent"], cursor="hand2", padx=8)
            link.pack(side="right")
            link.bind("<Button-1>", lambda e, command=command: command())
        start = self.log_start
        if start and start.strftime("%Y-%m-%d") > usage.recent_months(datetime.now())[-1] + "-01":
            note = f"대화 기록이 {start:%Y-%m-%d}부터만 남아 있어 기준(최근 2개월)보다 짧습니다. 그 전에 쓴 스킬도 후보로 보일 수 있습니다."
            self._label(parent, note, 10, fg=c["warn_fg"], bg=c["warn_bg"], anchor="w", justify="left", padx=14, pady=6,
                        wraplength=self.px(self.W - self.RAIL - 28 - 6 - 22) - self.px(28)).pack(fill="x", pady=(4, 0))

    def _fit(self, text: str, room: int) -> str:
        """Shorten text with an ellipsis so it fits in `room` pixels at the row-name font (full name stays in the copy menu)."""
        while len(text) > 1 and self.name_font.measure(text) > room:
            text = text.rstrip("…")[:-1] + "…"
        return text

    def _row(self, parent, s: dict, show_cat: bool = False, calls: int | None = None, idle: str | None = None) -> None:
        """idle: the idle list's 'last used' text; its rows also get a tick box (when the skill may be deleted here)."""
        c = self.c
        fg, _ = self.cat_colors(s["cat"])
        wrap = tk.Frame(parent, bg=c["surface"], cursor="hand2")
        wrap.pack(fill="x")
        tk.Frame(wrap, width=self.px(6), bg=fg).pack(side="left", fill="y")
        f = tk.Frame(wrap, bg=c["surface"], cursor="hand2")
        f.pack(side="left", fill="x", expand=True)
        top = tk.Frame(f, bg=c["surface"])
        top.pack(fill="x", padx=(14, 8), pady=(9, 0))
        more = self._label(top, "⋯", 13, fg=c["muted"], padx=8)  # right-hand items are packed first so a long name can't push them out
        more.pack(side="right")
        tag = self._label(top, s["origin"], 9, fg=c["muted"])
        tag.pack(side="right")
        if calls:
            self._label(top, f"{calls}회", 10, bold=True, fg=c["on_accent"], bg=c["accent"], padx=7).pack(side="right", padx=(0, 10))
        if idle:
            self._label(top, idle, 10, fg=c["muted"], bg=c["rail"], padx=7).pack(side="right", padx=(0, 10))
        # room left for the name once the tag, the ⋯ button and any badges are placed (badge widths are estimates)
        inner = self.px(self.W - self.RAIL - 28 - 6 - 22)
        room = inner - self.tag_font.measure(s["origin"]) - self.px(40)
        room -= self.px(46) * bool(s["manual"]) + self.px(88) * (s["repo"] in self.updatable) + self.px(60) * bool(calls)
        room -= self.px(86) * bool(idle)
        box = None
        if idle and s["managed"]:  # ☐/☑ to pick it for deletion; a skill some other app provides can't be deleted here
            box = self._label(top, "", 13, cursor="hand2", padx=2)
            self._paint_check(box, s["name"] in self.checked)
            box.pack(side="left", padx=(0, 4))
            self.checks.setdefault(s["name"], []).append(box)
            room -= self.px(30)
        star = self._label(top, "", 13, cursor="hand2", padx=2)  # a click on it stars / unstars the skill
        self._paint_star(star, s["name"] in self.pins[s["agent"]])
        star.pack(side="left", padx=(0, 4))
        self.icons.setdefault(s["name"], []).append(star)
        extra = [star, *([box] if box else [])]  # labels that tint on hover together with the rest of the row
        room -= self.px(30)
        if show_cat:  # in the star list the colour bar alone doesn't say which category a skill came from
            room -= self.tag_font.measure(s["cat"]) + self.px(24)
        name = self._label(top, self._fit(s["name"], room), 11, bold=True, anchor="w")
        name.pack(side="left")
        if show_cat:
            extra.append(self._label(top, s["cat"], 9, fg=fg))
            extra[-1].pack(side="left", padx=8)
        if s["manual"]:
            self._label(top, "수동", 9, fg=c["warn_fg"], bg=c["warn_bg"], padx=6).pack(side="left", padx=8)
        if s["repo"] in self.updatable:
            self._label(top, "업데이트 있음", 9, bold=True, fg=c["on_accent"], bg=c["accent"], padx=6).pack(side="left", padx=2)
        text = f"{s['origin']} 제공 스킬이라 여기서 삭제할 수 없습니다" if idle and not s["managed"] else s["note"]
        note = self._label(f, text, 10, fg=c["muted"], anchor="w", justify="left", wraplength=inner - self.px(28))
        note.pack(fill="x", padx=(14, 12), pady=(2, 9))
        tk.Frame(parent, height=1, bg=c["line"]).pack(fill="x")
        parts = (wrap, f, top, name, tag, more, note, *extra)

        def tint(color):
            for w in parts:
                w.config(bg=color)
        for w in (*parts, *top.winfo_children()):
            w.bind("<Enter>", lambda e: tint(c["soft"]))
            w.bind("<Leave>", lambda e: tint(c["surface"]))
            w.bind("<Button-1>", lambda e, n=s["name"]: self.copy(n))
            w.bind("<Double-Button-1>", lambda e, s=s: self.detail(s))
            for seq in plat.RIGHT_CLICK:
                w.bind(seq, lambda e, s=s: self.row_menu(s, e.x_root, e.y_root))
        more.bind("<Button-1>", lambda e, s=s: self.row_menu(s, e.x_root, e.y_root))
        star.bind("<Button-1>", lambda e, s=s: self.toggle_pin(s))  # after the loop above, which gave every label "copy the name"
        # a quick second click on the star is still a star click, not the row's double-click (which opens the detail page)
        star.bind("<Double-Button-1>", lambda e, s=s: self.toggle_pin(s))
        if box:  # same for the tick box
            for seq in ("<Button-1>", "<Double-Button-1>"):
                box.bind(seq, lambda e, n=s["name"]: self.toggle_check(n))

    def copy(self, name: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(name)
        self.say(f"복사됨: {name}", ms=2000)

    def toggle_pin(self, s: dict) -> None:
        pins = self.pins[s["agent"]]
        pins ^= {s["name"]}
        mgr.save_setting("pins", {a: sorted(p) for a, p in self.pins.items()})
        self.say(f"★ 추가됨: {s['name']}" if s["name"] in pins else f"★ 해제됨: {s['name']}")
        live = self.icons[s["name"]] = [w for w in self.icons.get(s["name"], []) if w.winfo_exists()]
        for w in live:  # repaint in place: rebuilding the skill's whole category just to flip one icon is slow
            self._paint_star(w, s["name"] in pins)
        if g := self.sections.pop(STAR, None):  # the star list itself has different rows now
            g.destroy()
        self.render(rebuild=False)

    def _paint_star(self, label: tk.Label, starred: bool) -> None:
        label.config(text="★" if starred else "☆", fg=self.cat_colors(STAR)[0] if starred else self.c["muted"])

    # ---- idle list: tick, then delete in one go ---------------------------------------------
    def _paint_check(self, label: tk.Label, on: bool) -> None:
        label.config(text="☑" if on else "☐", fg=self.c["accent"] if on else self.c["muted"])

    def set_checked(self, names: set[str]) -> None:
        self.checked = names
        for name, labels in self.checks.items():  # repaint in place, like the stars: rebuilding the list for a tick is slow
            for w in labels:
                if w.winfo_exists():
                    self._paint_check(w, name in names)
        self._draw_idle_bar()

    def toggle_check(self, name: str) -> None:
        self.set_checked(self.checked ^ {name})

    def select_all(self) -> None:
        self.set_checked({s["name"] for s in self.shown if s["managed"]})

    def clear_checks(self) -> None:
        self.set_checked(set())

    def _draw_idle_bar(self) -> None:
        """The strip under the idle list: what is ticked and the delete button. Not shown in any other view."""
        c, bar = self.c, self.idle_bar
        for w in bar.winfo_children():
            w.destroy()
        if self.cat != IDLE or not self.shown:
            bar.pack_forget()
            return
        n = len(self.checked)
        hidden = n - sum(s["name"] in self.checked for s in self.shown)  # ticked, but a search keeps them off the screen
        bar.pack(side="bottom", fill="x", padx=14, pady=(0, 10), before=self.list.wrap)
        # ticking only marks; nothing changes on disk until the delete button is confirmed
        text = f"{n}개 선택됨" + (f" (검색에 가려진 {hidden}개 포함)" if hidden else "") + " · 아직 삭제하지 않았습니다"
        self._label(bar, text if n else "지울 스킬의 ☐를 눌러 체크하세요", 10, fg=c["text"] if n else c["muted"],
                    padx=12, pady=9).pack(side="left")
        self._button(bar, "선택한 스킬 삭제…", self.delete_checked, danger=True).pack(side="right", padx=8, pady=5)

    def delete_checked(self) -> None:
        """Delete the ticked skills of the idle list: a link is unlinked, a real folder goes to the trash (see mgr.remove)."""
        skills = [s for s in self._idle_items if s["name"] in self.checked and s["managed"]]  # all ticked, even if a search hides them
        if not skills:
            self.say("먼저 지울 스킬에 체크하세요", warn=True)
            return
        how = {s["name"]: "연결만 해제" if mgr.is_link(s["path"]) else "휴지통으로 이동" for s in skills}
        lines = "\n".join(f"· {s['name']}  ({how[s['name']]})" for s in skills[:12])
        # a long list stays short on screen, yet every name is there: nothing is deleted that the question did not name
        more = f"\n그 밖에 {len(skills) - 12}개: " + ", ".join(s["name"] for s in skills[12:]) if len(skills) > 12 else ""
        body = ("연결만 해제하는 스킬은 원본과 다른 에이전트의 같은 스킬이 그대로 남습니다.\n"
                f"휴지통으로 옮기는 스킬은 {mgr.STORE / 'trash'} 에서 복구할 수 있습니다.")
        question = f"Claude Code에서 {len(skills)}개를 삭제할까요?\n\n{lines}{more}\n\n{body}"
        if not messagebox.askyesno("스킬 삭제", question, parent=self.root):
            return
        done, failed = [], []
        for s in skills:
            try:
                mgr.remove(s["agent"], s["path"].name)
                done.append(s["name"])
            except mgr.ManagerError as e:
                failed.append(f"{s['name']}: {e}")
        self.checked -= set(done)
        if failed:
            messagebox.showerror("삭제하지 못한 스킬이 있습니다", "\n".join(failed), parent=self.root)
        self.say(f"삭제됨: {len(done)}개" + (f" · 실패 {len(failed)}개" if failed else ""), warn=bool(failed))
        self.load()

    # ---- row actions ------------------------------------------------------------------------
    def menu_items(self, s: dict) -> list[tuple[str, object, str]]:
        """(label, action, kind) for a skill's ⋯ menu. kind: '' | 'danger' | 'note' (no action) | '-' (separator)."""
        starred = s["name"] in self.pins[s["agent"]]
        items: list[tuple[str, object, str]] = [
            ("상세 보기", lambda: self.detail(s), ""),
            ("이름 복사", lambda: self.copy(s["name"]), ""),
            ("★ 자주 쓰는 스킬에서 해제" if starred else "★ 자주 쓰는 스킬에 추가", lambda: self.toggle_pin(s), ""),
            ("", None, "-"),
            ("용도 설명 수정…", lambda: self.edit_note(s), ""),
            ("SKILL.md 수정…" if s["managed"] else "SKILL.md 보기", lambda: self.detail(s, page="SKILL.md", edit=s["managed"]), ""),
            ("", None, "-"),
        ]
        if s["managed"]:
            if s["repo"]:
                items.append(("이 스킬의 저장소 업데이트 확인", lambda: self.check_updates([s["repo"]]), ""))
            items.append(("삭제…", lambda: self.delete(s), "danger"))
        else:
            items.append((f"{s['origin']} 제공 스킬이라 여기서 삭제할 수 없습니다", None, "note"))
        return items

    def row_menu(self, s: dict, x: int, y: int) -> None:
        self.close_popup()
        self.popup = Popup(self, self.menu_items(s), x, y)

    def close_popup(self) -> None:
        if self.popup:
            self.popup.close()
            self.popup = None

    def detail(self, s: dict, page: str = "정보", edit: bool = False) -> None:
        self.close_popup()
        if self.detail_win and self.detail_win.win.winfo_exists():
            if not self.detail_win.discard_ok():  # unsaved edits in the open one: let the user decide
                return
            self.detail_win.editing = False
            self.detail_win.win.destroy()
        self.detail_win = DetailDialog(self, s, page, edit)

    def _side_window(self, title: str, width: int, height: int) -> tk.Toplevel:
        """A themed window centered over the panel (kept on screen), for dialogs."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=self.c["bg"])
        win.transient(self.root)  # stays above the panel without being topmost
        left, top, right, bottom = plat.work_area(self.root)
        frame = self.px(48)
        w, h = min(self.px(width), right - left - 24), min(self.px(height), bottom - top - frame - 24)
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        win.geometry(f"{w}x{h}+{max(left, min(x, right - w))}+{max(top, min(y, bottom - h - frame))}")
        self._dark_titlebar(win)
        return win

    def edit_note(self, s: dict) -> None:
        cur = brief(s["name"], s["desc"], mgr.load_notes())
        text = simpledialog.askstring("용도 설명 수정", f"{s['name']}\n한 줄 설명 (비우면 기본 설명으로 되돌림)",
                                      initialvalue=cur, parent=self.root)
        if text is None:
            return
        mgr.set_note(s["name"], text)
        self.load()

    def edit_file(self, s: dict) -> None:
        if s["repo"] and not messagebox.askokcancel(
                "원본 저장소 파일 수정",
                f"이 스킬은 git 저장소({mgr.repo_name(s['repo'])})를 그대로 가리킵니다.\n"
                "파일을 고치면 원본 저장소의 파일이 바뀌고, 이후 업데이트 때 충돌할 수 있습니다.\n계속할까요?",
                parent=self.root):
            return
        mgr.open_in_editor(s["path"] / "SKILL.md")

    def delete(self, s: dict) -> None:
        agent, p = s["agent"], s["path"]
        if mgr.is_link(p):
            body = "연결만 해제됩니다. 원본 파일과 다른 에이전트의 같은 스킬은 그대로입니다."
        else:
            body = f"폴더가 휴지통 폴더로 옮겨집니다(복구 가능):\n{mgr.STORE / 'trash'}"
        if not messagebox.askyesno("스킬 삭제", f"'{s['name']}'을(를) {agent}에서 삭제할까요?\n\n{body}", parent=self.root):
            return
        try:
            moved = mgr.remove(agent, p.name)
        except mgr.ManagerError as e:
            messagebox.showerror("삭제 실패", str(e), parent=self.root)
            return
        self.say(f"삭제됨: {s['name']}" + (f" (백업: {moved.name})" if moved else ""))
        self.load()

    # ---- updates ----------------------------------------------------------------------------
    def check_updates(self, repos: list[Path] | None = None) -> None:
        if repos is None:
            repos = sorted({s["repo"] for skills in self.data.values() for s in skills if s["repo"]}, key=str)
        if not repos:
            self.say("업데이트를 확인할 git 스킬이 없습니다")
            return
        self.say(f"업데이트 확인 중… ({len(repos)}개 저장소)", ms=60_000)

        def work():
            found, errors = [], []
            for r in repos:
                try:
                    if mgr.has_update(r):
                        found.append(r)
                except mgr.ManagerError as e:
                    errors.append(f"{mgr.repo_name(r)}: {e}")
            return found, errors
        self.bg(work, self._offer_updates)

    def _offer_updates(self, res) -> None:
        if isinstance(res, Exception):
            self.say(f"업데이트 확인 실패: {res}", warn=True)
            return
        found, errors = res
        self.updatable = set(found)
        self.render()
        if errors:
            messagebox.showwarning("일부 저장소를 확인하지 못했습니다", "\n".join(errors), parent=self.root)
        if not found:
            self.say("모두 최신입니다" if not errors else "확인된 저장소는 모두 최신입니다")
            return
        users = Counter(s["repo"] for skills in self.data.values() for s in skills if s["repo"] in found)
        lines = "\n".join(f"· {mgr.repo_name(r)}  (스킬 {users[r]}개 연결)" for r in found)
        if messagebox.askyesno("업데이트", f"새 버전이 있는 저장소 {len(found)}개:\n{lines}\n\n지금 업데이트할까요?",
                               parent=self.root):
            self.say("업데이트 중…", ms=60_000)
            self.bg(lambda: [(r, mgr.update(r)) for r in found], self._updated)

    def _updated(self, res) -> None:
        if isinstance(res, Exception):
            messagebox.showerror("업데이트 실패", f"{res}\n\n저장소 파일을 직접 고친 경우 충돌일 수 있습니다.", parent=self.root)
            self.say("업데이트 실패", warn=True)
            return
        lines, gone = [], []
        for r, out in res:
            lines.append(f"· {mgr.repo_name(r)}: {out['old']} → {out['new']}")
            gone += out["gone"]
        self.updatable -= {r for r, _ in res}
        if gone:
            lines.append("\n저장소에서 사라져 연결이 끊긴 스킬: " + ", ".join(gone))
        messagebox.showinfo("업데이트 완료", "\n".join(lines), parent=self.root)
        self.load()

    # ---- window / tray ----------------------------------------------------------------------
    def add_dialog(self) -> None:
        if not self.visible:
            self.show()
        if self.dialog and self.dialog.win.winfo_exists():
            if isinstance(self.dialog, AddDialog):
                self.dialog.win.lift()
                return
            self.dialog.close()
        self.dialog = AddDialog(self)

    def settings_dialog(self) -> None:
        if not self.visible:
            self.show()
        if self.dialog and self.dialog.win.winfo_exists():
            self.dialog.close()  # one side dialog at a time
        self.dialog = SettingsDialog(self)

    def harness_dialog(self) -> None:
        if not self.visible:
            self.show()
        if self.dialog and self.dialog.win.winfo_exists():
            self.dialog.close()
        self.dialog = HarnessWindow(self)

    def set_agents(self, agents: list[str]) -> None:
        """Show only these agent tabs (kept in AGENT_DIRS order) and rebuild the panel around them."""
        self.agents = [a for a in AGENTS if a in agents] or self.agents
        mgr.save_setting("agents", self.agents)
        if self.agent.get() not in self.agents:
            self.agent.set(self.agents[0])
            self.cat = None
        self.data = {}
        for w in self.root.winfo_children():
            if not isinstance(w, tk.Toplevel):  # keep the open dialog (the one that called this)
                w.destroy()
        self._build()
        self.load()

    def place(self) -> None:
        """Size the window and put it in the middle of the usable screen (what a normal app does the first time)."""
        left, top, right, bottom = plat.work_area(self.root)
        frame = self.px(48)  # title bar + borders: Tk sizes the inside, but positions the outside
        w, h = min(self.px(self.W), right - left - 24), min(self.px(self.H), bottom - top - frame - 24)
        self.root.geometry(f"{w}x{h}+{left + (right - left - w) // 2}+{top + (bottom - top - h - frame) // 2}")

    def show(self) -> None:
        self.root.deiconify()  # it comes back where the user left it; only the first start centers it
        self.root.lift()
        self.root.attributes("-topmost", True)  # Windows often refuses a plain lift() from the tray; pop to the front once...
        self.root.attributes("-topmost", False)  # ...then behave like a normal window (clicking another app puts it behind)
        self.root.focus_force()
        self.visible = True
        self.load()

    def hide(self) -> None:
        self.root.withdraw()
        self.visible = False

    def _on_unmap(self, e) -> None:
        # the minimize button: go to the tray, not the taskbar / Dock. macOS reports 'iconic' only after the
        # unmap event, so look a moment later.
        if e.widget is self.root:
            self.root.after(150, lambda: self.hide() if self.root.state() == "iconic" else None)

    def toggle(self) -> None:
        # a double-click on the tray icon arrives as two clicks: acting on both showed the window and hid it again
        # The clock starts when the first click has been handled: showing reloads the list, which can take longer than
        # the gap between the two clicks.
        if time.monotonic() - self._last_toggle < 0.6:
            return
        self.hide() if self.visible else self.show()
        self._last_toggle = time.monotonic()

    # ---- updating SkillDesk itself ------------------------------------------------------------
    def auto_update(self, manual: bool = False) -> None:
        """Check GitHub for a newer SkillDesk and install it (see skill_update.py for when that is allowed).
        Runs again every 6 hours by itself; manual=True (the tray item) also reports "already up to date" and why not."""
        if not manual:
            self.root.after(6 * 3600 * 1000, self.auto_update)
            if not mgr.load_settings().get("auto_update", True):
                return
        if self.pending_restart:  # the new code is already on disk; asking GitHub again would only repeat it
            if manual:
                self.say("이미 새 버전을 받았습니다. 위쪽의 '다시 시작'을 누르세요", ms=6000)
            return
        if manual:
            self.say("SkillDesk 업데이트 확인 중…", ms=60_000)
        self.bg(lambda: upd.apply(), lambda res: self._update_done(res, manual))

    def update_app(self) -> None:
        if not self.visible:  # asked from the tray: show the window so the answer (and the restart button) can be seen
            self.show()
        self.auto_update(manual=True)

    def toggle_auto_update(self) -> None:
        on = not mgr.load_settings().get("auto_update", True)
        mgr.save_setting("auto_update", on)
        self.say(f"SkillDesk 자동 업데이트: {'켬' if on else '끔'}")

    def _update_done(self, res, manual: bool = False) -> None:
        if isinstance(res, Exception):
            res = upd.Result("failed", str(res))
        if res.status == "updated" and res.data_only:
            pass  # only skill_recommend.json changed: the 추천 tab reads it each time it opens, so no restart is needed
        elif res.status == "updated":
            self.pending_restart = res
            self._show_restart_pill()
            if self.visible:
                self.say(f"SkillDesk {res.old} → {res.new} 업데이트됨. 위쪽의 '다시 시작'을 누르면 적용됩니다", ms=10_000)
            else:
                self.restart()  # nobody is looking at the window, so switch to the new version right away
        elif res.status == "failed" and (manual or res.new != self._failed_update):
            self._failed_update = res.new
            self.say(res.detail, warn=True, ms=10_000)
        elif manual and res.status in ("up-to-date", "skipped"):
            self.say("SkillDesk는 최신 버전입니다" if res.status == "up-to-date" else res.detail, ms=8000)

    def _show_restart_pill(self) -> None:
        if not self.pending_restart or (self.restart_pill and self.restart_pill.winfo_exists()):
            return
        c = self.c
        pill = self.restart_pill = self._label(self.actions, "새 버전 · 다시 시작", 10, bold=True, fg=c["on_accent"], bg=c["accent"],
                                               padx=10, pady=3, cursor="hand2")
        pill.pack(side="left", padx=(0, 8), before=self.actions.winfo_children()[0])
        pill.bind("<Button-1>", lambda e: self.restart())

    def restart(self) -> None:
        """Start the updated app and close this one."""
        upd.restart(hidden=not self.visible)
        self.quit()

    def quit(self) -> None:
        if self.tray:
            self.tray.stop()
        if self.tray_proc:
            self.tray_proc.terminate()
        self.root.destroy()

    def start_tray(self) -> None:
        if plat.MAC:
            # pystray must own the main thread on macOS and Tk needs it too, so the menu-bar icon lives in its own
            # process and reports clicks as one command per line.
            self.tray_proc = subprocess.Popen([sys.executable, str(HERE / "skill_viewer.py"), "--tray-child"],
                                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            threading.Thread(target=self._read_tray, daemon=True).start()
            return
        import pystray
        # tray callbacks run on pystray's thread; the Tk thread drains the queue
        self.tray = pystray.Icon("skill_viewer", icon_image(64), "SkillDesk", tray_menu(self.events.put))
        self.tray.run_detached()

    def _read_tray(self) -> None:
        if self.tray_proc and self.tray_proc.stdout:
            for line in self.tray_proc.stdout:
                if line.strip() in TRAY_COMMANDS:  # never act on anything else a child process prints
                    self.events.put(line.strip())

    def _pump(self) -> None:
        while not self.events.empty():
            item = self.events.get_nowait()
            if callable(item):
                item()
            elif item in TRAY_COMMANDS:
                getattr(self, item)()
        if plat.take_show_request(self.INSTANCE):  # the user started the app again while it was hidden in the tray
            self.show()
        self.root.after(100, self._pump)

    def _tick(self) -> None:
        if self.visible and not self.query.get():  # don't rebuild under the user's search
            self.load()
        self.root.after(60_000, self._tick)

    def run(self, hidden: bool = False) -> None:
        if hidden:
            self.hide()
        self.root.mainloop()


class AddDialog:
    """Install skills from a link, or create a new one. Both tabs link into the agents you tick. The 추천 tab only lists
    the bundled skills.sh snapshot: picking a row hands its repo to the link tab, which does the usual download → choose → install."""
    W, H = 540, 720
    # 추천 tab: sub-tab -> (key in the snapshot, what its install count counts)
    RECOMMENDED = {"인기": ("popular", "누적 설치 수"), "요즘 뜨는": ("trending", "최근 24시간 설치 수"),
                   "어제 급등": ("surge", "늘어난 스타 수")}

    def __init__(self, app: App):
        self.app, c = app, app.c
        self.win = win = app._side_window("스킬 추가", self.W, self.H)
        self.mode = "link"
        self.rec_tab = "인기"
        self.picks: dict[Path, tk.BooleanVar] = {}
        self.draft: skill_agent.Draft | None = None  # 에이전트로 만들기: what the agent wrote, waiting for 설치
        self.cancel = threading.Event()
        self.running = False
        self.agents = {a: tk.BooleanVar(value=True) for a in app.agents}
        bar, self._restyle = app._tab_bar(win, ["링크로 설치", "새로 만들기", "에이전트로 만들기", "추천"], self.show)
        bar.pack(fill="x")
        tk.Frame(win, height=1, bg=c["line"]).pack(fill="x")
        self.panel = tk.Frame(win, bg=c["bg"])
        self.panel.pack(fill="both", expand=True, padx=14, pady=12)
        win.protocol("WM_DELETE_WINDOW", self.close)
        self.show("링크로 설치")

    def close(self) -> None:
        self.cancel.set()  # stops a running agent; its thread then cleans up its own temp folder
        if self.draft:
            skill_agent.discard(self.draft)
        self.win.destroy()

    def say(self, text: str, warn: bool = False) -> None:
        c = self.app.c
        self.status.config(text=text, fg=c["warn_fg"] if warn else c["muted"])

    def _agent_checks(self) -> None:
        app, c = self.app, self.app.c
        row = tk.Frame(self.panel, bg=c["bg"])
        row.pack(side="bottom", fill="x", pady=(8, 0))
        app._label(row, "설치할 에이전트", 10, fg=c["muted"], bg=c["bg"]).pack(anchor="w")
        for a, var in self.agents.items():
            app._check(row, a, var).pack(side="left", padx=(0, 12))

    def show(self, tab: str) -> None:
        for w in self.panel.winfo_children():
            w.destroy()
        self._restyle(tab)
        self.mode = tab
        {"링크로 설치": self._build_link, "새로 만들기": self._build_new, "에이전트로 만들기": self._build_agent,
         "추천": self._build_recommended}[tab]()

    # -- tab 1: install from link
    def _build_link(self) -> None:
        app, c, p = self.app, self.app.c, self.panel
        self.link = tk.StringVar()
        row = tk.Frame(p, bg=c["bg"])
        row.pack(fill="x")
        box, e = app._entry(row, self.link)
        box.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.load_link())
        e.focus_set()
        app._button(row, "불러오기", self.load_link, primary=True).pack(side="left", padx=(8, 0))
        app._label(p, "GitHub 저장소·폴더·SKILL.md 링크 또는 owner/repo[/경로]", 10, fg=c["muted"], bg=c["bg"],
                   anchor="w").pack(fill="x", pady=(5, 0))
        self.status = app._label(p, "", 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                                 wraplength=app.px(self.W - 50))
        self.status.pack(fill="x", pady=(5, 3))
        # bottom-up so the list can take the remaining space
        btns = tk.Frame(p, bg=c["bg"])
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        self.install_btn = app._button(btns, "선택한 스킬 설치", self.do_install, primary=True)
        self.install_btn.pack(side="right")
        self.app._enable(self.install_btn, False)
        app._button(btns, "닫기", self.close).pack(side="right", padx=8)
        app._label(p, "⚠ 스킬은 에이전트가 그대로 따르는 지시문이며 스크립트를 포함할 수 있습니다. "
                      "신뢰할 수 있는 출처만 설치하세요.", 10, fg=c["warn_fg"], bg=c["bg"], anchor="w", justify="left",
                   wraplength=app.px(self.W - 50)).pack(side="bottom", fill="x", pady=(8, 0))
        self.replace = tk.BooleanVar(value=False)
        app._check(p, "같은 이름이 있으면 교체 (기존 것은 휴지통 폴더에 백업)", self.replace).pack(side="bottom", fill="x")
        self._agent_checks()
        self.results = Scroll(p, c)
        self.results.wrap.pack(fill="both", expand=True, pady=(4, 0))

    def load_link(self, prefer: str | None = None) -> None:
        """prefer: the skill to tick when the repo holds several (a pick from the 추천 tab)."""
        text = self.link.get()
        self.say("불러오는 중… (처음 받는 저장소는 몇 초 걸립니다)")
        self.app._enable(self.install_btn, False)

        def work():
            _, root = mgr.fetch(text)
            return mgr.discover(root)
        self.app.bg(work, lambda res: self._loaded(res, prefer))

    def _loaded(self, res, prefer: str | None = None) -> None:
        if not self.win.winfo_exists() or self.mode != "링크로 설치":  # the user moved to another tab while it downloaded
            return
        if isinstance(res, Exception):
            self.say(f"불러오지 못했습니다: {res}", warn=True)
            return
        self._show_found(res, prefer)

    def _show_found(self, found: list[Path], prefer: str | None = None) -> None:
        """The skill named `prefer` goes first and is the only one ticked; without it, a lone skill is ticked. Named by its folder,
        or by the `name:` in its SKILL.md (skills.sh lists the latter: 'vercel-react-best-practices' is the folder react-best-practices)."""
        app, c = self.app, self.app.c
        self.results.clear()
        want = next((d for d in found if prefer in (d.name, mgr.parse(d / "SKILL.md")["name"])), None) if prefer else None
        if want:
            found = [want, *(d for d in found if d != want)]
        self.picks = {d: tk.BooleanVar(value=d == want if want else len(found) == 1) for d in found}
        if not found:
            self.say("SKILL.md가 들어 있는 폴더를 찾지 못했습니다", warn=True)
            return
        self.say(f"스킬 {len(found)}개를 찾았습니다. 설치할 것을 고르세요.")
        if len(found) > 1:
            head = tk.Frame(self.results.body, bg=c["surface"])
            head.pack(fill="x", padx=10, pady=(8, 0))
            for text, val in (("전체 선택", True), ("전체 해제", False)):
                lab = app._label(head, text, 10, bold=True, fg=c["accent"], cursor="hand2", padx=4)
                lab.pack(side="left")
                lab.bind("<Button-1>", lambda e, v=val: [x.set(v) for x in self.picks.values()])
        notes = mgr.load_notes()
        for d, var in self.picks.items():
            info = mgr.parse(d / "SKILL.md")
            row = tk.Frame(self.results.body, bg=c["surface"])
            row.pack(fill="x", padx=10, pady=(8, 0))
            app._check(row, d.name, var, bg=c["surface"]).pack(anchor="w")
            app._label(row, brief(d.name, info["desc"], notes), 10, fg=c["muted"], anchor="w", justify="left",
                       wraplength=app.px(self.W - 100)).pack(fill="x", padx=(26, 0))
        self.app._enable(self.install_btn, True)

    def do_install(self) -> None:
        picks = [d for d, v in self.picks.items() if v.get()]
        agents = [a for a, v in self.agents.items() if v.get()]
        if not picks or not agents:
            self.say("설치할 스킬과 에이전트를 하나 이상 선택하세요", warn=True)
            return
        replace = self.replace.get()
        self.say("설치 중…")
        self.app._enable(self.install_btn, False)
        self.app.bg(lambda: mgr.install(picks, agents, replace), self._installed)

    def _installed(self, res) -> None:
        if not self.win.winfo_exists():
            return
        self.app._enable(self.install_btn, True)
        if isinstance(res, Exception):
            self.say(f"설치 실패: {res}", warn=True)
            return
        n = Counter(r.status for r in res)
        text = f"설치 {n['installed']}건 · 교체 {n['replaced']}건 · 이미 설치됨 {n['exists']}건"
        if n["conflict"]:
            text += f" · 같은 이름이 있어 건너뜀 {n['conflict']}건 (교체를 체크하고 다시 설치하세요)"
        self.say(text, warn=bool(n["conflict"]))
        self.app.say(text)
        self.app.load()

    # -- tab 2: create
    def _build_new(self) -> None:
        app, c, p = self.app, self.app.c, self.panel
        self.name, self.desc = tk.StringVar(), tk.StringVar()
        app._label(p, "스킬 이름 (영문·숫자·-_. , 예: my-skill)", 10, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        box, e = app._entry(p, self.name)
        box.pack(fill="x", pady=(3, 12))
        e.focus_set()
        app._label(p, "한 줄 설명 (에이전트가 언제 쓸지 판단하는 문장)", 10, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        box, e2 = app._entry(p, self.desc)
        box.pack(fill="x", pady=(3, 12))
        e2.bind("<Return>", lambda ev: self.do_create())
        self.status = app._label(p, "", 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                                 wraplength=app.px(self.W - 50))
        self.status.pack(fill="x")
        btns = tk.Frame(p, bg=c["bg"])
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        app._button(btns, "만들고 편집기 열기", self.do_create, primary=True).pack(side="right")
        app._button(btns, "닫기", self.close).pack(side="right", padx=8)
        self._agent_checks()

    def do_create(self) -> None:
        agents = [a for a, v in self.agents.items() if v.get()]
        if not agents:
            self.say("에이전트를 하나 이상 선택하세요", warn=True)
            return
        try:
            d, _ = mgr.create(self.name.get().strip(), self.desc.get(), agents)
        except mgr.ManagerError as e:
            self.say(str(e), warn=True)
            return
        self.say(f"만들었습니다: {d}\n편집기에서 SKILL.md 본문을 채워 주세요.")
        self.app.load()
        mgr.open_in_editor(d / "SKILL.md")

    # -- tab 3: let the agent write it
    def _build_agent(self) -> None:
        app, c, p = self.app, self.app.c, self.panel
        exe, info = skill_agent.agent_status()
        self.request = tk.StringVar()
        app._label(p, f"{info}\n만든 결과는 먼저 보여 드리고, '설치'를 눌러야 스킬 폴더에 들어갑니다.", 10, fg=c["muted"], bg=c["bg"],
                   anchor="w", justify="left", wraplength=app.px(self.W - 50)).pack(fill="x", pady=(0, 10))
        app._label(p, "어떤 스킬이 필요한가요? (이 문장만 에이전트에 보냅니다)", 10, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        row = tk.Frame(p, bg=c["bg"])
        row.pack(fill="x", pady=(3, 0))
        box, e = app._entry(row, self.request)
        box.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.agent_start())
        self.make_btn = app._button(row, "만들기", self.agent_start, primary=True)
        self.make_btn.pack(side="left", padx=(8, 0))
        self.status = app._label(p, "", 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left", wraplength=app.px(self.W - 50))
        self.status.pack(fill="x", pady=(8, 3))
        btns = tk.Frame(p, bg=c["bg"])
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        self.install_btn = app._button(btns, "설치", self.agent_install, primary=True)
        self.install_btn.pack(side="right")
        self.cancel_btn = app._button(btns, "취소", lambda: self.cancel.set())
        self.cancel_btn.pack(side="right", padx=8)
        app._button(btns, "닫기", self.close).pack(side="right")
        self._agent_checks()
        self.files = app._label(p, "", 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left", wraplength=app.px(self.W - 50))
        self.files.pack(side="bottom", fill="x", pady=(6, 0))
        self.preview = tk.Text(p, wrap="word", relief="flat", bd=0, padx=12, pady=10, font=app.f(10), bg=c["surface"], fg=c["text"],
                               selectbackground=c["soft"], selectforeground=c["text"], highlightthickness=1,
                               highlightbackground=c["line"], height=10)
        self.preview.pack(fill="both", expand=True)
        self._agent_state()

    def _agent_state(self) -> None:
        """Fill the widgets from self.running / self.draft (the tab is rebuilt on every visit)."""
        app, ready = self.app, bool(skill_agent.agent_status()[0]) and not self.running
        app._enable(self.make_btn, ready)
        app._enable(self.cancel_btn, self.running)
        app._enable(self.install_btn, bool(self.draft) and not self.running)
        self.preview.config(state="normal")
        self.preview.delete("1.0", "end")
        if self.draft:
            self.preview.insert("1.0", self.draft.preview())
            self.files.config(text=f"스킬 이름: {self.draft.name}\n파일 {len(self.draft.files)}개: {', '.join(self.draft.files)}")
        else:
            self.files.config(text="")
        self.preview.config(state="disabled")
        if self.running:
            self.say("에이전트가 스킬을 만드는 중입니다… (보통 1~2분, 최대 10분)")

    def agent_start(self) -> None:
        request = self.request.get().strip()
        if self.running or not request:
            self.say("어떤 스킬이 필요한지 적어 주세요", warn=True)
            return
        exe, info = skill_agent.agent_status()
        if not exe:
            self.say(info, warn=True)
            return
        if not messagebox.askokcancel(
                "에이전트 실행", f"{skill_agent.SUPPORTED}를 실행해 스킬을 만듭니다.\n\n- 보내는 내용: 입력한 요청문뿐입니다.\n"
                "- 임시 폴더에서만 파일을 쓰며, 설치는 결과를 본 뒤 직접 누릅니다.\n"
                f"- 사용 중인 계정의 사용량이 쓰입니다 (한 번에 최대 약 {skill_agent.BUDGET_USD}달러).\n\n실행할까요?",
                parent=self.win):
            return
        if self.draft:
            skill_agent.discard(self.draft)
            self.draft = None
        self.cancel, self.running = threading.Event(), True
        self._agent_state()
        cancel = self.cancel
        self.app.bg(lambda: skill_agent.generate(request, cancel), self._agent_done)

    def _agent_done(self, res) -> None:
        self.running = False
        if not self.win.winfo_exists():
            if isinstance(res, skill_agent.Draft):
                skill_agent.discard(res)
            return
        if isinstance(res, skill_agent.Draft):
            self.draft = res
        if self.mode != "에이전트로 만들기":  # the user is on another tab; the result shows when they come back
            return
        self._agent_state()
        if isinstance(res, skill_agent.Cancelled):
            self.say("취소했습니다")
        elif isinstance(res, Exception):
            self.say(f"만들지 못했습니다: {res}", warn=True)
        else:
            self.say("만들었습니다. 아래 내용을 확인하고, 괜찮으면 '설치'를 누르세요.")

    def agent_install(self) -> None:
        agents = [a for a, v in self.agents.items() if v.get()]
        if not self.draft or not agents:
            self.say("설치할 에이전트를 하나 이상 선택하세요", warn=True)
            return
        draft = self.draft
        self.app._enable(self.install_btn, False)
        self.say("설치 중…")
        self.app.bg(lambda: skill_agent.install_draft(draft, agents), self._agent_installed)

    def _agent_installed(self, res) -> None:
        if not isinstance(res, Exception):
            self.draft = None  # install_draft already removed the temp folder
        if not self.win.winfo_exists() or self.mode != "에이전트로 만들기":
            return
        if isinstance(res, Exception):
            self.say(f"설치 실패: {res}", warn=True)
            self._agent_state()
            return
        self._agent_state()
        self.say(f"설치했습니다: {res[0]}")
        self.app.say(f"스킬을 설치했습니다: {res[0].name}")
        self.app.load()

    # -- tab 4: recommended
    def _build_recommended(self) -> None:
        app, c, p = self.app, self.app.c, self.panel
        self.recommended = mgr.load_recommended()
        btns = tk.Frame(p, bg=c["bg"])
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        app._button(btns, "닫기", self.close).pack(side="right")
        app._label(p, "⚠ 설치 수는 품질·안전을 보증하지 않습니다. 고르면 그 저장소를 받아 스킬 목록만 보여 주고, "
                      "설치는 내용을 확인한 뒤 '선택한 스킬 설치'를 눌러야 시작됩니다.", 10, fg=c["warn_fg"], bg=c["bg"], anchor="w",
                   justify="left", wraplength=app.px(self.W - 50)).pack(side="bottom", fill="x", pady=(8, 0))
        bar, self._restyle_rec = app._tab_bar(p, list(self.RECOMMENDED), self.show_recommended)
        bar.pack(fill="x")
        self.rec_caption = app._label(p, "", 10, fg=c["muted"], bg=c["bg"], anchor="w")
        self.rec_caption.pack(fill="x", pady=(8, 4))
        self.rec_list = Scroll(p, c)
        self.rec_list.wrap.pack(fill="both", expand=True)
        self.show_recommended(self.rec_tab)

    def show_recommended(self, tab: str) -> None:
        app, c = self.app, self.app.c
        key, counts = self.RECOMMENDED[tab]
        self.rec_tab = tab
        self._restyle_rec(tab)
        updated = self.recommended["updated"]
        since = self.recommended["surge_since"] if key == "surge" else ""
        self.rec_caption.config(text=f"{counts} 순" + (f" · {updated} 기준" if updated else "") + (f" · {since} 대비" if since else ""))
        self.rec_list.clear()
        self.rec_list.canvas.yview_moveto(0)
        if key == "surge" and not self.recommended[key] and updated:
            tip = ("급등한 스킬이 없습니다." if since
                   else "아직 비교할 어제 기록이 없습니다.\n하루가 지나면 전날보다 스타가 늘어난 스킬이 여기에 나옵니다.")
            app._label(self.rec_list.body, tip, 10, fg=c["muted"], anchor="w", justify="left", wraplength=app.px(self.W - 90),
                       padx=14, pady=14).pack(fill="x")
            return
        if not self.recommended[key]:
            app._label(self.rec_list.body, "추천 목록이 비어 있습니다.\nskill_recommend.json이 없거나 읽을 수 없습니다. "
                       "tools/update_recommend.py로 다시 만들 수 있습니다.", 10, fg=c["muted"], anchor="w", justify="left",
                       wraplength=app.px(self.W - 90), padx=14, pady=14).pack(fill="x")
            return
        installed = {s["name"] for skills in app.data.values() for s in skills}
        for e in self.recommended[key]:
            self._rec_row(e, e["skill"] in installed)

    def _rec_row(self, e: dict, installed: bool) -> None:
        app, c, body = self.app, self.app.c, self.rec_list.body
        row = tk.Frame(body, bg=c["surface"], cursor="hand2")
        row.pack(fill="x")
        top = tk.Frame(row, bg=c["surface"], cursor="hand2")
        top.pack(fill="x", padx=14, pady=(8, 0))
        count = app._label(top, f"+{e['gain']:,}★" if "gain" in e else f"{e['installs']:,}회", 10, fg=c["muted"], cursor="hand2")
        count.pack(side="right")  # the right-hand items are packed first so a long name can't push them out
        badge = None
        if installed:
            badge = app._label(top, "설치됨", 9, bold=True, fg=c["on_accent"], bg=c["accent"], padx=6, cursor="hand2")
            badge.pack(side="right", padx=(0, 10))
        room = app.px(self.W - 60 - 130) - app.px(64) * installed
        name = app._label(top, app._fit(e["skill"], room), 11, bold=True, anchor="w", cursor="hand2")
        name.pack(side="left")
        source = app._label(row, e["source"], 10, fg=c["muted"], anchor="w", cursor="hand2")
        source.pack(fill="x", padx=14, pady=(1, 8))
        tk.Frame(body, height=1, bg=c["line"]).pack(fill="x")
        tinted = (row, top, count, name, source)  # the badge keeps its own colors

        def tint(color):
            for w in tinted:
                w.config(bg=color)
        for w in (*tinted, *([badge] if badge else [])):
            w.bind("<Enter>", lambda ev: tint(c["soft"]))
            w.bind("<Leave>", lambda ev: tint(c["surface"]))
            w.bind("<Button-1>", lambda ev, e=e: self.use_recommended(e))

    def use_recommended(self, e: dict) -> None:
        """Hand the pick to the link tab, which downloads the repo and lists what is in it (nothing is installed yet)."""
        self.show("링크로 설치")
        self.link.set(e["source"])
        self.load_link(prefer=e["skill"])


class SettingsDialog:
    """Which agents the panel shows. Applies on every click; at least one stays on."""
    W, H = 460, 290

    def __init__(self, app: App):
        self.app, c = app, app.c
        self.win = win = app._side_window("설정", self.W, self.H)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda e: self.close())
        app._label(win, "표시할 에이전트", 13, bold=True, bg=c["bg"]).pack(anchor="w", padx=18, pady=(18, 2))
        app._label(win, "이 PC에 설치한 에이전트만 고르세요. 고르지 않은 에이전트의 탭과 목록은 보이지 않습니다.", 10,
                   fg=c["muted"], bg=c["bg"], wraplength=app.px(self.W - 40), justify="left").pack(anchor="w", padx=18)
        found = mgr.detected_agents()
        self.vars = {a: tk.BooleanVar(value=a in app.agents) for a in AGENTS}
        for a, var in self.vars.items():
            row = tk.Frame(win, bg=c["bg"])
            row.pack(fill="x", padx=18, pady=(12, 0))
            app._check(row, a, var).pack(side="left")
            app._label(row, "이 PC에서 발견됨" if a in found else "발견되지 않음", 10,
                       fg=c["muted"], bg=c["bg"]).pack(side="left", padx=8)
            var.trace_add("write", lambda *_, a=a: self.changed(a))
        self.status = app._label(win, "", 10, fg=c["muted"], bg=c["bg"])
        self.status.pack(anchor="w", padx=18, pady=(16, 0))

    def changed(self, name: str) -> None:
        picked = [a for a, v in self.vars.items() if v.get()]
        if not picked:  # an empty panel has nothing to show: undo the last click
            self.vars[name].set(True)
            self.status.config(text="하나 이상은 선택해야 합니다.", fg=self.app.c["warn_fg"])
            return
        self.status.config(text="", fg=self.app.c["muted"])
        if picked != self.app.agents:
            self.app.set_agents(picked)

    def close(self) -> None:
        self.win.destroy()


class HarnessWindow:
    """Per-project skill sets. Left: the projects (folders) the user registered. Right: the selected project's set (a
    named list of installed skills, ticked here), where to link it, and apply / release. The logic is skill_harness."""
    W, H = 900, 800

    def __init__(self, app: App):
        self.app, c = app, app.c
        self.win = win = app._side_window("프로젝트 하네스", self.W, self.H)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda e: self.close())
        self.project: str | None = None
        self.set_name: str | None = None
        self.creating = False  # "＋ 새 세트" was clicked: an empty set waiting for a name
        self.name_var = tk.StringVar()
        self.skill_vars: dict[str, tk.BooleanVar] = {}
        self.target_vars: dict[str, tk.BooleanVar] = {}
        self.exclude_var, self.exclude_for = tk.BooleanVar(), None
        self.data = hn.load()

        head = tk.Frame(win, bg=c["surface"])
        head.pack(fill="x")
        app._label(head, "프로젝트 하네스", 16, bold=True, anchor="w", padx=20).pack(fill="x", pady=(14, 0))
        app._label(head, "프로젝트마다 쓸 스킬 묶음(세트)을 정해 그 프로젝트 폴더에만 연결합니다. 원본 스킬은 그대로 둡니다.", 10,
                   fg=c["muted"], anchor="w", padx=20).pack(fill="x", pady=(0, 12))
        tk.Frame(win, height=1, bg=c["line"]).pack(fill="x")
        self.page = "세트 편집"

        # bottom first, so the middle takes the height left. Same shape as the skill detail window: tools in an even
        # 2-column grid, then the destructive and closing actions apart below a rule.
        bottom = tk.Frame(win, bg=c["bg"])
        bottom.pack(side="bottom", fill="x", padx=16, pady=(8, 12))
        self.status = app._label(bottom, "", 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                                 wraplength=app.px(self.W - 60))
        self.status.pack(fill="x", pady=(0, 8))
        grid = tk.Frame(bottom, bg=c["bg"])
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1, uniform="tool")
        grid.grid_columnconfigure(1, weight=1, uniform="tool")
        tools = [("세트 저장", self.save_set), ("이 프로젝트에 적용…", self.apply),
                 ("연결 해제", self.release), ("프로젝트 폴더 열기", self.open_folder)]
        for i, (text, cmd) in enumerate(tools):
            app._button(grid, text, cmd).grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 0 if i % 2 else 6), pady=(0, 6))
        tk.Frame(bottom, height=1, bg=c["line"]).pack(fill="x", pady=(6, 10))
        ends = tk.Frame(bottom, bg=c["bg"])
        ends.pack(fill="x")
        app._button(ends, "세트 삭제…", self.delete_set, danger=True).pack(side="left")
        app._button(ends, "닫기", self.close, primary=True).pack(side="right")
        tk.Frame(win, height=1, bg=c["line"]).pack(side="bottom", fill="x")

        bar, self._restyle = app._tab_bar(win, ["세트 편집", "구조 가이드"], self.show_page)
        bar.pack(fill="x")
        tk.Frame(win, height=1, bg=c["line"]).pack(fill="x")
        body = self.sets_page = tk.Frame(win, bg=c["bg"])
        body.pack(fill="both", expand=True)
        self.guide_page = tk.Frame(win, bg=c["bg"])
        left = tk.Frame(body, bg=c["rail"], width=app.px(250))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Frame(body, width=1, bg=c["line"]).pack(side="left", fill="y")
        app._label(left, "프로젝트", 11, bold=True, bg=c["rail"], anchor="w", padx=14).pack(fill="x", pady=(12, 6))
        row = tk.Frame(left, bg=c["rail"])
        row.pack(side="bottom", fill="x", padx=10, pady=10)
        app._button(row, "＋ 폴더 추가", self.add_project).pack(side="left")
        app._button(row, "제거", self.remove_project).pack(side="right")
        self.projects = tk.Frame(left, bg=c["rail"])
        self.projects.pack(fill="both", expand=True)
        self.right = tk.Frame(body, bg=c["bg"])
        self.right.pack(side="left", fill="both", expand=True, padx=16, pady=(12, 0))
        self.refresh()

    # ---- drawing --------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Redraw both panes from the saved file and the current choices."""
        app, c = self.app, self.app.c
        self.data = hn.load()
        paths = list(self.data["projects"])
        if self.project not in paths:
            self.project = paths[0] if paths else None
            self.set_name, self.creating = None, False
        entry = self.data["projects"].get(self.project)
        if entry and self.set_name is None and not self.creating:
            self.set_name = entry["set"]
        for w in self.projects.winfo_children():
            w.destroy()
        for p in paths:
            on = p == self.project
            bg = c["soft"] if on else c["rail"]
            cell = tk.Frame(self.projects, bg=bg, cursor="hand2")
            cell.pack(fill="x")
            parts = [app._label(cell, Path(p).name or p, 11, bold=on, fg=c["accent"] if on else c["text"], bg=bg, anchor="w",
                                padx=14, cursor="hand2"),
                     app._label(cell, ellipsis(tilde(Path(p).parent), 30), 9, fg=c["muted"], bg=bg, anchor="w", padx=14,
                                cursor="hand2")]
            parts[0].pack(fill="x", pady=(7, 0))
            parts[1].pack(fill="x", pady=(0, 7))
            for w in (cell, *parts):
                w.bind("<Button-1>", lambda e, p=p: self.select_project(p))
        if not paths:
            app._label(self.projects, "아직 등록한 프로젝트가 없습니다.\n아래 '＋ 폴더 추가'로 시작하세요.", 10, fg=c["muted"],
                       bg=c["rail"], anchor="w", justify="left", padx=14, pady=8).pack(fill="x")

        for w in self.right.winfo_children():
            w.destroy()
        if entry is None:
            app._label(self.right, "프로젝트를 추가하면 여기서 세트를 고르고 연결할 수 있습니다.", 11, fg=c["muted"], bg=c["bg"],
                       anchor="w").pack(fill="x", pady=8)
            self.say("")
            return
        assert self.project  # entry exists only for a selected project
        app._label(self.right, Path(self.project).name or self.project, 13, bold=True, bg=c["bg"], anchor="w").pack(fill="x")
        app._label(self.right, tilde(Path(self.project)), 10, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x", pady=(0, 10))

        app._label(self.right, "세트", 10, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        chips = tk.Frame(self.right, bg=c["bg"])
        chips.pack(fill="x", pady=(2, 8))
        self.chips: dict[str | None, tk.Label] = {}
        for name in (*self.data["sets"], None):
            on = name == self.set_name if name is not None else self.creating
            chip = app._label(chips, name or "＋ 새 세트", 10, bold=on, fg=c["on_accent"] if on else c["text"],
                              bg=c["accent"] if on else c["rail"], padx=10, pady=3, cursor="hand2")
            chip.pack(side="left", padx=(0, 6), pady=(0, 4))
            chip.bind("<Button-1>", lambda e, n=name: self.choose_set(n))
            self.chips[name] = chip
        self.name_var.set(self.set_name or "")
        name_row = tk.Frame(self.right, bg=c["bg"])
        name_row.pack(fill="x", pady=(0, 8))
        app._label(name_row, "이름", 10, fg=c["muted"], bg=c["bg"]).pack(side="left", padx=(0, 8))
        box, self.name_entry = app._entry(name_row, self.name_var)
        box.pack(side="left", fill="x", expand=True)

        app._label(self.right, "이 세트에 넣을 스킬 (설치된 스킬 중에서)", 10, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        saved = set(self.data["sets"].get(self.set_name or "", {}).get("skills", []))
        installed = sorted({s["path"].name for skills in app.data.values() for s in skills if s["managed"]}, key=str.lower)
        self.skill_vars = {n: tk.BooleanVar(value=n in saved) for n in installed}
        self.skill_vars |= {n: tk.BooleanVar(value=True) for n in sorted(saved - set(installed))}  # in the set, no longer installed
        opts = tk.Frame(self.right, bg=c["bg"])  # packed before the list, so a short window shrinks the list, not these
        opts.pack(side="bottom", fill="x")
        self.list = Scroll(self.right, c)
        self.list.canvas.config(height=app.px(120))
        self.list.wrap.pack(fill="both", expand=True, pady=(2, 8))
        for n, var in self.skill_vars.items():
            r = tk.Frame(self.list.body, bg=c["surface"])
            r.pack(fill="x", padx=8, pady=1)
            app._check(r, n, var, bg=c["surface"]).pack(side="left")
            if n not in installed:
                app._label(r, "설치되어 있지 않음", 9, fg=c["warn_fg"], bg=c["surface"]).pack(side="left", padx=6)
        if not self.skill_vars:
            app._label(self.list.body, "설치된 스킬이 없습니다. ＋ 추가로 먼저 설치하세요.", 10, fg=c["muted"], padx=10, pady=10).pack()

        app._label(opts, "연결할 위치 (프로젝트 폴더 안)", 10, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        self.target_vars = {t: tk.BooleanVar(value=t in entry["targets"]) for t in hn.PROJECT_DIRS}  # fresh vars: no stale traces
        for t, var in self.target_vars.items():
            var.trace_add("write", lambda *_, t=t: self.target_changed(t))
            app._check(opts, f"{t}   {hn.PROJECT_DIRS[t]}/", var).pack(anchor="w", pady=1)
        # off unless an earlier apply turned it on; a redraw for the same project keeps what the user just ticked
        self.exclude_var = tk.BooleanVar(value=self.exclude_var.get() if self.exclude_for == self.project else bool(entry["exclude"]))
        self.exclude_for = self.project
        if hn.is_git_repo(self.project):
            app._check(opts, "git status에 새 파일로 보이지 않게 하기 (.git/info/exclude에 추가)",
                       self.exclude_var).pack(anchor="w", pady=(6, 0))
        self.report_state()

    def say(self, text: str, warn: bool = False) -> None:
        c = self.app.c
        self.status.config(text=text, fg=c["warn_fg"] if warn else c["muted"])

    def report_state(self) -> None:
        assert self.project
        if not Path(self.project).is_dir():
            self.say("이 프로젝트 폴더를 찾을 수 없습니다. 옮기거나 지웠다면 '제거'로 목록에서 빼세요.", warn=True)
            return
        on = hn.applied(self.project)
        self.say("연결된 스킬: " + ", ".join(on) if on else "아직 이 프로젝트에 연결된 스킬이 없습니다.")

    def show_page(self, name: str) -> None:
        self.page = name
        self._restyle(name)
        self.sets_page.pack_forget()
        self.guide_page.pack_forget()
        if name == "세트 편집":
            self.sets_page.pack(fill="both", expand=True)
        else:
            self.guide_page.pack(fill="both", expand=True)
            self.draw_guide()

    def draw_guide(self) -> None:
        """Read-only: the how-to steps, then the selected project's agent setup as a tree (what exists, what this app made)."""
        app, c = self.app, self.app.c
        for w in self.guide_page.winfo_children():
            w.destroy()
        steps = ["1. 왼쪽 '＋ 폴더 추가'로 프로젝트 폴더를 등록합니다.",
                 "2. 세트 편집에서 이 프로젝트에 쓸 스킬을 골라 세트로 저장합니다.",
                 "3. '이 프로젝트에 적용'을 누르면 아래 트리의 skills 폴더에 연결이 만들어집니다. '연결 해제'로 되돌립니다.",
                 "4. CLAUDE.md · AGENTS.md · .mcp.json · settings.json은 프로젝트에서 직접 작성합니다. "
                 "이 앱은 아직 만들거나 고치지 않고, 있는지만 보여 줍니다."]
        box = tk.Frame(self.guide_page, bg=c["bg"])
        box.pack(fill="both", expand=True, padx=20, pady=(14, 0))
        app._label(box, "하네스 만드는 순서", 11, bold=True, bg=c["bg"], anchor="w").pack(fill="x", pady=(0, 4))
        for line in steps:
            app._label(box, line, 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                       wraplength=app.px(self.W - 60)).pack(fill="x")
        if not self.project:
            app._label(box, "\n프로젝트를 등록하면 그 폴더의 구조가 여기에 트리로 나옵니다.", 10, fg=c["muted"], bg=c["bg"],
                       anchor="w").pack(fill="x")
            return
        app._label(box, "프로젝트 구조", 11, bold=True, bg=c["bg"], anchor="w").pack(fill="x", pady=(14, 4))
        mono = (plat.MONO_FONT, round(10 * plat.FONT_SCALE), "normal", "roman")
        text = tk.Text(box, font=mono, bg=c["surface"], fg=c["text"], relief="flat", highlightthickness=1,
                       highlightbackground=c["line"], wrap="none", cursor="arrow", padx=12, pady=10)
        text.pack(fill="both", expand=True, pady=(0, 8))
        text.tag_config("missing", foreground=c["muted"])
        text.tag_config("mine", foreground=c["accent"], font=(*mono[:2], "bold", "roman"))
        text.tag_config("note", foreground=c["muted"])
        text.insert("end", f"{Path(self.project).name}/\n")
        try:
            rows = hn.guide_tree(self.project)
        except mgr.ManagerError as e:
            rows = []
            text.insert("end", str(e), ("missing",))
        width = max((len(r["prefix"] + r["name"]) for r in rows), default=0) + 3
        for r in rows:
            state = ("mine" if r["owner"] == "harness" else "") if r["exists"] else "missing"
            mark = "연결" if r["owner"] == "harness" else "있음" if r["exists"] else "없음"
            text.insert("end", (r["prefix"] + r["name"]).ljust(width), (state,) if state else ())
            text.insert("end", f"{mark}  ", (state,) if state else ())
            text.insert("end", r["note"] + "\n", ("note",))
        text.configure(state="disabled")

    # ---- choices --------------------------------------------------------------------------------
    def select_project(self, path: str) -> None:
        self.project, self.set_name, self.creating = path, None, False
        self.refresh()

    def choose_set(self, name: str | None) -> None:
        self.set_name, self.creating = name, name is None
        if name is not None:
            self.persist()
        self.refresh()
        if name is None:
            self.name_entry.focus_set()

    def targets(self) -> list[str]:
        return [t for t, v in self.target_vars.items() if v.get()]

    def target_changed(self, name: str) -> None:
        if not self.targets():  # at least one place stays on: undo the last click
            self.target_vars[name].set(True)
            self.say("연결할 위치는 하나 이상 골라야 합니다.", warn=True)
            return
        self.persist()

    def persist(self) -> None:
        if self.project:
            hn.configure_project(self.project, self.set_name, self.targets())

    def run(self, fn, *args):
        """Call skill_harness; a failure whose text is fit for the user goes up as a message. -> result, or None
        (apply, release and remove_project return lists, add_project a key: none of them returns None itself)."""
        try:
            return fn(*args)
        except mgr.ManagerError as e:
            messagebox.showerror("프로젝트 하네스", str(e), parent=self.win)
            return None

    # ---- projects -------------------------------------------------------------------------------
    def add_project(self) -> None:
        folder = filedialog.askdirectory(parent=self.win, title="프로젝트 폴더 고르기")
        if folder and (key := self.run(hn.add_project, folder)):
            self.select_project(key)

    def remove_project(self) -> None:
        if not self.project:
            return self.say("제거할 프로젝트를 먼저 고르세요.", warn=True)
        if not messagebox.askokcancel(
                "프로젝트 제거",
                f"'{Path(self.project).name}'을(를) 목록에서 뺍니다.\n이 앱이 만든 스킬 연결도 함께 끊깁니다. "
                "프로젝트 폴더와 원본 스킬은 그대로입니다.", parent=self.win):
            return
        if self.run(hn.remove_project, self.project) is not None:
            self.project = None
            self.refresh()

    def open_folder(self) -> None:
        if self.project:
            plat.open_folder(Path(self.project))

    # ---- sets -----------------------------------------------------------------------------------
    def save_set(self) -> str | None:
        """Save the ticked skills under the typed name and make it this project's set. -> the name, or None."""
        if not self.project:
            self.say("프로젝트를 먼저 추가하세요.", warn=True)
            return None
        name = " ".join(self.name_var.get().split())
        try:
            hn.save_set(name, [n for n, v in self.skill_vars.items() if v.get()])
        except mgr.ManagerError as e:
            messagebox.showerror("프로젝트 하네스", str(e), parent=self.win)
            return None
        self.set_name, self.creating = name, False
        self.persist()
        self.refresh()
        self.say(f"'{name}' 세트를 저장했습니다.")
        return name

    def delete_set(self) -> None:
        if not self.set_name:
            return self.say("지울 세트를 먼저 고르세요.", warn=True)
        if not messagebox.askokcancel(
                "세트 삭제", f"'{self.set_name}' 세트를 지웁니다. 스킬 자체와 이미 만든 연결은 그대로입니다.", parent=self.win):
            return
        self.run(hn.delete_set, self.set_name)
        self.set_name = None
        self.refresh()

    # ---- apply / release ------------------------------------------------------------------------
    def apply(self) -> None:
        if not self.project:
            return self.say("프로젝트를 먼저 추가하세요.", warn=True)
        if not self.save_set():
            return
        skills = self.data["sets"][self.set_name]["skills"] if self.set_name in self.data["sets"] else []
        places = ", ".join(f"{hn.PROJECT_DIRS[t]}/" for t in self.targets())
        text = f"'{self.set_name}' 세트의 스킬 {len(skills)}개를 이 프로젝트의 {places} 에 연결합니다.\n" \
               "같은 이름의 폴더가 이미 있으면 건드리지 않고 건너뜁니다."
        if hn.is_git_repo(self.project) and not self.exclude_var.get():
            text += "\n\n이 프로젝트는 git 저장소입니다. 연결한 폴더가 git status에 새 파일로 나타납니다.\n" \
                    "보이지 않게 하려면 취소하고 '새 파일로 보이지 않게 하기'를 켠 뒤 다시 적용하세요."
        if not messagebox.askokcancel("프로젝트에 적용", text, parent=self.win):
            return
        out = self.run(hn.apply, self.project, self.exclude_var.get())
        if out is not None:
            self.refresh()
            self.say(*summarize(out))

    def release(self) -> None:
        if not self.project:
            return self.say("프로젝트를 먼저 추가하세요.", warn=True)
        out = self.run(hn.release, self.project)
        if out is not None:
            self.refresh()
            self.say(*summarize(out, release=True))

    def close(self) -> None:
        self.win.destroy()


def ellipsis(text: str, n: int) -> str:
    return text if len(text) <= n else "…" + text[-(n - 1):]


def summarize(results: list, release: bool = False) -> tuple[str, bool]:
    """Apply / release results -> (one status line, whether something was skipped and needs a warning color)."""
    by: dict[str, list[str]] = {}
    for r in results:
        by.setdefault(r.status, []).append(r.name)
    parts = []
    for status, label in (("installed", "새로 연결"), ("exists", "이미 연결됨"), ("removed", "연결 해제"), ("gone", "이미 없음")):
        if status in by:
            parts.append(f"{label} {len(by[status])}개")
    warn = False
    if "conflict" in by:
        warn = True
        parts.append("건너뜀(같은 이름의 폴더가 이미 있어 그대로 둠): " + ", ".join(by["conflict"]))
    if "missing" in by:
        warn = True
        parts.append("설치되어 있지 않아 건너뜀: " + ", ".join(by["missing"]))
    return (" · ".join(parts) or ("해제할 연결이 없습니다." if release else "연결할 스킬이 없습니다.")), warn


class Popup:
    """The ⋯ menu, drawn in the app's colors (the system menu can't be themed). Closes on pick, Esc or click-away."""

    def __init__(self, app: App, items: list[tuple[str, object, str]], x: int, y: int):
        c = app.c
        self.app = app
        self.win = win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=c["line"])
        box = tk.Frame(win, bg=c["surface"])
        box.pack(padx=1, pady=1)  # the 1px gap shows the line color as a border
        tk.Frame(box, height=app.px(4), bg=c["surface"]).pack(fill="x")
        self.rows: list[tk.Label] = []
        for label, action, kind in items:
            if kind == "-":
                tk.Frame(box, height=1, bg=c["line"]).pack(fill="x", padx=10, pady=4)
                continue
            fg = c["danger"] if kind == "danger" else c["muted"] if kind == "note" else c["text"]
            row = app._label(box, label, 11, fg=fg, anchor="w", padx=16, pady=7, cursor="hand2" if action else "arrow")
            row.pack(fill="x")
            self.rows.append(row)
            if callable(action):
                hover_bg, hover_fg = (c["danger_bg"], fg) if kind == "danger" else (c["soft"], c["accent"])
                row.bind("<Enter>", lambda e, r=row, b=hover_bg, h=hover_fg: r.config(bg=b, fg=h))
                row.bind("<Leave>", lambda e, r=row, f=fg: r.config(bg=c["surface"], fg=f))
                row.bind("<Button-1>", lambda e, a=action: self.pick(a))
        tk.Frame(box, height=app.px(4), bg=c["surface"]).pack(fill="x")
        win.update_idletasks()
        left, top, right, bottom = plat.work_area(app.root)
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = max(left, min(x, right - w - 4))
        y = y if y + h < bottom else max(top, y - h)  # open upward near the bottom of the screen
        win.geometry(f"+{x}+{y}")
        win.bind("<Escape>", lambda e: self.close())
        win.bind("<FocusOut>", lambda e: self.close())
        win.focus_force()

    def pick(self, action) -> None:
        self.close()
        action()

    def close(self) -> None:
        if self.win.winfo_exists():
            self.win.destroy()


INLINE = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`)")


def render_markdown(app: "App", text: tk.Text, md: str) -> None:
    """Show SKILL.md readably: front matter muted, headings large, lists with bullets, code blocks and `inline code`
    in a monospace font on a tint, **bold** bold. Never italic."""
    c = app.c
    mono = (plat.MONO_FONT, round(10 * plat.FONT_SCALE), "normal", "roman")
    text.configure(state="normal")
    text.delete("1.0", "end")
    for tag, spec in {
        "h1": dict(font=app.f(16, True), spacing1=14, spacing3=6),
        "h2": dict(font=app.f(13, True), spacing1=12, spacing3=4),
        "h3": dict(font=app.f(11, True), spacing1=8, spacing3=2),
        "bold": dict(font=app.f(10, True)),
        "code": dict(font=mono, background=c["rail"], lmargin1=10, lmargin2=10),
        "inline": dict(font=mono, background=c["rail"]),
        "bullet": dict(lmargin1=14, lmargin2=30, spacing1=2),
        "quote": dict(foreground=c["muted"], lmargin1=14, lmargin2=14),
        "meta": dict(font=mono, foreground=c["muted"]),
        "rule": dict(font=app.f(4), spacing1=2),
    }.items():
        text.tag_config(tag, **spec)

    def put(line: str, *tags: str) -> None:
        for part in INLINE.split(line):
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                text.insert("end", part[2:-2], (*tags, "bold"))
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                text.insert("end", part[1:-1], (*tags, "inline"))
            else:
                text.insert("end", part, tags)
        text.insert("end", "\n", tags)

    front, body = mgr.split_skill_md(md.replace("\r\n", "\n"))
    if front:
        for line in front.split("\n"):
            put(line, "meta")
        text.insert("end", "\n", ("rule",))
    in_code = False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        elif in_code:
            text.insert("end", line + "\n", ("code",))
        elif m := re.match(r"(#{1,3})\s+(.*)", line):
            put(m[2], f"h{len(m[1])}")
        elif m := re.match(r"\s*[-*+]\s+(.*)", line):
            text.insert("end", "•  ", ("bullet",))
            put(m[1], "bullet")
        elif m := re.match(r"\s*\d+[.)]\s+(.*)", line):
            text.insert("end", f"{re.match(r'\s*(\d+[.)])', line)[1]}  ", ("bullet",))  # type: ignore[index]
            put(m[1], "bullet")
        elif line.startswith(">"):
            put(line.lstrip("> "), "quote")
        else:
            put(line)
    text.configure(state="disabled")


class DetailDialog:
    """Everything about one skill, on two pages: 정보 (purpose, category, origin and version, where it lives, which
    agents have it, its files) and SKILL.md (a readable view, and for skills managed here an edit form)."""

    W, H = 640, 780

    def __init__(self, app: App, s: dict, page: str = "정보", edit: bool = False):
        self.app, self.s, c = app, s, app.c
        self.win = win = app._side_window(f"{s['name']} 상세", self.W, self.H)
        win.protocol("WM_DELETE_WINDOW", self.close)
        fg, soft = app.cat_colors(s["cat"])
        self.editing = False
        self.page = ""

        head = tk.Frame(win, bg=c["surface"])
        head.pack(fill="x")
        tk.Frame(head, height=app.px(4), bg=fg).pack(fill="x")
        app._label(head, s["name"], 16, bold=True, anchor="w", justify="left", wraplength=app.px(self.W - 40),
                   padx=20, pady=(12)).pack(fill="x")
        chips = tk.Frame(head, bg=c["surface"])
        chips.pack(fill="x", padx=20, pady=(0, 12))
        app._label(chips, s["cat"], 10, bold=True, fg=fg, bg=soft, padx=8, pady=2).pack(side="left")
        app._label(chips, s["origin"], 10, fg=c["muted"], bg=c["rail"], padx=8, pady=2).pack(side="left", padx=6)
        if s["manual"]:
            app._label(chips, "수동", 10, fg=c["warn_fg"], bg=c["warn_bg"], padx=8, pady=2).pack(side="left")
        if s["repo"] in app.updatable:
            app._label(chips, "업데이트 있음", 10, bold=True, fg=c["on_accent"], bg=c["accent"], padx=8, pady=2).pack(side="left", padx=6)
        tk.Frame(win, height=1, bg=c["line"]).pack(fill="x")

        # actions at the bottom first, so the page can take whatever height is left. Two rows: they don't fit one line.
        bottom = tk.Frame(win, bg=c["bg"])
        bottom.pack(side="bottom", fill="x", padx=16, pady=(8, 12))
        # tools as an even 2-column grid (a row of four crowds the width); the destructive and closing actions sit apart below a rule
        tools = [("외부 편집기로 열기", lambda: app.edit_file(s)), ("폴더 열기", lambda: plat.open_folder(s["path"])),
                 ("이름 복사", lambda: app.copy(s["name"]))]
        if s["repo"]:
            tools.append(("업데이트 확인", lambda: app.check_updates([s["repo"]])))
        grid = tk.Frame(bottom, bg=c["bg"])
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1, uniform="tool")
        grid.grid_columnconfigure(1, weight=1, uniform="tool")
        for i, (text, cmd) in enumerate(tools):
            lone = i == len(tools) - 1 and i % 2 == 0  # an odd last one spans both columns instead of leaving a gap
            app._button(grid, text, cmd).grid(row=i // 2, column=0 if lone else i % 2, columnspan=2 if lone else 1, sticky="ew",
                                              padx=(0, 0 if i % 2 or lone else 6), pady=(0, 6))
        tk.Frame(bottom, height=1, bg=c["line"]).pack(fill="x", pady=(6, 10))
        ends = tk.Frame(bottom, bg=c["bg"])
        ends.pack(fill="x")
        if s["managed"]:
            app._button(ends, "삭제…", self.delete, danger=True).pack(side="left")
        app._button(ends, "닫기", self.close, primary=True).pack(side="right")
        tk.Frame(win, height=1, bg=c["line"]).pack(side="bottom", fill="x")

        bar, self._restyle = app._tab_bar(win, ["정보", "SKILL.md"], self.show_page)
        bar.pack(fill="x")
        tk.Frame(win, height=1, bg=c["line"]).pack(fill="x")
        self.holder = tk.Frame(win, bg=c["bg"])
        self.holder.pack(fill="both", expand=True, padx=20, pady=(14, 0))
        self.show_page(page)
        if edit:
            self.begin_edit()

    # ---- pages ----------------------------------------------------------------------------------
    def show_page(self, name: str) -> None:
        if name != self.page and not self.discard_ok():
            self._restyle(self.page)  # the tab bar already moved; put it back
            return
        self.editing = False
        for w in self.holder.winfo_children():
            w.destroy()
        self.page = name
        self._restyle(name)
        (self._info if name == "정보" else self._view)()

    def _info(self) -> None:
        app, s, c, body = self.app, self.s, self.app.c, self.holder
        app._label(body, s["note"], 12, bold=True, bg=c["bg"], anchor="w", justify="left",
                   wraplength=app.px(self.W - 40)).pack(fill="x")
        if s["desc"] and s["desc"] != s["note"]:
            app._label(body, s["desc"], 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                       wraplength=app.px(self.W - 40)).pack(fill="x", pady=(4, 0))
        grid = tk.Frame(body, bg=c["bg"])
        grid.pack(fill="x", pady=(14, 10))
        for i, (key, value) in enumerate(self.facts()):
            app._label(grid, key, 10, fg=c["muted"], bg=c["bg"], anchor="nw").grid(row=i, column=0, sticky="nw", pady=3)
            app._label(grid, value, 10, bg=c["bg"], anchor="w", justify="left",
                       wraplength=app.px(self.W - 160)).grid(row=i, column=1, sticky="w", padx=(14, 0), pady=3)

    def _text_box(self, parent, height: int = 8, mono: bool = False) -> tk.Text:
        """A bordered Text in the app's colors; the border turns accent-colored while it has the cursor."""
        app, c = self.app, self.app.c
        frame = tk.Frame(parent, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"], highlightcolor=c["accent"])
        font = (plat.MONO_FONT, round(10 * plat.FONT_SCALE), "normal", "roman") if mono else app.f(10)
        text = tk.Text(frame, wrap="word", relief="flat", bd=0, padx=12, pady=10, font=font, undo=True,
                       bg=c["surface"], fg=c["text"], insertbackground=c["text"], height=height,
                       selectbackground=c["soft"], selectforeground=c["text"], highlightthickness=0)
        text.pack(fill="both", expand=True)
        text.bind("<FocusIn>", lambda e: frame.config(highlightbackground=c["accent"]))
        text.bind("<FocusOut>", lambda e: frame.config(highlightbackground=c["line"]))
        text.frame = frame  # type: ignore[attr-defined]
        return text

    def _view(self) -> None:
        """The readable SKILL.md, with the button that turns it into the edit form."""
        app, s, c, body = self.app, self.s, self.app.c, self.holder
        top = tk.Frame(body, bg=c["bg"])
        top.pack(fill="x", pady=(0, 6))
        app._label(top, "SKILL.md", 10, bold=True, fg=c["muted"], bg=c["bg"]).pack(side="left")
        if s["managed"]:
            self.edit_btn = app._button(top, "수정", self.begin_edit, primary=True)
            self.edit_btn.pack(side="right")
        else:
            app._label(top, f"{s['origin']} 제공이라 보기만 가능합니다", 10, fg=c["muted"], bg=c["bg"]).pack(side="right")
        self.text = self._text_box(body, height=8)
        self.text.frame.pack(fill="both", expand=True)  # type: ignore[attr-defined]
        try:
            content = (s["path"] / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            content = f"읽지 못했습니다: {e}"
        render_markdown(app, self.text, content)  # leaves it read-only

    # ---- editing --------------------------------------------------------------------------------
    def begin_edit(self) -> None:
        app, s = self.app, self.s
        if not s["managed"] or self.editing:
            return
        if s["repo"] and not messagebox.askokcancel(
            "원본 저장소 파일 수정",
            f"이 스킬은 git 저장소({mgr.repo_name(s['repo'])})를 그대로 가리킵니다.\n"
            "저장하면 원본 저장소의 파일이 바뀌고, 이후 업데이트 때 충돌할 수 있습니다.\n계속할까요?",
            parent=self.win):
            return
        if self.page != "SKILL.md":
            self.show_page("SKILL.md")
        try:
            self.orig = mgr.read_skill_md(s["path"])
        except OSError as e:
            messagebox.showerror("읽지 못했습니다", str(e), parent=self.win)
            return
        self.editing = True
        c, body = app.c, self.holder
        for w in body.winfo_children():
            w.destroy()
        top = tk.Frame(body, bg=c["bg"])
        top.pack(fill="x", pady=(0, 8))
        app._label(top, "SKILL.md 수정", 10, bold=True, fg=c["muted"], bg=c["bg"]).pack(side="left")
        app._label(top, "Ctrl+S 저장 · Esc 취소", 9, fg=c["muted"], bg=c["bg"]).pack(side="right")

        # save / cancel stay pinned under the form
        foot = tk.Frame(body, bg=c["bg"])
        foot.pack(side="bottom", fill="x", pady=(8, 8))
        self.status = app._label(foot, "", 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left", wraplength=app.px(self.W - 220))
        self.status.pack(side="left", fill="x", expand=True)
        app._button(foot, "취소", self.cancel).pack(side="right")
        app._button(foot, "저장", self.save, primary=True).pack(side="right", padx=(0, 8))

        app._label(body, f"이름  {s['name']}  (폴더 이름이라 여기서는 바꾸지 않습니다)", 10, fg=c["muted"], bg=c["bg"],
                   anchor="w").pack(fill="x")
        app._label(body, "설명 — 에이전트가 이 스킬을 언제 쓸지 판단하는 문장입니다", 10, bold=True, bg=c["bg"],
                   anchor="w").pack(fill="x", pady=(10, 3))
        self.desc_text = self._text_box(body, height=3)
        self.desc_text.frame.pack(fill="x")  # type: ignore[attr-defined]
        self.manual_var = tk.BooleanVar(value=self.orig[1])
        app._check(body, "수동 호출 전용 (사용자가 /이름으로 직접 부를 때만 사용)", self.manual_var).pack(fill="x", pady=(8, 0))
        app._label(body, "본문 — 스킬이 따를 지시 (마크다운)", 10, bold=True, bg=c["bg"], anchor="w").pack(fill="x", pady=(10, 3))
        self.body_text = self._text_box(body, height=10, mono=True)
        self.body_text.frame.pack(fill="both", expand=True)  # type: ignore[attr-defined]
        self.desc_text.insert("1.0", self.orig[0])
        self.body_text.insert("1.0", self.orig[2].rstrip("\n"))
        for t in (self.desc_text, self.body_text):
            t.edit_reset()  # Ctrl+Z must not undo the initial fill
        self.win.bind("<Control-s>", lambda e: self.save())
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.body_text.focus_set()

    def current(self) -> tuple[str, bool, str]:
        return (" ".join(self.desc_text.get("1.0", "end").split()), self.manual_var.get(), self.body_text.get("1.0", "end").rstrip("\n"))

    def dirty(self) -> bool:
        if not self.editing:
            return False
        desc, manual, body = self.current()
        return (desc, manual, body) != (" ".join(self.orig[0].split()), self.orig[1], self.orig[2].rstrip("\n"))

    def discard_ok(self) -> bool:
        """True unless there are unsaved edits the user wants to keep."""
        return not self.dirty() or messagebox.askyesno("저장하지 않은 변경", "저장하지 않은 변경이 있습니다. 버릴까요?", parent=self.win)

    def cancel(self) -> None:
        if self.discard_ok():
            self.win.unbind("<Escape>")
            self.win.unbind("<Control-s>")
            self.editing = False
            self.show_page("SKILL.md")

    def save(self) -> None:
        desc, manual, body = self.current()
        try:
            backup = mgr.save_skill_md(self.s["path"], desc, manual, body)
        except (mgr.ManagerError, OSError) as e:
            self.status.config(text=str(e), fg=self.app.c["danger"])
            return
        app, agent, path = self.app, self.s["agent"], self.s["path"]
        app.say("저장했습니다" + (f" (이전 파일: {backup.name})" if backup else " (바뀐 내용이 없습니다)"), ms=6000)
        self.editing = False
        app.load()  # the list (note, manual badge, search text) follows the file
        new = next((x for x in app.data.get(agent, []) if x["path"] == path), self.s)
        app.detail(new, page="SKILL.md")  # reopen on the fresh data, back in read mode

    # ---- the rest -------------------------------------------------------------------------------
    def facts(self) -> list[tuple[str, str]]:
        app, s = self.app, self.s
        p = s["path"]
        where = tilde(p) + (f"\n→ {tilde(mgr.target_of(p))}" if mgr.is_link(p) else "")
        agents = [a for a in AGENTS if any(x["name"] == s["name"] for x in app.data.get(a, []))]
        files = sorted(str(f.relative_to(p)) for f in p.rglob("*") if f.is_file() and ".git" not in f.parts)
        shown = ", ".join(files[:8]) + (f" 외 {len(files) - 8}개" if len(files) > 8 else "")
        facts = [("카테고리", f"{s['cat']} — {ABOUT.get(s['cat'], '')}"), ("출처", s["origin"])]
        if s["repo"]:
            facts += [("저장소", mgr.repo_name(s["repo"])), ("버전", mgr.version(s["repo"]))]
        facts += [
            ("호출", "사용자가 /이름으로 직접 불러야 함 (수동)" if s["manual"] else "에이전트가 상황에 맞으면 스스로 사용"),
            ("설치된 에이전트", ", ".join(agents) or "-"),
            ("위치", where),
            (f"파일 {len(files)}개", shown or "-"),
        ]
        return facts

    def delete(self) -> None:
        if not self.discard_ok():
            return
        before = self.app.data
        self.app.delete(self.s)
        if self.app.data is not before:  # the list was reloaded, so the skill is gone
            self.close()

    def close(self) -> None:
        if self.win.winfo_exists() and self.discard_ok():
            self.win.destroy()


def startup_error_text(e: BaseException) -> str:
    if isinstance(e, ModuleNotFoundError) and e.name:
        return (
            f"필요한 모듈 '{e.name.split('.')[0]}'을(를) 찾을 수 없어 시작하지 못했습니다.\n\n"
            "Claude 앱 안이 아닌 일반 터미널(PowerShell)에서 아래를 실행한 뒤 다시 시작하세요. "
            "앱 안의 터미널로 설치하면 그 앱만 볼 수 있는 폴더에 설치됩니다.\n\n"
            f'python -m pip install -r "{HERE / "requirements.txt"}"'
        )
    return f"시작하지 못했습니다.\n\n{type(e).__name__}: {e}\n\n자세한 내용: {mgr.STORE / 'error.log'}"


def report_startup_failure(e: BaseException) -> None:
    """pythonw has no console, so a failed start would look like "nothing happened": say why, and keep the traceback."""
    import traceback

    with contextlib.suppress(OSError):
        mgr.STORE.mkdir(parents=True, exist_ok=True)
        (mgr.STORE / "error.log").write_text("".join(traceback.format_exception(e)), encoding="utf-8")
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("SkillDesk를 시작하지 못했습니다", startup_error_text(e), parent=root)
    root.destroy()


if __name__ == "__main__":
    if "--tray-child" in sys.argv:
        tray_child()
    elif "--check" in sys.argv:
        check()
    elif "--make-icon" in sys.argv:
        make_ico(HERE / "skill_viewer.ico")
    elif "--make-icns" in sys.argv:
        make_icns(HERE / "skill_viewer.icns")
    elif not plat.already_running():
        try:
            App().run(hidden="--hidden" in sys.argv)
        except Exception as e:  # noqa: BLE001 - whatever stopped the start: show it instead of dying silently
            report_startup_failure(e)
    elif "--hidden" not in sys.argv:  # started by hand while a copy is already up (maybe in the tray): bring its window forward
        plat.ask_running_copy_to_show()

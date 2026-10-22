"""python -m unittest test_viewer -v

Drives the panel in-process (no synthetic mouse/keyboard input) against temp agent folders, so the real
~/.claude, ~/.codex and ~/.gemini are only read for plugin/built-in skills and never written.
"""

import ctypes
import json
import inspect
import os
import re
import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import font as tkfont
from unittest import mock

from PIL import ImageGrab

import install
import platform_support as plat
import skill_harness as hn
import skill_manager as mgr
import skill_update as upd
import skill_viewer as v

SHOTS = Path(tempfile.gettempdir())


def write_skill(d: Path, desc: str = "does a thing") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: {desc}\n---\nbody\n", encoding="utf-8")


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def texts(w):
    return [x.cget("text") for x in walk(w) if isinstance(x, tk.Label)]


def open_menu(app, s, x, y):
    """Open a skill's ⋯ menu that stays open while a test inspects it. Click-away closing listens for the menu
    losing focus, which other test windows grabbing focus would trigger at random."""
    app.row_menu(s, x, y)
    app.popup.win.unbind("<FocusOut>")
    app.root.update()


def usage_line(tid: str, skill: str, when) -> str:
    block = {"type": "tool_use", "id": tid, "name": "Skill", "input": {"skill": skill}}
    return json.dumps({"type": "assistant", "timestamp": when.isoformat(), "message": {"content": [block]}}, separators=(",", ":"))


def find(app: v.App, agent: str, name: str) -> dict | None:
    return next((s for s in app.data[agent] if s["name"] == name), None)


class PanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        t = cls.tmp = Path(cls._tmp.name).resolve()
        cls._patches = [
            mock.patch.object(mgr, "STORE", t / "store"),
            mock.patch.object(mgr, "AGENT_DIRS", {"Claude Code": t / "claude", "Codex": t / "codex", "Antigravity": t / "agy"}),
            mock.patch.object(mgr, "UPDATER", t / "updater.ps1"),
            mock.patch.object(mgr, "CLAUDE_HOME", t / "claudehome"),  # no real plugins, and the usage logs below
            mock.patch.object(mgr, "open_in_editor"),
        ]
        for p in cls._patches:
            p.start()
        write_skill(t / "codex/legacy")  # a real folder the user made by hand
        # so the Claude tab has several categories and a read-only skill even on a clean CI machine without plugins
        write_skill(t / "claude/tdd")
        write_skill(t / "claude/synced/app/grilling")  # nested = synced by the desktop app = view only
        # Claude Code logs: tdd 3x and gitskill 1x this month, tdd 1x last month, plus a built-in command
        now = datetime.now().astimezone()
        cls.this_month, last = now.strftime("%Y-%m"), now.replace(day=1) - timedelta(days=2)
        cls.last_month = last.strftime("%Y-%m")
        lines = [usage_line(f"t{i}", "tdd", now) for i in range(3)] + [usage_line("g1", "gitskill", now), usage_line("old", "tdd", last)]
        lines.append(
            json.dumps(
                {"type": "user", "timestamp": now.isoformat(), "uuid": "u1", "message": {"content": "<command-name>/clear</command-name>"}}
            )
        )
        (t / "claudehome/projects/p").mkdir(parents=True)
        (t / "claudehome/projects/p/s.jsonl").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        cls.repo = t / "gitrepo"  # a git-backed skill linked into Claude Code
        write_skill(cls.repo / "skills/gitskill")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q", str(cls.repo)], check=True, capture_output=True)
        subprocess.run([*git, "add", "-A"], cwd=cls.repo, check=True, capture_output=True)
        subprocess.run([*git, "commit", "-qm", "init"], cwd=cls.repo, check=True, capture_output=True)
        mgr.install([cls.repo / "skills/gitskill"], ["Claude Code"])
        cls.app = v.App(with_tray=plat.WIN, auto_update=False)  # the macOS tray is a child process; CI has no menu bar to show it in
        cls.app.INSTANCE = f"skill-catalog-test-{os.getpid()}"  # not "skilldesk": an app running on this PC must not get this app's notes
        cls.app.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.quit()
        for d in mgr.AGENT_DIRS.values():  # unlink junctions first so cleanup can't walk into a clone through them
            for p in d.iterdir() if d.is_dir() else []:
                if mgr.is_link(p):
                    plat.unlink_dir(p)
        for p in cls._patches:
            p.stop()
        cls._tmp.cleanup()

    def settle(self, cond, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            self.app.root.update()
            self.app._pump()
            if cond():
                return
            time.sleep(0.05)
        self.fail("timed out waiting for the UI")

    def shot(self, win, name):
        win.update()
        time.sleep(0.7)  # let Windows finish its window fade-in, or the grab is half transparent
        win.update()
        box = (win.winfo_rootx(), win.winfo_rooty(), win.winfo_rootx() + win.winfo_width(), win.winfo_rooty() + win.winfo_height())
        if plat.WIN:  # macOS asks for screen-recording permission, which a test run can not give
            ImageGrab.grab(bbox=box).save(SHOTS / f"{name}.png")
        return box

    def rows(self):
        return len(self.app.shown)

    # ---- window, tray, list ---------------------------------------------------------------
    def test_01_centered_and_tray_toggle(self):
        app = self.app
        if plat.WIN:
            self.assertTrue(app.tray is not None and app.tray.HAS_MENU)
        left, top, right, bottom = plat.work_area(app.root)
        app.place()
        app.root.update()
        x, y, x2, y2 = self.shot(app.root, "t1_default")
        self.assertTrue(app.visible and left <= x and x2 <= right and top <= y and y2 <= bottom)
        # the window sits in the middle of the usable screen (within a few pixels, title bar and borders included)
        self.assertAlmostEqual((x + x2) / 2, (left + right) / 2, delta=app.px(24))
        self.assertAlmostEqual((y + y2) / 2, (top + bottom) / 2, delta=app.px(40))
        moved = app.root.winfo_x() + 37  # the user drags it somewhere: hiding and showing must not snap it back
        app.root.geometry(f"+{moved}+{app.root.winfo_y()}")
        app.root.update()
        app._last_toggle = 0.0  # tests toggle faster than a double-click
        app.events.put("toggle")
        app._pump()
        self.assertTrue(not app.visible and app.root.state() == "withdrawn")
        app._last_toggle = 0.0  # tests toggle faster than a double-click
        app.events.put("toggle")
        app._pump()
        self.assertTrue(app.visible and app.root.state() == "normal")
        self.assertEqual(app.root.winfo_x(), moved)

    def test_01c_starting_the_app_again_brings_the_hidden_window_back(self):
        app = self.app
        app.hide()
        app._pump()
        self.assertFalse(app.visible)  # no note: it stays in the tray
        plat.ask_running_copy_to_show(app.INSTANCE)
        app._pump()
        app.root.update()
        self.assertTrue(app.visible and app.root.state() == "normal")
        app.hide()
        app._pump()
        self.assertFalse(app.visible)  # the note was read once: it does not bring the window back again
        app.show()

    def test_01d_dialogs_open_centered_over_the_panel_and_stay_on_screen(self):
        app = self.app
        app.place()
        app.root.update()
        app.add_dialog()
        win = app.dialog.win
        win.update()
        left, top, right, bottom = plat.work_area(app.root)
        self.assertTrue(left <= win.winfo_x() and win.winfo_x() + win.winfo_width() <= right)
        self.assertTrue(top <= win.winfo_y() and win.winfo_y() + win.winfo_height() <= bottom)
        centre = lambda w: (w.winfo_x() + w.winfo_width() / 2, w.winfo_y() + w.winfo_height() / 2)  # noqa: E731
        (dx, dy), (rx, ry) = centre(win), centre(app.root)
        self.assertLessEqual(abs(dx - rx), app.px(60))  # horizontally centered over the panel
        self.assertLessEqual(abs(dy - ry), app.px(80))
        app.dialog.close()

    def test_01e_small_screens_keep_the_whole_window_and_dialogs_inside_the_work_area(self):
        app = self.app
        area = (0, 0, 1024, 700)  # a small screen (like a CI machine's): the title bar and borders must fit too
        deco = app.px(48)  # generous title bar + borders
        with mock.patch.object(plat, "work_area", return_value=area):
            app.place()
            app.root.update()
            self.assertGreaterEqual(app.root.winfo_y(), area[1])
            self.assertLessEqual(app.root.winfo_y() + app.root.winfo_height() + deco // 2, area[3])
            app.add_dialog()
            win = app.dialog.win
            win.update()
            self.assertGreaterEqual(win.winfo_y(), area[1])
            self.assertLessEqual(win.winfo_y() + win.winfo_height() + deco // 2, area[3])
            self.assertLessEqual(win.winfo_x() + win.winfo_width(), area[2])
            app.dialog.close()
        app.place()

    def test_01b_window_has_minimize_and_close_but_no_toolwindow_style(self):
        app = self.app
        app.root.update()
        if plat.WIN:
            hwnd = ctypes.windll.user32.GetParent(app.root.winfo_id())
            style, ex = ctypes.windll.user32.GetWindowLongW(hwnd, -16), ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            self.assertTrue(style & 0x20000, "WS_MINIMIZEBOX missing")  # the button the user asked for
            self.assertFalse(ex & 0x80, "WS_EX_TOOLWINDOW would hide the minimize button")
        app.root.iconify()  # minimizing sends the window to the tray instead of leaving it on the taskbar
        self.settle(lambda: not app.visible)
        self.assertEqual(app.root.state(), "withdrawn")
        app._last_toggle = 0.0  # tests toggle faster than a double-click
        app.events.put("toggle")
        app._pump()
        self.assertTrue(app.visible and app.root.state() == "normal")

    def test_01c_window_is_not_always_on_top(self):
        app = self.app
        app.show()  # opening from the tray pops to the front once, then must not stay topmost
        app.root.update()
        self.assertFalse(app.root.attributes("-topmost"))
        app.add_dialog()
        app.dialog.win.update()
        self.assertFalse(app.dialog.win.attributes("-topmost"))
        app.dialog.close()

    def test_app_name_is_the_same_everywhere(self):
        app = self.app
        self.assertEqual(app.root.title(), install.APP_NAME)  # window title = Start menu / Spotlight name
        self.assertIn(install.APP_NAME, texts(app.root))  # header

    def test_02_origin_labels(self):
        legacy = find(self.app, "Codex", "legacy")
        self.assertEqual((legacy["origin"], legacy["managed"], legacy["repo"]), ("로컬", True, None))
        g = find(self.app, "Claude Code", "gitskill")
        self.assertTrue(g["origin"].startswith("git · ") and g["repo"] == self.repo.resolve() and g["managed"])

    def test_03_tabs_search_rail(self):
        app = self.app
        app.set_agent("Claude Code")
        app.query.set("gitskill")
        app.root.update()
        self.assertEqual(self.rows(), 1)
        app.query.set("zzzz-no-match")
        app.root.update()
        self.assertEqual(self.rows(), 0)
        app.query.set("git ·")
        app.root.update()  # origin text is searchable too
        self.assertIn("gitskill", [s["name"] for s in app.shown])
        self.assertGreater(self.rows(), 0)
        app.query.set("")
        total = self.rows()
        cats = sorted({s["cat"] for s in app.data["Claude Code"]})
        self.assertGreater(len(cats), 1)
        app.pick(cats[0])  # clicking a category in the rail lists only that category
        self.assertTrue(all(s["cat"] == cats[0] for s in app.shown))
        self.assertTrue(0 < self.rows() < total)
        self.shot(app.root, "t3_category")
        app.pick(None)
        self.assertEqual(self.rows(), total)
        app.pick(cats[0])
        app.query.set("zzzz-no-match")  # the picked category has no match: fall back to all, not an empty rail
        self.assertIsNone(app.cat)
        app.query.set("")
        self.shot(app.root, "t3_list")
        app.copy("grilling")
        self.assertEqual(app.root.clipboard_get(), "grilling")

    def test_03a_every_category_has_its_own_color(self):
        app = self.app
        for theme in ("light", "dark"):
            app.theme = theme
            colors = [app.cat_colors(c) for c in v.CAT_COLORS]
            self.assertEqual(len(set(colors)), len(v.CAT_COLORS), theme)  # no two categories share a color pair
            self.assertEqual(len({fg for fg, _ in colors}), len(v.CAT_COLORS), theme)
        app.theme = "light"

    def test_03b_long_names_are_shortened_not_wrapped(self):
        app = self.app
        self.assertEqual(app._fit("tdd", 200), "tdd")
        long = "superpowers:finishing-a-development-branch"
        out = app._fit(long, 150)
        self.assertTrue(out.endswith("…") and out != long and app.name_font.measure(out) <= 150, out)
        self.assertEqual(app._fit(long, 10_000), long)
        self.assertTrue(app._fit("x" * 500, 1))  # never loops forever or returns nothing

    def test_01c_tray_double_click_does_not_undo_itself(self):
        app = self.app
        app.hide()
        app._last_toggle = 0.0
        app.events.put("toggle")  # a double-click on the tray icon sends two clicks at once
        app.events.put("toggle")
        app._pump()
        self.assertTrue(app.visible and app.root.state() == "normal")

    def test_03c_category_click_reuses_built_rows(self):
        app = self.app
        app.set_agent("Claude Code")
        app.pick(None)
        cats = sorted({s["cat"] for s in app.shown})
        self.assertGreater(len(cats), 1)
        built = app.sections[cats[0]]
        app.pick(cats[1])
        self.assertEqual(built.winfo_manager(), "")  # hidden, not destroyed
        app.pick(cats[0])
        self.assertIs(app.sections[cats[0]], built)  # same widgets as before, nothing rebuilt
        self.assertEqual(built.winfo_manager(), "pack")
        self.assertTrue(all(s["cat"] == cats[0] for s in app.shown))
        app.pick(None)  # everything shown again: the hidden groups come back in category order
        order = [c for c in [n for n, _ in v.CATEGORIES] + [v.OTHER] if c in app.sections]
        self.assertEqual(app.body.pack_slaves(), [app.sections[c] for c in order])
        app.pick(cats[0])
        app.query.set("zzzz-no-match")  # a search changes the rows, so the groups must be rebuilt
        self.assertIsNot(app.sections.get(cats[0]), built)
        self.assertFalse(built.winfo_exists())
        app.query.set("")
        app.pick(None)

    def test_03d_load_redraws_only_when_something_changed(self):
        app = self.app
        with mock.patch.object(app, "render") as render:
            app.load()
            render.assert_not_called()  # same skills as on screen: leave the list alone
            app.data["Codex"] = [{"name": "stale"}]
            app.load()
            render.assert_called_once()
        app.render()

    def test_03e_star_list_is_per_agent_saved_and_always_in_the_rail(self):
        app = self.app
        app.set_agent("Claude Code")
        app.query.set("")
        names = texts(app.rail)
        # the star entry is first and shows at 0; on the Claude Code tab the usage entry sits between it and "전체"
        self.assertEqual((names[1], names[3], names[5]), (v.STAR, v.USAGE, "전체"))
        self.assertEqual(names[2], "0")
        gs = find(app, "Claude Code", "gitskill")
        self.assertIn("★ 자주 쓰는 스킬에 추가", [label for label, _, _ in app.menu_items(gs)])
        icon = app.icons["gitskill"][0]
        self.assertEqual(icon.cget("text"), "☆")  # every row has the icon, hollow until starred
        before = dict(app.sections)
        app.toggle_pin(gs)
        self.assertEqual(icon.cget("text"), "★")
        self.assertEqual(app.sections, before)  # the icon was repainted in place; no group was rebuilt
        self.assertEqual(mgr.load_settings()["pins"]["Claude Code"], ["gitskill"])  # saved
        self.assertEqual(v.load_pins()["Claude Code"], {"gitskill"})
        self.assertEqual(app.pins["Codex"], set())  # another agent's list is separate
        self.assertIn("★ 자주 쓰는 스킬에서 해제", [label for label, _, _ in app.menu_items(gs)])
        app.pick(v.STAR)
        self.assertEqual([s["name"] for s in app.shown], ["gitskill"])
        self.assertIn(gs["cat"], texts(app.body))  # the star list names each skill's own category
        app.toggle_pin(gs)
        self.assertEqual((app.shown, app.cat), ([], v.STAR))  # stays on the star list; nothing starred
        self.assertIn("아직 지정한 스킬이 없습니다", "".join(texts(app.body)))
        self.assertEqual(texts(app.rail)[1], v.STAR)  # the entry stays at 0
        app.set_agent("Claude Code")

    def test_03g_settings_dialog_chooses_which_agent_tabs_exist(self):
        app = self.app
        app.set_agent("Codex")
        app.settings_dialog()
        app.root.update()
        d = app.dialog
        self.assertEqual(set(d.vars), set(v.AGENTS))
        d.vars["Codex"].set(False)  # untick: the tab and its list go away, the panel falls back to the first tab
        app.root.update()
        try:
            self.assertEqual(app.agents, ["Claude Code", "Antigravity"])
            self.assertEqual(mgr.load_settings()["agents"], ["Claude Code", "Antigravity"])
            self.assertEqual(app.agent.get(), "Claude Code")
            self.assertEqual([t for t in texts(app.tabs) if "Codex" in t], [])
            self.assertNotIn("Codex", app.data)
            self.assertTrue(d.win.winfo_exists())  # the dialog stays open while the panel is rebuilt
            self.assertEqual([a for a, var in app.dialog.vars.items() if var.get()], app.agents)
            self.shot(d.win, "t3g_settings")
            for a in ("Claude Code", "Antigravity"):  # the last way to an empty panel is refused
                d.vars[a].set(False)
            self.assertEqual(len(app.agents), 1)
            self.assertIn("하나 이상", d.status.cget("text"))
            self.assertTrue(any(var.get() for var in d.vars.values()))
            app.add_dialog()  # the add dialog only offers the shown agents
            self.assertEqual(list(app.dialog.agents), app.agents)
            app.dialog.close()
        finally:
            app.settings_dialog()
            for var in app.dialog.vars.values():
                var.set(True)
            app.dialog.close()
            app.root.update()
        self.assertEqual(app.agents, list(v.AGENTS))
        self.assertIn("Codex", app.data)
        app.set_agent("Claude Code")

    @unittest.skipUnless(plat.WIN, "the one synthetic click is only checked on Windows")
    def test_03f_clicking_the_star_icon_stars_the_skill_without_copying_its_name(self):
        app = self.app
        app.set_agent("Claude Code")
        icon = app.icons["gitskill"][0]
        app.copy("sentinel")
        app.root.update()  # a widget that isn't mapped yet ignores events
        icon.event_generate("<Button-1>", x=2, y=2)
        self.assertEqual((icon.cget("text"), app.root.clipboard_get()), ("★", "sentinel"))
        self.assertIn("gitskill", app.pins["Claude Code"])
        icon.event_generate("<Button-1>", x=2, y=2)  # a second click takes it back
        self.assertEqual(icon.cget("text"), "☆")

    def test_03g_most_used_skills_by_month(self):
        app = self.app
        app.set_agent("Claude Code")
        app.pick(v.USAGE)
        self.settle(lambda: app.usage is not None)
        self.assertEqual([s["name"] for s in app.shown], ["tdd", "gitskill"])  # most called first; /clear is not a skill
        self.assertEqual((app.calls["tdd"], app.calls["gitskill"]), (3, 1))
        shown = texts(app.body)
        self.assertIn("3회", shown)
        self.assertIn(v.month_label(self.this_month), shown)  # the month picker
        rail = texts(app.rail)
        self.assertEqual(rail[rail.index(v.USAGE) + 1], "2")  # two skills were used this month
        self.shot(app.root, "t3g_usage")
        bad = low_contrast(app, app.root, "usage light")
        app.set_usage_month(self.last_month)
        self.assertEqual([(s["name"], app.calls[s["name"]]) for s in app.shown], [("tdd", 1)])
        app.set_usage_month(v.usage.ALL)
        self.assertEqual(app.calls["tdd"], 4)
        app.toggle_theme()
        app.pick(v.USAGE)
        self.settle(lambda: app.shown)
        bad += low_contrast(app, app.root, "usage dark")
        app.toggle_theme()
        self.assertEqual(bad, [])
        app.set_usage_month(self.this_month)
        app.set_agent("Codex")  # only Claude Code's logs are read, so other agents have no such entry
        self.assertNotIn(v.USAGE, texts(app.rail))
        self.assertIsNone(app.cat)
        app.set_agent("Claude Code")

    def test_03i_idle_skills_are_ticked_then_deleted_together(self):
        app, claude = self.app, self.tmp / "claude"
        app.set_agent("Claude Code")
        app.query.set("")
        write_skill(claude / "stale-a")  # the logs never mention these four
        write_skill(claude / "stale-b")
        write_skill(claude / "stale-new")
        write_skill(self.tmp / "src/stale-link")
        mgr.install([self.tmp / "src/stale-link"], ["Claude Code"])
        old = datetime.now() - timedelta(days=200)
        born = lambda p: datetime.now() if Path(p).name == "stale-new" else old  # noqa: E731
        try:
            with mock.patch.object(mgr, "installed_on", side_effect=born):
                app.load()
                app.usage = None  # as at start-up: the logs are read on the first visit
                app.render()
                self.assertEqual(texts(app.rail)[-2:], [v.IDLE, "–"])  # last in the rail, after every category; not counted yet
                app.pick(v.IDLE)
                self.settle(lambda: app.usage is not None)
                app.root.update()
                # tdd and gitskill were called this month; stale-new was installed this month, so it is left out (for now)
                self.assertEqual([s["name"] for s in app.shown], ["grilling", "stale-a", "stale-b", "stale-link"])
                self.assertEqual(texts(app.rail)[-2:], [v.IDLE, "4"])
                self.assertEqual(app.idle_bar.winfo_manager(), "pack")
                body = texts(app.body)
                self.assertEqual(body.count("기록 없음"), 4)
                self.assertIn("앱 동기화 제공 스킬이라 여기서 삭제할 수 없습니다", body)  # view-only: no tick box, only the reason
                self.assertEqual(set(app.checks), {"stale-a", "stale-b", "stale-link"})
                self.assertTrue(any(t.startswith("대화 기록이") and "부터만 남아" in t for t in body))  # the test logs are minutes old
                app.log_start = datetime(2020, 1, 1)  # logs that reach back past the window: no note
                app._drop_usage_group()
                app.render(rebuild=False)
                self.assertFalse(any("부터만 남아" in t for t in texts(app.body)))
                self.shot(app.root, "t3i_idle")
                bad = low_contrast(app, app.root, "idle light")

                with mock.patch.object(v.messagebox, "askyesno") as ask:  # nothing ticked: no question, a hint instead
                    app.delete_checked()
                    ask.assert_not_called()
                self.assertEqual(app.footer.cget("text"), "먼저 지울 스킬에 체크하세요")
                app.toggle_check("stale-a")
                app.toggle_check("stale-link")
                self.assertEqual(app.checks["stale-a"][0].cget("text"), "☑")
                self.assertIn("2개 선택됨 · 아직 삭제하지 않았습니다", texts(app.idle_bar))
                self.assertTrue((claude / "stale-a").exists())  # ticking changes nothing on disk
                app.hide_new.set(False)  # include what was installed inside the window
                self.assertEqual([s["name"] for s in app.shown][-1], "stale-new")
                self.assertEqual(app.checked, {"stale-a", "stale-link"})  # the ticks survive the redraw
                app.hide_new.set(True)
                app.query.set("stale-link")  # a search must not untick what it hides
                self.assertEqual([s["name"] for s in app.shown], ["stale-link"])
                self.assertEqual(app.checked, {"stale-a", "stale-link"})
                self.assertIn("2개 선택됨 (검색에 가려진 1개 포함) · 아직 삭제하지 않았습니다", texts(app.idle_bar))
                app.query.set("")
                self.assertEqual(app.checks["stale-a"][-1].cget("text"), "☑")  # and it is ticked again when it comes back
                app.select_all()
                self.assertEqual(app.checked, {"stale-a", "stale-b", "stale-link"})  # not the view-only one
                app.clear_checks()
                self.assertEqual(texts(app.idle_bar)[0], "지울 스킬의 ☐를 눌러 체크하세요")
                app.toggle_theme()
                app.pick(v.IDLE)
                self.settle(lambda: app.shown)
                bad += low_contrast(app, app.root, "idle dark")
                app.toggle_theme()
                self.assertEqual(bad, [])

                app.toggle_check("stale-b")
                with mock.patch.object(v.messagebox, "askyesno", return_value=False):  # declined: everything stays
                    app.delete_checked()
                self.assertTrue((claude / "stale-b").exists())
                self.assertEqual(app.checked, {"stale-b"})
                app.toggle_check("stale-b")
                app.toggle_check("stale-a")
                app.toggle_check("stale-link")
                with mock.patch.object(v.messagebox, "askyesno", return_value=True) as ask:
                    app.delete_checked()
                question = ask.call_args.args[1]
                self.assertIn("2개를 삭제할까요", question)
                self.assertIn("stale-a  (휴지통으로 이동)", question)  # a real folder goes to the trash
                self.assertIn("stale-link  (연결만 해제)", question)  # a link is only unlinked
                self.assertFalse((claude / "stale-a").exists() or mgr.is_link(claude / "stale-link"))
                self.assertTrue((self.tmp / "src/stale-link/SKILL.md").exists())  # the original stays
                self.assertTrue(any("stale-a" in p.name for p in (mgr.STORE / "trash").iterdir()))
                self.assertEqual(app.checked, set())
                self.assertEqual([s["name"] for s in app.shown], ["grilling", "stale-b"])
                self.assertEqual(texts(app.rail)[-2:], [v.IDLE, "2"])
                self.assertIn("삭제됨: 2개", app.footer.cget("text"))

                saved = app.usage  # without any skill call in the logs every skill looks idle: no list, and the reason
                app.usage = {}
                app.render()
                self.assertIn("하나도 찾지 못했습니다", "".join(texts(app.body)))
                self.assertEqual(app.shown, [])
                app.usage = {self.this_month: Counter({s["name"]: 1 for s in app.data["Claude Code"]})}
                app.render()
                self.assertIn("정리할 스킬이 없습니다", "".join(texts(app.body)))
                self.assertIn("설치 2개월 미만 제외", "".join(texts(app.body)))  # the switch stays so the new ones can be looked at
                app.usage = saved
                app.render()
                app.pick(None)
                self.assertEqual(app.idle_bar.winfo_manager(), "")  # the bar belongs to the idle list only
                app.set_agent("Codex")  # only Claude Code's logs are read
                self.assertNotIn(v.IDLE, texts(app.rail))
        finally:
            for name in ("stale-a", "stale-b", "stale-new", "stale-link"):
                if (claude / name).exists() or mgr.is_link(claude / name):
                    mgr.remove("Claude Code", name)
            app.set_agent("Claude Code")
            app.load()

    def test_03j_the_delete_question_names_every_ticked_skill(self):
        app, claude = self.app, self.tmp / "claude"
        names = [f"bulk-{i:02d}" for i in range(14)]  # more than the dialog lists one per line
        for n in names:
            write_skill(claude / n)
        try:
            with mock.patch.object(mgr, "installed_on", return_value=datetime.now() - timedelta(days=200)):
                app.set_agent("Claude Code")
                app.query.set("")
                app.load()
                app.pick(v.IDLE)
                self.settle(lambda: app.usage is not None)
                app.root.update()
                app.select_all()
                app.query.set("bulk-13")  # the search hides 13 of the 14 ticked skills; they are still going to be deleted
                self.assertEqual(len(app.checked), 14)
                with mock.patch.object(v.messagebox, "askyesno", return_value=False) as ask:
                    app.delete_checked()
                question = ask.call_args.args[1]
                self.assertIn("14개를 삭제할까요", question)
                self.assertEqual([n for n in names if n not in question], [])  # every name is in the question
                self.assertTrue(all((claude / n).exists() for n in names))  # declined: nothing was deleted
        finally:
            app.query.set("")
            for n in names:
                if (claude / n).exists():
                    mgr.remove("Claude Code", n)
            app.set_agent("Claude Code")
            app.load()

    def test_03j_update_check_from_the_tray_brings_the_window_up_to_show_the_answer(self):
        app = self.app
        app.hide()
        with mock.patch.object(upd, "apply", return_value=upd.Result("up-to-date", "")):
            app.update_app()  # what the tray item sends
            self.settle(lambda: "최신 버전" in app.footer.cget("text"))
        self.assertTrue(app.visible)
        self.assertIn("SkillDesk는 최신 버전입니다", app.footer.cget("text"))
        app.footer.config(text=v.DEFAULT_HINT)  # later tests look at the footer

    def test_03h_app_updates_itself_and_restarts_without_surprises(self):
        app = self.app
        done = upd.Result("updated", "", "aaa1111", "bbb2222", ["Feat: 새 기능"])
        with (
            mock.patch.object(upd, "apply", return_value=done),
            mock.patch.object(upd, "restart") as restart,
            mock.patch.object(app, "quit") as quit_,
        ):
            self.assertFalse(app.pending_restart)
            app.auto_update(manual=True)
            self.settle(lambda: app.pending_restart)
            self.assertIn("새 버전 · 다시 시작", texts(app.actions))  # the header pill
            restart.assert_not_called()  # the window is open: the user decides when to restart
            app.toggle_theme()  # the header is rebuilt, the pill must survive
            self.assertIn("새 버전 · 다시 시작", texts(app.actions))
            app.toggle_theme()
            app.root.update()  # a widget that isn't mapped yet ignores events
            pill = next(w for w in walk(app.actions) if isinstance(w, tk.Label) and w.cget("text") == "새 버전 · 다시 시작")
            pill.event_generate("<Button-1>", x=2, y=2)
            app.root.update()
            restart.assert_called_once_with(hidden=False)
            quit_.assert_called_once()
            with mock.patch.object(upd, "apply") as again:  # an update is already on disk: no second check
                app.auto_update()
                app.auto_update(manual=True)
                again.assert_not_called()
            restart.reset_mock()
            app.hide()  # nobody is looking: switch right away, and start hidden again
            app._update_done(done)
            restart.assert_called_once_with(hidden=True)
            app.show()
        app.pending_restart = None
        if app.restart_pill:
            app.restart_pill.destroy()
            app.restart_pill = None

    def test_03h2_a_data_only_update_does_not_ask_for_a_restart(self):
        app = self.app
        done = upd.Result("updated", "", "aaa1111", "bbb2222", ["Chore: 추천 목록 갱신"], data_only=True)
        with mock.patch.object(upd, "restart") as restart, mock.patch.object(app, "quit") as quit_:
            app._update_done(done)
            app.hide()
            app._update_done(done)  # even with the window hidden
            app.show()
        restart.assert_not_called()
        quit_.assert_not_called()
        self.assertFalse(app.pending_restart)
        self.assertNotIn("새 버전 · 다시 시작", texts(app.actions))

    def test_03i_update_messages_and_the_on_off_switch(self):
        app = self.app
        with mock.patch.object(upd, "apply", return_value=upd.Result("up-to-date", "", "a", "a")) as apply:
            app.auto_update()  # automatic and nothing new: silent
            self.settle(lambda: apply.called)
            self.assertNotIn("최신", app.footer.cget("text"))
            app.auto_update(manual=True)
            self.settle(lambda: apply.call_count == 2 and "최신" in app.footer.cget("text"))
        failed = upd.Result("failed", "새 버전을 확인하는 중 실패해 이전 버전으로 되돌렸습니다: x", "a", "c")
        app._update_done(failed)
        self.assertIn("되돌렸습니다", app.footer.cget("text"))
        app.footer.config(text="")
        app._update_done(failed)  # the same broken version is not reported again every few hours
        self.assertEqual(app.footer.cget("text"), "")
        app._update_done(failed, manual=True)  # unless the user asked
        self.assertIn("되돌렸습니다", app.footer.cget("text"))
        self.assertTrue(mgr.load_settings().get("auto_update", True))
        app.toggle_auto_update()
        self.assertFalse(mgr.load_settings()["auto_update"])
        with mock.patch.object(upd, "apply") as off:
            app.auto_update()
            app.root.update()
            off.assert_not_called()  # switched off: the automatic check does nothing
        app.toggle_auto_update()
        self.assertTrue(mgr.load_settings()["auto_update"])

    def test_04_menu_entries(self):
        app = self.app

        def labels(s):
            return [label for label, _, kind in app.menu_items(s) if kind != "-"]

        legacy = labels(find(app, "Codex", "legacy"))
        self.assertEqual(legacy[0], "상세 보기")
        self.assertIn("삭제…", legacy)
        self.assertIn("SKILL.md 수정…", legacy)
        self.assertIn("이 스킬의 저장소 업데이트 확인", labels(find(app, "Claude Code", "gitskill")))
        ro = next(s for s in app.data["Claude Code"] if not s["managed"])
        self.assertNotIn("삭제…", labels(ro))  # plugin / app-synced skills can't be deleted from here

        # the themed popup: one at a time, runs the picked action, closes on pick and on Esc
        open_menu(app, find(app, "Codex", "legacy"), 200, 200)
        app.root.update()
        first = app.popup
        self.assertEqual([r.cget("text") for r in first.rows][0], "상세 보기")
        self.shot(first.win, "t4_popup")
        open_menu(app, find(app, "Codex", "legacy"), 220, 220)
        app.root.update()  # a click only reaches a window that is on screen
        self.assertFalse(first.win.winfo_exists())
        next(r for r in app.popup.rows if r.cget("text") == "이름 복사").event_generate("<Button-1>")
        app.root.update()
        self.assertEqual(app.root.clipboard_get(), "legacy")
        self.assertFalse(app.popup.win.winfo_exists())
        open_menu(app, find(app, "Codex", "legacy"), 200, 200)
        app.root.update()
        app.popup.win.event_generate("<Escape>")
        app.root.update()
        self.assertFalse(app.popup.win.winfo_exists())

    def test_04b_detail_page(self):
        app = self.app
        app.detail(find(app, "Claude Code", "gitskill"))
        app.root.update()
        d = app.detail_win
        shown = texts(d.win)
        for expected in ("gitskill", "저장소", "버전", "설치된 에이전트", "위치", "SKILL.md", "삭제…", "업데이트 확인"):
            self.assertTrue(any(expected in t for t in shown), expected)
        self.assertTrue(any(t.startswith("git · ") for t in shown))
        d.show_page("SKILL.md")
        self.assertIn("description: does a thing", d.text.get("1.0", "end"))
        self.assertEqual(str(d.text.cget("state")), "disabled")  # a viewer until 수정 is pressed
        d.show_page("정보")
        with mock.patch.object(v.plat, "open_folder") as opened:
            next(w for w in walk(d.win) if isinstance(w, tk.Label) and w.cget("text") == "폴더 열기").event_generate("<Button-1>")
        opened.assert_called_once()
        buttons = [w for w in walk(d.win) if isinstance(w, tk.Label) and str(w.cget("cursor")) == "hand2"]
        for b in buttons:  # nothing in the action rows is cut off
            self.assertLessEqual(b.winfo_reqwidth(), b.winfo_width() + 1, b.cget("text"))
            self.assertLessEqual(b.winfo_x() + b.winfo_width(), b.master.winfo_width(), b.cget("text"))
        self.shot(d.win, "t4b_detail")
        ro = next(s for s in app.data["Claude Code"] if not s["managed"])
        app.detail(ro)
        app.root.update()
        self.assertFalse(d.win.winfo_exists())  # one detail window at a time
        self.assertNotIn("삭제…", texts(app.detail_win.win))
        app.detail_win.close()

    def test_04d_skill_md_view_is_rendered_and_the_edit_form_saves(self):
        app = self.app
        legacy = find(app, "Codex", "legacy")
        path = legacy["path"]
        (path / "SKILL.md").write_text(
            "---\nname: legacy\ndescription: does a thing\nallowed-tools: Read\n---\n"
            "# Heading\n\nSome **bold** text and `code`.\n\n- first item\n- second item\n\n```\nprint('x')\n```\n",
            encoding="utf-8",
        )
        app.load()
        app.detail(find(app, "Codex", "legacy"), page="SKILL.md")
        app.root.update()
        d = app.detail_win
        # readable view: headings, bullets, code blocks and inline code get their own tags; the file is hidden behind them
        t = d.text
        shown = t.get("1.0", "end")
        self.assertIn("•  first item", shown)
        self.assertNotIn("**bold**", shown)
        self.assertNotIn("`code`", shown)
        self.assertTrue(t.tag_ranges("h1") and t.tag_ranges("bold") and t.tag_ranges("inline") and t.tag_ranges("code"))
        self.assertTrue(t.tag_ranges("meta"))  # the front matter, muted
        self.assertEqual(str(t.cget("state")), "disabled")
        # no tag may be italic (the app never is)
        for tag in ("h1", "h2", "h3", "bold", "code", "inline", "meta", "bullet", "quote"):
            font = t.tag_cget(tag, "font")
            if font:
                self.assertEqual(tkfont.Font(root=app.root, font=font).actual("slant"), "roman", tag)
        self.shot(d.win, "t4d_skill_md_view")

        # a click on 수정 opens the form with the file's current values
        with mock.patch.object(v.messagebox, "askokcancel", return_value=True):
            d.edit_btn.event_generate("<Button-1>", x=2, y=2)
            app.root.update()
        self.assertTrue(d.editing)
        self.assertEqual(d.desc_text.get("1.0", "end").strip(), "does a thing")
        self.assertFalse(d.manual_var.get())
        self.assertIn("# Heading", d.body_text.get("1.0", "end"))
        self.assertFalse(d.dirty())
        self.shot(d.win, "t4d_skill_md_edit")

        # edit, then 저장: the file changes, other keys survive, the old file is backed up, the list follows
        d.desc_text.delete("1.0", "end")
        d.desc_text.insert("1.0", "새로 쓴 설명")
        d.manual_var.set(True)
        d.body_text.insert("end", "\nAdded line.")
        self.assertTrue(d.dirty())
        trash_before = len(list((mgr.STORE / "trash").glob("*_SKILL.md"))) if (mgr.STORE / "trash").exists() else 0
        d.save()
        app.root.update()
        out = (path / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('description: "새로 쓴 설명"', out)
        self.assertIn("allowed-tools: Read", out)
        self.assertIn("disable-model-invocation: true", out)
        self.assertIn("Added line.", out)
        self.assertEqual(len(list((mgr.STORE / "trash").glob("*_SKILL.md"))), trash_before + 1)
        fresh = find(app, "Codex", "legacy")
        self.assertEqual((fresh["desc"], fresh["manual"]), ("새로 쓴 설명", True))
        d2 = app.detail_win
        self.assertIsNot(d2, d)
        self.assertEqual((d2.page, d2.editing), ("SKILL.md", False))  # reopened on the fresh data, in read mode
        self.assertIn("수동", texts(d2.win))  # the manual badge appeared

        # validation: an empty description is refused and the file is left alone
        with mock.patch.object(v.messagebox, "askokcancel", return_value=True):
            d2.begin_edit()
        before = (path / "SKILL.md").read_bytes()
        d2.desc_text.delete("1.0", "end")
        d2.save()
        self.assertIn("설명", d2.status.cget("text"))
        self.assertEqual((path / "SKILL.md").read_bytes(), before)

        # unsaved edits are not thrown away silently: cancel, tab switch and close all ask first
        d2.desc_text.insert("1.0", "temporary")
        with mock.patch.object(v.messagebox, "askyesno", return_value=False) as ask:
            d2.cancel()
            d2.show_page("정보")
            d2.close()
            self.assertEqual(ask.call_count, 3)
        self.assertTrue(d2.editing and d2.win.winfo_exists() and d2.page == "SKILL.md")
        with mock.patch.object(v.messagebox, "askyesno", return_value=True):
            d2.cancel()  # confirmed: back to the read-only view, nothing written
        self.assertEqual((d2.editing, d2.page), (False, "SKILL.md"))
        self.assertEqual((path / "SKILL.md").read_bytes(), before)
        d2.close()

    def test_04e_edit_form_respects_who_owns_the_skill(self):
        app = self.app
        ro = next(s for s in app.data["Claude Code"] if not s["managed"])  # plugin / app-synced: view only
        app.detail(ro, page="SKILL.md")
        app.root.update()
        self.assertFalse(hasattr(app.detail_win, "edit_btn"))
        app.detail_win.begin_edit()
        self.assertFalse(app.detail_win.editing)
        app.detail_win.close()
        gs = find(app, "Claude Code", "gitskill")  # linked into a git clone: ask before touching the clone's file
        app.detail(gs, page="SKILL.md")
        with mock.patch.object(v.messagebox, "askokcancel", return_value=False) as ask:
            app.detail_win.begin_edit()
            ask.assert_called_once()
            self.assertIn("git", ask.call_args[0][1])
        self.assertFalse(app.detail_win.editing)
        app.detail_win.close()
        labels = [label for label, _, _ in app.menu_items(find(app, "Codex", "legacy"))]
        self.assertIn("SKILL.md 수정…", labels)
        self.assertIn("SKILL.md 보기", [label for label, _, _ in app.menu_items(ro)])

    def test_04c_themed_checkbox(self):
        app = self.app
        holder = tk.Frame(app.root)
        holder.pack()
        var = tk.BooleanVar(app.root, value=False)
        cb = app._check(holder, "옵션", var)
        cb.pack()
        app.root.update()

        def fill():
            return cb.box.itemcget(cb.box.find_all()[0], "fill")

        self.assertEqual((fill(), len(cb.box.find_all())), (app.c["surface"], 1))
        cb.box.event_generate("<Button-1>")
        app.root.update()
        self.assertTrue(var.get())
        self.assertEqual((fill(), len(cb.box.find_all())), (app.c["accent"], 2))  # filled box + tick
        cb.focus_force()  # keys go to the focused widget; another test's window may still hold focus
        app.root.update()
        cb.event_generate("<space>")  # keyboard works too
        app.root.update()
        self.assertFalse(var.get())
        holder.destroy()
        var.set(True)  # the trace went away with the widget, so nothing redraws a dead canvas
        for pal in (v.LIGHT, v.DARK):  # a control's outline and tick need 3:1 (WCAG 1.4.11)
            self.assertGreaterEqual(contrast(app, pal["muted"], pal["bg"]), 3)
            self.assertGreaterEqual(contrast(app, pal["accent"], pal["bg"]), 3)
            self.assertGreaterEqual(contrast(app, pal["on_accent"], pal["accent"]), 3)

    def test_05_edit_note(self):
        app = self.app
        with mock.patch.object(v.simpledialog, "askstring", return_value="내가 쓴 설명"):
            app.edit_note(find(app, "Codex", "legacy"))
        self.assertEqual(find(app, "Codex", "legacy")["note"], "내가 쓴 설명")
        with mock.patch.object(v.simpledialog, "askstring", return_value=""):
            app.edit_note(find(app, "Codex", "legacy"))
        self.assertNotEqual(find(app, "Codex", "legacy")["note"], "내가 쓴 설명")

    # ---- add: create / link ----------------------------------------------------------------
    def test_06_add_dialog_create(self):
        app = self.app
        app.add_dialog()
        dlg = app.dialog
        dlg.show("새로 만들기")
        dlg.name.set("bad name")
        dlg.do_create()
        self.assertIn("영문", dlg.status.cget("text"))
        dlg.name.set("my-skill")
        dlg.desc.set("테스트용 스킬")
        dlg.do_create()
        for d in mgr.AGENT_DIRS.values():
            self.assertTrue((d / "my-skill/SKILL.md").is_file())
        self.assertEqual(find(app, "Codex", "my-skill")["origin"], "내 스킬")
        mgr.open_in_editor.assert_called()
        self.shot(dlg.win, "t6_dialog_new")
        dlg.close()

    def test_07_add_dialog_link(self):
        app = self.app
        app.add_dialog()
        dlg = app.dialog
        dlg.show("링크로 설치")
        dlg.link.set("file:///c:/evil")
        dlg.load_link()
        self.settle(lambda: "https" in dlg.status.cget("text"))  # rejected without touching git or network
        pack = self.tmp / "pack"
        for n in ("pa", "pb"):
            write_skill(pack / n, f"{n} skill")
        dlg._show_found(mgr.discover(pack))
        self.shot(dlg.win, "t7_dialog_link")
        dlg.do_install()  # nothing ticked yet
        self.assertIn("선택", dlg.status.cget("text"))
        dlg.picks[pack / "pb"].set(True)
        dlg.agents["Antigravity"].set(False)
        dlg.do_install()
        self.settle(lambda: "설치 2건" in dlg.status.cget("text"))
        self.assertTrue((mgr.AGENT_DIRS["Claude Code"] / "pb/SKILL.md").is_file())
        self.assertFalse((mgr.AGENT_DIRS["Antigravity"] / "pb").exists())
        write_skill(mgr.AGENT_DIRS["Codex"] / "pa")  # a real folder in the way
        dlg.picks[pack / "pa"].set(True)
        dlg.do_install()
        self.settle(lambda: "건너뜀" in dlg.status.cget("text"))
        self.assertEqual(mgr.parse(mgr.AGENT_DIRS["Codex"] / "pa/SKILL.md")["desc"], "does a thing")  # untouched
        dlg.replace.set(True)
        dlg.do_install()
        self.settle(lambda: "교체 1건" in dlg.status.cget("text"))
        self.assertEqual(mgr.parse(mgr.AGENT_DIRS["Codex"] / "pa/SKILL.md")["desc"], "pa skill")
        dlg.close()

    def recommend_file(self, **lists) -> Path:
        path = self.tmp / "skill_recommend.json"
        path.write_text(json.dumps({"updated": "2026-10-21", "popular": [], "trending": [], **lists}), encoding="utf-8")
        return path

    def test_07b_recommend_tab_lists_the_snapshot_with_installed_marks(self):
        app = self.app
        path = self.recommend_file(
            popular=[{"source": "o/pack", "skill": "newbie", "installs": 1234567}, {"source": "o/other", "skill": "tdd", "installs": 999}],
            trending=[{"source": "o/new", "skill": "fresh", "installs": 4321}],
        )
        with mock.patch.object(mgr, "RECOMMENDED", path):
            app.add_dialog()
            dlg = app.dialog
            self.assertEqual(dlg.mode, "링크로 설치")  # the tab is a third one: the dialog still opens on the link tab
            dlg.show("추천")
            app.root.update()
            shown = texts(dlg.win)
            for want in ("추천", "인기", "요즘 뜨는", "newbie", "o/pack", "1,234,567회", "tdd", "o/other", "999회",
                         "누적 설치 수 순 · 2026-10-21 기준"):
                self.assertIn(want, shown)
            self.assertNotIn("fresh", shown)
            self.assertTrue(any("설치 수는 품질·안전을 보증하지 않습니다" in t for t in shown))
            self.assertEqual(shown.count("설치됨"), 1)  # tdd is in the Claude Code folder; newbie is not
            badge = next(w for w in walk(dlg.win) if isinstance(w, tk.Label) and w.cget("text") == "설치됨")
            self.assertEqual(badge.master.winfo_children()[-1].cget("text"), "tdd")  # the badge sits on tdd's own row
            dlg.show_recommended("요즘 뜨는")
            shown = texts(dlg.win)
            self.assertIn("fresh", shown)
            self.assertIn("최근 24시간 설치 수 순 · 2026-10-21 기준", shown)
            self.assertNotIn("newbie", shown)
            self.assertNotIn("설치됨", shown)
            dlg.show("새로 만들기")
            dlg.show("추천")  # the sub-tab you were on is kept
            self.assertEqual(dlg.rec_tab, "요즘 뜨는")
            self.assertIn("fresh", texts(dlg.win))
            self.shot(dlg.win, "t7b_dialog_recommended")
            dlg.close()

    def test_07b2_surge_tab_shows_the_gain_and_explains_an_empty_list(self):
        app = self.app
        gains = [{"source": "o/pack", "skill": "newbie", "stars": 12000, "gain": 1234},
                 {"source": "o/other", "skill": "tdd", "stars": 900, "gain": 12}]
        cases = {"gains": {"surge": gains, "surge_since": "2026-10-20"}, "first day": {}, "quiet day": {"surge_since": "2026-10-20"}}
        for name, extra in cases.items():
            with self.subTest(name), mock.patch.object(mgr, "RECOMMENDED", self.recommend_file(**extra)):
                app.add_dialog()
                dlg = app.dialog
                dlg.show("추천")
                dlg.show_recommended("어제 급등")
                shown = texts(dlg.win)
                if name == "gains":
                    for want in ("+1,234★", "newbie", "o/pack", "+12★", "tdd", "늘어난 스타 수 순 · 2026-10-21 기준 · 2026-10-20 대비"):
                        self.assertIn(want, shown)
                    self.shot(dlg.win, "t7b2_dialog_surge")
                else:
                    want = "아직 비교할 어제 기록이 없습니다" if name == "first day" else "급등한 스킬이 없습니다"
                    self.assertTrue(any(want in t for t in shown))
                    self.assertFalse(any("비어 있습니다" in t for t in shown))
                dlg.close()

    def test_07c_picking_a_recommendation_opens_the_link_tab_and_ticks_that_skill(self):
        app = self.app
        pack = self.tmp / "rec-pack"
        for n in ("ra", "rb", "rc"):
            write_skill(pack / n)
        path = self.recommend_file(popular=[{"source": "o/rec-pack", "skill": "rb", "installs": 5000}])
        with mock.patch.object(mgr, "RECOMMENDED", path), mock.patch.object(mgr, "fetch", return_value=(pack, pack)) as fetch:
            app.add_dialog()
            dlg = app.dialog
            dlg.show("추천")
            app.root.update()
            row = next(w for w in walk(dlg.win) if isinstance(w, tk.Label) and w.cget("text") == "rb")
            row.event_generate("<Button-1>")
            self.settle(lambda: dlg.picks)
            fetch.assert_called_once_with("o/rec-pack")  # no network: fetch is the one thing that would reach out
            self.assertEqual(dlg.mode, "링크로 설치")
            self.assertEqual(dlg.link.get(), "o/rec-pack")
            self.assertEqual({d.name: v.get() for d, v in dlg.picks.items()}, {"ra": False, "rb": True, "rc": False})
            self.assertEqual(next(iter(dlg.picks)).name, "rb")  # listed first, so it is on screen however long the repo's list is
            self.assertEqual(str(dlg.install_btn.cget("state")), "normal")
            self.assertIn("3개", dlg.status.cget("text"))
            self.assertFalse((mgr.AGENT_DIRS["Claude Code"] / "rb").exists())  # picking only lists; installing is the button's job
            dlg.close()

    def test_07d_a_pick_that_is_not_in_the_repo_ticks_nothing_and_no_pick_is_unchanged(self):
        app = self.app
        pack = self.tmp / "rec-pack2"
        for n in ("sa", "sb"):
            write_skill(pack / n)
        app.add_dialog()
        dlg = app.dialog
        dlg._show_found(mgr.discover(pack), "nope")
        self.assertEqual([v.get() for v in dlg.picks.values()], [False, False])
        dlg._show_found(mgr.discover(pack), None)
        self.assertEqual([v.get() for v in dlg.picks.values()], [False, False])
        renamed = self.tmp / "rec-pack3"  # skills.sh names a skill by its SKILL.md name, which may differ from the folder's
        write_skill(renamed / "other")
        write_skill(renamed / "react-best-practices")
        skill_md = renamed / "react-best-practices/SKILL.md"
        skill_md.write_text("---\nname: vercel-react-best-practices\ndescription: d\n---\n", encoding="utf-8")
        dlg._show_found(mgr.discover(renamed), "vercel-react-best-practices")
        self.assertEqual({d.name: v.get() for d, v in dlg.picks.items()}, {"react-best-practices": True, "other": False})
        dlg._show_found(mgr.discover(pack / "sa"), "nope")  # one skill in the repo: ticked as before
        self.assertEqual([v.get() for v in dlg.picks.values()], [True])
        dlg._show_found([], "x")
        self.assertIn("찾지 못했습니다", dlg.status.cget("text"))
        dlg.show("추천")
        dlg._loaded(mgr.discover(pack))  # a download finishing after the user left the link tab changes nothing and raises nothing
        dlg.close()

    def test_07e_without_a_usable_snapshot_the_tab_says_so(self):
        app = self.app
        broken = self.tmp / "broken-recommend.json"
        broken.write_text("{not json", encoding="utf-8")
        for path in (self.tmp / "no-such-file.json", broken, self.recommend_file()):
            with self.subTest(path.name), mock.patch.object(mgr, "RECOMMENDED", path):
                app.add_dialog()
                dlg = app.dialog
                dlg.show("추천")
                for tab in ("인기", "요즘 뜨는"):
                    dlg.show_recommended(tab)
                    self.assertTrue(any("추천 목록이 비어 있습니다" in t for t in texts(dlg.win)))
                dlg.close()

    # ---- update / delete --------------------------------------------------------------------
    def test_08_check_and_apply_updates(self):
        app = self.app
        calls = []
        with (
            mock.patch.object(mgr, "has_update", return_value=True),
            mock.patch.object(mgr, "update", side_effect=lambda r: calls.append(r) or {"old": "aaa", "new": "bbb", "gone": []}),
            mock.patch.object(v.messagebox, "askyesno", return_value=True),
            mock.patch.object(v.messagebox, "showinfo") as info,
        ):
            app.check_updates()
            self.settle(lambda: info.called)
        self.assertEqual(calls, [self.repo.resolve()])
        self.assertIn("aaa → bbb", info.call_args[0][1])
        self.assertEqual(app.updatable, set())

    def test_09_update_check_marks_rows_and_survives_decline(self):
        app = self.app
        with (
            mock.patch.object(mgr, "has_update", return_value=True),
            mock.patch.object(mgr, "update") as upd,
            mock.patch.object(v.messagebox, "askyesno", return_value=False),
        ):
            app.check_updates([self.repo.resolve()])
            self.settle(lambda: app.updatable)
        upd.assert_not_called()
        app.set_agent("Claude Code")
        app.render()
        self.assertIn("업데이트 있음", texts(app.body))
        self.shot(app.root, "t9_update_badge")
        app.updatable.clear()

    def test_10_delete(self):
        app = self.app
        with mock.patch.object(v.messagebox, "askyesno", return_value=False):
            app.delete(find(app, "Codex", "legacy"))
        self.assertIsNotNone(find(app, "Codex", "legacy"))  # declined: nothing happens
        with mock.patch.object(v.messagebox, "askyesno", return_value=True):
            app.delete(find(app, "Codex", "legacy"))
            app.delete(find(app, "Claude Code", "gitskill"))
        self.assertIsNone(find(app, "Codex", "legacy"))
        self.assertIsNone(find(app, "Claude Code", "gitskill"))
        self.assertTrue((self.repo / "skills/gitskill/SKILL.md").is_file())  # a link was removed, not the source
        trashed = [p.name.split("_", 2)[2] for p in (mgr.STORE / "trash").iterdir()]
        self.assertIn("legacy", trashed)  # the real folder was moved, not destroyed
        self.assertNotIn("gitskill", trashed)  # a link is only unlinked

    # ---- theme, fonts, discovery ------------------------------------------------------------
    def test_11_theme_toggle_is_saved_and_rebuilds(self):
        app = self.app
        self.assertEqual(app.theme, "light")  # bright by default
        app.set_agent("Claude Code")
        n = len(app.shown)
        app.toggle_theme()
        self.assertEqual((app.theme, mgr.load_settings()["theme"]), ("dark", "dark"))
        self.assertEqual(len(app.shown), n)
        self.shot(app.root, "t11_dark")
        app.toggle_theme()
        self.assertEqual((app.theme, mgr.load_settings()["theme"]), ("light", "light"))
        self.shot(app.root, "t11_light")

    def test_12_no_italic_anywhere(self):
        app = self.app
        app.add_dialog()
        dlg = app.dialog

        def check(win):
            for w in walk(win):
                try:
                    spec = w.cget("font")
                except tk.TclError:
                    continue  # frames etc. have no font option
                f = tkfont.Font(root=app.root, font=spec)
                self.assertEqual(f.actual("slant"), "roman", f"{w} {spec}")

        app.detail(app.shown[0])
        open_menu(app, app.shown[0], 200, 200)
        app.root.update()
        check(app.popup.win)
        app.close_popup()
        for page in ("링크로 설치", "새로 만들기"):
            dlg.show(page)
            for win in (app.root, dlg.win, app.detail_win.win):
                check(win)
        app.detail_win.close()
        self.assertEqual(app.f(11)[-1], "roman")
        dlg.close()

    def test_13_bundled_pretendard_is_used(self):
        fonts = Path(v.__file__).parent / "fonts"
        self.assertTrue((fonts / "Pretendard-Regular.otf").is_file() and (fonts / "Pretendard-Bold.otf").is_file())
        self.assertTrue((fonts / "LICENSE.txt").is_file())  # OFL: the license travels with the font files
        app = self.app
        self.assertEqual(app.family, "Pretendard")
        regular = tkfont.Font(root=app.root, family=app.family, size=10)
        self.assertEqual((regular.actual("family"), regular.actual("weight")), ("Pretendard", "normal"))
        self.assertEqual((app.name_font.actual("family"), app.name_font.actual("weight")), ("Pretendard", "bold"))

    def test_14_text_is_readable_on_its_background_in_both_themes(self):
        """WCAG contrast for every text/background pair actually on screen, including hover, selected and disabled."""
        app = self.app
        pack = self.tmp / "contrast-pack"
        for n in ("ca", "cb"):
            write_skill(pack / n)
        proj = hn.add_project(self.tmp)  # any existing folder will do for drawing the harness window
        bad = []
        for _ in range(2):  # light, then dark
            app.set_agent("Claude Code")
            app.pick(None)
            bad += low_contrast(app, app.root, app.theme)
            app.pick(sorted({s["cat"] for s in app.shown})[0])  # a selected rail row
            bad += low_contrast(app, app.root, app.theme + " picked")
            for w in walk(app.root):  # hover states: header buttons, rows, rail
                if isinstance(w, tk.Label) and str(w.cget("cursor")) == "hand2":
                    w.event_generate("<Enter>")
                    bad += low_contrast(app, w.master, app.theme + " hover")
                    w.event_generate("<Leave>")
            for s in (app.shown[0], next(s for s in app.data["Claude Code"] if not s["managed"])):
                open_menu(app, s, 200, 200)
                app.root.update()
                bad += low_contrast(app, app.popup.win, app.theme + " menu")
                for r in app.popup.rows:
                    r.event_generate("<Enter>")
                    bad += low_contrast(app, r, app.theme + " menu hover")
                    r.event_generate("<Leave>")
                app.close_popup()
                app.detail(s)
                app.root.update()
                bad += low_contrast(app, app.detail_win.win, app.theme + " detail")
                app.detail_win.close()
            app.add_dialog()
            dlg = app.dialog
            bad += low_contrast(app, dlg.win, app.theme + " dialog, install disabled")
            dlg._show_found(mgr.discover(pack))
            bad += low_contrast(app, dlg.win, app.theme + " dialog, install enabled")
            dlg.show("새로 만들기")
            bad += low_contrast(app, dlg.win, app.theme + " dialog, new")
            both = [{"source": "o/pack", "skill": "tdd", "installs": 12345}, {"source": "o/b", "skill": "newbie", "installs": 9}]
            with mock.patch.object(mgr, "RECOMMENDED", self.recommend_file(popular=both)):
                dlg.show("추천")
            bad += low_contrast(app, dlg.win, app.theme + " dialog, recommended")
            for w in walk(dlg.win):  # the row under the pointer (tinted), and the installed badge on it
                if isinstance(w, tk.Label) and w.cget("text") in ("tdd", "newbie"):
                    w.event_generate("<Enter>")
                    bad += low_contrast(app, dlg.win, app.theme + " dialog, recommended hover")
                    w.event_generate("<Leave>")
            dlg.close()
            app.harness_dialog()
            hw = app.dialog
            hw.select_project(proj)
            hw.choose_set(None)
            hw.name_var.set("x")
            bad += low_contrast(app, hw.win, app.theme + " harness, new set")
            hw.show_page("구조 가이드")
            bad += low_contrast(app, hw.win, app.theme + " harness, guide")
            hw.show_page("세트 편집")
            for w in walk(hw.win):
                if isinstance(w, tk.Label) and str(w.cget("cursor")) == "hand2":
                    w.event_generate("<Enter>")
                    bad += low_contrast(app, hw.win, app.theme + " harness hover")
                    w.event_generate("<Leave>")
            hw.close()
            app.toggle_theme()
        self.maxDiff = None
        self.assertEqual(bad, [])

    def test_15_harness_window_sets_a_project_up_and_takes_it_down_again(self):
        app, claude = self.app, self.tmp / "claude"
        write_skill(claude / "harness-demo")
        proj = self.tmp / "harness-proj"
        proj.mkdir()
        subprocess.run(["git", "init", "-q", str(proj)], check=True, capture_output=True)
        exclude = proj / ".git/info/exclude"
        app.set_agent("Claude Code")
        app.load()
        app.harness_dialog()
        app.root.update()
        w = app.dialog
        self.assertIsInstance(w, v.HarnessWindow)
        with mock.patch.object(v.filedialog, "askdirectory", return_value=str(proj)):
            w.add_project()
        self.assertIn("harness-demo", w.skill_vars)
        self.assertEqual(w.project, str(proj.resolve()))
        w.choose_set(None)  # a new set, named and ticked here
        w.name_var.set("web")
        w.skill_vars["harness-demo"].set(True)
        link = proj / ".claude/skills/harness-demo"
        with mock.patch.object(v.messagebox, "askokcancel", return_value=False) as ask:
            w.apply()  # declined: nothing linked
        self.assertIn("git status", ask.call_args[0][1])  # a git project is warned before anything is linked
        self.assertFalse(link.exists())
        self.assertEqual(hn.load()["sets"]["web"]["skills"], ["harness-demo"])  # the set itself was saved
        with mock.patch.object(v.messagebox, "askokcancel", return_value=True):
            w.apply()
        self.assertTrue(mgr.is_link(link))
        self.assertIn("새로 연결 1개", w.status.cget("text"))
        self.assertNotIn("harness-demo", exclude.read_text(encoding="utf-8") if exclude.is_file() else "")  # opt-in only
        w.show_page("구조 가이드")  # read-only tree of the project: our link marked, the user's own file only "있음"
        (proj / "CLAUDE.md").write_text("x", encoding="utf-8")
        w.draw_guide()
        tree = next(x for x in walk(w.guide_page) if isinstance(x, tk.Text)).get("1.0", "end")
        self.assertIn("harness-demo", tree)
        self.assertRegex(tree, r"CLAUDE\.md\s+있음")
        self.assertRegex(tree, r"AGENTS\.md\s+없음")
        self.assertEqual(w.guide_page.winfo_manager(), "pack")
        self.assertEqual(w.sets_page.winfo_manager(), "")
        w.show_page("세트 편집")
        self.assertEqual(w.sets_page.winfo_manager(), "pack")
        w.release()
        self.assertFalse(link.exists())
        self.assertIn("연결 해제 1개", w.status.cget("text"))
        w.exclude_var.set(True)
        with mock.patch.object(v.messagebox, "askokcancel", return_value=True) as ask:
            w.apply()
        self.assertNotIn("git status", ask.call_args[0][1])
        self.assertIn("/.claude/skills/harness-demo", exclude.read_text(encoding="utf-8"))
        write_skill(proj / ".claude/skills/mine")  # the user's own skill beside ours survives removing the project
        with mock.patch.object(v.messagebox, "askokcancel", return_value=True):
            w.remove_project()
            self.assertNotIn(str(proj.resolve()), hn.load()["projects"])
            w.delete_set()  # nothing chosen: says so, asks nothing
        self.assertFalse(mgr.is_link(link))
        self.assertTrue((proj / ".claude/skills/mine/SKILL.md").is_file())
        self.assertNotIn("harness-demo", exclude.read_text(encoding="utf-8"))
        with mock.patch.object(v.filedialog, "askdirectory", return_value=str(proj)):
            w.add_project()
        w.choose_set("web")
        with mock.patch.object(v.messagebox, "askokcancel", return_value=True):
            w.delete_set()
        self.assertNotIn("web", hn.load()["sets"])
        w.close()
        mgr.remove("Claude Code", "harness-demo")
        app.load()


def rgb(app, color):
    return [x / 65535 for x in app.root.winfo_rgb(color)]


def contrast(app, a, b):
    def lum(color):
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb(app, color)]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def low_contrast(app, top, where):
    """-> 'where: widget text fg/bg ratio' for every pair below WCAG AA (4.5, or 3 for large or disabled text)."""
    out = []
    for w in walk(top):
        pairs = []
        try:
            font = tkfont.Font(root=app.root, font=w.cget("font"))
        except tk.TclError:
            continue  # no text on this widget
        large = font.actual("size") >= 18 or (font.actual("size") >= 14 and font.actual("weight") == "bold")
        need = 3.0 if large else 4.5
        if isinstance(w, tk.Menu):
            pairs = [
                ("fg", w.cget("fg"), w.cget("bg"), need),
                ("active", w.cget("activeforeground"), w.cget("activebackground"), need),
                ("disabled", w.cget("disabledforeground"), w.cget("bg"), 3.0),
            ]
        elif isinstance(w, (tk.Label, tk.Checkbutton, tk.Entry, tk.Text)):
            pairs = [("fg", w.cget("fg"), w.cget("bg"), need)]
            if isinstance(w, tk.Label) and str(w.cget("state")) == "disabled":  # a disabled button
                pairs = [("disabled", w.cget("disabledforeground"), w.cget("bg"), 3.0)]
            if isinstance(w, tk.Checkbutton):
                pairs.append(("check", w.cget("fg"), w.cget("selectcolor"), 3.0))
        for kind, fg, bg, limit in pairs:
            r = contrast(app, fg, bg)
            if r < limit:
                text = w.cget("text") if "text" in w.keys() else w.winfo_class()  # noqa: SIM118 - Tk widgets have keys() but no __contains__
                out.append(f"{where}: {str(text)[:20]!r} {kind} {fg} on {bg} = {r:.2f} < {limit}")
    return out


class PlatformTest(unittest.TestCase):
    """Behaviour that must be the same on Windows and macOS."""

    def test_directory_link_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            real = t / "real"
            real.mkdir()
            (real / "SKILL.md").write_text("x", encoding="utf-8")
            link = t / "link"
            plat.make_dir_link(link, real)
            self.assertTrue(mgr.is_link(link) and (link / "SKILL.md").is_file())
            plat.unlink_dir(link)  # removes only the link
            self.assertFalse(os.path.lexists(link))
            self.assertTrue((real / "SKILL.md").is_file())
            with self.assertRaises(OSError):
                plat.unlink_dir(real)  # a real folder is never removed this way

    def test_second_copy_of_the_app_is_refused(self):
        name = f"skill-catalog-test-{os.getpid()}"
        self.assertFalse(plat.already_running(name))
        other = subprocess.run(
            ["python", "-c", f"import platform_support as p; print(p.already_running({name!r}))"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
        )
        self.assertEqual(other.stdout.strip(), "True")

    def test_starting_the_app_again_leaves_one_note_for_the_running_copy(self):
        name = f"skill-catalog-test-show-{os.getpid()}"
        note = Path(tempfile.gettempdir()) / f"{name}.show"
        try:
            self.assertFalse(plat.take_show_request(name))  # nobody asked
            plat.ask_running_copy_to_show(name)
            plat.ask_running_copy_to_show(name)  # two quick starts are one request
            self.assertTrue(plat.take_show_request(name))
            self.assertFalse(plat.take_show_request(name))  # a note is read once
            plat.ask_running_copy_to_show(name)
            old = time.time() - 2 * plat.SHOW_REQUEST_TTL
            os.utime(note, (old, old))  # left for a copy that is long gone
            self.assertFalse(plat.take_show_request(name))
            self.assertFalse(note.exists())  # and it is cleared away
        finally:
            note.unlink(missing_ok=True)

    def test_right_click_sequences_match_the_platform(self):
        self.assertEqual(plat.RIGHT_CLICK[0], "<Button-2>" if plat.MAC else "<Button-3>")

    def test_tray_menu_builds_and_only_known_commands_are_accepted(self):
        sent = []
        menu = v.tray_menu(sent.append)
        for item in menu.items:
            if item.text != "":
                item(None)  # pystray calls the action with (icon, item); the lambdas ignore them
        self.assertEqual(set(sent), v.TRAY_COMMANDS)
        self.assertTrue(all(c in v.TRAY_COMMANDS for c in sent))

    def test_tray_child_output_is_filtered(self):
        class Fake:
            stdout = ["toggle\n", "rm -rf /\n", "quit\n", "__class__\n"]

        class Stub:
            tray_proc = Fake()
            events = __import__("queue").Queue()

        v.App._read_tray(Stub())  # type: ignore[arg-type]
        got = []
        while not Stub.events.empty():
            got.append(Stub.events.get_nowait())
        self.assertEqual(got, ["toggle", "quit"])

    def test_font_scale_keeps_sizes_comparable(self):
        self.assertAlmostEqual(plat.FONT_SCALE, 4 / 3 if plat.MAC else 1.0)

    def test_open_folder_only_reveals_and_never_launches(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(plat.subprocess, "Popen") as popen:
            app_like = Path(t) / "evil.app"  # a skill folder name a downloaded repo could use
            app_like.mkdir()
            plat.open_folder(app_like)
            cmd = popen.call_args[0][0]
            expected = ["explorer"] if plat.WIN else ["open", "-R"] if plat.MAC else ["xdg-open"]
            self.assertEqual(cmd[:-1], expected)
            popen.reset_mock()
            plat.open_folder(Path(t) / "missing")  # not a folder: nothing is started
            popen.assert_not_called()


class TrayMenuTest(unittest.TestCase):
    def test_every_tray_item_is_an_allowed_command_the_app_can_run(self):
        # a merge once dropped the app-update items while every other test still passed: check the menu itself
        sent = re.findall(r'put\("(\w+)"\)', inspect.getsource(v.tray_menu))
        self.assertTrue({"update_app", "toggle_auto_update"} <= set(sent), sent)
        for cmd in sent:
            self.assertIn(cmd, v.TRAY_COMMANDS, cmd)  # the macOS child process may only ask for these
            self.assertTrue(callable(getattr(v.App, cmd, None)), cmd)


class StartupErrorTest(unittest.TestCase):
    def test_a_missing_module_says_what_to_install_and_where(self):
        text = v.startup_error_text(ModuleNotFoundError("No module named 'PIL.ImageTk'", name="PIL.ImageTk"))
        self.assertIn("'PIL'", text)
        self.assertIn("pip install -r", text)
        self.assertIn("requirements.txt", text)
        self.assertIn("일반 터미널", text)  # installing from inside the Claude app went to a folder only that app can see

    def test_any_other_failure_is_shown_and_its_traceback_kept(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(mgr, "STORE", Path(t) / "store"):
            err = RuntimeError("boom")
            with (
                mock.patch.object(v.tk, "Tk"),
                mock.patch.object(v.messagebox, "showerror") as show,
            ):
                v.report_startup_failure(err)
            self.assertIn("boom", show.call_args[0][1])
            self.assertIn("RuntimeError: boom", (Path(t) / "store" / "error.log").read_text(encoding="utf-8"))


class CategoryTest(unittest.TestCase):
    def test_every_skill_is_in_exactly_one_category(self):
        listed = [s for c in v.CATALOG for s in c["skills"]]
        dupes = sorted({s for s in listed if listed.count(s) > 1})
        self.assertEqual(dupes, [], "a skill is listed under two categories")

    def test_every_described_skill_has_a_category(self):
        missing = sorted(n for n in v.NOTES if n not in v.CATEGORY_OF)
        self.assertEqual(missing, [], "add these to skill_categories.json")

    def test_categories_colors_and_descriptions_line_up(self):
        names = [c["name"] for c in v.CATALOG]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(v.CAT_COLORS), set(names) | {v.OTHER})
        self.assertTrue(all(c["about"] for c in v.CATALOG))

    def test_listed_skills_ignore_keyword_guesses(self):
        # the 'ponytail' keyword would otherwise file ponytail-gain under 개발·테스트; the list wins, with or without a plugin prefix
        self.assertEqual(v.categorize("ponytail-gain", ""), "리뷰·품질")
        self.assertEqual(v.categorize("superpowers:requesting-code-review", ""), "리뷰·품질")
        self.assertEqual(v.categorize("zz-helper", "Review pull requests for bugs"), "리뷰·품질")  # unknown: keywords
        self.assertEqual(v.categorize("zzz", "nothing matches here"), v.OTHER)


class TildeTest(unittest.TestCase):
    def test_home_paths_are_shortened(self):
        home = Path.home()
        self.assertEqual(v.tilde(home / "x" / "y"), "~" + str(Path("/x/y")))
        if plat.WIN:  # Windows paths ignore case
            self.assertEqual(v.tilde(Path(str(home).upper()) / "z"), "~" + str(Path("/z")))
        outside = Path(home.anchor) / "elsewhere-for-test"
        self.assertEqual(v.tilde(outside), str(outside))


class DiscoveryTest(unittest.TestCase):
    """What happens on someone else's PC: other layouts, missing agents, broken files. No Tk needed."""

    def test_linked_skills_are_listed_and_link_loops_do_not_hang(self):
        # macOS CI caught this: installed skills are symlinks there, and os.walk does not enter them by default
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            source = t / "source/alpha"
            write_skill(source)
            agent = t / "agent"
            agent.mkdir()
            plat.make_dir_link(agent / "alpha", source)
            plat.make_dir_link(agent / "loop", agent)  # points back up the tree
            try:
                self.assertEqual([s["name"] for s in v.scan(agent)], ["alpha"])
            finally:
                plat.unlink_dir(agent / "alpha")
                plat.unlink_dir(agent / "loop")

    def test_pins_tolerate_odd_settings(self):
        empty = {a: set() for a in v.AGENTS}
        for odd in (None, "junk", [], {"Codex": "abc"}):
            with mock.patch.object(mgr, "load_settings", return_value={"pins": odd}):
                self.assertEqual(v.load_pins(), empty)
        with mock.patch.object(mgr, "load_settings", return_value={"pins": {"Codex": [1, "x"]}}):
            self.assertEqual(v.load_pins()["Codex"], {"x"})

    def test_odd_skill_files_do_not_break_the_list(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "plain").mkdir()
            (root / "plain/SKILL.md").write_text("no front matter at all\n", encoding="utf-8")
            (root / "binary").mkdir()
            (root / "binary/SKILL.md").write_bytes(b"\xff\xfe\x00garbage")
            (root / "multiline").mkdir()
            (root / "multiline/SKILL.md").write_text("---\nname: ml\ndescription: >\n  first line\n  second line\n---\n", encoding="utf-8")
            got = {s["name"]: s for s in v.scan(root)}
            self.assertEqual(set(got), {"plain", "binary", "ml"})
            self.assertEqual(got["ml"]["desc"], "first line second line")

    def test_missing_agent_folders_are_just_empty(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            dirs = {"Claude Code": t / "a", "Codex": t / "b", "Antigravity": t / "c"}
            with mock.patch.object(mgr, "AGENT_DIRS", dirs), mock.patch.object(mgr, "CLAUDE_HOME", t / "home"):
                for agent in dirs:
                    # only a built-in skill folder (not under the user's control) can exist on such a PC
                    self.assertTrue(all(s["origin"] == "내장" for s in v.collect(agent)))

    def test_plugin_roots_tolerate_missing_or_odd_files(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(mgr, "CLAUDE_HOME", Path(t)):
            home = Path(t)
            self.assertEqual(v.claude_plugin_roots(), [])  # no plugins folder at all
            (home / "plugins").mkdir()
            (home / "plugins/installed_plugins.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(v.claude_plugin_roots(), [])
            odd = {"plugins": {"a@m": [{}], "b@m": [], "c@m": [{"installPath": str(home / "c")}]}}
            (home / "plugins/installed_plugins.json").write_text(json.dumps(odd), encoding="utf-8")
            (home / "settings.json").write_text(json.dumps({"enabledPlugins": {"a@m": True, "b@m": True, "c@m": True}}), encoding="utf-8")
            self.assertEqual(v.claude_plugin_roots(), [(home / "c" / "skills", "c:")])

    def test_agent_homes_follow_env_vars(self):
        out = subprocess.run(
            ["python", "-c", "import skill_manager as m; print(m.AGENT_DIRS['Claude Code']); print(m.AGENT_DIRS['Codex'])"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            env={**os.environ, "CLAUDE_CONFIG_DIR": "D:/cc", "CODEX_HOME": "D:/cx"},
        ).stdout.split()
        self.assertEqual([Path(x) for x in out], [Path("D:/cc/skills"), Path("D:/cx/skills")])


if __name__ == "__main__":
    unittest.main()

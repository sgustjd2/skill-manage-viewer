"""Everything that differs between Windows and macOS lives here, so the rest of the app stays portable.

Windows: junctions, Win32 calls, Pretendard via GDI.  macOS: symlinks, CoreText, `open`, a separate tray process.
Linux is not a target, but the POSIX branches are what the macOS ones fall back on.
"""

import contextlib
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"

NO_WINDOW = 0x08000000 if WIN else 0  # CREATE_NO_WINDOW: keep git from flashing a console under pythonw
# Tk on macOS numbers the buttons differently: right = 2 (or ctrl+click), not 3.
RIGHT_CLICK = ("<Button-2>", "<Control-Button-1>") if MAC else ("<Button-3>",)
MONO_FONT = "Consolas" if WIN else "Menlo" if MAC else "TkFixedFont"  # source text and code
FALLBACK_FONT = "Malgun Gothic" if WIN else "Apple SD Gothic Neo" if MAC else "TkDefaultFont"
# Tk on macOS sizes fonts at 72 dpi (1pt = 1px) while Windows uses 96, so point sizes are scaled to look alike.
FONT_SCALE = 4 / 3 if MAC else 1.0


# ---- directory links -----------------------------------------------------------------------------
def make_dir_link(link: Path, target: Path) -> None:
    """Junction on Windows (needs no admin rights), symlink elsewhere."""
    if WIN:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True,
                           timeout=30, creationflags=NO_WINDOW)
        if r.returncode:
            raise OSError((r.stderr or r.stdout).strip() or "mklink failed")
    else:
        os.symlink(target, link, target_is_directory=True)


def unlink_dir(p: Path) -> None:
    """Remove a directory link without touching what it points to. Fails on a real directory."""
    if WIN:
        os.rmdir(p)  # removes junctions and directory symlinks alike, never their target
    else:
        p.unlink() if p.is_symlink() else os.rmdir(p)


# ---- finding programs ----------------------------------------------------------------------------
def find_executable(name: str, known: list[Path]) -> Path | None:
    """`name` on PATH, else the first of the `known` install locations that holds a file. The app is often started from the
    Start menu / Spotlight, whose PATH lacks the folders a terminal gets from the user's shell profile."""
    found = shutil.which(name)
    return Path(found) if found else next((p for p in known if p.is_file()), None)


# ---- opening things ------------------------------------------------------------------------------
def open_path(path: Path) -> None:
    """Open a file in the user's editor."""
    if WIN:
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError:
            subprocess.Popen(["notepad", str(path)])  # no program is associated with .md
    elif MAC:
        subprocess.Popen(["open", "-t", str(path)])  # -t: the default text editor
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_folder(path: Path) -> None:
    """Show a folder in Explorer / Finder without "opening" it: a skill folder from a downloaded repo may be
    named like `x.app`, which `open`/the shell's default verb would launch instead of showing."""
    if not path.is_dir():
        return
    if WIN:
        subprocess.Popen(["explorer", str(path)])
    elif MAC:
        subprocess.Popen(["open", "-R", str(path)])  # -R: reveal in Finder, never launch
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ---- screen --------------------------------------------------------------------------------------
def dpi_aware() -> None:
    if WIN:
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shcore.SetProcessDpiAwareness(1)


def pixel_scale(root) -> float:
    """Pixels per layout unit: follows Windows display scaling; macOS already works in scaled points."""
    return 1.0 if MAC else root.winfo_fpixels("1i") / 96


def work_area(root) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the screen minus taskbar / menu bar / Dock, in pixels."""
    if WIN:
        class RECT(ctypes.Structure):
            _fields_ = [(n, ctypes.c_long) for n in ("left", "top", "right", "bottom")]
        r = RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    # shortcut: Tk can't report the menu bar / Dock, so assume a 25px menu bar and a bottom Dock of about 90px.
    return 0, 25 if MAC else 0, root.winfo_screenwidth(), root.winfo_screenheight() - (90 if MAC else 0)


def dark_titlebar(window, dark: bool) -> None:
    if WIN:
        with contextlib.suppress(AttributeError, OSError):
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(dark)), 4)


# ---- bundled fonts -------------------------------------------------------------------------------
def register_font(path: Path) -> bool:
    """Make a font file usable by this process only; nothing is installed system-wide."""
    try:
        if WIN:
            return bool(ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0))  # FR_PRIVATE
        if MAC:
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
            cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
            cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
            ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            raw = str(path).encode()
            url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
            try:
                return bool(ct.CTFontManagerRegisterFontsForURL(url, 1, None))  # 1 = kCTFontManagerScopeProcess
            finally:
                cf.CFRelease(url)
    except (OSError, AttributeError):
        pass
    return False


# ---- single instance -----------------------------------------------------------------------------
_lock_file = None  # keeps the lock for the life of the process
INSTANCE = "skilldesk"  # names the lock and the 'show yourself' note of the one copy of the app that may run
SHOW_REQUEST_TTL = 60  # seconds a note stays valid: a copy that is still starting up reads it late, a dead one never does


def already_running(name: str = INSTANCE) -> bool:
    """True if another copy of the app holds the lock. The OS drops the lock when that process dies."""
    global _lock_file
    f = open(Path(tempfile.gettempdir()) / f"{name}.lock", "a+b")  # noqa: SIM115 - must stay open
    try:
        if WIN:
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # pyright: ignore[reportAttributeAccessIssue]
    except OSError:
        f.close()
        return True
    _lock_file = f
    return False


def release_lock() -> None:
    """Let go of the single-instance lock now (before starting the updated copy of the app)."""
    global _lock_file
    if _lock_file:
        _lock_file.close()  # closing the file drops the OS lock
        _lock_file = None


def ask_running_copy_to_show(name: str = INSTANCE) -> None:
    """The app was started again while a copy sits in the tray: leave it a note to show its window.
    A file in the temp folder works the same on Windows and macOS; two quick starts leave one note."""
    (Path(tempfile.gettempdir()) / f"{name}.show").touch()


def take_show_request(name: str = INSTANCE) -> bool:
    """True once per note left by ask_running_copy_to_show (reading it removes it). An old note is not for anybody."""
    note = Path(tempfile.gettempdir()) / f"{name}.show"
    try:
        age = time.time() - note.stat().st_mtime
        note.unlink()
    except OSError:  # no note, or its writer still has it open: the next look finds it
        return False
    return age < SHOW_REQUEST_TTL

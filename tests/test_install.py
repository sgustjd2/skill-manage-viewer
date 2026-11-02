"""python -m unittest test_install -v   (writes only into temp folders, never into the real Start menu or ~/Applications)"""

import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import install
import platform_support as plat


class MacBundle(unittest.TestCase):
    """The .app is plain files, so it can be built and checked on any OS."""

    def test_bundle_launcher_and_login_item(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            py = Path("/usr/local/bin/python3")  # str(py) uses backslashes when this runs on Windows
            made = install.install_mac(t / "Applications", t / "LaunchAgents", True, py)
            app = made[0]
            self.assertEqual(app.name, "SkillDesk.app")
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual((info["CFBundleExecutable"], info["CFBundleDisplayName"]), ("launcher", "SkillDesk"))
            launcher = (app / "Contents/MacOS/launcher").read_text(encoding="utf-8")
            self.assertTrue(launcher.startswith("#!/bin/bash\n"))
            self.assertIn(f'cd "{install.HERE}"', launcher)
            self.assertIn(f'exec "{py}" skill_viewer.py "$@"', launcher)
            self.assertEqual((app / "Contents/Resources/AppIcon.icns").read_bytes()[:4], b"icns")
            agent = plistlib.loads(made[1].read_bytes())
            self.assertEqual(agent["ProgramArguments"], [str(app / "Contents/MacOS/launcher"), "--hidden"])
            self.assertTrue(agent["RunAtLoad"])

    def test_no_login_item_unless_asked(self):
        with tempfile.TemporaryDirectory() as t:
            made = install.install_mac(Path(t) / "A", Path(t) / "L", False, Path("/usr/bin/python3"))
            self.assertEqual(len(made), 1)
            self.assertFalse((Path(t) / "L").exists())

    def test_uninstall_removes_only_what_we_made(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            made = install.install_mac(t / "A", t / "L", True, Path("/usr/bin/python3"))
            foreign = t / "A" / "Other.app"
            (foreign / "Contents/MacOS").mkdir(parents=True)
            (foreign / "Contents/MacOS/launcher").write_text("#!/bin/bash\necho mine\n", encoding="utf-8")
            gone = install.remove([*made, foreign])
            self.assertEqual(set(gone), set(made))
            self.assertTrue(foreign.exists())  # someone else's app with the same file layout is left alone


@unittest.skipUnless(plat.WIN, "Windows shortcut")
class WindowsShortcut(unittest.TestCase):
    def read(self, lnk: Path) -> dict:
        copy = lnk.with_name("read-check.lnk")  # WScript.Shell can't open a Korean file name on a non-Korean Windows
        shutil.copyfile(lnk, copy)
        script = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{copy}');"
            "$s | Select-Object TargetPath,Arguments,WorkingDirectory,IconLocation | ConvertTo-Json"
        )
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, check=True).stdout
        finally:
            copy.unlink()
        return json.loads(out)

    def test_start_menu_and_startup_shortcuts(self):
        with tempfile.TemporaryDirectory() as t:
            programs = Path(t) / "Programs"
            made = install.install_windows(programs, True, Path(sys.executable))
            self.assertEqual([p.name for p in made], ["SkillDesk.lnk"] * 2)
            menu, startup = (self.read(p) for p in made)
            self.assertTrue(menu["TargetPath"].lower().endswith("pythonw.exe"))  # no console window
            self.assertEqual(menu["Arguments"], f'"{install.HERE / "skill_viewer.py"}"')
            self.assertEqual(menu["WorkingDirectory"], str(install.HERE))
            self.assertEqual(menu["IconLocation"].split(",")[0], str(install.HERE / "skill_viewer.ico"))
            self.assertTrue(startup["Arguments"].endswith("--hidden"))
            self.assertEqual(made[1].parent.name, "Startup")
            self.assertEqual(set(install.remove(made)), set(made))
            self.assertFalse(any(p.exists() for p in made))

    def test_cli_with_dest(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(install.main(["--dest", t]), 0)
            self.assertTrue((Path(t) / "SkillDesk.lnk").is_file())
            self.assertEqual(install.main(["--dest", t, "--uninstall"]), 0)
            self.assertFalse((Path(t) / "SkillDesk.lnk").exists())


if __name__ == "__main__":
    unittest.main()

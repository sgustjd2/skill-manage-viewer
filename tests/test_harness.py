"""python -m unittest test_harness -v   (everything in a temp folder; no window, nothing of the real ~/.claude is touched)"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import platform_support as plat
import skill_harness as h
import skill_manager as mgr


def write_skill(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: x\n---\n", encoding="utf-8")


class HarnessTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = self.t = Path(self._tmp.name).resolve()
        agents = {"Claude Code": t / "claude", "Codex": t / "codex", "Antigravity": t / "agy"}
        for p in (mock.patch.object(mgr, "STORE", t / "store"), mock.patch.object(mgr, "AGENT_DIRS", agents)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)
        self.src = t / "sources"  # the real skill folders, which release must never remove
        for n in ("alpha", "beta", "gamma"):
            write_skill(self.src / n)
        mgr.install([self.src / n for n in ("alpha", "beta", "gamma")], ["Claude Code"])
        self.proj = t / "proj"
        self.proj.mkdir()
        self.key = h.add_project(self.proj)
        h.save_set("web", ["alpha", "beta"])
        h.configure_project(self.key, "web", ["Claude Code"])

    def tearDown(self):  # unlink junctions before the temp folder is removed, so cleanup can't walk into the originals
        for root in (self.t / "claude", self.proj / ".claude/skills", self.proj / ".agents/skills"):
            for p in root.iterdir() if root.is_dir() else []:
                if mgr.is_link(p):
                    plat.unlink_dir(p)

    def link(self, name, rel=".claude/skills"):
        return self.proj / rel / name

    def test_sets_and_projects_are_saved_and_loaded(self):
        data = h.load()
        self.assertEqual(data["sets"]["web"]["skills"], ["alpha", "beta"])
        self.assertEqual(data["projects"][self.key]["set"], "web")
        self.assertEqual(json.loads((mgr.STORE / "harness.json").read_text(encoding="utf-8"))["version"], h.VERSION)
        h.save_set("web", ["alpha", "alpha", "gamma"])  # same name replaces; duplicates collapse
        self.assertEqual(h.load()["sets"]["web"]["skills"], ["alpha", "gamma"])

    def test_unknown_keys_of_a_set_survive_a_save(self):
        f = mgr.STORE / "harness.json"
        f.write_text(json.dumps({"version": 9, "sets": {"x": {"skills": ["alpha"], "files": ["CLAUDE.md"]}}}), encoding="utf-8")
        h.save_set("x", ["beta"])
        self.assertEqual(h.load()["sets"]["x"], {"skills": ["beta"], "files": ["CLAUDE.md"]})

    def test_bad_names_and_odd_files_are_refused_or_ignored(self):
        for bad in ("", "  ", "x" * 41):
            with self.assertRaises(mgr.ManagerError):
                h.save_set(bad, [])
        with self.assertRaises(mgr.ManagerError):
            h.save_set("ok", ["../escape"])
        (mgr.STORE / "harness.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(h.load()["sets"], {})
        (mgr.STORE / "harness.json").write_text(json.dumps(
            {"sets": {"s": {"skills": ["a", "../x", 5]}, "t": 3},
             "projects": {"p": {"links": [{"dir": "..", "name": "a", "target": "z"}]}}}),
            encoding="utf-8")
        data = h.load()
        self.assertEqual(data["sets"], {"s": {"skills": ["a"]}})
        self.assertEqual(data["projects"]["p"]["links"], [])

    def test_apply_links_the_set_into_the_project(self):
        out = h.apply(self.key)
        self.assertEqual({(r.name, r.status) for r in out}, {("alpha", "installed"), ("beta", "installed")})
        self.assertTrue(mgr.is_link(self.link("alpha")))
        self.assertEqual(mgr.target_of(self.link("alpha")), (self.src / "alpha").resolve())
        self.assertFalse(self.link("gamma").exists())
        self.assertEqual(h.applied(self.key), ["alpha", "beta"])
        self.assertEqual({r.status for r in h.apply(self.key)}, {"exists"})  # a second apply changes nothing

    def test_codex_and_antigravity_share_the_agents_folder(self):
        h.configure_project(self.key, "web", ["Claude Code", "Codex·Antigravity"])
        h.apply(self.key)
        self.assertTrue(mgr.is_link(self.link("alpha", ".agents/skills")))
        self.assertTrue(mgr.is_link(self.link("alpha")))

    def test_release_removes_only_our_links_and_keeps_the_originals(self):
        h.apply(self.key)
        write_skill(self.proj / ".claude/skills/handmade")  # the user's own skill in the same folder
        out = h.release(self.key)
        self.assertEqual({r.status for r in out}, {"removed"})
        self.assertFalse(self.link("alpha").exists() or mgr.is_link(self.link("alpha")))
        self.assertTrue((self.src / "alpha/SKILL.md").is_file())
        self.assertTrue((self.link("handmade") / "SKILL.md").is_file())
        self.assertEqual(h.applied(self.key), [])

    def test_a_real_folder_of_the_same_name_is_skipped_and_survives_apply_and_release(self):
        write_skill(self.link("alpha"))
        out = {r.name: r.status for r in h.apply(self.key)}
        self.assertEqual(out, {"alpha": "conflict", "beta": "installed"})
        self.assertFalse(mgr.is_link(self.link("alpha")))
        h.release(self.key)
        self.assertTrue((self.link("alpha") / "SKILL.md").is_file())  # never in the manifest, so never removed

    def test_a_link_the_user_replaced_is_not_removed_on_release(self):
        h.apply(self.key)
        plat.unlink_dir(self.link("beta"))
        write_skill(self.link("beta"))  # the user swapped our link for their own folder
        out = {r.name: r.status for r in h.release(self.key)}
        self.assertEqual(out, {"alpha": "removed", "beta": "conflict"})
        self.assertTrue((self.link("beta") / "SKILL.md").is_file())

    def test_applying_a_smaller_set_drops_only_the_links_it_no_longer_wants(self):
        h.apply(self.key)
        write_skill(self.link("gamma"))
        h.save_set("web", ["alpha"])
        h.apply(self.key)
        self.assertTrue(mgr.is_link(self.link("alpha")))
        self.assertFalse(mgr.is_link(self.link("beta")))
        self.assertTrue((self.link("gamma") / "SKILL.md").is_file())

    def test_a_skill_that_is_not_installed_is_reported_not_linked(self):
        h.save_set("web", ["alpha", "nothere"])
        out = {r.name: r.status for r in h.apply(self.key)}
        self.assertEqual(out, {"alpha": "installed", "nothere": "missing"})

    def test_missing_project_folder_is_an_error_and_remove_still_forgets_it(self):
        h.apply(self.key)
        for n in ("alpha", "beta"):
            plat.unlink_dir(self.link(n))  # so rmtree can't walk into the originals through a link
        shutil.rmtree(self.proj)
        for call in (h.apply, h.release):
            with self.assertRaises(mgr.ManagerError):
                call(self.key)
        self.assertEqual(h.applied(self.key), [])
        h.remove_project(self.key)
        self.assertNotIn(self.key, h.load()["projects"])
        with self.assertRaises(mgr.ManagerError):
            h.add_project(self.t / "never-made")

    def test_paths_are_validated(self):
        (self.t / "file.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(mgr.ManagerError):
            h.add_project(self.t / "file.txt")
        # that folder's project skills ARE the global skills: refuse
        with mock.patch.object(h.mgr, "AGENT_DIRS", {"Claude Code": self.proj / ".claude/skills"}), self.assertRaises(mgr.ManagerError):
            h.add_project(self.proj)
        with self.assertRaises(mgr.ManagerError):
            h.configure_project(str(self.t / "unregistered"), None, [])
        with self.assertRaises(mgr.ManagerError):
            h.configure_project(self.key, "no-such-set", [])
        with self.assertRaises(mgr.ManagerError):
            h.apply(str(self.t / "unregistered"))

    def test_apply_needs_a_chosen_set_and_deleting_a_set_clears_the_choice(self):
        h.delete_set("web")
        self.assertIsNone(h.load()["projects"][self.key]["set"])
        with self.assertRaises(mgr.ManagerError):
            h.apply(self.key)

    def test_git_exclude_is_opt_in_and_taken_back_on_release(self):
        subprocess.run(["git", "init", "-q", str(self.proj)], check=True, capture_output=True)
        exclude = self.proj / ".git/info/exclude"
        before = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        h.apply(self.key)
        self.assertEqual(exclude.read_text(encoding="utf-8") if exclude.is_file() else "", before)  # off by default
        h.release(self.key)
        h.apply(self.key, git_exclude=True)
        self.assertIn("/.claude/skills/alpha", exclude.read_text(encoding="utf-8").splitlines())
        status = subprocess.run(["git", "-C", str(self.proj), "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")
        h.release(self.key)
        self.assertNotIn("alpha", exclude.read_text(encoding="utf-8"))
        self.assertTrue(h.is_git_repo(self.proj))

    def test_guide_tree_shows_what_exists_and_who_made_each_skill_link(self):
        (self.proj / "CLAUDE.md").write_text("x", encoding="utf-8")
        h.apply(self.key)
        write_skill(self.link("handmade"))
        rows = {r["rel"]: r for r in h.guide_tree(self.key)}
        self.assertTrue(rows["CLAUDE.md"]["exists"])
        self.assertFalse(rows["AGENTS.md"]["exists"])
        self.assertEqual(rows[".claude/skills/alpha"]["owner"], "harness")
        self.assertEqual(rows[".claude/skills/handmade"]["owner"], "user")
        self.assertNotIn(".agents/skills/alpha", rows)
        tree = [r["prefix"] + r["name"] for r in h.guide_tree(self.key)]
        self.assertEqual(tree[0], "├─ CLAUDE.md")
        self.assertIn("│  └─ skills", tree)  # .claude is not the last folder, so its children hang off a bar
        self.assertEqual(sorted(p.name for p in self.proj.iterdir()), [".claude", "CLAUDE.md"])  # reading created nothing
        with self.assertRaises(mgr.ManagerError):
            h.guide_tree(str(self.t / "gone"))


if __name__ == "__main__":
    unittest.main()

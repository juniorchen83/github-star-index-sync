from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "install.py"
SPEC = importlib.util.spec_from_file_location("install_skill", SCRIPT)
install_skill = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = install_skill
SPEC.loader.exec_module(install_skill)


class InstallTests(unittest.TestCase):
    def test_installer_creates_shared_copy_and_two_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            args = install_skill.parse_args(["--home", str(home)])
            with mock.patch.object(install_skill, "run_package_tests"):
                self.assertEqual(install_skill.install(args), 0)

            canonical = home / ".agents" / "skills" / install_skill.SKILL_NAME
            codex = home / ".codex" / "skills" / install_skill.SKILL_NAME
            claude = home / ".claude" / "skills" / install_skill.SKILL_NAME
            self.assertTrue((canonical / "SKILL.md").is_file())
            self.assertTrue(codex.is_symlink())
            self.assertTrue(claude.is_symlink())
            self.assertEqual(codex.resolve(), canonical.resolve())
            self.assertEqual(claude.resolve(), canonical.resolve())

    def test_dry_run_does_not_create_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            args = install_skill.parse_args(
                ["--home", str(home), "--dry-run"]
            )
            self.assertEqual(install_skill.install(args), 0)
            self.assertFalse((home / ".agents").exists())
            self.assertFalse((home / ".codex").exists())
            self.assertFalse((home / ".claude").exists())

    def test_existing_unrelated_entry_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            entry = home / ".claude" / "skills" / install_skill.SKILL_NAME
            entry.mkdir(parents=True)
            (entry / "keep.txt").write_text("keep", encoding="utf-8")
            args = install_skill.parse_args(["--home", str(home)])
            with mock.patch.object(install_skill, "run_package_tests"):
                with self.assertRaisesRegex(FileExistsError, "入口已存在"):
                    install_skill.install(args)
            self.assertEqual((entry / "keep.txt").read_text(encoding="utf-8"), "keep")
            self.assertFalse(
                (home / ".agents" / "skills" / install_skill.SKILL_NAME).exists()
            )


if __name__ == "__main__":
    unittest.main()

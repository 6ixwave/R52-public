"""Командная строка: работа без ядра, копии и восстановление, импорт, экспорт, коды выхода."""

import re
import unittest

import helpers
from r52 import __version__
from r52.config import load_config
from r52.desktop.link import CoreLink

BACKUP_NAME = re.compile(r"r52-\d{8}T\d{6}Z-[a-z-]+\.sqlite3")


class CliTest(unittest.TestCase):
    def run_ok(self, home, *args):
        result = helpers.cli(home, *args)
        self.assertEqual(result.returncode, 0, f"{args}: {result.stdout}\n{result.stderr}")
        return result

    def test_version(self):
        self.assertIn(__version__, self.run_ok(helpers.temp_home(self), "version").stdout)

    def test_offline_commands(self):
        home = helpers.temp_home(self)
        self.run_ok(home, "doctor")
        status = helpers.cli(home, "status")
        self.assertEqual(status.returncode, 1, status.stdout + status.stderr)
        self.run_ok(home, "import-eye", str(helpers.write_eye(home, "eye.json")))
        self.run_ok(home, "backup")
        names = BACKUP_NAME.findall(self.run_ok(home, "backups").stdout)
        self.assertTrue(names)
        refused = helpers.cli(home, "restore", names[0])
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        restored = self.run_ok(home, "restore", names[0], "--yes")
        self.assertEqual((restored.stdout + restored.stderr).count("База восстановлена"), 1)
        self.run_ok(home, "export")
        self.assertTrue(list(load_config(home).paths.exports_dir.glob("*.zip")))
        self.run_ok(home, "migrate")

    def test_second_core_refused(self):
        home = helpers.temp_home(self)
        link = CoreLink(home)
        link.connect()
        try:
            second = helpers.cli(home, "serve")
            self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
            status = self.run_ok(home, "status")
            self.assertIn(__version__, status.stdout)
            self.assertNotIn("MANUAL", status.stdout)
            restore = helpers.cli(home, "restore", "anything.sqlite3", "--yes")
            self.assertNotEqual(restore.returncode, 0)
        finally:
            link.stop()

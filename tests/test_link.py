"""Связь окна с ядром: запуск, подключение к работающему ядру, остановка."""

import subprocess
import sys
import time
import unittest

import helpers
from r52.desktop.link import CoreLink
from r52.lease import is_core_running


class CoreLinkTest(unittest.TestCase):
    def test_start_attach_stop(self):
        home = helpers.temp_home(self)
        first = CoreLink(home)
        self.assertEqual(first.connect(), "started")
        process = first.process
        try:
            self.assertTrue(first.owns_core)
            self.assertEqual(first.client.request("GET", "/api/status")["service"], "r52-core")
            self.assertEqual(first.connect(), "started")  # повторное подключение ничего не запускает
            second = CoreLink(home)
            self.assertEqual(second.connect(), "attached")
            self.assertFalse(second.owns_core)
            second.stop()  # чужое ядро не останавливается
            self.assertIsNone(process.poll())
        finally:
            first.stop()
        self.assertEqual(process.returncode, 0)
        self.assertFalse(first.paths.runtime_file.exists())
        self.assertFalse(is_core_running(first.paths.lock_file))

    def test_api_shutdown_with_open_stdin(self):
        """Ядро окна, остановленное через API при открытом канале stdin, завершается чисто, без аварийного abort."""
        home = helpers.temp_home(self)
        link = CoreLink(home)
        self.assertEqual(link.connect(), "started")
        process = link.process
        try:
            link.client.request("POST", "/api/system/shutdown", {})
            self.assertEqual(process.wait(40), 0)
        finally:
            link.stop()
        self.assertFalse(link.paths.runtime_file.exists())
        self.assertFalse(is_core_running(link.paths.lock_file))
        self.assertEqual(link.connect(), "started")  # после остановки окно запускает новое ядро
        again = link.process
        link.stop()
        self.assertEqual(again.returncode, 0)

    def test_attach_to_background_core(self):
        home = helpers.temp_home(self)
        core = subprocess.Popen([sys.executable, "-m", "r52", "serve", "--home", str(home)], cwd=helpers.ROOT,
                                env=helpers.child_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            link = CoreLink(home)
            deadline = time.monotonic() + 40
            while not (is_core_running(link.paths.lock_file) and link.paths.runtime_file.exists()):
                self.assertIsNone(core.poll(), "фоновое ядро завершилось")
                self.assertLess(time.monotonic(), deadline, "фоновое ядро не запустилось")
                time.sleep(0.1)
            self.assertEqual(link.connect(), "attached")
            link.stop()
            time.sleep(0.5)
            self.assertIsNone(core.poll())
            link.client.request("POST", "/api/system/shutdown", {})
            self.assertEqual(core.wait(40), 0)
        finally:
            if core.poll() is None:
                core.kill()
                core.wait(10)

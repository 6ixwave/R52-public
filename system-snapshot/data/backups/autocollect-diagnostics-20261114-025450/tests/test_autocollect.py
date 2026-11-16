import json
import os
import subprocess
import sys
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import helpers
from r52.api.routes import build_router
from r52.config import load_config
from r52.core.autocollect import accept_deliveries, batch
from r52.errors import AppError
from r52.service import App


class AutoCollectTest(unittest.TestCase):
    def setUp(self):
        home = helpers.temp_home(self)
        config = replace(load_config(home), intake_folders=(home/"manual",))
        self.app = App(config, install_guard=False).open()
        self.addCleanup(self.app.stop)
        self.router = build_router(self.app)

    def api(self, method, path, body=None):
        return self.router.dispatch(method, "/api/autocollect" + path, {}, body or {})[1]

    def test_collect_accept_refresh_and_disable(self):
        first = helpers.eye_document()
        first.update(format="JARVIS_BR02_COLLECTOR_AUTONOMOUS", capture_action="COLLECTOR_RUN", fully_autonomous=True)
        second = helpers.eye_document(prices=(("regular","30000","30 000 ₽"),), observed_at="2026-11-02T09:00:00Z")
        second.update(format=first["format"], capture_action="COLLECTOR_RUN", fully_autonomous=True)
        calls = []

        def fixture(app, ctx, params, directory):
            calls.append(params["mode"])
            doc = second if params["mode"] == "refresh" else first
            (directory/"capture.json").write_text(json.dumps(doc), encoding="utf-8")

        body = dict(source="OZON", kind="query", value="тест", item_limit=1)
        target = self.api("POST", "/targets", body)["id"]
        self.api("PUT", f"/targets/{target}", dict(body,value="тест 2"))
        with patch("r52.core.autocollect.invoke",side_effect=fixture):
            for index in (1,2):
                task = self.api("POST","/run")["task_id"]
                self.assertEqual(task,self.api("POST","/run")["task_id"])
                self.app.tasks.run_one()
                self.app.watcher.scan()
                self.app.watcher.scan()
                accept_deliveries(self.app)
                with self.app.db.read() as conn:
                    self.assertEqual(index,conn.execute("SELECT count(*) FROM observation").fetchone()[0])
                    self.assertEqual(1,conn.execute("SELECT count(*) FROM product").fetchone()[0])
                    self.assertEqual("COMPLETED", conn.execute("SELECT status FROM task WHERE id=?", (task,)).fetchone()[0])
        self.assertEqual(calls,["discover","discover","refresh"])
        self.api("DELETE",f"/targets/{target}")
        self.app.settings.update({"autocollect.enabled":False})
        with self.assertRaises(AppError):
            self.api("POST","/run")

    def test_url_validation(self):
        with self.assertRaises(AppError):
            self.api("POST","/targets",dict(source="WB",kind="url",value="https://127.0.0.1/",item_limit=1))


    def test_saved_targets_survive_restart_and_blocks_are_local(self):
        self.app.settings.update({"autocollect.new_limit": 1})
        for source in ("AVITO", "OZON", "WB"):
            self.api("POST", "/targets", dict(source=source,value="карта",item_limit=5))
        self.api("PATCH","/sources/WB",{"enabled":False})
        self.app.stop()
        self.app.open()
        self.router = build_router(self.app)
        attempted = []
        def source_batch(app, ctx, params):
            attempted.append((params["source"],params["limit"]))
            if params["source"] == "AVITO":
                raise ValueError("LISTING_HTTP_429")
            return 1, 0, 0
        with patch("r52.core.autocollect.batch",side_effect=source_batch):
            self.api("POST", "/run")
            self.app.tasks.run_one()
        self.assertEqual(attempted,[("AVITO",1),("OZON",1)])
        state = self.api("GET","")
        avito = next(s for s in state["sources"] if s["source"]=="AVITO")
        self.assertEqual(avito["last_result"]["reason"],"LISTING_HTTP_429")
        self.assertTrue(avito["retry_after"])
        attempted.clear()
        with patch("r52.core.autocollect.batch", side_effect=source_batch):
            self.api("POST", "/run")
            self.app.tasks.run_one()
        self.assertIn(("AVITO", 1), attempted)

    def test_failed_worker_log_is_preserved(self):
        def fail(app, ctx, params, directory):
            (directory / "worker.log").write_text("offline diagnostic", encoding="utf-8")
            raise TimeoutError("WORKER_TIMEOUT")

        ctx = SimpleNamespace(check=lambda: None)
        with patch("r52.core.autocollect.invoke", side_effect=fail):
            with self.assertRaisesRegex(TimeoutError, "WORKER_TIMEOUT"):
                batch(self.app, ctx, {"source": "WB"})
        logs = list(self.app.paths.logs_dir.glob("collect-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].read_text(encoding="utf-8"), "offline diagnostic")
        self.assertEqual(list(self.app.paths.run_dir.glob("collect-*")), [])

    def test_generic_source_page_uses_only_matching_links(self):
        from r52.collectors.discovery import ListingLinks
        parser = ListingLinks("ALIEXPRESS","https://www.aliexpress.com/wholesale", 2)
        parser.feed('<a href="/item/111.html">a</a><a href="https://other.example/item/222.html">b</a>'
                    '<a href="/item/111.html">duplicate</a><a href="/item/333.html">c</a>')
        self.assertEqual(list(parser.links),["111","333"])


class AutoCollectStartupTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows inherited stdin regression")
    def test_worker_starts_while_core_waits_for_desktop_stdin(self):
        script = textwrap.dedent('''
            import sys, threading, time, tempfile
            from pathlib import Path
            from types import SimpleNamespace
            from unittest.mock import patch
            from r52.core.autocollect import invoke

            ready = threading.Event()
            def wait_for_desktop():
                ready.set()
                sys.stdin.readline()
            threading.Thread(target=wait_for_desktop, daemon=True).start()
            ready.wait()
            time.sleep(0.2)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                app = SimpleNamespace(paths=SimpleNamespace(home=path))
                ctx = SimpleNamespace(check=lambda: None)
                with patch("r52.core.autocollect.check_enabled"):
                    try:
                        invoke(app, ctx, {"source": "OFFLINE_INVALID_SOURCE", "limit": 1}, path)
                    except ValueError as error:
                        assert str(error) == "VALUEERROR", str(error)
                    else:
                        raise AssertionError("Unknown source must fail before any network access")
                assert (path / "error.txt").read_text() == "VALUEERROR"
            print("WORKER_STARTED", flush=True)
        ''')
        for executable in (Path(sys.executable), Path(sys.executable).with_name("pythonw.exe")):
            with self.subTest(executable=executable.name):
                process = subprocess.Popen(
                    [str(executable), "-B", "-c", script], cwd=helpers.ROOT,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    self.fail("Worker stalled while desktop stdin was held open")
                finally:
                    stdout, stderr = process.communicate()
                self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
                self.assertIn(b"WORKER_STARTED", stdout)

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import unittest
from dataclasses import replace
from contextlib import closing
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
        self.app.settings.update({"autocollect.broad_search": False})
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

    def test_real_subprocess_error_reaches_source_status_and_saved_log(self):
        # Only the source's network operation is replaced inside the child.
        # Runner, process supervision, error.txt, log preservation and API are real.
        bootstrap = textwrap.dedent('''
            import runpy
            from types import SimpleNamespace
            from unittest.mock import patch
            from r52.collectors.discovery import SearchError
            class OfflineCollector:
                def __init__(self, **kwargs):
                    self.pool = SimpleNamespace(stats=lambda: {"total":0,"alive":0})
                    self.stats = {}
                def collect_by_search(self, *args, **kwargs):
                    raise SearchError("SEARCH_HTTP_403")
                def close(self):
                    pass
            with patch("r52.collectors.auto_worker._get_collector_class", return_value=OfflineCollector):
                runpy.run_module("r52.collectors.runner", run_name="__main__")
        ''')
        original_popen = subprocess.Popen
        def spawn(command, **kwargs):
            self.assertEqual(command[1:3], ["-m", "r52.collectors.runner"])
            return original_popen([command[0], "-B", "-c", bootstrap, *command[3:]], **kwargs)
        self.api("POST", "/targets", dict(source="OZON", kind="query", value="offline", item_limit=1))
        with patch("r52.core.autocollect.subprocess.Popen", side_effect=spawn):
            task_id = self.api("POST", "/run")["task_id"]
            self.app.tasks.run_one()
        source = next(s for s in self.api("GET", "")["sources"] if s["source"] == "OZON")
        self.assertEqual(source["last_result"]["reason"], "SEARCH_HTTP_403")
        with self.app.db.read() as conn:
            self.assertEqual(conn.execute("SELECT status FROM task WHERE id=?", (task_id,)).fetchone()[0], "PARTIAL")
            self.assertEqual(conn.execute("SELECT count(*) FROM product").fetchone()[0], 0)
        logs = list(self.app.paths.logs_dir.glob("collect-*.log"))
        self.assertEqual(len(logs), 1)
        text = logs[0].read_text(encoding="utf-8", errors="replace")
        self.assertIn("phase=worker_failed code=SEARCH_HTTP_403", text)
        self.assertIn("Traceback", text)

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

    def test_empty_successful_worker_log_is_preserved(self):
        def empty(app, ctx, params, directory):
            (directory / "worker.log").write_text("listing=123 result=NO_RESULT", encoding="utf-8")
        with patch("r52.core.autocollect.invoke", side_effect=empty):
            result = batch(self.app, SimpleNamespace(check=lambda: None),
                           {"source": "OZON", "mode": "refresh", "ids": ["123"], "limit": 1})
        self.assertEqual(result, (0, 0, 0))
        logs = list(self.app.paths.logs_dir.glob("collect-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("NO_RESULT", logs[0].read_text())

    def test_broad_search_imports_and_rotates_without_user_queries(self):
        self.app.settings.update({"autocollect.broad_search": True})
        for source in ("WB", "AVITO", "FARPOST", "ALIEXPRESS", "ALIEXPRESS_RU"):
            self.api("PATCH", "/sources/" + source, {"enabled": False})
        first = helpers.eye_document()
        first.update(format="JARVIS_BR02_COLLECTOR_AUTONOMOUS", capture_action="COLLECTOR_RUN", fully_autonomous=True)
        searches = []
        def fixture(app, ctx, params, directory):
            if params["mode"] == "discover":
                searches.append((params["value"], params["page"]))
            (directory / "capture.json").write_text(json.dumps(first), encoding="utf-8")
        with patch("r52.core.autocollect.invoke", side_effect=fixture):
            self.app.tasks.submit("autocollect", {})
            self.app.tasks.run_one()
            self.app.watcher.scan()
            self.app.watcher.scan()
            accept_deliveries(self.app)
            with self.app.db.read() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM product").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT count(*) FROM auto_target WHERE origin='USER'").fetchone()[0], 0)
            self.app.tasks.submit("autocollect", {})
            self.app.tasks.run_one()
            self.assertEqual(len(searches), 1)
            self.app.stop()
            self.app.open()
            self.router = build_router(self.app)
            self.api("POST", "/run")
            self.app.tasks.run_one()
        self.assertEqual(searches, [("смартфоны", 1), ("ноутбуки", 1)])
        with self.app.db.read() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM auto_target WHERE origin='CATALOG'").fetchone()[0], 96)
            self.assertEqual(conn.execute("SELECT page FROM auto_target WHERE source='OZON' AND value='смартфоны'").fetchone()[0], 2)

    def test_broad_search_respects_budget_and_disabled_mode(self):
        self.app.settings.update({"autocollect.broad_search": True, "autocollect.new_limit": 1})
        attempted = []
        def source_batch(app, ctx, params):
            attempted.append((params["source"], params["limit"]))
            return 1, 0, 0
        with patch("r52.core.autocollect.batch", side_effect=source_batch):
            for _ in range(2):
                self.api("POST", "/run")
                self.app.tasks.run_one()
            self.assertEqual(len(attempted), 2)
            self.assertNotEqual(attempted[0][0], attempted[1][0])
            self.assertEqual([v[1] for v in attempted], [1, 1])
            self.app.settings.update({"autocollect.broad_search": False})
            self.api("POST", "/run")
            self.app.tasks.run_one()
            self.assertEqual(len(attempted), 2)

    def test_manual_query_can_take_over_automatic_category(self):
        ident = self.api("POST", "/targets", dict(source="WB", kind="query", value="смартфоны", item_limit=2))["id"]
        target = next(t for t in self.api("GET", "")["targets"] if t["id"] == ident)
        self.assertEqual(target["origin"], "USER")
        self.assertEqual(target["item_limit"], 2)
        with self.assertRaises(AppError):
            self.api("POST", "/targets", dict(source="WB", value="смартфоны", item_limit=2))

    def test_generic_source_page_uses_only_matching_links(self):
        from r52.collectors.discovery import ListingLinks
        parser = ListingLinks("ALIEXPRESS","https://www.aliexpress.com/wholesale", 2)
        parser.feed('<a href="/item/111.html">a</a><a href="https://other.example/item/222.html">b</a>'
                    '<a href="/item/111.html">duplicate</a><a href="/item/333.html">c</a>')
        self.assertEqual(list(parser.links),["111","333"])


class AutoCollectStartupTest(unittest.TestCase):
    def test_upgrade_keeps_existing_manual_targets(self):
        from r52.db.schema import V9_AUTOCOLLECT, V10_BROAD_SEARCH
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript(V9_AUTOCOLLECT)
            conn.execute("INSERT INTO auto_target(source,kind,value,enabled,item_limit,page) VALUES('WB','query','смартфоны',0,3,7)")
            conn.execute("UPDATE auto_source SET enabled=0 WHERE source='WB'")
            conn.executescript(V10_BROAD_SEARCH)
            self.assertEqual(conn.execute("SELECT origin,enabled,item_limit,page FROM auto_target WHERE source='WB' AND value='смартфоны'").fetchone(),
                             ("USER", 0, 3, 7))
            self.assertEqual(conn.execute("SELECT count(*) FROM auto_target WHERE origin='CATALOG'").fetchone()[0], 95)
            self.assertEqual(conn.execute("SELECT enabled FROM auto_source WHERE source='WB'").fetchone()[0], 0)

    def test_no_result_identifies_listing_and_unattempted_items(self):
        from r52.collectors import auto_worker
        from unittest.mock import Mock
        collector = Mock()
        collector.collect_product.return_value = None
        collector.pool.stats.return_value = {"total": 0, "alive": 0, "use_proxies": True}
        collector.stats = {"requests": 1, "failed": 1}
        path = helpers.temp_home(self)
        with patch.object(auto_worker, "_get_collector_class", return_value=Mock(return_value=collector)):
            with self.assertLogs("r52.autocollect.worker", level="INFO") as output:
                auto_worker.run({"source": "OZON", "mode": "refresh", "home": str(path),
                                 "ids": ["111", "222"], "limit": 2}, path)
        messages = "\n".join(output.output)
        self.assertIn("listing=111", messages)
        self.assertIn("result=NO_RESULT", messages)
        self.assertIn("remaining_not_attempted=['222']", messages)
        self.assertIn("phase=write_complete", messages)
        collector.collect_product.assert_called_once()
        collector.close.assert_called_once()

    def test_uncaught_error_retains_traceback(self):
        from r52.collectors import auto_worker
        path = helpers.temp_home(self)
        (path / "request").write_text("{}")
        with patch.object(auto_worker, "follow_parent"), patch.object(auto_worker, "faulthandler"), \
             patch.object(auto_worker, "run", side_effect=RuntimeError("offline failure")):
            with self.assertLogs(level="ERROR") as output, self.assertRaises(SystemExit):
                auto_worker.main(path, os.getpid())
        self.assertIn("Traceback", "\n".join(output.output))
        self.assertIn("RuntimeError: offline failure", "\n".join(output.output))
        self.assertEqual((path / "error.txt").read_text(), "RUNTIMEERROR")

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
                        invoke(app, ctx, {"source": "OFFLINE_INVALID_SOURCE", "mode": "refresh", "limit": 1}, path)
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

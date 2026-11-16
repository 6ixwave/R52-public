"""Local integration acceptance; no marketplace connections or owner data."""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import timedelta
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import helpers
from r52.api.routes import build_router
from r52.config import load_config
from r52.client import CoreClient
from r52.errors import AppError
from r52.core.autocollect import accept_deliveries, batch, invoke, publish
from r52.core.tasks import TaskCancelled
from r52.service import App
from r52.timeutil import FixedClock, iso, parse_aware


def export(listing="100200300", observed="2026-11-14T00:00:00Z"):
    doc = helpers.eye_document(listing_id=listing, observed_at=observed)
    doc.update(format="JARVIS_BR02_COLLECTOR_AUTONOMOUS", capture_action="COLLECTOR_RUN", fully_autonomous=True)
    return json.dumps(doc).encode("utf-8")


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.clock = FixedClock(parse_aware("2026-11-14T00:00:00Z"))
        self.app = App(load_config(helpers.temp_home(self)), clock=self.clock, install_guard=False).open()
        self.addCleanup(self.app.stop)
        self.app.settings.update({"autocollect.broad_search": False})
        self.router = build_router(self.app)

    def api(self, method="GET", path="", body=None):
        return self.router.dispatch(method, "/api/autocollect" + path, {}, body or {})[1]

    def target(self, source="OZON"):
        return self.api("POST", "/targets", dict(source=source, kind="query", value="offline", item_limit=2))

    def drain(self):
        self.app.watcher.scan()
        self.app.watcher.scan()
        accept_deliveries(self.app)

    def test_partial_worker_export_survives_error_and_import_is_distinct(self):
        self.target()
        def worker(app, ctx, params, directory):
            (directory / "item.json").write_bytes(export())
            (directory / "invalid.json").write_text("not json")
            (directory / "worker.log").write_text("phase=close_start\nclose failure")
            raise ValueError("CLOSE_FAILED")
        with patch("r52.core.autocollect.invoke", side_effect=worker):
            task = self.api("POST", "/run")["task_id"]
            self.app.tasks.run_one()
        result = self.app.tasks.get(task)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["result"]["found"], 1)
        self.assertEqual(result["result"]["sources"]["OZON"]["reason"], "CLOSE_FAILED")
        state = next(s for s in self.api()["sources"] if s["source"] == "OZON")
        self.assertEqual((state["pending_import"], state["imported_observations"]), (1, 0))
        self.drain()
        self.drain()
        state = next(s for s in self.api()["sources"] if s["source"] == "OZON")
        self.assertEqual((state["pending_import"], state["imported_observations"]), (0, 1))
        self.assertTrue((self.app.paths.logs_dir / result["result"]["sources"]["OZON"]["diagnostic"]).is_file())

    def test_backoff_starts_after_slow_failure(self):
        self.target()
        self.app.settings.update({"autocollect.discovery_seconds": 60})
        def slow(app, ctx, params, directory):
            self.clock.advance(120)
            raise TimeoutError("WORKER_TIMEOUT")
        with patch("r52.core.autocollect.invoke", side_effect=slow):
            self.api("POST", "/run")
            self.app.tasks.run_one()
        state = next(s for s in self.api()["sources"] if s["source"] == "OZON")
        self.assertEqual(state["last_run"], iso(self.clock.now()))
        self.assertEqual(state["retry_after"], iso(self.clock.now() + timedelta(seconds=60)))
        with patch("r52.core.autocollect.invoke") as worker:
            self.app.tasks.submit("autocollect", {})
            self.app.tasks.run_one()
            worker.assert_not_called()

    def test_manual_product_uses_same_subprocess_publish_path(self):
        item, _ = self.app.inbox.stage_bytes(export(observed="2026-11-13T00:00:00Z"))
        product = self.app.inbox.accept(item["id"])["product_id"]
        def worker(app, ctx, params, directory):
            self.assertEqual(params, dict(source="OZON", mode="refresh", ids=["100200300"], limit=1))
            (directory / "item.json").write_bytes(export())
        with patch("r52.core.autocollect.invoke", side_effect=worker) as called:
            task, _ = self.app.tasks.submit("collect", {"product_id": product})
            self.app.tasks.run_one()
            called.assert_called_once()
        saved = self.app.tasks.get(task["id"])
        self.assertEqual(saved["status"], "COMPLETED")
        self.assertTrue(saved["result"]["import_pending"])
        self.drain()
        with self.app.db.read() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM observation").fetchone()[0], 2)

    def test_source_only_collect_stays_with_selected_source(self):
        self.target("OZON")
        self.target("WB")
        def worker(app, ctx, params, directory):
            self.assertEqual(params["source"], "OZON")
            (directory / "item.json").write_bytes(export())
        with patch("r52.core.autocollect.invoke", side_effect=worker) as called:
            task, _ = self.app.tasks.submit("collect", {"source": "OZON"})
            self.app.tasks.run_one()
            called.assert_called_once()
        self.assertEqual(self.app.tasks.get(task["id"])["status"], "COMPLETED")

    def test_delivery_recovery_disable_and_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "item.json").write_bytes(export())
            params = dict(source="OZON", mode="discover", limit=1)
            self.assertEqual(publish(self.app, params, folder), (1, 0, 0))
            target = next((self.app.paths.home / "auto-inbox").glob("*.json"))
            target.replace(target.with_suffix(".pending"))
            home = self.app.paths.home
            self.app.stop()
            self.app = App(load_config(home), clock=self.clock, install_guard=False).open()
            self.addCleanup(self.app.stop)
            accept_deliveries(self.app)
            self.assertTrue(target.is_file())
            self.app.watcher.scan()
            self.app.watcher.scan()
            self.app.settings.update({"intake.enabled": False})
            accept_deliveries(self.app)
            with self.app.db.read() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM observation").fetchone()[0], 0)
            self.app.settings.update({"intake.enabled": True})
            accept_deliveries(self.app)
            self.assertEqual(publish(self.app, params, folder), (0, 0, 1))
            self.drain()
            with self.app.db.read() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM observation").fetchone()[0], 1)

    def test_scheduler_first_run_dedup_disable_and_defaults(self):
        self.app.scheduler.configure_auto(self.app.settings.all())
        self.assertEqual({s["key"] for s in self.app.scheduler.list()}, {"backup", "maintenance", "digest", "autocollect"})
        self.clock.advance(14)
        self.assertEqual(self.app.scheduler.tick()["submitted"], [])
        self.clock.advance(1)
        first = self.app.scheduler.tick()["submitted"]
        self.assertEqual(len(first), 1)
        self.clock.advance(60)
        self.assertEqual(self.app.scheduler.tick()["submitted"], first)
        self.app.settings.update({"autocollect.enabled": False})
        self.clock.advance(60)
        self.assertEqual(self.app.scheduler.tick()["submitted"], [])
        with patch("r52.core.autocollect.invoke") as called:
            self.app.tasks.run_one()
            called.assert_not_called()
        self.assertEqual(self.app.tasks.get(first[0])["status"], "BLOCKED")

    def test_deadline_keeps_source_diagnostic_and_unpublished_export(self):
        self.target()
        def worker(app, ctx, params, directory):
            (directory / "item.json").write_bytes(export())
            self.clock.advance(3601)
        with patch("r52.core.autocollect.invoke", side_effect=worker):
            task = self.api("POST", "/run")["task_id"]
            self.app.tasks.run_one()
        saved = self.app.tasks.get(task)
        self.assertEqual(saved["status"], "PARTIAL")
        self.assertEqual(saved["error_code"], "LOCAL_TASK_DEADLINE")
        self.assertEqual(saved["result"]["sources"]["OZON"]["reason"], "LOCAL_TASK_DEADLINE")
        self.assertEqual(len(list(self.app.paths.logs_dir.glob("collect-*-exports/item.json"))), 1)
        self.assertEqual(list((self.app.paths.home / "auto-inbox").glob("*.json")), [])

    def test_cancel_preserves_export_without_import(self):
        def worker(app, ctx, params, directory):
            (directory / "item.json").write_bytes(export())
            raise TaskCancelled()
        with patch("r52.core.autocollect.invoke", side_effect=worker), self.assertRaises(TaskCancelled):
            batch(self.app, SimpleNamespace(check=lambda: None), dict(source="OZON", mode="discover", limit=1))
        self.assertEqual(len(list(self.app.paths.logs_dir.glob("collect-*-exports/item.json"))), 1)
        self.drain()
        with self.app.db.read() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM observation").fetchone()[0], 0)

    def test_disabled_queued_target_is_not_executed(self):
        first = self.target()["id"]
        second = self.api("POST", "/targets", dict(source="OZON", value="second", item_limit=1))["id"]
        def worker(app, ctx, params, directory):
            self.assertEqual(params["value"], "offline")
            with self.app.db.write() as conn:
                conn.execute("UPDATE auto_target SET enabled=0 WHERE id=?", (second,))
            (directory / "item.json").write_bytes(export())
        with patch("r52.core.autocollect.invoke", side_effect=worker) as called:
            self.api("POST", "/run")
            self.app.tasks.run_one()
            called.assert_called_once()

    def test_wrong_source_and_refresh_id_never_reach_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "item.json").write_bytes(export())
            for params in (dict(source="WB", mode="discover", limit=1),
                           dict(source="OZON", mode="refresh", limit=1, ids=["999"])):
                with self.subTest(params=params):
                    self.assertEqual(publish(self.app, params, folder), (0, 1, 0))
            self.drain()
            with self.app.db.read() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM observation").fetchone()[0], 0)


@unittest.skipUnless(os.name == "nt", "Windows process/job acceptance")
class WorkerLifetimeTest(unittest.TestCase):
    def test_abrupt_core_exit_stops_worker_and_descendant(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        worker = textwrap.dedent('''
            import os,sys,time,subprocess,json
            from pathlib import Path
            from r52.collectors.auto_worker import follow_parent
            follow_parent(os.getppid())
            child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
            Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]))
            time.sleep(60)
        ''')
        parent = textwrap.dedent('''
            import sys,time,subprocess
            subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]],creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(60)
        ''')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"pids"
            proc = subprocess.Popen([sys.executable,"-B","-c",parent,worker,str(path)],cwd=helpers.ROOT,
                                    env=helpers.child_env(),creationflags=subprocess.CREATE_NO_WINDOW)
            handles = []
            try:
                deadline = time.monotonic()+10
                while time.monotonic()<deadline:
                    try:
                        pids=json.loads(path.read_text())
                        break
                    except (FileNotFoundError, ValueError):
                        time.sleep(.05)
                else:
                    self.fail("Worker startup did not complete")
                for pid in pids:
                    handle=kernel.OpenProcess(0x100000,False,pid)
                    self.assertTrue(handle)
                    handles.append(handle)
                proc.kill()
                proc.wait(timeout=5)
                for handle in handles:
                    self.assertEqual(kernel.WaitForSingleObject(handle,5000),0,"Process survived abrupt parent exit")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                for handle in handles:
                    kernel.CloseHandle(handle)

    def test_timeout_cancel_and_normal_exit_stop_descendants(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        bootstrap = textwrap.dedent('''
            import os,sys,time,subprocess
            from pathlib import Path
            from r52.collectors.auto_worker import follow_parent
            follow_parent(os.getppid())
            folder=Path(sys.argv[1]); mode=sys.argv[2]
            child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], creationflags=subprocess.CREATE_NO_WINDOW)
            (folder/'child.pid').write_text(str(child.pid))
            while not (folder/'observed').exists(): time.sleep(.01)
            if mode != 'normal': time.sleep(60)
        ''')
        original = subprocess.Popen
        for mode in ("timeout", "cancel", "normal"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                handles = []
                def spawn(command, **kwargs):
                    return original([sys.executable, "-B", "-c", bootstrap, str(directory), mode], **kwargs)
                def check():
                    file = directory / "child.pid"
                    if file.exists() and not handles:
                        try:
                            pid = int(file.read_text())
                        except ValueError:
                            return
                        handle = kernel.OpenProcess(0x100000, False, pid)
                        self.assertTrue(handle)
                        handles.append(handle)
                        (directory / "observed").touch()
                        if mode == "cancel":
                            raise TaskCancelled()
                try:
                    with patch("r52.core.autocollect.subprocess.Popen", side_effect=spawn), \
                         patch("r52.core.autocollect.check_enabled"), \
                         patch("r52.core.autocollect.WORKER_MAX_SECONDS", 3):
                        args = (SimpleNamespace(paths=SimpleNamespace(home=directory)), SimpleNamespace(check=check),
                                dict(source="OZON", mode="refresh", limit=1), directory)
                        if mode == "normal":
                            invoke(*args)
                        else:
                            with self.assertRaises(TimeoutError if mode == "timeout" else TaskCancelled):
                                invoke(*args)
                    self.assertEqual(len(handles), 1, "Worker never created a child")
                    self.assertEqual(kernel.WaitForSingleObject(handles[0], 5000), 0, "Descendant survived worker exit")
                finally:
                    for handle in handles:
                        kernel.CloseHandle(handle)


class LiveLocalPipelineTest(unittest.TestCase):
    def test_http_scheduler_real_worker_watcher_history_and_stop(self):
        """Real local services/processes; only external marketplace is replaced."""
        config = replace(load_config(helpers.temp_home(self)), intake_folders=(), api_port=0)
        app = App(config, install_guard=False).open()
        self.addCleanup(app.stop)
        app.settings.update({"autocollect.broad_search": False})
        bootstrap = textwrap.dedent('''
            import json,runpy
            from types import SimpleNamespace
            from unittest.mock import patch
            class OfflineCollector:
                def __init__(self, **kwargs):
                    assert kwargs['use_proxies'] is True
                    self.pool=SimpleNamespace(stats=lambda: {'total':0,'alive':0})
                    self.stats={}
                def collect_by_search(self,*args,**kwargs): return [FIRST]
                def collect_product(self,*args,**kwargs): return SECOND
                def build_url(self,id): return 'https://www.ozon.ru/product/'+id+'/'
                def write_observations(self,rows,directory):
                    for i,row in enumerate(rows):
                        (directory/(str(i)+'.json')).write_text(json.dumps(row),encoding='utf-8')
                def close(self): pass
            with patch('r52.collectors.auto_worker._get_collector_class',return_value=OfflineCollector):
                runpy.run_module('r52.collectors.runner',run_name='__main__')
        ''').replace("FIRST", repr(json.loads(export(observed="2026-11-13T00:00:00Z")))).replace("SECOND", repr(json.loads(export())))
        original = subprocess.Popen
        def spawn(command, **kwargs):
            self.assertEqual(command[1:3], ["-m", "r52.collectors.runner"])
            return original([command[0], "-B", "-c", bootstrap, *command[3:]], **kwargs)
        def wait_for(predicate, seconds):
            deadline = time.monotonic()+seconds
            while time.monotonic()<deadline:
                if predicate():
                    return
                time.sleep(.1)
            self.fail("Local pipeline did not finish within bounded wait")
        with patch("r52.core.autocollect.subprocess.Popen", side_effect=spawn):
            app.start(port=0)
            client = CoreClient(app.paths)
            client.request("POST", "/api/autocollect/targets", dict(source="OZON", kind="query", value="offline", item_limit=1))
            def count():
                state=client.request("GET", "/api/autocollect")
                return next(s["imported_observations"] for s in state["sources"] if s["source"]=="OZON")
            wait_for(lambda: count()==1, 40)
            with app.db.read() as conn:
                self.assertEqual(conn.execute("SELECT origin FROM task WHERE kind='autocollect'").fetchone()[0], "SCHEDULE")
                product=conn.execute("SELECT id FROM product").fetchone()[0]
            task=client.request("POST", "/api/tasks", {"kind":"collect","params":{"product_id":product}})["task"]["id"]
            wait_for(lambda: count()==2, 20)
            self.assertEqual(client.request("GET", "/api/tasks/"+task)["status"], "COMPLETED")
            client.request("PATCH", "/api/settings", {"autocollect.enabled":False})
            with self.assertRaises(AppError) as failure:
                client.request("POST", "/api/autocollect/run", {})
            self.assertEqual(failure.exception.code, "AUTOCOLLECT_DISABLED")
            self.assertEqual(len(list(app.paths.logs_dir.glob("collect-*.log"))), 2)
            app.stop()
            self.assertFalse(app.tasks.alive())
            self.assertFalse(app.scheduler.alive())
            self.assertFalse(app.watcher.alive())
            self.assertFalse(app.paths.runtime_file.exists())

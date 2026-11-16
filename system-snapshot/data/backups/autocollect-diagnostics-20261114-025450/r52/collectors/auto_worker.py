"""One bounded invocation of a registered collector. Only this process uses the network."""
import json
import logging
import os
import sys
import threading
from pathlib import Path

from .runner import _get_collector_class
from .discovery import collect_links


def follow_parent(pid):
    """Exit even on an ungraceful core shutdown; on Windows take descendants with us."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        parent = kernel.OpenProcess(0x100000, False, pid)
        if not parent:
            raise RuntimeError("CORE_GONE")
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        def watch():
            kernel.WaitForSingleObject(parent, 0xffffffff)
            os._exit(1)
        # Set kill-on-close before a collector can start browser children.
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("process_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                        ("memory", ctypes.c_size_t * 4)]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        job = kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000
        handle = wintypes.HANDLE(job)
        if not job or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(-1)):
            raise ctypes.WinError(ctypes.get_last_error())
        # The process owns both handles until exit.
    else:
        import time
        def watch():
            while os.getppid() == pid:
                time.sleep(0.5)
            os._exit(1)
    threading.Thread(target=watch, daemon=True).start()


def run(params, directory):
    source = params["source"]
    collector = _get_collector_class(source)(data_dir=Path(params["home"]), use_proxies=True)
    try:
        if params["mode"] == "refresh":
            rows = []
            for listing_id in params["ids"]:
                row = collector.collect_product(listing_id, collector.build_url(listing_id))
                if not row or row.get("observation", {}).get("status") in ("FAILED", "BLOCKED"):
                    break
                rows.append(row)
        elif params["kind"] == "url" or params["value"].startswith("https://"):
            rows = collect_links(collector, source, params["value"], params["limit"], params["page"])
        else:
            rows = collector.collect_by_search(params["value"], page=params["page"], page_size=params["limit"])
        valid = [r for r in rows[:params["limit"]] if isinstance(r, dict) and
                 r.get("observation", {}).get("status") not in ("FAILED", "BLOCKED")]
        collector.write_observations(valid, directory)
    finally:
        collector.close()


def main(folder, parent_pid):
    follow_parent(parent_pid)
    try:
        run(json.loads((folder / "request").read_text("utf-8")), folder)
    except Exception as error:
        import re
        message = str(error)
        code = message if re.fullmatch(r"[A-Z0-9_]{1,80}", message) else type(error).__name__.upper()
        (folder / "error.txt").write_text(code, encoding="ascii")
        logging.error("Collector stopped: %s", code)
        sys.exit(1)


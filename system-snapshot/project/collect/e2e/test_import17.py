"""Быстрый тест: импорты + инициализация Inbox + mock App."""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("[1] Basic imports...")
import sqlite3, json, logging
from datetime import datetime, timezone, timedelta
print("  OK")

print("[2] r52.collectors.base...")
from r52.collectors.base import BaseCollector
print("  OK")

print("[3] r52.collectors.wb...")
try:
    from r52.collectors.wb import WBCollector
    print("  OK")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("[4] r52.collectors.ozon...")
try:
    from r52.collectors.ozon import OzonCollector
    print("  OK")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("[5] r52.collectors.farpost...")
try:
    from r52.collectors.farpost import FarpostCollector
    print("  OK")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("[6] r52.collectors.avito...")
try:
    from r52.collectors.avito import AvitoCollector
    print("  OK")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("[7] r52.core.watches...")
try:
    from r52.core.watches import evaluate
    print(f"  OK: evaluate signature: {evaluate.__code__.co_varnames[:evaluate.__code__.co_argcount]}")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("[8] r52.intake.eye...")
try:
    from r52.intake.eye import Inbox
    print("  OK")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("[9] Mock App + Inbox init...")
TZ = timezone(timedelta(hours=10))
DB_PATH = r"C:\Users\aleks\AppData\Local\R52\data\r52.sqlite3"

class _DBCtx:
    def __init__(self, db_path):
        self.db_path = db_path
    def write(self):
        return _Conn(self.db_path)

class _Conn:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
    def execute(self, *a, **kw):
        return self.conn.execute(*a, **kw)
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        if not exc[1]:
            self.conn.commit()
        self.conn.close()

class _Clock:
    def now(self):
        return datetime.now(TZ)

class _Settings:
    def get(self, key, default=None):
        return default

class _Notifier:
    def notify(self, *a, **kw):
        pass

class _App:
    def __init__(self, db_path):
        self.db = _DBCtx(db_path)
        self.clock = _Clock()
        self.settings = _Settings()
        self.notifier = _Notifier()

try:
    app = _App(DB_PATH)
    inbox = Inbox(app)
    print("  OK: Inbox created")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("[10] Collector init (no network)...")
try:
    wb = WBCollector(data_dir=None, use_proxies=False)
    print(f"  WB OK: stats={wb.stats}")
except Exception as e:
    print(f"  WB FAIL: {e}")
    traceback.print_exc()

try:
    fp = FarpostCollector(data_dir=None, use_proxies=False)
    print(f"  FARPOST OK")
except Exception as e:
    print(f"  FARPOST FAIL: {e}")
    traceback.print_exc()

print("\nDONE")

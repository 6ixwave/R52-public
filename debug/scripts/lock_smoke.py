"""Быстрый smoke-тест: App → open → close, дважды подряд."""
import sys
sys.path.insert(0, ".")
from r52.config import load_config
from r52.service import App

c = load_config(home="data/test_home")

# Первый цикл
a = App(c, install_guard=False)
a.open()
a.db.close()
a.lock.release()
a._opened = False
print("APP1 OK")

# Второй цикл
a2 = App(c, install_guard=False)
a2.open()
a2.db.close()
a2.lock.release()
a2._opened = False
print("APP2 OK - LOCK FIX WORKS")

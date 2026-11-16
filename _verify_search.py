"""Верификация: collect_by_search у OZON и WB реально возвращает товары
(в т.ч. через новый PwFetcher-fallback при 403/498)."""
import sys
import time
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s %(message)s")
sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")

import r52.collectors.wb as wbmod
import r52.collectors.ozon as ozmod


def pick(mod):
    for n in dir(mod):
        if n.endswith("Collector") and n != "BaseCollector":
            return getattr(mod, n)
    return None


WbCollector = pick(wbmod)
OzonCollector = pick(ozmod)
print(f"classes: WB={WbCollector.__name__ if WbCollector else None} "
      f"OZON={OzonCollector.__name__ if OzonCollector else None}")

home = Path(r"C:\Users\aleks\AppData\Local\R52")


def run(col, query, n=5):
    t0 = time.monotonic()
    try:
        res = col.collect_by_search(query, 1, n)
        print(f"RESULT {col.source}: {len(res)} items in {time.monotonic() - t0:.1f}s")
        for r in res[:3]:
            obs = r.get("observation", {}) if isinstance(r, dict) else {}
            print(f"  - id={obs.get('id')} | {str(obs.get('title'))[:55]}")
        return len(res)
    except Exception as e:
        print(f"ERR {col.source}: {type(e).__name__}: {e} "
              f"({time.monotonic() - t0:.1f}s)")
        return -1
    finally:
        try:
            col.close()
        except Exception:
            pass


total = 0
if OzonCollector:
    total += run(OzonCollector(data_dir=home, use_proxies=True), "видеокарты")
if WbCollector:
    total += run(WbCollector(data_dir=home, use_proxies=True), "видеокарты")
print(f"DONE total_items={total}")

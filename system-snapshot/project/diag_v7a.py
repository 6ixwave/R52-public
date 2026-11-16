"""
diag_v7a.py — Комплексная проверка всех 6 коллекторов: качество, скорость, ошибки.
Запуск: python diag_v7a.py
"""
import sys, os, time, json, logging

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7a")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v7a_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def get_field(item, field_name):
    obs = item.get("observation", item)
    fields = obs.get("fields", {})
    f = fields.get(field_name, {})
    if isinstance(f, dict):
        return f.get("value", "")
    return f

def get_direct(item, key):
    obs = item.get("observation", item)
    return obs.get(key, "")

QUERY = "наушники"
PAGE_SIZE = 8

emit(f"=== diag_v7a — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}, page_size={PAGE_SIZE}")
emit()

results_summary = {}

# ─── 1. ALIEXPRESS COM ───
emit("── [1/6] ALIEXPRESS COM ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    ae = AliExpressCollector(use_proxies=False, domain="com")
    ae.source = "ALIEXPRESS"
    t0 = time.monotonic()
    items = ae.collect_by_search(QUERY, page=1, page_size=PAGE_SIZE)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: {len(items)} items {el}s")
    for i, item in enumerate(items[:3]):
        iid = get_direct(item, "id")
        title = get_field(item, "title")
        pc = item.get("observation", item).get("price_candidates", [])
        prices_str = ", ".join(f"{c.get('currency','?')} {c.get('amount','?')}" for c in pc[:2]) if pc else "NONE"
        avail = get_field(item, "availability")
        emit(f"  [{i}] id={iid} title={str(title)[:50]!r} price={prices_str} avail={avail}")
    ae.close()
    results_summary["ALIEXPRESS"] = f"OK {len(items)} items {el}s"
    emit(f"  Stats: {ae.stats}")
except Exception as e:
    el = round(time.monotonic() - t0, 1) if 't0' in dir() else 0
    err = f"{type(e).__name__}: {str(e)[:200]}"
    emit(f"  p1: ERR {err} ({el}s)")
    results_summary["ALIEXPRESS"] = f"ERR {err}"

emit()

# ─── 2. ALIEXPRESS_RU ───
emit("── [2/6] ALIEXPRESS_RU ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    aeru = AliExpressRUCollector(use_proxies=False)
    aeru.source = "ALIEXPRESS_RU"
    t0 = time.monotonic()
    items = aeru.collect_by_search(QUERY, page=1, page_size=PAGE_SIZE)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: {len(items)} items {el}s")
    for i, item in enumerate(items[:3]):
        iid = get_direct(item, "id")
        title = get_field(item, "title")
        pc = item.get("observation", item).get("price_candidates", [])
        prices_str = ", ".join(f"{c.get('currency','?')} {c.get('amount','?')}" for c in pc[:2]) if pc else "NONE"
        avail = get_field(item, "availability")
        emit(f"  [{i}] id={iid} title={str(title)[:50]!r} price={prices_str} avail={avail}")
    aeru.close()
    results_summary["ALIEXPRESS_RU"] = f"OK {len(items)} items {el}s"
    emit(f"  Stats: {aeru.stats}")
except Exception as e:
    el = round(time.monotonic() - t0, 1) if 't0' in dir() else 0
    err = f"{type(e).__name__}: {str(e)[:200]}"
    emit(f"  p1: ERR {err} ({el}s)")
    results_summary["ALIEXPRESS_RU"] = f"ERR {err}"

emit()

# ─── 3. WB ───
emit("── [3/6] WB ──")
try:
    from r52.collectors.wb import WBCollector
    wb = WBCollector(use_proxies=False)
    wb.source = "WB"
    t0 = time.monotonic()
    items = wb.collect_by_search(QUERY, page=1, page_size=PAGE_SIZE)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: {len(items)} items {el}s")
    for i, item in enumerate(items[:3]):
        iid = get_direct(item, "id")
        title = get_field(item, "title")
        pc = item.get("observation", item).get("price_candidates", [])
        prices_str = ", ".join(f"{c.get('currency','?')} {c.get('amount','?')}" for c in pc[:2]) if pc else "NONE"
        emit(f"  [{i}] id={iid} title={str(title)[:50]!r} price={prices_str}")
    wb.close()
    results_summary["WB"] = f"OK {len(items)} items {el}s"
    emit(f"  Stats: {wb.stats}")
except Exception as e:
    el = round(time.monotonic() - t0, 1) if 't0' in dir() else 0
    err = f"{type(e).__name__}: {str(e)[:200]}"
    emit(f"  p1: ERR {err} ({el}s)")
    results_summary["WB"] = f"ERR {err}"

emit()

# ─── 4. OZON ───
emit("── [4/6] OZON ──")
try:
    from r52.collectors.ozon import OzonCollector
    oz = OzonCollector(use_proxies=False)
    oz.source = "OZON"
    t0 = time.monotonic()
    items = oz.collect_by_search(QUERY, page=1, page_size=PAGE_SIZE)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: {len(items)} items {el}s")
    for i, item in enumerate(items[:3]):
        iid = get_direct(item, "id")
        title = get_field(item, "title")
        pc = item.get("observation", item).get("price_candidates", [])
        prices_str = ", ".join(f"{c.get('currency','?')} {c.get('amount','?')}" for c in pc[:2]) if pc else "NONE"
        emit(f"  [{i}] id={iid} title={str(title)[:50]!r} price={prices_str}")
    oz.close()
    results_summary["OZON"] = f"OK {len(items)} items {el}s"
    emit(f"  Stats: {oz.stats}")
except Exception as e:
    el = round(time.monotonic() - t0, 1) if 't0' in dir() else 0
    err = f"{type(e).__name__}: {str(e)[:200]}"
    emit(f"  p1: ERR {err} ({el}s)")
    results_summary["OZON"] = f"ERR {err}"

emit()

# ─── 5. AVITO ───
emit("── [5/6] AVITO ──")
try:
    from r52.collectors.avito import AvitoCollector
    av = AvitoCollector(use_proxies=False)
    av.source = "AVITO"
    t0 = time.monotonic()
    items = av.collect_by_search(QUERY, page=1, page_size=PAGE_SIZE)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: {len(items)} items {el}s")
    for i, item in enumerate(items[:3]):
        iid = get_direct(item, "id")
        title = get_field(item, "title")
        pc = item.get("observation", item).get("price_candidates", [])
        prices_str = ", ".join(f"{c.get('currency','?')} {c.get('amount','?')}" for c in pc[:2]) if pc else "NONE"
        emit(f"  [{i}] id={iid} title={str(title)[:50]!r} price={prices_str}")
    av.close()
    results_summary["AVITO"] = f"OK {len(items)} items {el}s"
    emit(f"  Stats: {av.stats}")
except Exception as e:
    el = round(time.monotonic() - t0, 1) if 't0' in dir() else 0
    err = f"{type(e).__name__}: {str(e)[:200]}"
    emit(f"  p1: ERR {err} ({el}s)")
    results_summary["AVITO"] = f"ERR {err}"

emit()

# ─── 6. FARPOST ───
emit("── [6/6] FARPOST ──")
try:
    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(use_proxies=False)
    fp.source = "FARPOST"
    t0 = time.monotonic()
    items = fp.collect_by_search(QUERY, page=1, page_size=PAGE_SIZE)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: {len(items)} items {el}s")
    for i, item in enumerate(items[:3]):
        iid = get_direct(item, "id")
        title = get_field(item, "title")
        pc = item.get("observation", item).get("price_candidates", [])
        prices_str = ", ".join(f"{c.get('currency','?')} {c.get('amount','?')}" for c in pc[:2]) if pc else "NONE"
        emit(f"  [{i}] id={iid} title={str(title)[:50]!r} price={prices_str}")
    fp.close()
    results_summary["FARPOST"] = f"OK {len(items)} items {el}s"
    emit(f"  Stats: {fp.stats}")
except Exception as e:
    el = round(time.monotonic() - t0, 1) if 't0' in dir() else 0
    err = f"{type(e).__name__}: {str(e)[:200]}"
    emit(f"  p1: ERR {err} ({el}s)")
    results_summary["FARPOST"] = f"ERR {err}"

emit()
emit("=== SUMMARY ===")
for src, status in results_summary.items():
    emit(f"  {src:15s} → {status}")

emit()
emit("=== DONE ===")
flush()
log.info("diag_v7a complete")

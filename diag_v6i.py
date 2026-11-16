"""
diag_v6i.py — Проверка трёх оставшихся источников: WB, ALIEXPRESS, ALIEXPRESS_RU.
Запуск: python diag_v6i.py
"""
import sys, os, time, logging, json

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6i")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6i_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
PAGE_SIZE = 10

emit(f"=== diag_v6i — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}, page_size={PAGE_SIZE}")
emit()

# ─── 1. WB ───
emit("── WB ──")
try:
    from r52.collectors.wb import WBCollector
    wb = WBCollector(use_proxies=False)
    wb.source = "WB"
    emit("  Init: OK")

    for pg in (1, 2):
        t0 = time.monotonic()
        try:
            items = wb.collect_by_search(QUERY, page=pg, page_size=PAGE_SIZE)
            el = round(time.monotonic() - t0, 1)
            ids = [str(it.get("observation", it).get("id", "")) for it in items[:PAGE_SIZE]]
            titles = [str(it.get("observation", it).get("title", ""))[:40] for it in items[:3]]
            emit(f"  p{pg}: OK {len(items)} items {el}s")
            emit(f"       IDs: {ids[:5]}")
            emit(f"       Titles: {titles}")
        except Exception as e:
            el = round(time.monotonic() - t0, 1)
            emit(f"  p{pg}: ERR {type(e).__name__}: {str(e)[:200]} ({el}s)")
        if pg == 1:
            time.sleep(3)

    # Проверка пересечения p1/p2
    try:
        p1_items = wb.collect_by_search(QUERY, page=1, page_size=PAGE_SIZE)
        p2_items = wb.collect_by_search(QUERY, page=2, page_size=PAGE_SIZE)
        p1_ids = set(str(it.get("observation", it).get("id", "")) for it in p1_items)
        p2_ids = set(str(it.get("observation", it).get("id", "")) for it in p2_items)
        overlap = p1_ids & p2_ids
        emit(f"  Pagination check: p1={len(p1_ids)}, p2={len(p2_ids)}, overlap={len(overlap)}/{min(len(p1_ids),len(p2_ids))}")
    except Exception as e:
        emit(f"  Pagination check ERR: {type(e).__name__}: {str(e)[:100]}")

    wb.close()
    emit(f"  Stats: {wb.stats}")
except Exception as e:
    emit(f"  FATAL: {type(e).__name__}: {e}")

emit()

# ─── 2. ALIEXPRESS ───
emit("── ALIEXPRESS ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    ae = AliExpressCollector(use_proxies=False, domain="com")
    ae.source = "ALIEXPRESS"
    emit("  Init: OK")

    t0 = time.monotonic()
    try:
        items = ae.collect_by_search(QUERY, page=1, page_size=PAGE_SIZE)
        el = round(time.monotonic() - t0, 1)
        ids = [str(it.get("observation", it).get("id", "")) for it in items[:PAGE_SIZE]]
        titles = [str(it.get("observation", it).get("title", ""))[:40] for it in items[:3]]
        emit(f"  p1: OK {len(items)} items {el}s")
        emit(f"       IDs: {ids[:5]}")
        emit(f"       Titles: {titles}")
    except Exception as e:
        el = round(time.monotonic() - t0, 1)
        emit(f"  p1: ERR {type(e).__name__}: {str(e)[:200]} ({el}s)")

    ae.close()
    emit(f"  Stats: {ae.stats}")
except Exception as e:
    emit(f"  FATAL: {type(e).__name__}: {e}")

emit()

# ─── 3. ALIEXPRESS_RU ───
emit("── ALIEXPRESS_RU ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    aeru = AliExpressRUCollector(use_proxies=False)
    aeru.source = "ALIEXPRESS_RU"
    emit("  Init: OK")

    t0 = time.monotonic()
    try:
        items = aeru.collect_by_search(QUERY, page=1, page_size=PAGE_SIZE)
        el = round(time.monotonic() - t0, 1)
        ids = [str(it.get("observation", it).get("id", "")) for it in items[:PAGE_SIZE]]
        titles = [str(it.get("observation", it).get("title", ""))[:40] for it in items[:3]]
        emit(f"  p1: OK {len(items)} items {el}s")
        emit(f"       IDs: {ids[:5]}")
        emit(f"       Titles: {titles}")
    except Exception as e:
        el = round(time.monotonic() - t0, 1)
        emit(f"  p1: ERR {type(e).__name__}: {str(e)[:200]} ({el}s)")

    aeru.close()
    emit(f"  Stats: {aeru.stats}")
except Exception as e:
    emit(f"  FATAL: {type(e).__name__}: {e}")

emit()
emit("=== DONE ===")
flush()
log.info("diag_v6i complete, output: %s", OUT)

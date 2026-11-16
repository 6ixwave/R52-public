"""
diag_v6j.py — Детальная проверка AE COM / AE_RU: качество данных, пагинация, WB-проба.
Запуск: python diag_v6j.py
"""
import sys, os, time, logging, json

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6j")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6j_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def get_field(item, field_name):
    """Извлекает value из observation.fields.<name>.value"""
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
PAGE_SIZE = 10

emit(f"=== diag_v6j — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}, page_size={PAGE_SIZE}")
emit()

# ─── 1. ALIEXPRESS COM: p1 + p2 + quality ───
emit("── ALIEXPRESS COM ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    ae = AliExpressCollector(use_proxies=False, domain="com")
    ae.source = "ALIEXPRESS"
    emit("  Init: OK")

    p1_items = []
    p2_items = []
    for pg in (1, 2):
        t0 = time.monotonic()
        try:
            items = ae.collect_by_search(QUERY, page=pg, page_size=PAGE_SIZE)
            el = round(time.monotonic() - t0, 1)
            if pg == 1:
                p1_items = items
            else:
                p2_items = items
            emit(f"  p{pg}: OK {len(items)} items {el}s")
        except Exception as e:
            el = round(time.monotonic() - t0, 1)
            emit(f"  p{pg}: ERR {type(e).__name__}: {str(e)[:200]} ({el}s)")
        if pg == 1:
            time.sleep(3)

    # Quality check p1
    if p1_items:
        emit(f"  ── Quality p1 (first 3 items) ──")
        for i, item in enumerate(p1_items[:3]):
            obs = item.get("observation", item)
            iid = get_direct(item, "id")
            title = get_field(item, "title")
            price = get_field(item, "price")
            avail = get_field(item, "availability")
            url = get_direct(item, "url")
            status = get_direct(item, "status")
            emit(f"  [{i}] id={iid}")
            emit(f"      title={str(title)[:60]!r}")
            emit(f"      price={price!r}  avail={avail!r}")
            emit(f"      url={str(url)[:80]}")
            emit(f"      status={status}")

    # Pagination check
    if p1_items and p2_items:
        p1_ids = set(str(get_direct(it, "id")) for it in p1_items)
        p2_ids = set(str(get_direct(it, "id")) for it in p2_items)
        overlap = p1_ids & p2_ids
        emit(f"  ── Pagination ──")
        emit(f"  p1: {len(p1_ids)} unique IDs: {sorted(p1_ids)[:5]}")
        emit(f"  p2: {len(p2_ids)} unique IDs: {sorted(p2_ids)[:5]}")
        emit(f"  overlap: {len(overlap)}/{min(len(p1_ids),len(p2_ids))}")
        if overlap:
            emit(f"  OVERLAP IDs: {sorted(overlap)[:5]}")

    ae.close()
    emit(f"  Stats: {ae.stats}")
except Exception as e:
    emit(f"  FATAL: {type(e).__name__}: {e}")

emit()

# ─── 2. ALIEXPRESS_RU: p1 + p2 + quality ───
emit("── ALIEXPRESS_RU ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    aeru = AliExpressRUCollector(use_proxies=False)
    aeru.source = "ALIEXPRESS_RU"
    emit("  Init: OK")

    rp1_items = []
    rp2_items = []
    for pg in (1, 2):
        t0 = time.monotonic()
        try:
            items = aeru.collect_by_search(QUERY, page=pg, page_size=PAGE_SIZE)
            el = round(time.monotonic() - t0, 1)
            if pg == 1:
                rp1_items = items
            else:
                rp2_items = items
            emit(f"  p{pg}: OK {len(items)} items {el}s")
        except Exception as e:
            el = round(time.monotonic() - t0, 1)
            emit(f"  p{pg}: ERR {type(e).__name__}: {str(e)[:200]} ({el}s)")
        if pg == 1:
            time.sleep(3)

    # Quality check p1
    if rp1_items:
        emit(f"  ── Quality p1 (first 3 items) ──")
        for i, item in enumerate(rp1_items[:3]):
            obs = item.get("observation", item)
            iid = get_direct(item, "id")
            title = get_field(item, "title")
            price = get_field(item, "price")
            avail = get_field(item, "availability")
            url = get_direct(item, "url")
            status = get_direct(item, "status")
            emit(f"  [{i}] id={iid}")
            emit(f"      title={str(title)[:60]!r}")
            emit(f"      price={price!r}  avail={avail!r}")
            emit(f"      url={str(url)[:80]}")
            emit(f"      status={status}")

    # Pagination
    if rp1_items and rp2_items:
        rp1_ids = set(str(get_direct(it, "id")) for it in rp1_items)
        rp2_ids = set(str(get_direct(it, "id")) for it in rp2_items)
        rolap = rp1_ids & rp2_ids
        emit(f"  ── Pagination ──")
        emit(f"  p1: {len(rp1_ids)} unique IDs: {sorted(rp1_ids)[:5]}")
        emit(f"  p2: {len(rp2_ids)} unique IDs: {sorted(rp2_ids)[:5]}")
        emit(f"  overlap: {len(rolap)}/{min(len(rp1_ids),len(rp2_ids))}")

    aeru.close()
    emit(f"  Stats: {aeru.stats}")
except Exception as e:
    emit(f"  FATAL: {type(e).__name__}: {e}")

emit()

# ─── 3. WB: quick probe (confirm 403 pattern) ───
emit("── WB (probe) ──")
try:
    from r52.collectors.wb import WBCollector
    wb = WBCollector(use_proxies=False)
    wb.source = "WB"
    emit("  Init: OK")

    t0 = time.monotonic()
    try:
        items = wb.collect_by_search(QUERY, page=1, page_size=5)
        el = round(time.monotonic() - t0, 1)
        emit(f"  p1: OK {len(items)} items {el}s")
        for i, item in enumerate(items[:3]):
            iid = get_direct(item, "id")
            title = get_field(item, "title")
            price = get_field(item, "price")
            emit(f"  [{i}] id={iid} title={str(title)[:50]!r} price={price!r}")
    except Exception as e:
        el = round(time.monotonic() - t0, 1)
        emit(f"  p1: ERR {type(e).__name__}: {str(e)[:200]} ({el}s)")

    wb.close()
    emit(f"  Stats: {wb.stats}")
except Exception as e:
    emit(f"  FATAL: {type(e).__name__}: {e}")

emit()
emit("=== DONE ===")
flush()
log.info("diag_v6j complete, output: %s", OUT)

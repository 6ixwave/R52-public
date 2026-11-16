"""
diag_v6l.py — Быстрая проверка AE_RU после фикса None-ID + качество полей.
Запуск: python diag_v6l.py
"""
import sys, os, time, json, logging

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6l")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6l_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"

emit(f"=== diag_v6l — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ─── AE_RU: p1 + p2 ───
emit("── ALIEXPRESS_RU ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    a = AliExpressRUCollector(use_proxies=False)
    a.source = "ALIEXPRESS_RU"
    emit("  Init: OK")

    all_items = []
    for pg in (1, 2):
        t0 = time.monotonic()
        try:
            items = a.collect_by_search(QUERY, page=pg, page_size=10)
            el = round(time.monotonic() - t0, 1)
            all_items.extend(items)
            emit(f"  p{pg}: OK {len(items)} items {el}s")
        except Exception as e:
            el = round(time.monotonic() - t0, 1)
            err_txt = type(e).__name__ + ": " + str(e)[:200]
            emit("  p" + str(pg) + ": ERR " + err_txt + " (" + str(el) + "s)")
        if pg == 1:
            time.sleep(3)

    # Quality
    if all_items:
        none_ids = 0
        empty_titles = 0
        no_price = 0
        no_image = 0
        for i, it in enumerate(all_items):
            obs = it.get("observation", it)
            iid = str(obs.get("id", ""))
            title = obs.get("fields", {}).get("title", {}).get("value", "")
            pc = obs.get("price_candidates", [])
            media = obs.get("media", {}).get("items", [])
            if iid in ("None", "", "null"):
                none_ids += 1
            if len(str(title)) < 10:
                empty_titles += 1
            if not pc:
                no_price += 1
            if not media:
                no_image += 1

        emit(f"  ── Quality ({len(all_items)} items total) ──")
        emit(f"  None-ID: {none_ids}/{len(all_items)}")
        emit(f"  Empty-title: {empty_titles}/{len(all_items)}")
        emit(f"  No-price: {no_price}/{len(all_items)}")
        emit(f"  No-image: {no_image}/{len(all_items)}")

        # First 3 items detail
        emit(f"  ── First 3 items ──")
        for i, it in enumerate(all_items[:3]):
            obs = it.get("observation", it)
            iid = obs.get("id", "")
            title = obs.get("fields", {}).get("title", {}).get("value", "")
            pc = obs.get("price_candidates", [])
            prices_str = ", ".join(f"{c.get('currency','?')} {c.get('amount','?')}" for c in pc) if pc else "NONE"
            url = str(obs.get("url", ""))[:80]
            emit(f"  [{i}] id={iid}")
            emit(f"      title={str(title)[:70]!r}")
            emit(f"      price={prices_str}")
            emit(f"      url={url}")

        # Pagination
        p1_ids = set(str(it.get("observation", it).get("id", "")) for it in all_items[:10])
        p2_ids = set(str(it.get("observation", it).get("id", "")) for it in all_items[10:])
        if p1_ids and p2_ids:
            overlap = p1_ids & p2_ids
            emit(f"  ── Pagination ──")
            emit(f"  p1: {len(p1_ids)} IDs, p2: {len(p2_ids)} IDs, overlap: {len(overlap)}")

    a.close()
    emit(f"  Stats: {a.stats}")
except Exception as e:
    emit(f"  FATAL: {type(e).__name__}: {e}")

emit()
emit("=== DONE ===")
flush()
log.info("diag_v6l complete")

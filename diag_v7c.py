"""
diag_v7c.py — Быстрая верификация цен AE_RU после фикса pdp_npi → PRIMARY.
Запуск: python diag_v7c.py
"""
import sys, os, time, logging

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7c")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v7c_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v7c — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ─── AE_RU ───
emit("── ALIEXPRESS_RU ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    a = AliExpressRUCollector(use_proxies=False)
    a.source = "ALIEXPRESS_RU"
    t0 = time.monotonic()
    items = a.collect_by_search(QUERY, page=1, page_size=10)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: {len(items)} items {el}s")

    # Quality
    no_price = 0
    bad_price = 0  # price == 1.00 or < 0.5
    for it in items:
        obs = it.get("observation", it)
        pc = obs.get("price_candidates", [])
        if not pc:
            no_price += 1
        else:
            amt = float(pc[0].get("amount", 0))
            if amt <= 1.0:
                bad_price += 1

    emit(f"  No-price: {no_price}/{len(items)}")
    emit(f"  Bad-price (<=1.00): {bad_price}/{len(items)}")

    # Detail: first 5
    for i, it in enumerate(items[:5]):
        obs = it.get("observation", it)
        iid = obs.get("id", "")
        title = obs.get("fields", {}).get("title", {}).get("value", "")
        pc = obs.get("price_candidates", [])
        if pc:
            pstr = " | ".join(
                f"{c.get('role','?')} {c.get('currency','?')} {c.get('amount','?')}"
                for c in pc
            )
        else:
            pstr = "NONE"
        imgs = obs.get("media", {}).get("items", [])
        emit(f"  [{i}] id={iid}")
        emit(f"      title={str(title)[:60]!r}")
        emit(f"      price={pstr}")
        emit(f"      images={len(imgs)}")

    a.close()
    emit(f"  Stats: {a.stats}")
except Exception as e:
    import traceback
    emit(f"  FATAL: {type(e).__name__}: {e}")
    emit(traceback.format_exc())

emit()
emit("=== DONE ===")
flush()
log.info("diag_v7c complete")

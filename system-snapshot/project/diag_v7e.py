"""
diag_v7e.py — AE_COM wholesale + AE_RU + 6-source quick scan.
Запуск: python diag_v7e.py
"""
import sys, os, time, logging

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7e")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v7e_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v7e — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ─── PART 1: AE_COM (base class, new wholesale path) ───
emit("── [1] ALIEXPRESS (COM, base class) ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    c = AliExpressCollector(use_proxies=False)
    c.source = "ALIEXPRESS"
    t0 = time.monotonic()
    items = c.collect_by_search(QUERY, page=1, page_size=10)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: {len(items)} items {el}s")

    no_price = 0
    bad_price = 0
    for it in items:
        obs = it.get("observation", it)
        pc = obs.get("price_candidates", [])
        if not pc:
            no_price += 1
        elif float(pc[0].get("amount", 0)) <= 1.0:
            bad_price += 1

    emit(f"  No-price: {no_price}/{len(items)}")
    emit(f"  Bad-price (<=1.00): {bad_price}/{len(items)}")

    for i, it in enumerate(items[:5]):
        obs = it.get("observation", it)
        iid = obs.get("id", "")
        title = obs.get("fields", {}).get("title", {}).get("value", "")
        pc = obs.get("price_candidates", [])
        pstr = " | ".join(
            f"{c.get('role','?')} {c.get('currency','?')} {c.get('amount','?')}"
            for c in pc
        ) if pc else "NONE"
        imgs = obs.get("media", {}).get("items", [])
        emit(f"  [{i}] id={iid}")
        emit(f"      title={str(title)[:60]!r}")
        emit(f"      price={pstr}")
        emit(f"      images={len(imgs)}")

    c.close()
    emit(f"  Stats: {c.stats}")
except Exception as e:
    import traceback
    emit(f"  FATAL: {type(e).__name__}: {e}")
    emit(traceback.format_exc())

emit()

# ─── PART 2: AE_RU (subclass, verify still works) ───
emit("── [2] ALIEXPRESS_RU (subclass) ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    a = AliExpressRUCollector(use_proxies=False)
    a.source = "ALIEXPRESS_RU"
    t0 = time.monotonic()
    items_ru = a.collect_by_search(QUERY, page=1, page_size=10)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: {len(items_ru)} items {el}s")

    no_price_ru = sum(1 for it in items_ru if not it.get("observation", it).get("price_candidates"))
    emit(f"  No-price: {no_price_ru}/{len(items_ru)}")

    for i, it in enumerate(items_ru[:3]):
        obs = it.get("observation", it)
        pc = obs.get("price_candidates", [])
        pstr = " | ".join(f"{c.get('role','?')} {c.get('currency','?')} {c.get('amount','?')}" for c in pc) if pc else "NONE"
        emit(f"  [{i}] id={obs.get('id','')} price={pstr}")

    a.close()
    emit(f"  Stats: {a.stats}")
except Exception as e:
    import traceback
    emit(f"  FATAL: {type(e).__name__}: {e}")
    emit(traceback.format_exc())

emit()

# ─── PART 3: Quick 6-source scan ───
emit("── [3] 6-SOURCE QUICK SCAN ──")
emit()

sources = [
    ("ALIEXPRESS", "ALIEXPRESS"),
    ("ALIEXPRESS_RU", "ALIEXPRESS_RU"),
    ("WB", "WB"),
    ("OZON", "OZON"),
    ("AVITO", "AVITO"),
    ("FARPOST", "FARPOST"),
]

for idx, (src_name, src_key) in enumerate(sources, 1):
    emit(f"  [{idx}/6] {src_name}")
    try:
        if src_key == "ALIEXPRESS":
            from r52.collectors.aliexpress import AliExpressCollector
            col = AliExpressCollector(use_proxies=False)
        elif src_key == "ALIEXPRESS_RU":
            from r52.collectors.aliexpress import AliExpressRUCollector
            col = AliExpressRUCollector(use_proxies=False)
        elif src_key == "WB":
            from r52.collectors.wildberries import WildberriesCollector
            col = WildberriesCollector(use_proxies=False)
        elif src_key == "OZON":
            from r52.collectors.ozon import OzonCollector
            col = OzonCollector(use_proxies=False)
        elif src_key == "AVITO":
            from r52.collectors.avito import AvitoCollector
            col = AvitoCollector(use_proxies=False)
        elif src_key == "FARPOST":
            from r52.collectors.farpost import FarpostCollector
            col = FarpostCollector(use_proxies=False)
        else:
            emit(f"    SKIP (unknown)")
            continue

        col.source = src_key
        t0 = time.monotonic()
        items = col.collect_by_search(QUERY, page=1, page_size=8)
        el = round(time.monotonic() - t0, 1)

        no_price = sum(1 for it in items if not it.get("observation", it).get("price_candidates"))
        sample = ""
        if items:
            obs0 = items[0].get("observation", items[0])
            pc0 = obs0.get("price_candidates", [])
            if pc0:
                sample = f" first_price={pc0[0].get('currency','?')} {pc0[0].get('amount','?')}"
            else:
                sample = " first_price=NONE"

        emit(f"    {len(items)} items {el}s | no_price={no_price}{sample}")
        col.close()
    except Exception as e:
        emit(f"    ERR {type(e).__name__}: {str(e)[:80]}")
    emit()

emit("=== DONE ===")
flush()
log.info("diag_v7e complete")

"""
diag_v6k.py — Dump полного JSON первого item + поиск id=None в AE_RU.
Запуск: python diag_v6k.py
"""
import sys, os, time, logging, json

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6k")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6k_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"

emit(f"=== diag_v6k — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit()

# ─── 1. AE COM: dump first item ───
emit("── AE COM: full item dump ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    ae = AliExpressCollector(use_proxies=False, domain="com")
    ae.source = "ALIEXPRESS"
    t0 = time.monotonic()
    items = ae.collect_by_search(QUERY, page=1, page_size=3)
    el = round(time.monotonic() - t0, 1)
    emit(f"  Got {len(items)} items in {el}s")
    if items:
        emit(f"  ── Item[0] full JSON ──")
        emit(json.dumps(items[0], indent=2, ensure_ascii=False, default=str))
        if len(items) > 1:
            emit(f"  ── Item[1] observation keys ──")
            obs1 = items[1].get("observation", {})
            emit(f"  keys: {list(obs1.keys())}")
            fields1 = obs1.get("fields", {})
            emit(f"  fields keys: {list(fields1.keys())}")
            for fk, fv in fields1.items():
                if isinstance(fv, dict):
                    emit(f"    {fk}: value={str(fv.get('value',''))[:80]!r} src={fv.get('source','')}")
                else:
                    emit(f"    {fk}: {str(fv)[:80]!r}")
    ae.close()
except Exception as e:
    emit(f"  FATAL: {type(e).__name__}: {e}")

emit()

# ─── 2. AE_RU: dump + find None IDs ───
emit("── AE_RU: full item dump + None-ID hunt ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    aeru = AliExpressRUCollector(use_proxies=False)
    aeru.source = "ALIEXPRESS_RU"
    t0 = time.monotonic()
    items = aeru.collect_by_search(QUERY, page=1, page_size=10)
    el = round(time.monotonic() - t0, 1)
    emit(f"  Got {len(items)} items in {el}s")

    none_count = 0
    for i, item in enumerate(items):
        obs = item.get("observation", item)
        iid = str(obs.get("id", ""))
        if iid in ("None", "", "null"):
            none_count += 1
            emit(f"  [None-ID #{none_count}] index={i}")
            emit(f"    full obs: {json.dumps(obs, indent=2, ensure_ascii=False, default=str)[:800]}")

    if items:
        emit(f"  ── Item[0] full JSON ──")
        emit(json.dumps(items[0], indent=2, ensure_ascii=False, default=str))

    emit(f"  None-ID total: {none_count}/{len(items)}")
    aeru.close()
except Exception as e:
    emit(f"  FATAL: {type(e).__name__}: {e}")

emit()
emit("=== DONE ===")
flush()
log.info("diag_v6k complete")

"""
diag_retest_v6b.py — Целевой re-test после фиксов OZON/AVITO + FARPOST p3.
Запуск: python diag_retest_v6b.py
"""
import sys, os, time, logging

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("retest_v6b")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_retest_v6b_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"

emit(f"=== R-52 retest_v6b — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ─── Инициализация ───
sources = {}
from r52.collectors.ozon import OzonCollector
from r52.collectors.avito import AvitoCollector
from r52.collectors.farpost import FarpostCollector

for name, cls in [("OZON", OzonCollector), ("AVITO", AvitoCollector), ("FARPOST", FarpostCollector)]:
    try:
        obj = cls(use_proxies=False)
        obj.source = name
        sources[name] = obj
        emit(f"  {name}: OK")
    except Exception as e:
        emit(f"  {name}: FAIL {type(e).__name__}: {e}")

emit()

# ─── OZON: p1 + p2 ───
emit("── OZON ──")
oz = sources.get("OZON")
if oz:
    for pg in (1, 2):
        t0 = time.monotonic()
        try:
            items = oz.collect_by_search(QUERY, page=pg, page_size=10)
            el = round(time.monotonic() - t0, 1)
            ids = [str(it.get("observation", it).get("id", "")) for it in items[:10]]
            emit(f"  p{pg}: OK {len(items)} items {el}s IDs={ids[:5]}")
        except Exception as e:
            el = round(time.monotonic() - t0, 1)
            emit(f"  p{pg}: ERR {type(e).__name__}: {str(e)[:150]} ({el}s)")
        if pg == 1:
            time.sleep(5)

# ─── AVITO: p1 + p2 ───
emit()
emit("── AVITO ──")
av = sources.get("AVITO")
if av:
    for pg in (1, 2):
        t0 = time.monotonic()
        try:
            items = av.collect_by_search(QUERY, page=pg, page_size=10)
            el = round(time.monotonic() - t0, 1)
            ids = [str(it.get("observation", it).get("id", "")) for it in items[:10]]
            emit(f"  p{pg}: OK {len(items)} items {el}s IDs={ids[:5]}")
        except Exception as e:
            el = round(time.monotonic() - t0, 1)
            emit(f"  p{pg}: ERR {type(e).__name__}: {str(e)[:150]} ({el}s)")
        if pg == 1:
            time.sleep(14)  # Avito P2 delay

# ─── FARPOST: p1 + p2 + p3 (проверка пагинации) ───
emit()
emit("── FARPOST (p1+p2+p3) ──")
fp = sources.get("FARPOST")
if fp:
    all_pages = {}
    for pg in (1, 2, 3):
        t0 = time.monotonic()
        try:
            items = fp.collect_by_search(QUERY, page=pg, page_size=10)
            el = round(time.monotonic() - t0, 1)
            ids = [str(it.get("observation", it).get("id", "")) for it in items[:10]]
            all_pages[pg] = ids
            emit(f"  p{pg}: OK {len(items)} items {el}s IDs={ids[:5]}")
        except Exception as e:
            el = round(time.monotonic() - t0, 1)
            emit(f"  p{pg}: ERR {type(e).__name__}: {str(e)[:150]} ({el}s)")
        if pg < 3:
            time.sleep(18)  # Farpost P2+ delay

    # Overlap analysis
    if len(all_pages) >= 2:
        emit()
        emit("  Overlap:")
        keys = sorted(all_pages.keys())
        for i in range(len(keys) - 1):
            a, b = keys[i], keys[i + 1]
            ov = set(all_pages[a]) & set(all_pages[b])
            emit(f"    p{a}∩p{b}: {len(ov)}/{min(len(all_pages[a]),len(all_pages[b]))}")

emit()
emit("=== DONE ===")
flush()
print(f"\nOutput: {OUT}")

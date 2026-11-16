"""
diag_retest_v6d.py — FARPOST pagination fix + OZON PwFetcher 5s wait.
Запуск: python diag_retest_v6d.py
"""
import sys, os, time, logging, traceback
from urllib.parse import quote

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("retest_v6d")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_retest_v6d_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== retest_v6d — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ═══════════════════════════════════════════
# 1. FARPOST: p1 + p2 (pagination fix)
# ═══════════════════════════════════════════
emit("── FARPOST: p1 + p2 ──")
from r52.collectors.farpost import FarpostCollector

fp = FarpostCollector(use_proxies=False)
fp.source = "FARPOST"

all_ids = {}
for pg in (1, 2):
    t0 = time.monotonic()
    try:
        items = fp.collect_by_search(QUERY, page=pg, page_size=10)
        el = round(time.monotonic() - t0, 1)
        ids = [str(it.get("observation", it).get("id", "")) for it in items[:10]]
        all_ids[pg] = ids
        emit(f"  p{pg}: OK {len(items)} items {el}s")
        emit(f"    IDs: {ids}")
    except Exception as e:
        el = round(time.monotonic() - t0, 1)
        _em = f"{type(e).__name__}: {str(e)[:200]} ({el}s)"
        emit(f"  p{pg}: ERR {_em}")
        emit(f"    {traceback.format_exc()[:500]}")
    if pg == 1:
        emit("  (sleep 18s for p2)")
        time.sleep(18)

if len(all_ids) >= 2:
    ov = set(all_ids.get(1, [])) & set(all_ids.get(2, []))
    _n1 = len(all_ids.get(1, []))
    _n2 = len(all_ids.get(2, []))
    emit(f"  Overlap p1∩p2: {len(ov)}/{min(_n1, _n2)}")
    if ov:
        emit(f"  Common: {sorted(ov)[:5]}")

# ═══════════════════════════════════════════
# 2. OZON: PwFetcher with extra_wait_ms=5000
# ═══════════════════════════════════════════
emit()
emit("── OZON: PwFetcher 5s wait ──")
from r52.collectors.ozon import OzonCollector

oz = OzonCollector(use_proxies=False)
oz.source = "OZON"

for pg in (1, 2):
    t0 = time.monotonic()
    try:
        items = oz.collect_by_search(QUERY, page=pg, page_size=10)
        el = round(time.monotonic() - t0, 1)
        ids = [str(it.get("observation", it).get("id", "")) for it in items[:10]]
        emit(f"  p{pg}: OK {len(items)} items {el}s")
        emit(f"    IDs: {ids[:5]}")
    except Exception as e:
        el = round(time.monotonic() - t0, 1)
        emit(f"  p{pg}: ERR {type(e).__name__}: {str(e)[:200]} ({el}s)")
    if pg == 1:
        time.sleep(5)

# ═══════════════════════════════════════════
# 3. AVITO: quick check (expect 429/439)
# ═══════════════════════════════════════════
emit()
emit("── AVITO: quick check ──")
from r52.collectors.avito import AvitoCollector

av = AvitoCollector(use_proxies=False)
av.source = "AVITO"
t0 = time.monotonic()
try:
    items = av.collect_by_search(QUERY, page=1, page_size=10)
    el = round(time.monotonic() - t0, 1)
    emit(f"  p1: OK {len(items)} items {el}s")
except Exception as e:
    el = round(time.monotonic() - t0, 1)
    _em = f"{type(e).__name__}: {str(e)[:150]} ({el}s)"
    emit(f"  p1: {_em}")

emit()
emit("=== DONE ===")
flush()
print(f"\nOutput: {OUT}")

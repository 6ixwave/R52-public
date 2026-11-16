"""
diag_retest_v6e.py — FARPOST v8b + OZON 10s wait + AVITO cooldown.
Запуск: python diag_retest_v6e.py
"""
import sys, os, time, logging, traceback
from urllib.parse import quote

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("retest_v6e")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_retest_v6e_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== retest_v6e — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ─── FARPOST first (least aggressive WAF) ───
emit("── FARPOST: p1 + p2 ──")
from r52.collectors.farpost import FarpostCollector

fp = FarpostCollector(use_proxies=False)
fp.source = "FARPOST"

fp_ids = {}
for pg in (1, 2):
    t0 = time.monotonic()
    try:
        items = fp.collect_by_search(QUERY, page=pg, page_size=10)
        el = round(time.monotonic() - t0, 1)
        ids = [str(it.get("observation", it).get("id", "")) for it in items[:10]]
        fp_ids[pg] = ids
        emit(f"  p{pg}: OK {len(items)} items {el}s")
        emit(f"    IDs: {ids}")
    except Exception as e:
        el = round(time.monotonic() - t0, 1)
        _em = f"{type(e).__name__}: {str(e)[:200]} ({el}s)"
        emit(f"  p{pg}: ERR {_em}")
    if pg == 1:
        emit("  (sleep 20s)")
        time.sleep(20)

if len(fp_ids) >= 2:
    ov = set(fp_ids.get(1, [])) & set(fp_ids.get(2, []))
    _n = min(len(fp_ids.get(1, [])), len(fp_ids.get(2, [])))
    emit(f"  Overlap p1/p2: {len(ov)}/{_n}")
    if ov:
        emit(f"  Common: {sorted(ov)[:5]}")

# ─── OZON: PwFetcher 10s ───
emit()
emit("── OZON: PwFetcher 10s wait ──")
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
        _em = f"{type(e).__name__}: {str(e)[:200]} ({el}s)"
        emit(f"  p{pg}: ERR {_em}")
    if pg == 1:
        time.sleep(6)

# ─── AVITO: after 25s cooldown from OZON ───
emit()
emit("── AVITO: post-cooldown check ──")
emit("  (sleep 25s cooldown)")
time.sleep(25)

from r52.collectors.avito import AvitoCollector
av = AvitoCollector(use_proxies=False)
av.source = "AVITO"
t0 = time.monotonic()
try:
    items = av.collect_by_search(QUERY, page=1, page_size=10)
    el = round(time.monotonic() - t0, 1)
    ids = [str(it.get("observation", it).get("id", "")) for it in items[:10]]
    emit(f"  p1: OK {len(items)} items {el}s")
    emit(f"    IDs: {ids[:5]}")
except Exception as e:
    el = round(time.monotonic() - t0, 1)
    _em = f"{type(e).__name__}: {str(e)[:150]} ({el}s)"
    emit(f"  p1: {_em}")

emit()
emit("=== DONE ===")
flush()
print(f"\nOutput: {OUT}")

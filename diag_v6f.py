"""
diag_v6f.py — Deep OZON HTML analysis + FARPOST v8b + AVITO cooldown.
Запуск: python diag_v6f.py
"""
import sys, os, time, logging, traceback, re as _re
from urllib.parse import quote

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6f")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6f_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v6f — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ═══════════════════════════════════════════════════
# 0. Initial cooldown (IP was hot from v6e)
# ═══════════════════════════════════════════════════
emit("── Cooldown 20s ──")
time.sleep(20)

# ═══════════════════════════════════════════════════
# 1. OZON: deep HTML analysis of the 315KB page
# ═══════════════════════════════════════════════════
emit("── OZON: deep HTML analysis ──")
from r52.collectors.ozon import OzonCollector

oz = OzonCollector(use_proxies=False)
oz.source = "OZON"

try:
    oz._warmup()
    time.sleep(3)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.ozon.ru/",
    }
    o_url = f"https://www.ozon.ru/search/?text={quote(QUERY)}&from_global=true&page=1"
    t0 = time.monotonic()
    if oz._cffi:
        o_resp = oz._cffi.get(o_url, impersonate="chrome", timeout=(15, 30), headers=headers, verify=False)
    else:
        o_resp = oz.session.get(o_url, headers=headers, timeout=(10, 30), verify=False)
    el = time.monotonic() - t0
    emit(f"  HTTP {o_resp.status_code}, {len(o_resp.text)} bytes, {el:.1f}s")
    emit(f"  Final URL: {o_resp.url}")

    html = o_resp.text

    # Title
    tm = _re.search(r'<title>(.*?)</title>', html, _re.S)
    emit(f"  Title: {(tm.group(1)[:120] if tm else 'N/A')}")

    # Key patterns
    patterns = [
        ("__INITIAL_STATE__", r'__INITIAL_STATE__\s*=\s*'),
        ("__NEXT_DATA__", r'__NEXT_DATA__\s*=\s*'),
        ("window.__state", r'window\.__state\s*=\s*'),
        ("window.__APP", r'window\.__APP'),
        ("data-widget", r'data-widget='),
        ("tileId", r'tileId'),
        ("productId", r'productId'),
        ("sku", r'"sku"'),
        ("/product/", r'/product/'),
        ("data-tid", r'data-tid='),
        ("captcha", r'captcha'),
        ("antibot", r'antibot'),
        ("challenge", r'challenge'),
        ("fab-chlg", r'fab.chlg'),
        ("ozone.ru/s3/abt", r'ozone\.ru/s3/abt'),
        ("searchResults", r'searchResults'),
        ("widgetState", r'widgetState'),
        ("__PRELOADED", r'__PRELOADED'),
        ("data-page", r'data-page'),
        ("script type=json", r'<script[^>]*type=["\']application/json["\']'),
    ]
    emit("  Pattern hits:")
    for name, pat in patterns:
        hits = len(_re.findall(pat, html, _re.I))
        if hits > 0:
            emit(f"    {name}: {hits}")

    # Product IDs
    prod_ids = _re.findall(r'/product/(\d{6,})/', html)
    emit(f"  /product/ IDs: {len(prod_ids)} (first 5: {prod_ids[:5]})")

    # data-tid
    tids = _re.findall(r'data-tid=["\'](\d+)["\']', html)
    emit(f"  data-tid: {len(tids)} (first 5: {tids[:5]})")

    # JSON script blocks
    json_blocks = _re.findall(r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', html, _re.S)
    emit(f"  JSON script blocks: {len(json_blocks)}")
    for i, jb in enumerate(json_blocks[:3]):
        emit(f"    block[{i}]: {len(jb)} chars, head: {jb[:100]!r}")

    # Look for embedded product data
    # Ozon uses various formats: __NEXT_DATA__, window.__state, or inline JSON
    for marker in ['"items"', '"products"', '"listings"', '"tiles"', '"cards"', '"searchResults"']:
        ms = [(m.start(), m.group()) for m in _re.finditer(_re.escape(marker), html)]
        if ms:
            emit(f"  {marker}: {len(ms)} hits, first at pos {ms[0][0]}")
            # Show context around first hit
            pos = ms[0][0]
            ctx = html[max(0,pos-50):pos+200]
            emit(f"    ctx: {ctx[:250]!r}")

    # First 3000 chars
    emit("  --- HTML head (first 3000 chars) ---")
    for line in html[:3000].splitlines()[:40]:
        emit(f"    {line[:160]}")

    # Save full HTML for offline analysis
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ozon_315k_dump.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    emit(f"  Saved full HTML to: {html_path}")

except Exception as e:
    emit(f"  OZON error: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()}")

# ═══════════════════════════════════════════════════
# 2. FARPOST: v8b pagination test (after OZON, IP slightly cool)
# ═══════════════════════════════════════════════════
emit()
emit("── FARPOST: v8b pagination ──")
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
        time.sleep(20)

if len(fp_ids) >= 2:
    ov = set(fp_ids.get(1, [])) & set(fp_ids.get(2, []))
    _n = min(len(fp_ids.get(1, [])), len(fp_ids.get(2, [])))
    emit(f"  Overlap p1/p2: {len(ov)}/{_n}")
    if ov:
        emit(f"  Common: {sorted(ov)[:5]}")
else:
    emit("  (not enough pages for overlap check)")

# ═══════════════════════════════════════════════════
# 3. AVITO: after cooldown
# ═══════════════════════════════════════════════════
emit()
emit("── AVITO: post-cooldown ──")
emit("  (sleep 15s)")
time.sleep(15)

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

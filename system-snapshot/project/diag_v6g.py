"""
diag_v6g.py — OZON PwFetcher deep-dive: 2-pass (home→search), 15s wait, URL/cookie tracking.
Запуск: python diag_v6g.py
"""
import sys, os, time, logging, traceback, re as _re
from urllib.parse import quote

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6g")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6g_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v6g — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ═══════════════════════════════════════════════════
# Cooldown 30s (IP was hot from v6f)
# ═══════════════════════════════════════════════════
emit("── Cooldown 30s ──")
time.sleep(30)

# ═══════════════════════════════════════════════════
# OZON PwFetcher: manual 2-pass
# ═══════════════════════════════════════════════════
emit("── OZON: PwFetcher 2-pass (home → search) ──")

from r52.collectors.pw_fetch import PwFetcher

pw = PwFetcher(headless=True)
started = pw.start()
emit(f"  PwFetcher started: {started}")

if not started:
    emit("  ABORT: PwFetcher failed to start")
    flush()
    sys.exit(1)

try:
    # ─── PASS 1: Homepage (collect base cookies) ───
    emit("  [PASS 1] Homepage: https://www.ozon.ru/")
    t0 = time.monotonic()
    page = pw._context.new_page()
    page.set_default_timeout(30000)
    resp1 = page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(8000)  # 8s for JS/cookies
    el1 = time.monotonic() - t0
    html1 = page.content()
    url1 = page.url
    title1 = page.title() or ""
    cookies1 = pw._context.cookies()
    emit(f"  [PASS 1] HTTP {resp1.status if resp1 else '?'}, {len(html1)}B, {el1:.1f}s")
    emit(f"  [PASS 1] URL: {url1[:120]}")
    emit(f"  [PASS 1] Title: {title1[:100]}")
    emit(f"  [PASS 1] Cookies: {len(cookies1)}")
    for c in cookies1[:10]:
        emit(f"    {c['name']}={c['value'][:40]}... (domain={c.get('domain','?')})")
    # Check if still on challenge
    if "challenge" in title1.lower() or "antibot" in title1.lower():
        emit(f"  [PASS 1] WARNING: still on challenge page after 8s")
    page.close()

    time.sleep(3)

    # ─── PASS 2: Search (with cookies from PASS 1) ───
    emit(f"  [PASS 2] Search: ozon.ru/search/?text={QUERY}")
    t0 = time.monotonic()
    page = pw._context.new_page()
    page.set_default_timeout(30000)
    search_url = f"https://www.ozon.ru/search/?text={quote(QUERY)}&from_global=true&page=1"
    resp2 = page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
    # Wait 15s for FAB JS cycle
    page.wait_for_timeout(15000)
    el2 = time.monotonic() - t0
    html2 = page.content()
    url2 = page.url
    title2 = page.title() or ""
    cookies2 = pw._context.cookies()
    emit(f"  [PASS 2] HTTP {resp2.status if resp2 else '?'}, {len(html2)}B, {el2:.1f}s")
    emit(f"  [PASS 2] URL: {url2[:120]}")
    emit(f"  [PASS 2] Title: {title2[:100]}")
    emit(f"  [PASS 2] Cookies: {len(cookies2)}")
    
    # Did URL change (redirect from challenge)?
    if "search" in url2:
        emit(f"  [PASS 2] OK: on search page")
    elif "challenge" in url2.lower() or "verify" in url2.lower():
        emit(f"  [PASS 2] WARNING: redirected to {url2[:80]}")
    else:
        emit(f"  [PASS 2] URL changed: {url2[:80]}")
    
    # Analyze search HTML
    if len(html2) > 5000:
        prod_ids = _re.findall(r'/product/(\d{6,})/', html2)
        tids = _re.findall(r'data-tid=["\'](\d+)["\']', html2)
        emit(f"  [PASS 2] /product/ IDs: {len(prod_ids)} (first 5: {prod_ids[:5]})")
        emit(f"  [PASS 2] data-tid: {len(tids)} (first 5: {tids[:5]})")
        
        # Check for product tiles
        tile_pat = _re.compile(r'class=["\'][^"\']*tile[^"\']*["\'][^>]*data-tid=["\'](\d+)["\']')
        tiles = tile_pat.findall(html2)
        emit(f"  [PASS 2] tile-pattern: {len(tiles)} (first 5: {tiles[:5]})")
        
        # JSON state
        for marker in ['__INITIAL_STATE__', '__NEXT_DATA__', 'window.__state', 'widgetState']:
            ms = list(_re.finditer(_re.escape(marker), html2, _re.I))
            if ms:
                emit(f"  [PASS 2] {marker}: {len(ms)} hits")
    
    # HTML head
    emit(f"  [PASS 2] HTML head (first 1500 chars):")
    for line in html2[:1500].splitlines()[:20]:
        emit(f"    {line[:140]}")
    
    # Save search HTML
    html2_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ozon_pw_search.html")
    with open(html2_path, "w", encoding="utf-8") as f:
        f.write(html2)
    emit(f"  Saved: {html2_path} ({len(html2)}B)")
    
    page.close()

    # ─── PASS 3 (bonus): if PASS 2 was challenge, wait more ───
    if "challenge" in title2.lower() or len(html2) < 20000:
        emit()
        emit("  [PASS 3] Extended wait (10s more on same page)")
        page = pw._context.new_page()
        page.set_default_timeout(30000)
        page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(25000)  # 25s total
        html3 = page.content()
        url3 = page.url
        title3 = page.title() or ""
        emit(f"  [PASS 3] {len(html3)}B, title={title3[:80]}, url={url3[:80]}")
        prod3 = _re.findall(r'/product/(\d{6,})/', html3)
        emit(f"  [PASS 3] /product/ IDs: {len(prod3)} (first 5: {prod3[:5]})")
        page.close()

except Exception as e:
    emit(f"  ERROR: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()}")
finally:
    pw.stop()

emit()
emit("=== DONE ===")
flush()
print(f"\nOutput: {OUT}")

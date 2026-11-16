"""
diag_v6h.py — OZON Composer-API search + PwFetcher wait_for_url + FARPOST p1.
Запуск: python diag_v6h.py
"""
import sys, os, time, logging, traceback, re as _re
from urllib.parse import quote

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6h")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6h_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v6h — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ═══════════════════════════════════════════════════
# Cooldown 60s
# ═══════════════════════════════════════════════════
emit("── Cooldown 60s ──")
time.sleep(60)

# ═══════════════════════════════════════════════════
# 1. OZON: Composer API for SEARCH page
# ═══════════════════════════════════════════════════
emit("── OZON: Composer API (search) ──")
from r52.collectors.ozon import OzonCollector

oz = OzonCollector(use_proxies=False)
oz.source = "OZON"

try:
    oz._warmup()
    time.sleep(2)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.ozon.ru/",
    }
    # Try composer-api with search URL
    search_paths = [
        f"/search/?text={quote(QUERY)}&from_global=true&page=1",
        f"/search/?text={quote(QUERY)}&page=1",
    ]
    for sp in search_paths:
        api_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url={quote(sp, safe='/:?&=')}"
        emit(f"  URL: {api_url[:100]}")
        t0 = time.monotonic()
        try:
            if oz._cffi:
                cr = oz._cffi.get(api_url, impersonate="chrome", timeout=(10, 20), headers=headers, verify=False)
            else:
                cr = oz.session.get(api_url, headers=headers, timeout=(8, 20), verify=False)
            el = time.monotonic() - t0
            emit(f"  HTTP {cr.status_code}, {len(cr.text)}B, {el:.1f}s")
            if cr.status_code == 200:
                ct = (cr.headers.get("Content-Type") or "").lower()
                emit(f"  Content-Type: {ct}")
                body = cr.text
                # Look for product data
                prod_ids = _re.findall(r'"(?:sku|productId|id)"\s*:\s*"?(\d{6,})"?', body)
                emit(f"  Product IDs in JSON: {len(prod_ids)} (first 5: {prod_ids[:5]})")
                # Look for items array
                for marker in ['"items"', '"tiles"', '"cards"', '"searchResults"', '"listings"']:
                    ms = list(_re.finditer(_re.escape(marker), body))
                    if ms:
                        emit(f"  {marker}: {len(ms)} hits")
                # First 500 chars
                emit(f"  Body head: {body[:300]!r}")
                # Save
                dump_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"ozon_composer_{sp.split('/')[1][:20]}.json")
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(body)
                emit(f"  Saved: {dump_path}")
            elif cr.status_code in (403, 429):
                emit(f"  BLOCKED: {cr.status_code}")
                emit(f"  Body: {cr.text[:200]!r}")
            else:
                emit(f"  Body: {cr.text[:200]!r}")
        except Exception as e:
            emit(f"  Error: {type(e).__name__}: {e}")
        time.sleep(3)

except Exception as e:
    emit(f"  OZON Composer error: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()}")

# ═══════════════════════════════════════════════════
# 2. OZON: PwFetcher with wait_for_url (challenge redirect)
# ═══════════════════════════════════════════════════
emit()
emit("── OZON: PwFetcher wait_for_url ──")
from r52.collectors.pw_fetch import PwFetcher

pw = PwFetcher(headless=True)
started = pw.start()
emit(f"  PwFetcher started: {started}")

if started:
    try:
        search_url = f"https://www.ozon.ru/search/?text={quote(QUERY)}&from_global=true&page=1"
        page = pw._context.new_page()
        page.set_default_timeout(30000)

        t0 = time.monotonic()
        resp = page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        emit(f"  goto: HTTP {resp.status if resp else '?'}, {time.monotonic()-t0:.1f}s")

        # Check for challenge input
        try:
            ch_el = page.query_selector("#challenge")
            emit(f"  #challenge element: {'FOUND' if ch_el else 'not found'}")
        except Exception:
            emit(f"  #challenge element: check error")

        # Wait for navigation (JS challenge might trigger page reload)
        emit(f"  Waiting 20s for potential JS redirect...")
        t0 = time.monotonic()
        try:
            # wait_for_url with wildcard — catches any URL change
            page.wait_for_url("**/search/**", timeout=20000)
            emit(f"  wait_for_url: redirect to search detected! ({time.monotonic()-t0:.1f}s)")
        except Exception as we:
            emit(f"  wait_for_url: no redirect ({time.monotonic()-t0:.1f}s): {type(we).__name__}")

        # Also try: wait for a product tile to appear
        emit(f"  Waiting 10s for product tiles...")
        try:
            page.wait_for_selector("[data-tid], .tile, [class*='tile']", timeout=10000)
            emit(f"  Product tiles APPEARED!")
        except Exception:
            emit(f"  Product tiles: not found after 10s")

        html = page.content()
        url_now = page.url
        title_now = page.title() or ""
        el_total = time.monotonic() - t0
        emit(f"  Final: {len(html)}B, {el_total:.1f}s total")
        emit(f"  URL: {url_now[:120]}")
        emit(f"  Title: {title_now[:100]}")

        # Cookies
        cookies = pw._context.cookies()
        emit(f"  Cookies: {len(cookies)}")
        for c in cookies[:8]:
            emit(f"    {c['name']} (domain={c.get('domain','?')})")

        # Product data
        prod_ids = _re.findall(r'/product/(\d{6,})/', html)
        tids = _re.findall(r'data-tid=["\'](\d+)["\']', html)
        emit(f"  /product/ IDs: {len(prod_ids)} (first 5: {prod_ids[:5]})")
        emit(f"  data-tid: {len(tids)} (first 5: {tids[:5]})")

        # If still challenge, try manual reload
        if "challenge" in title_now.lower() or "соединени" in title_now.lower() or len(html) < 10000:
            emit(f"  Still blocked. Trying reload...")
            page.reload(wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(10000)
            html2 = page.content()
            title2 = page.title() or ""
            emit(f"  After reload: {len(html2)}B, title={title2[:80]}")
            prod2 = _re.findall(r'/product/(\d{6,})/', html2)
            emit(f"  /product/ IDs after reload: {len(prod2)} (first 5: {prod2[:5]})")

        page.close()
    except Exception as e:
        emit(f"  PwFetcher error: {type(e).__name__}: {e}")
        emit(f"  {traceback.format_exc()}")
    finally:
        pw.stop()

# ═══════════════════════════════════════════════════
# 3. FARPOST: single p1 (minimize IP exposure)
# ═══════════════════════════════════════════════════
emit()
emit("── FARPOST: p1 only ──")
from r52.collectors.farpost import FarpostCollector

fp = FarpostCollector(use_proxies=False)
fp.source = "FARPOST"
t0 = time.monotonic()
try:
    items = fp.collect_by_search(QUERY, page=1, page_size=10)
    el = round(time.monotonic() - t0, 1)
    ids = [str(it.get("observation", it).get("id", "")) for it in items[:10]]
    emit(f"  p1: OK {len(items)} items {el}s")
    emit(f"  IDs: {ids}")
except Exception as e:
    el = round(time.monotonic() - t0, 1)
    _em = f"{type(e).__name__}: {str(e)[:200]} ({el}s)"
    emit(f"  p1: ERR {_em}")

emit()
emit("=== DONE ===")
flush()
print(f"\nOutput: {OUT}")

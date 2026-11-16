"""
diag_wb_alt.py — Alternative approaches to bypass WB 403/498.
Tries: mobile API, different impersonation, different headers, batch API.
"""
import sys, os, re, json, time, logging
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("R52_HOME", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test_home"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.wb_alt")

DEBUG_DIR = Path(__file__).parent
HOME = Path(os.environ["R52_HOME"])
QUERY = "тест"

# Try curl_cffi directly with different approaches
try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
    log.info("curl_cffi available")
except ImportError:
    HAS_CFFI = False
    log.warning("curl_cffi NOT available, using requests")
    import requests as cffi_req

results = {}

def try_request(label, url, headers, impersonate=None, timeout=12):
    """Try a request and log result."""
    try:
        if HAS_CFFI and impersonate:
            r = cffi_req.get(url, headers=headers, impersonate=impersonate, timeout=timeout)
        elif HAS_CFFI:
            r = cffi_req.get(url, headers=headers, timeout=timeout)
        else:
            r = cffi_req.get(url, headers=headers, timeout=timeout)
        body = r.text if hasattr(r, 'text') else r.content.decode('utf-8', 'replace')
        st = r.status_code
        log.info("  %s: HTTP %s, %d bytes", label, st, len(body))
        if st == 200 and len(body) > 10:
            try:
                data = json.loads(body)
                prods = data.get('data', {}).get('products', data.get('products', []))
                log.info("    → %d products", len(prods))
                for p in prods[:3]:
                    name = p.get('name', '?')[:40]
                    price = p.get('salePriceU', p.get('priceU', '?'))
                    log.info("    %s → price=%s", name, price)
                results[label] = {"status": 200, "products": len(prods)}
                return data
            except json.JSONDecodeError:
                log.info("    (not JSON, first 200: %s)", body[:200])
                results[label] = {"status": 200, "html": True}
                return body
        else:
            results[label] = {"status": st, "bytes": len(body)}
            if body:
                log.info("    body[:150]: %s", body[:150])
    except Exception as e:
        log.warning("  %s: %s %s", label, type(e).__name__, e)
        results[label] = {"error": str(e)}
    return None

# --- A: Mobile API (m.wildberries.ru) ---
log.info("=== A: Mobile API ===")
mobile_headers = {
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Referer": "https://m.wildberries.ru/",
}
a1_url = f"https://m.wildberries.ru/exactmatch/ru/common/v4/search?query={quote(QUERY)}&resultset=catalog&dest=-1257786&curr=rub&spp=20&appType=1&lang=ru&sort=popular&page=1"
try_request("A1: m.wb v4", a1_url, mobile_headers, impersonate="chrome")

# --- B: WB API with different Origin/Referer ---
log.info("=== B: Different Origin/Referer ===")
b_headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/catalog/0/ru/?search=test",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
b1_url = f"https://search.wb.ru/exactmatch/ru/common/v5/search?query={quote(QUERY)}&resultset=catalog&dest=-1257786&curr=rub&spp=20&appType=1&lang=ru&sort=popular&page=1"
try_request("B1: search.wb v5 + Origin", b1_url, b_headers, impersonate="chrome")

# --- C: WB with no Referer (clean) ---
log.info("=== C: Clean headers, no Referer ===")
c_headers = {
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
c1_url = f"https://catalog.wb.ru/exactmatch/ru/common/v4/search?query={quote(QUERY)}&resultset=catalog&dest=-1257786&curr=rub&spp=20&appType=1&lang=ru&sort=popular&page=1"
try_request("C1: catalog v4 clean", c1_url, c_headers, impersonate="chrome")

# --- D: WB with firefox impersonation ---
log.info("=== D: Firefox impersonation ===")
d_headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}
d1_url = f"https://search.wb.ru/exactmatch/ru/common/v4/search?query={quote(QUERY)}&resultset=catalog&dest=-1257786&curr=rub&spp=20&appType=1&lang=ru&sort=popular&page=1"
try_request("D1: search v4 firefox", d1_url, d_headers, impersonate="firefox")

# --- E: WB with safari impersonation ---
log.info("=== E: Safari impersonation ===")
try_request("E1: search v4 safari", d1_url, d_headers, impersonate="safari")

# --- F: WB HTML with different approach ---
log.info("=== F: WB HTML page ===")
f_headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
f1_url = f"https://www.wildberries.ru/catalog/0/ru/?search={quote(QUERY)}&page=1"
f_data = try_request("F1: HTML page", f1_url, f_headers, impersonate="chrome")
if f_data and isinstance(f_data, str) and len(f_data) > 5000:
    # Save
    wb_html = DEBUG_DIR / "wb_html_alt.html"
    wb_html.write_text(f_data, encoding="utf-8")
    log.info("  Saved: %s (%d bytes)", wb_html, len(f_data))
    # Look for price patterns
    prices = re.findall(r'(\d[\d\s\u00a0]{2,12})\s*(?:₽|руб)', f_data)
    log.info("  Price matches: %d", len(prices))
    for p in prices[:10]:
        log.info("    %s", p)
    # Look for __NEXT_DATA__ or state
    for marker in ['__NEXT_DATA__', 'window.__STATE', 'window.__INITIAL', 'data-server-rendered']:
        m = re.search(marker, f_data)
        if m:
            log.info("  Found %s at %d", marker, m.start())

# --- G: WB batch API (card.wb.ru/cards/v2/query) ---
log.info("=== G: Batch API ===")
g_headers = {
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}
g1_url = f"https://card.wb.ru/cards/v2/query?query={quote(QUERY)}&resultset=catalog&dest=-1257786&curr=rub&spp=20&appType=1&lang=ru&sort=popular&page=1"
try_request("G1: card v2 query", g1_url, g_headers, impersonate="chrome")

# --- H: Try with explicit Content-Type and X-Requested-With ---
log.info("=== H: XHR-style headers ===")
h_headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
h1_url = f"https://search.wb.ru/exactmatch/ru/common/v4/search?query={quote(QUERY)}&resultset=catalog&dest=-1257786&curr=rub&spp=20&appType=1&lang=ru&sort=popular&page=1"
try_request("H1: XHR style", h1_url, h_headers, impersonate="chrome")

# --- SUMMARY ---
log.info("=== SUMMARY ===")
for k, v in results.items():
    log.info("  %s: %s", k, json.dumps(v, ensure_ascii=False))

report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": results}
report_path = DEBUG_DIR / "diag_wb_alt_report.json"
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Report: %s", report_path)
log.info("=== DONE ===")

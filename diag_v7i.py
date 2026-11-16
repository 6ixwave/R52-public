"""
diag_v7i.py — Verify COM URL locale fix + probe RU search API HTML content.
Usage: python -u diag_v7i.py
"""
import sys, os, time, logging, traceback

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7i")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v7i_output.txt")
lines = []

def emit(s=""):
    print(s, flush=True)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v7i — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ═══════════════════════════════════════════════════════════
# TEST 1: COM wholesale with _lang=en_US&_currency=USD in URL
# ═══════════════════════════════════════════════════════════
emit("── [1] COM wholesale (URL locale params) ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    import re as _re
    from urllib.parse import unquote
    from html import unescape

    c = AliExpressCollector(use_proxies=False)
    c.source = "ALIEXPRESS"

    t0 = time.monotonic()
    html = c._fetch_wholesale_html(QUERY, 1)
    el = time.monotonic() - t0

    if html:
        emit(f"  OK: {len(html)} bytes, {el:.1f}s")
        it_w = sum(1 for w in ("cuffie","auricolari","disponib","magazzino","esaurito") if w in html[:80000].lower())
        en_w = sum(1 for w in ("headphone","earphone","wireless","bluetooth","audio") if w in html[:80000].lower())
        ru_w = sum(1 for w in ("наушник","гарнитур","беспроводн") if w in html[:80000].lower())
        emit(f"  Locale: IT={it_w}, EN={en_w}, RU={ru_w}")

        # Currency from pdp_npi
        pdp = _re.findall(r'pdp_npi=([^&]{0,80})', html[:200000])
        curs = set()
        for pm in pdp[:20]:
            parts = unquote(unescape(pm)).split("!")
            if len(parts) >= 2 and len(parts[1]) == 3:
                curs.add(parts[1])
        emit(f"  pdp_npi currencies: {curs}")

        # Check for USD symbol in raw HTML
        usd_sym = html[:100000].count("$")
        eur_sym = html[:100000].count("€")
        emit(f"  Symbols in first 100KB: $={usd_sym}, €={eur_sym}")

        products = c._parse_wholesale_html(html)
        emit(f"  Parsed: {len(products)} products")
        for i, p in enumerate(products[:5]):
            emit(f"  [{i}] id={p.get('item_id')}")
            emit(f"      title={str(p.get('title',''))[:60]!r}")
            emit(f"      price={p.get('price')} {p.get('currency')}")
    else:
        emit(f"  FAILED: {el:.1f}s (no HTML)")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()[-400:]}")
emit()

# ═══════════════════════════════════════════════════════════
# TEST 2: RU search API — dump HTML content to understand it
# ═══════════════════════════════════════════════════════════
emit("── [2] RU search API HTML probe ──")
try:
    from curl_cffi import requests as cffi_req
    from urllib.parse import quote

    eq = quote(QUERY, safe="")
    url = (f"https://ru.aliexpress.com/aer-webapi/v1/search"
           f"?SearchText={eq}&page=1&pageSize=8&sortType=default&_lang=ru_RU&_currency=RUB")

    t0 = time.monotonic()
    r = cffi_req.get(
        url,
        impersonate="chrome",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={eq}",
            "Origin": "https://ru.aliexpress.com",
            "X-Requested-With": "XMLHttpRequest",
        },
        cookies={
            "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
            "aep_usuc": "f_region=RU&f_locale=ru-RU&f_currency=RUB",
        },
        timeout=(15, 30),
    )
    el = time.monotonic() - t0
    body = r.text
    emit(f"  HTTP {r.status_code}, {len(body)} bytes, {el:.1f}s")
    emit(f"  Content-Type: {r.headers.get('Content-Type', 'N/A')}")
    emit(f"  First 500 chars:")
    for i, line in enumerate(body[:500].split('\n')[:15]):
        emit(f"    {line[:120]}")
    emit(f"  Last 200 chars:")
    for line in body[-200:].split('\n')[-5:]:
        emit(f"    {line[:120]}")

    # Check for common SPA markers
    spa_markers = ["__NEXT_DATA__", "window.__INITIAL_STATE__", "data-reactroot",
                   "id=\"root\"", "id=\"app\"", "window.__APP_DATA__", "aer-webapi"]
    found = [m for m in spa_markers if m in body]
    emit(f"  SPA markers: {found}")

    # Try POST instead of GET
    emit("  --- POST probe ---")
    t0 = time.monotonic()
    r2 = cffi_req.post(
        "https://ru.aliexpress.com/aer-webapi/v1/search",
        impersonate="chrome",
        json={"SearchText": QUERY, "page": 1, "pageSize": 8, "sortType": "default",
              "_lang": "ru_RU", "_currency": "RUB"},
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://ru.aliexpress.com/",
            "Origin": "https://ru.aliexpress.com",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json",
        },
        cookies={
            "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
        },
        timeout=(15, 30),
    )
    el2 = time.monotonic() - t0
    body2 = r2.text.strip()
    is_json2 = body2.startswith('{') or body2.startswith('[')
    emit(f"  POST: HTTP {r2.status_code}, {len(body2)}B, {el2:.1f}s, JSON={is_json2}")
    if is_json2:
        import json
        try:
            d = json.loads(body2)
            emit(f"    keys={list(d.keys())[:8]}")
        except Exception as je:
            emit(f"    parse err: {je}")
    else:
        emit(f"    first 200: {body2[:200]!r}")
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()[-400:]}")
emit()

# ═══════════════════════════════════════════════════════════
# TEST 3: RU wholesale (baseline, should still work)
# ═══════════════════════════════════════════════════════════
emit("── [3] RU wholesale (baseline) ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    c = AliExpressRUCollector(use_proxies=False)
    c.source = "ALIEXPRESS_RU"

    t0 = time.monotonic()
    html = c._fetch_wholesale_html(QUERY, 1)
    el = time.monotonic() - t0

    if html:
        emit(f"  OK: {len(html)} bytes, {el:.1f}s")
        products = c._parse_wholesale_html(html)
        emit(f"  Parsed: {len(products)} products")
        if products:
            p0 = products[0]
            emit(f"  [0] title={str(p0.get('title',''))[:60]!r} price={p0.get('price')} {p0.get('currency')}")
        # Full collect_by_search
        t0 = time.monotonic()
        items = c.collect_by_search(QUERY, page=1, page_size=10)
        el2 = time.monotonic() - t0
        emit(f"  collect_by_search: {len(items)} items, {el2:.1f}s")
    else:
        emit(f"  FAILED: {el:.1f}s")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
emit()

emit("=== DONE ===")
flush()
log.info("diag_v7i complete")

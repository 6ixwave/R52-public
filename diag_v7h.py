"""
diag_v7h.py — Verify 3 fixes: COM locale, COM cookies, RU search API.
Usage: python -u diag_v7h.py
"""
import sys, os, time, logging, traceback

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7h")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v7h_output.txt")
lines = []

def emit(s=""):
    print(s, flush=True)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v7h — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ═══════════════════════════════════════════════════════════
# TEST 1: COM _fetch_wholesale_html (should now have EN/USD cookies)
# ═══════════════════════════════════════════════════════════
emit("── [1] COM wholesale (EN/USD cookies) ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    import re as _re
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
        pdp = _re.findall(r'pdp_npi=([^&]{0,80})', html[:200000])
        curs = set()
        for pm in pdp[:20]:
            from urllib.parse import unquote
            from html import unescape
            parts = unquote(unescape(pm)).split("!")
            if len(parts) >= 2 and len(parts[1]) == 3:
                curs.add(parts[1])
        emit(f"  pdp_npi currencies: {curs}")
        products = c._parse_wholesale_html(html)
        emit(f"  Parsed: {len(products)} products")
        if products:
            p0 = products[0]
            emit(f"  [0] title={str(p0.get('title',''))[:60]!r}")
            emit(f"      price={p0.get('price')} {p0.get('currency')}")
    else:
        emit(f"  FAILED: {el:.1f}s")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()[-400:]}")
emit()

# ═══════════════════════════════════════════════════════════
# TEST 2: RU _fetch_search_api (should now have X-Requested-With)
# ═══════════════════════════════════════════════════════════
emit("── [2] RU search API (X-Requested-With) ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    c = AliExpressRUCollector(use_proxies=False)
    c.source = "ALIEXPRESS_RU"

    t0 = time.monotonic()
    data = c._fetch_search_api(QUERY, 1, 8)
    el = time.monotonic() - t0

    if data:
        emit(f"  OK: {el:.1f}s, keys={list(data.keys())[:5]}")
        api_products = c._parse_search_api(data)
        emit(f"  Parsed: {len(api_products)} products")
        if api_products:
            p0 = api_products[0]
            emit(f"  [0] id={p0.get('item_id')} title={str(p0.get('title',''))[:50]!r} price={p0.get('price')}")
    else:
        emit(f"  None in {el:.1f}s")
        # Manual probe: try different URL patterns
        emit("  --- Manual API probes ---")
        from curl_cffi import requests as cffi_req
        from urllib.parse import quote
        eq = quote(QUERY, safe="")
        probes = [
            (f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={eq}&page=1&pageSize=8&sortType=default&_lang=ru_RU&_currency=RUB",
             "aer-webapi/v1/search"),
            (f"https://ru.aliexpress.com/api/search?SearchText={eq}&page=1&pageSize=8",
             "api/search"),
            (f"https://ru.aliexpress.com/wholesale?SearchText={eq}&page=1&pageSize=8&_lang=ru_RU&_currency=RUB",
             "wholesale (HTML fallback)"),
        ]
        for url, label in probes:
            try:
                t0 = time.monotonic()
                r = cffi_req.get(url, impersonate="chrome",
                                 headers={
                                     "Accept": "application/json, text/plain, */*",
                                     "Accept-Language": "ru-RU,ru;q=0.9",
                                     "Referer": "https://ru.aliexpress.com/",
                                     "Origin": "https://ru.aliexpress.com",
                                     "X-Requested-With": "XMLHttpRequest",
                                 },
                                 cookies={
                                     "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
                                     "aep_usuc": "f_region=RU&f_locale=ru-RU&f_currency=RUB",
                                 },
                                 timeout=(10, 20))
                el2 = time.monotonic() - t0
                body = r.text.strip()
                is_json = body.startswith('{') or body.startswith('[')
                emit(f"  [{label}] HTTP {r.status_code}, {len(body)}B, {el2:.1f}s, JSON={is_json}")
                if is_json:
                    import json
                    try:
                        d = json.loads(body)
                        emit(f"    keys={list(d.keys())[:6]}")
                    except Exception:
                        pass
                elif len(body) < 200:
                    emit(f"    body={body[:150]!r}")
            except Exception as e2:
                emit(f"  [{label}] ERR: {e2}")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()[-400:]}")
emit()

# ═══════════════════════════════════════════════════════════
# TEST 3: COM Playwright locale (should be en-US now)
# ═══════════════════════════════════════════════════════════
emit("── [3] COM Playwright locale check ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    c = AliExpressCollector(use_proxies=False)
    c.source = "ALIEXPRESS"
    t0 = time.monotonic()
    pw_ok = c._ensure_browser()
    el = time.monotonic() - t0
    emit(f"  _ensure_browser: {pw_ok} in {el:.1f}s")
    if pw_ok:
        # Check locale via page.evaluate
        try:
            loc = c._page.evaluate("navigator.language")
            langs = c._page.evaluate("JSON.stringify(navigator.languages)")
            emit(f"  navigator.language={loc}")
            emit(f"  navigator.languages={langs}")
        except Exception as e:
            emit(f"  eval err: {e}")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
emit()

emit("=== DONE ===")
flush()
log.info("diag_v7h complete")

r"""test_multi.py — Мульти-стратегийный тест 4 площадок.
Каждая площадка: 2-4 стратегии параллельно в одном процессе.
Run: C:\Python315\python.exe test_multi.py
"""
import sys, os, json, time, logging, threading, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-16s %(levelname)-5s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("TEST_MULTI")

RESULTS = {}
_lock = threading.Lock()

def record(platform, strategy, ok, detail, t0):
    dt = round(time.time() - t0, 1)
    with _lock:
        RESULTS[f"{platform}:{strategy}"] = {"ok": ok, "detail": detail, "s": dt}
    log.info("[%s:%s] %s %.1fs — %s", platform, strategy, "OK" if ok else "FAIL", dt, detail[:120])


# ============================================================
# FARPOST
# ============================================================
def fp_strategy_cffi():
    """F1: curl_cffi impersonate=chrome на карточку."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        lid = "134693091"
        url = f"https://www.farpost.ru/vladivostok/{lid}.html"
        # warmup
        w = cr.get("https://www.farpost.ru/", impersonate="chrome", timeout=15)
        time.sleep(2)
        r = cr.get(url, impersonate="chrome", timeout=20)
        html = r.text
        has_price = "data-bulletin-price" in html or "viewbull-summary-price" in html
        has_title = "<h1" in html or "og:title" in html
        has_img = "static.baza.farpost.ru" in html
        is_verify = "/verify" in r.url or ("Фарпост" in html and len(html) < 5000)
        if has_price and has_title:
            record("FP", "cffi", True, f"HTTP={r.status_code} price={has_price} title={has_title} img={has_img} len={len(html)}", t0)
        elif is_verify:
            record("FP", "cffi", False, f"/verify redirect url={r.url} len={len(html)}", t0)
        else:
            record("FP", "cffi", False, f"HTTP={r.status_code} price={has_price} title={has_title} img={has_img} len={len(html)}", t0)
    except Exception as e:
        record("FP", "cffi", False, f"exc: {e}", t0)


def fp_strategy_verify_flow():
    """F2: warmup → product → /verify → GET /verify (follow) → retry product."""
    t0 = time.time()
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
        lid = "121063385"
        url = f"https://www.farpost.ru/vladivostok/{lid}.html"
        # 1. warmup
        s.get("https://www.farpost.ru/", timeout=15)
        time.sleep(1)
        # 2. product
        r1 = s.get(url, timeout=20)
        if "/verify" in (r1.url or ""):
            # 3. GET the /verify URL explicitly (it sets a cookie)
            verify_url = r1.url
            r2 = s.get(verify_url, timeout=15, allow_redirects=True)
            time.sleep(3)
            # 4. retry product
            r3 = s.get(url, timeout=20)
            html = r3.text
            has_price = "data-bulletin-price" in html or "viewbull-summary-price" in html
            is_verify2 = "/verify" in (r3.url or "")
            if has_price and not is_verify2:
                record("FP", "verify_flow", True, f"cookies={list(s.cookies.keys())} price={has_price} len={len(html)}", t0)
            else:
                record("FP", "verify_flow", False, f"retry url={r3.url} verify={is_verify2} price={has_price} len={len(html)} cookies={list(s.cookies.keys())}", t0)
        else:
            html = r1.text
            has_price = "data-bulletin-price" in html
            record("FP", "verify_flow", has_price, f"direct HTTP={r1.status_code} price={has_price} len={len(html)}", t0)
    except Exception as e:
        record("FP", "verify_flow", False, f"exc: {e}", t0)


def fp_strategy_cdn_json():
    """F3: CDN JSON endpoint (baza.farpost.ru API)."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        lid = "136303743"
        # Try known CDN/API patterns
        endpoints = [
            f"https://baza.farpost.ru/api/bulletin/{lid}",
            f"https://www.farpost.ru/bulletin/{lid}",
            f"https://www.farpost.ru/vladivostok/{lid}.html?format=json",
        ]
        for ep in endpoints:
            r = cr.get(ep, impersonate="chrome", timeout=12)
            if r.status_code == 200 and len(r.text) > 100:
                ct = r.headers.get("content-type", "")
                if "json" in ct:
                    data = r.json()
                    record("FP", "cdn_json", True, f"ep={ep.split('/')[-2]}/{ep.split('/')[-1][:20]} JSON keys={list(data.keys())[:5]}", t0)
                    return
                elif "html" in ct and "data-bulletin-price" in r.text:
                    record("FP", "cdn_json", True, f"ep={ep.split('/')[-1][:30]} HTML price found len={len(r.text)}", t0)
                    return
        record("FP", "cdn_json", False, f"all {len(endpoints)} endpoints: " + ", ".join(
            f"{ep.split('farpost.ru')[-1][:25]}={cr.get(ep, impersonate='chrome', timeout=8).status_code}" for ep in endpoints[:2]
        ), t0)
    except Exception as e:
        record("FP", "cdn_json", False, f"exc: {e}", t0)


# ============================================================
# WILDBERRIES
# ============================================================
def wb_strategy_card_price():
    """W1: card.json (works) + price via card.wb.ru/cards/v2/detail."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        nm = 1097975275
        vol = nm // 100000
        # card.json
        card_url = f"https://basket-{vol}.wbbasket.ru/vol{vol}/part{vol % 10}/{nm}/info/card.json"
        r_card = cr.get(card_url, impersonate="chrome", timeout=12)
        card_ok = r_card.status_code == 200
        title = ""
        if card_ok:
            cd = r_card.json()
            title = (cd.get("name") or "")[:40]
        # Price: try multiple endpoints
        price_endpoints = [
            f"https://card.wb.ru/cards/v2/detail?nm={nm}",
            f"https://card.wb.ru/cards/v1/detail?nm={nm}",
            f"https://wbx-content-v2.wbstatic.net/price/{nm}.json",
            f"https://www.wildberries.ru/catalog/{nm}/info/price.aspx",
        ]
        price_found = False
        price_detail = ""
        for ep in price_endpoints:
            r_p = cr.get(ep, impersonate="chrome", timeout=10,
                         headers={"Referer": "https://www.wildberries.ru/", "Accept": "application/json"})
            if r_p.status_code == 200 and len(r_p.text) > 10:
                try:
                    pd = r_p.json()
                    if isinstance(pd, dict) and "data" in pd:
                        items = pd["data"].get("items", [pd["data"]])
                        for it in items:
                            prices = it.get("sizes", [{}])[0].get("prices", {})
                            if prices.get("basic") or prices.get("product"):
                                price_found = True
                                price_detail = f"ep={ep.split('wb.ru')[-1][:20]} basic={prices.get('basic')} product={prices.get('product')}"
                                break
                    elif isinstance(pd, list) and pd:
                        it = pd[0]
                        prices = it.get("sizes", [{}])[0].get("prices", {}) if it.get("sizes") else it.get("prices", {})
                        if prices.get("basic"):
                            price_found = True
                            price_detail = f"ep={ep.split('wb.ru')[-1][:20]} basic={prices.get('basic')}"
                            break
                except Exception:
                    pass
            if price_found:
                break
        detail = f"card={'OK' if card_ok else 'FAIL'} title='{title}' price={'OK' if price_found else 'FAIL'} {price_detail}"
        record("WB", "card_price", card_ok and price_found, detail, t0)
    except Exception as e:
        record("WB", "card_price", False, f"exc: {e}", t0)


def wb_strategy_html_cffi():
    """W2: HTML карточка через curl_cffi (обход 498)."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        nm = 1097975275
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        r = cr.get(url, impersonate="chrome", timeout=20,
                   headers={"Referer": "https://www.wildberries.ru/", "Accept-Language": "ru-RU,ru;q=0.9"})
        html = r.text
        has_price = "price" in html.lower() and ("₽" in html or "руб" in html.lower())
        has_title = "<title>" in html or "og:title" in html
        has_jsonld = "application/ld+json" in html
        has_state = "__NEXT_DATA__" in html or "window.__" in html
        detail = f"HTTP={r.status_code} len={len(html)} price={has_price} title={has_title} jsonld={has_jsonld} state={has_state}"
        record("WB", "html_cffi", r.status_code == 200 and has_price, detail, t0)
    except Exception as e:
        record("WB", "html_cffi", False, f"exc: {e}", t0)


def wb_strategy_web_api():
    """W3: web API (api.wildberries.ru) для цены."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        nm = 1097975275
        endpoints = [
            f"https://api.wildberries.ru/web/api/v1/cards/{nm}/price",
            f"https://www.wildberries.ru/web/api/v1/cards/{nm}/price",
            f"https://wbx-content-v2.wbstatic.net/cards/{nm}/price.json",
        ]
        for ep in endpoints:
            r = cr.get(ep, impersonate="chrome", timeout=10,
                       headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
            if r.status_code == 200:
                try:
                    d = r.json()
                    record("WB", "web_api", True, f"ep={ep.split('wildberries')[-1][:25]} data={str(d)[:80]}", t0)
                    return
                except:
                    pass
        record("WB", "web_api", False, f"all {len(endpoints)} failed", t0)
    except Exception as e:
        record("WB", "web_api", False, f"exc: {e}", t0)


# ============================================================
# OZON
# ============================================================
def oz_strategy_composer_cffi():
    """O1: Composer API через curl_cffi (fix SSL)."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        oid = "3481320800"
        ep = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/"
        r = cr.get(ep, impersonate="chrome", timeout=15, verify=False,
                   headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/",
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"})
        if r.status_code == 200:
            d = r.json()
            blocks = d.get("page", {}).get("blocks", [])
            has_title = any("title" in str(b.get("props", {})).lower() for b in blocks[:5]) if blocks else False
            has_price = any("price" in str(b).lower() for b in blocks[:10]) if blocks else False
            record("OZ", "composer_cffi", True, f"HTTP=200 blocks={len(blocks)} title={has_title} price={has_price} len={len(r.text)}", t0)
        else:
            record("OZ", "composer_cffi", False, f"HTTP={r.status_code} len={len(r.text)} body={r.text[:80]}", t0)
    except Exception as e:
        record("OZ", "composer_cffi", False, f"exc: {e}", t0)


def oz_strategy_ssr_cffi():
    """O2: SSR HTML через curl_cffi."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        oid = "3481320800"
        url = f"https://www.ozon.ru/product/{oid}/"
        r = cr.get(url, impersonate="chrome", timeout=20, verify=False,
                   headers={"Accept-Language": "ru-RU,ru;q=0.9", "Referer": "https://www.ozon.ru/"})
        html = r.text
        has_price = "price" in html.lower() and ("₽" in html or "data-widget" in html)
        has_title = "og:title" in html or "<title>" in html
        has_img = "ir.ozone.ru" in html
        is_bot = "no_conn" in html.lower() or "Похоже, нет соединения" in html
        detail = f"HTTP={r.status_code} len={len(html)} price={has_price} title={has_title} img={has_img} bot={is_bot}"
        record("OZ", "ssr_cffi", r.status_code == 200 and has_price and not is_bot, detail, t0)
    except Exception as e:
        record("OZ", "ssr_cffi", False, f"exc: {e}", t0)


def oz_strategy_mobile_api():
    """O3: Mobile API endpoint."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        oid = "3481320800"
        endpoints = [
            f"https://api.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/&platform=mobile",
            f"https://www.ozon.ru/api/entrypoint/pdp/{oid}",
            f"https://api.ozon.ru/v1/shares/product/{oid}",
        ]
        for ep in endpoints:
            r = cr.get(ep, impersonate="chrome", timeout=12, verify=False,
                       headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"})
            if r.status_code == 200 and len(r.text) > 50:
                record("OZ", "mobile_api", True, f"ep={ep.split('ozon.ru')[-1][:30]} len={len(r.text)} head={r.text[:60]}", t0)
                return
        record("OZ", "mobile_api", False, f"all {len(endpoints)} failed", t0)
    except Exception as e:
        record("OZ", "mobile_api", False, f"exc: {e}", t0)


# ============================================================
# AVITO
# ============================================================
def av_strategy_find_id():
    """A1: Найти живой ID через поиск на Avito."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        # Search page
        r = cr.get("https://www.avito.ru/vladivostok/telefony", impersonate="chrome", timeout=15,
                   headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        if r.status_code == 200:
            import re
            # Find ad IDs: /ad/ or /vladivostok/.../\d+
            ids = re.findall(r'avito\.ru/[a-z-]+/(\d{8,})', r.text)
            if not ids:
                ids = re.findall(r'/(\d{8,})\?s=', r.text)
            if ids:
                record("AV", "find_id", True, f"found {len(ids)} IDs, first={ids[0]} len={len(r.text)}", t0)
                # Save for other strategies
                RESULTS["AV:found_ids"] = ids[:5]
            else:
                record("AV", "find_id", False, f"HTTP=200 but no IDs in {len(r.text)} chars", t0)
        else:
            record("AV", "find_id", False, f"HTTP={r.status_code} len={len(r.text)}", t0)
    except Exception as e:
        record("AV", "find_id", False, f"exc: {e}", t0)


def av_strategy_direct():
    """A2: Прямой GET на карточку (www + m) с cffi."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        # Use ID from find_id if available, else fallback
        ids = RESULTS.get("AV:found_ids", ["1045231001"])
        aid = ids[0] if ids else "1045231001"
        urls = [f"https://www.avito.ru/ad/{aid}", f"https://m.avito.ru/ad/{aid}"]
        for u in urls:
            r = cr.get(u, impersonate="chrome", timeout=15,
                       headers={"Accept-Language": "ru-RU,ru;q=0.9"})
            html = r.text
            if r.status_code == 200 and len(html) > 1000:
                has_price = "price" in html.lower() or "₽" in html
                has_title = "og:title" in html or "<title>" in html
                has_img = "avito.st" in html or "mmedia.avito" in html
                is_captcha = "captcha" in html.lower() or "SmartCaptcha" in html
                detail = f"id={aid} url={u.split('/')[2]} HTTP={r.status_code} len={len(html)} price={has_price} title={has_title} img={has_img} cap={is_captcha}"
                record("AV", "direct", has_price and has_title and not is_captcha, detail, t0)
                return
            elif r.status_code == 200:
                record("AV", "direct", False, f"id={aid} url={u.split('/')[2]} HTTP=200 but short len={len(html)}", t0)
                return
        record("AV", "direct", False, f"id={aid} all URLs failed", t0)
    except Exception as e:
        record("AV", "direct", False, f"exc: {e}", t0)


def av_strategy_jsonld():
    """A3: JSON-LD / state extraction from Avito HTML."""
    t0 = time.time()
    try:
        from curl_cffi import requests as cr
        import re, json as j
        ids = RESULTS.get("AV:found_ids", ["1045231001"])
        aid = ids[0] if ids else "1045231001"
        r = cr.get(f"https://www.avito.ru/ad/{aid}", impersonate="chrome", timeout=15)
        html = r.text
        # JSON-LD
        ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        has_ld = False
        for lb in ld_blocks:
            try:
                d = j.loads(lb.strip())
                if isinstance(d, dict) and d.get("@type") == "Product":
                    has_ld = True
                    break
            except:
                pass
        # State
        has_state = "__PRELOADED_STATE__" in html or "data-state" in html
        # Price in HTML
        price_m = re.search(r'class="[^"]*price[^"]*"[^>]*>[\s]*([\d\s]+)₽', html)
        detail = f"id={aid} HTTP={r.status_code} ld={has_ld} state={has_state} price_html={bool(price_m)} len={len(html)}"
        record("AV", "jsonld", has_ld or (price_m and has_state), detail, t0)
    except Exception as e:
        record("AV", "jsonld", False, f"exc: {e}", t0)


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=" * 60)
    log.info("MULTI-STRATEGY TEST  %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    # Phase 1: Find Avito ID first (other AV strategies depend on it)
    log.info("--- Phase 1: AVITO find ID ---")
    av_strategy_find_id()

    # Phase 2: All strategies (grouped by platform for timing)
    log.info("--- Phase 2: FARPOST ---")
    fp_strategy_cffi()
    fp_strategy_verify_flow()
    fp_strategy_cdn_json()

    log.info("--- Phase 3: WILDBERRIES ---")
    wb_strategy_card_price()
    wb_strategy_html_cffi()
    wb_strategy_web_api()

    log.info("--- Phase 4: OZON ---")
    oz_strategy_composer_cffi()
    oz_strategy_ssr_cffi()
    oz_strategy_mobile_api()

    log.info("--- Phase 5: AVITO (with found ID) ---")
    av_strategy_direct()
    av_strategy_jsonld()

    # Summary
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    for k, v in sorted(RESULTS.items()):
        mark = "✓" if v["ok"] else "✗"
        log.info("  %s %-20s %s", mark, k, v["detail"][:90])
    passed = sum(1 for v in RESULTS.values() if v["ok"])
    log.info("  %d/%d strategies OK", passed, len(RESULTS))

    out = ROOT / "test_multi_result.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "results": RESULTS}, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved: %s", out)


if __name__ == "__main__":
    main()

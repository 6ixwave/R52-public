"""diag_v7k — тест переопределения xman_us_f / intl_locale cookies.
Гипотеза: сервер geo-ставит it_IT через xman_us_f, и aep_usuc_f не перебивает.
Решение: слать xman_us_f + intl_locale с нужной локалью.
"""
import sys, os, time, re, json, logging
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7k")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def fetch_ru(query="наушники", extra_cookies=None, ga="glo2rus", label=""):
    """Fetch RU wholesale with optional extra cookies."""
    from curl_cffi import requests as cffi_req
    url = (f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}"
           f"&page=1&_lang=ru_RU&_currency=RUB&gatewayAdapt={ga}")
    hdrs = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://ru.aliexpress.com/",
        "Upgrade-Insecure-Requests": "1",
    }
    cookies = {
        "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
        "aep_usuc": "f_region=RU&f_locale=ru-RU&f_currency=RUB",
    }
    if extra_cookies:
        cookies.update(extra_cookies)

    t0 = time.monotonic()
    resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=cookies)
    lat = time.monotonic() - t0
    html = resp.text

    # Extract key indicators
    lang_m = re.search(r'<html[^>]*lang="([^"]*)"', html, re.I)
    lang = lang_m.group(1) if lang_m else "?"

    # pdp_npi currencies (search in card-list area for accuracy)
    pdp_currencies = set()
    card_idx = html.find('id="card-list"')
    search_area = html[card_idx:card_idx+700000] if card_idx >= 0 else html[:700000]
    for pm in re.finditer(r'pdp_npi=([^&"]+)', search_area):
        raw = pm.group(1)
        # URL-decode in case of %21 etc
        from urllib.parse import unquote as uq
        decoded = uq(raw)
        parts = decoded.split("!")
        if len(parts) >= 4 and re.match(r'^[A-Z]{3}$', parts[1]):
            pdp_currencies.add(parts[1])

    # First title
    title = "?"
    if card_idx >= 0:
        t_m = re.search(r'<h3[^>]*>(.*?)</h3>', html[card_idx:card_idx+50000], re.DOTALL)
        if t_m:
            title = re.sub(r'<[^>]+>', '', t_m.group(1)).strip()[:80]

    # Count it.aliexpress vs ru.aliexpress
    it_cnt = html.count("it.aliexpress")
    ru_cnt = html.count("ru.aliexpress")

    # Set-Cookie response
    set_ck = resp.headers.get("set-cookie", "")[:200]

    log.info("  [%s] HTTP %d, %dB, %.1fs | lang=%s | pdp_cur=%s | IT=%d RU=%d",
             label, resp.status_code, len(html), lat, lang, pdp_currencies, it_cnt, ru_cnt)
    log.info("    title: %s", title)
    if set_ck:
        log.info("    Set-Cookie: %s", set_ck)

    return resp, html, lang, pdp_currencies


# ─── Test 1: Baseline (current state) ───
def test_baseline():
    log.info("── [1] Baseline: aep_usuc_f only (current) ──")
    return fetch_ru(label="baseline")


# ─── Test 2: Add xman_us_f + intl_locale ───
def test_xman_override():
    log.info("── [2] Override: + xman_us_f + intl_locale ──")
    extra = {
        "xman_us_f": "x_locale=ru_RU&x_l=0&x_c_chg=1",
        "intl_locale": "ru_RU",
        "intl_l": "ru",
    }
    return fetch_ru(extra_cookies=extra, label="xman_override")


# ─── Test 3: xman_us_f with full format from Set-Cookie ───
def test_xman_full():
    log.info("── [3] Override: xman_us_f full format ──")
    # Mimic the server's own format but with ru_RU
    extra = {
        "xman_us_f": "x_locale=ru_RU&x_l=0&x_c_chg=1&acs_rt=diagtest0000000000000000000000&intl_locale=ru_RU",
        "intl_locale": "ru_RU",
    }
    return fetch_ru(extra_cookies=extra, label="xman_full")


# ─── Test 4: gatewayAdapt variations ───
def test_ga_variants():
    log.info("── [4] gatewayAdapt variants ──")
    for ga in ["glo2rus", "ru", "RU", "glo2ru", "ru2ru", "rus"]:
        fetch_ru(ga=ga, label=f"ga={ga}")
        time.sleep(1)


# ─── Test 5: COM with xman override ───
def test_com_override():
    log.info("── [5] COM with xman_us_f=en_US ──")
    from curl_cffi import requests as cffi_req
    url = (f"https://www.aliexpress.com/wholesale?SearchText={quote('headphones')}"
           f"&page=1&_lang=en_US&_currency=USD&gatewayAdapt=glo2usa")
    hdrs = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.aliexpress.com/",
        "Upgrade-Insecure-Requests": "1",
    }
    cookies = {
        "aep_usuc_f": "f_locale=en-US&f_region=US&f_currency=USD&ais=en-US&fre=0&c_b_locale=en_US",
        "aep_usuc": "f_region=US&f_locale=en-US&f_currency=USD",
        "xman_us_f": "x_locale=en_US&x_l=0&x_c_chg=1",
        "intl_locale": "en_US",
    }
    t0 = time.monotonic()
    resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=cookies)
    lat = time.monotonic() - t0
    html = resp.text
    lang_m = re.search(r'<html[^>]*lang="([^"]*)"', html, re.I)
    lang = lang_m.group(1) if lang_m else "?"

    pdp_currencies = set()
    card_idx = html.find('id="card-list"')
    search_area = html[card_idx:card_idx+700000] if card_idx >= 0 else html[:700000]
    for pm in re.finditer(r'pdp_npi=([^&"]+)', search_area):
        from urllib.parse import unquote as uq
        parts = uq(pm.group(1)).split("!")
        if len(parts) >= 4 and re.match(r'^[A-Z]{3}$', parts[1]):
            pdp_currencies.add(parts[1])

    title = "?"
    if card_idx >= 0:
        t_m = re.search(r'<h3[^>]*>(.*?)</h3>', html[card_idx:card_idx+50000], re.DOTALL)
        if t_m:
            title = re.sub(r'<[^>]+>', '', t_m.group(1)).strip()[:80]

    log.info("  [COM+xman] HTTP %d, %dB, %.1fs | lang=%s | pdp_cur=%s",
             resp.status_code, len(html), lat, lang, pdp_currencies)
    log.info("    title: %s", title)
    set_ck = resp.headers.get("set-cookie", "")[:200]
    if set_ck:
        log.info("    Set-Cookie: %s", set_ck)


# ─── Test 6: Search API with xman + specific Accept ───
def test_search_api_xman():
    log.info("── [6] Search API with xman cookies ──")
    from curl_cffi import requests as cffi_req
    query = "наушники"
    eq = quote(query, safe="")

    cookies = {
        "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
        "aep_usuc": "f_region=RU&f_locale=ru-RU&f_currency=RUB",
        "xman_us_f": "x_locale=ru_RU&x_l=0&x_c_chg=1",
        "intl_locale": "ru_RU",
    }

    # 6a. Standard GET
    api_url = (f"https://ru.aliexpress.com/aer-webapi/v1/search"
               f"?SearchText={eq}&page=1&pageSize=40&sortType=default&_lang=ru_RU&_currency=RUB")
    hdrs = {
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={eq}",
        "Origin": "https://ru.aliexpress.com",
        "X-Requested-With": "XMLHttpRequest",
    }
    resp = cffi_req.get(api_url, impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=cookies)
    body = resp.text.strip()
    is_json = body.startswith("{") or body.startswith("[")
    log.info("  6a. GET aer-webapi/v1/search: HTTP %d, %dB, JSON=%s", resp.status_code, len(body), is_json)
    if is_json:
        data = json.loads(body)
        log.info("      keys: %s", list(data.keys())[:10])
    else:
        log.info("      body[:150]: %s", body[:150])

    # 6b. Try /aer-webapi/v1/search with _input_charset
    api_url2 = (f"https://ru.aliexpress.com/aer-webapi/v1/search"
                f"?SearchText={eq}&page=1&pageSize=40&_lang=ru_RU&_currency=RUB&_input_charset=utf-8")
    resp2 = cffi_req.get(api_url2, impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=cookies)
    body2 = resp2.text.strip()
    is_json2 = body2.startswith("{") or body2.startswith("[")
    log.info("  6b. +_input_charset: HTTP %d, %dB, JSON=%s", resp2.status_code, len(body2), is_json2)

    # 6c. Try the MTOP-style endpoint
    mtop_url = "https://ru.aliexpress.com/h5/mtop.ae.search.pc.search/1.0/"
    mtop_hdrs = {
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={eq}",
        "Origin": "https://ru.aliexpress.com",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    mtop_data = f"jsv=2.6.1&appKey=12574478&api=mtop.ae.search.pc.search&v=1.0&type=originaljson&dataType=json&data=%7B%22keyword%22%3A%22{eq}%22%2C%22page%22%3A1%2C%22pageSize%22%3A40%7D"
    resp3 = cffi_req.post(mtop_url, impersonate="chrome", timeout=(15, 30),
                          headers=mtop_hdrs, cookies=cookies, data=mtop_data)
    body3 = resp3.text.strip()
    is_json3 = body3.startswith("{") or body3.startswith("[")
    log.info("  6c. MTOP: HTTP %d, %dB, JSON=%s", resp3.status_code, len(body3), is_json3)
    if is_json3:
        try:
            d3 = json.loads(body3)
            log.info("      MTOP keys: %s", list(d3.keys())[:10])
            if "data" in d3:
                log.info("      data keys: %s", list(d3["data"].keys())[:10] if isinstance(d3["data"], dict) else type(d3["data"]))
        except json.JSONDecodeError:
            log.info("      MTOP parse fail: %s", body3[:200])
    else:
        log.info("      MTOP body[:200]: %s", body3[:200])

    # 6d. Check if the SPA shell has a data endpoint embedded
    log.info("  6d. SPA shell deep analysis")
    # Re-fetch the SPA shell
    resp4 = cffi_req.get(api_url, impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=cookies)
    spa = resp4.text
    # Look for JSON config in script tags
    script_blocks = re.findall(r'<script[^>]*>(.*?)</script>', spa, re.DOTALL)
    for i, sb in enumerate(script_blocks):
        if len(sb) > 50 and ("api" in sb.lower() or "endpoint" in sb.lower() or "search" in sb.lower()):
            log.info("    Script[%d] (%d chars): %s", i, len(sb), sb[:300])
    # Look for window.__ or global config
    for var in ["__APP_DATA__", "__INITIAL_STATE__", "__APP_CONFIG__", "pageData", "searchConfig"]:
        vm = re.search(rf'window\.{var}\s*=\s*(.+?)(?:;\s*(?:</script>|window\.|$))', spa, re.DOTALL)
        if vm:
            log.info("    window.%s: %s", var, vm.group(1)[:200])


if __name__ == "__main__":
    print(f"=== diag_v7k — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    try:
        test_baseline()
    except Exception as e:
        log.exception("T1 error: %s", e)
    try:
        test_xman_override()
    except Exception as e:
        log.exception("T2 error: %s", e)
    try:
        test_xman_full()
    except Exception as e:
        log.exception("T3 error: %s", e)
    try:
        test_ga_variants()
    except Exception as e:
        log.exception("T4 error: %s", e)
    try:
        test_com_override()
    except Exception as e:
        log.exception("T5 error: %s", e)
    try:
        test_search_api_xman()
    except Exception as e:
        log.exception("T6 error: %s", e)
    print("=== DONE ===")
    log.info("diag_v7k complete")

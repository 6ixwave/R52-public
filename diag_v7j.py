"""diag_v7j — точечная диагностика локали/валюты RU и Search API.
Цели:
  1. Извлечь embedded state (runParams, __INIT_DATA__, html lang, meta) из RU wholesale HTML
  2. Проверить, что сервер реально ставит в locale/currency
  3. Разобраться, почему Search API отдаёт SPA HTML вместо JSON
  4. Попробовать warm-up: homepage → cookies → search page
"""
import sys, os, time, re, json, logging
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7j")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── 1. RU wholesale: embedded state ───
def probe_ru_wholesale():
    log.info("── [1] RU wholesale embedded state ──")
    from curl_cffi import requests as cffi_req
    query = "наушники"
    url = (f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}"
           f"&page=1&_lang=ru_RU&_currency=RUB&gatewayAdapt=glo2rus")
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
    t0 = time.monotonic()
    resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=cookies)
    lat = time.monotonic() - t0
    html = resp.text
    log.info("HTTP %d, %d bytes, %.1fs", resp.status_code, len(html), lat)

    # 1a. <html lang>
    lang_m = re.search(r'<html[^>]*lang="([^"]*)"', html, re.I)
    log.info("  <html lang=%r>", lang_m.group(1) if lang_m else "NOT FOUND")

    # 1b. Meta tags
    for pat, label in [
        (r'<meta[^>]*name="locale"[^>]*content="([^"]*)"', "meta locale"),
        (r'<meta[^>]*name="currency"[^>]*content="([^"]*)"', "meta currency"),
        (r'<meta[^>]*property="og:locale"[^>]*content="([^"]*)"', "og:locale"),
    ]:
        m = re.search(pat, html, re.I)
        log.info("  %s: %r", label, m.group(1) if m else "NOT FOUND")

    # 1c. window.runParams / window.__INIT_DATA__ / window._dida_config
    for var_name in ["runParams", "__INIT_DATA__", "_dida_config", "__NEXT_DATA__", "pageConfig", "initData"]:
        pat = rf"window\.{var_name}\s*=\s*(.+?)(?:;\s*(?:window\.|</script>|var |const ))"
        m = re.search(pat, html, re.DOTALL)
        if m:
            raw = m.group(1).strip().rstrip(";").strip()
            log.info("  window.%s: found, %d chars, first 300: %s", var_name, len(raw), raw[:300])
            # Try to parse as JSON
            try:
                data = json.loads(raw)
                # Look for locale/currency keys
                flat = json.dumps(data)
                for key in ["locale", "currency", "language", "region", "lang"]:
                    km = re.findall(rf'"{key}"\s*:\s*"?([^",}}\s]+)', flat)
                    if km:
                        log.info("    %s → %s", key, list(set(km))[:5])
            except json.JSONDecodeError:
                log.info("    (not valid JSON, first 200: %s)", raw[:200])
        else:
            log.info("  window.%s: NOT FOUND", var_name)

    # 1d. Check for "glo2rus" or "gatewayAdapt" echo in HTML
    ga_m = re.search(r'gatewayAdapt["\s:=]+([a-z0-9_]+)', html, re.I)
    log.info("  gatewayAdapt in HTML: %r", ga_m.group(1) if ga_m else "NOT FOUND")

    # 1e. Check pdp_npi currencies across all cards
    pdp_currencies = set()
    for pm in re.finditer(r'pdp_npi=([^&"]+)', html):
        decoded = pm.group(1)
        parts = decoded.split("!")
        if len(parts) >= 4 and re.match(r'^[A-Z]{3}$', parts[1]):
            pdp_currencies.add(parts[1])
    log.info("  pdp_npi currencies (all cards): %s", pdp_currencies)

    # 1f. Check for Italian/Russian/English markers in titles
    card_idx = html.find('id="card-list"')
    if card_idx >= 0:
        sample = html[card_idx:card_idx+100000]
        it_markers = len(re.findall(r'\b(Cuffie|Auricolari|per|con|del|della|in|di|e|la|il)\b', sample, re.I))
        ru_markers = len(re.findall(r'\b(наушники|для|с |и |на |под|от|в |к |по |из)\b', sample, re.I))
        en_markers = len(re.findall(r'\b(headphone|earphone|for|with|the|and|in|of|to|a)\b', sample, re.I))
        log.info("  Language markers (first 100KB of cards): IT=%d, RU=%d, EN=%d", it_markers, ru_markers, en_markers)

    # 1g. Check <title> and first <h1>
    title_m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    if title_m:
        log.info("  <title>: %s", title_m.group(1).strip()[:120])

    # 1h. Check for "it.aliexpress" or redirect hints
    for marker in ["it.aliexpress", "ru.aliexpress", "www.aliexpress", "glo2rus", "glo2ita", "glo2usa"]:
        cnt = html.count(marker)
        if cnt > 0:
            log.info("  HTML contains '%s': %d times", marker, cnt)

    # 1i. Check response headers for locale hints
    for h in ["x-location", "x-locale", "set-cookie", "x-server-location"]:
        hv = resp.headers.get(h, "")
        if hv:
            log.info("  Header %s: %s", h, hv[:200])

    return resp


# ─── 2. RU Search API deep probe ───
def probe_search_api():
    log.info("── [2] RU Search API deep probe ──")
    from curl_cffi import requests as cffi_req
    query = "наушники"
    eq = quote(query, safe="")

    # 2a. GET with full params
    api_url = (f"https://ru.aliexpress.com/aer-webapi/v1/search"
               f"?SearchText={eq}&page=1&pageSize=40&sortType=default"
               f"&_lang=ru_RU&_currency=RUB&gatewayAdapt=glo2rus")
    hdrs = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={eq}",
        "Origin": "https://ru.aliexpress.com",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json",
    }
    cookies = {
        "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
        "aep_usuc": "f_region=RU&f_locale=ru-RU&f_currency=RUB",
    }
    log.info("  2a. GET %s", api_url[:100])
    resp = cffi_req.get(api_url, impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=cookies)
    body = resp.text.strip()
    is_json = body.startswith("{") or body.startswith("[")
    log.info("  GET: HTTP %d, %dB, JSON=%s", resp.status_code, len(body), is_json)
    if not is_json and body.startswith("<"):
        log.info("  GET body first 200: %s", body[:200])
        # Check if it's the SPA shell or an error page
        if "aer-webapi" in body or "search" in body.lower():
            log.info("  → Looks like SPA shell, not API response")
        # Check for redirect
        loc = resp.headers.get("location", "")
        if loc:
            log.info("  Location header: %s", loc)

    # 2b. POST with JSON body
    log.info("  2b. POST with JSON body")
    post_url = "https://ru.aliexpress.com/aer-webapi/v1/search"
    post_body = {
        "SearchText": query,
        "page": 1,
        "pageSize": 40,
        "sortType": "default",
        "_lang": "ru_RU",
        "_currency": "RUB",
    }
    hdrs_post = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={eq}",
        "Origin": "https://ru.aliexpress.com",
        "Content-Type": "application/json",
    }
    resp2 = cffi_req.post(post_url, impersonate="chrome", timeout=(15, 30),
                          headers=hdrs_post, cookies=cookies, json=post_body)
    body2 = resp2.text.strip()
    is_json2 = body2.startswith("{") or body2.startswith("[")
    log.info("  POST: HTTP %d, %dB, JSON=%s", resp2.status_code, len(body2), is_json2)
    if is_json2:
        try:
            data = json.loads(body2)
            log.info("  POST JSON keys: %s", list(data.keys())[:10])
            items = data.get("items") or data.get("products") or data.get("data", {}).get("items", [])
            log.info("  POST items count: %d", len(items) if isinstance(items, list) else "N/A")
        except json.JSONDecodeError:
            log.info("  POST JSON parse failed, first 200: %s", body2[:200])
    else:
        log.info("  POST body first 200: %s", body2[:200])

    # 2c. Try alternative API paths
    alt_paths = [
        "/aer-webapi/v2/search",
        "/aer-webapi/v1/search/items",
        "/api/search",
        "/wholesale/api/search",
    ]
    for ap in alt_paths:
        alt_url = f"https://ru.aliexpress.com{ap}?SearchText={eq}&page=1&pageSize=10&_lang=ru_RU&_currency=RUB"
        try:
            resp3 = cffi_req.get(alt_url, impersonate="chrome", timeout=(10, 20), headers=hdrs, cookies=cookies)
            b3 = resp3.text.strip()
            j3 = b3.startswith("{") or b3.startswith("[")
            log.info("  2c. GET %s → HTTP %d, %dB, JSON=%s", ap, resp3.status_code, len(b3), j3)
            if j3:
                log.info("      first 200: %s", b3[:200])
        except Exception as e:
            log.info("  2c. GET %s → ERROR: %s", ap, e)

    # 2d. Check if the SPA shell has embedded API config
    log.info("  2d. SPA shell analysis")
    if not is_json and body.startswith("<"):
        # Look for API endpoint references in the SPA
        api_refs = re.findall(r'["\'](/aer-webapi[^"\']*)["\']', body)
        if api_refs:
            log.info("    API refs in SPA: %s", list(set(api_refs))[:5])
        # Look for fetch/axios config
        fetch_refs = re.findall(r'(?:fetch|axios|ajax)\s*\(\s*[\"\'`](/[^\"\'`\s]+)', body)
        if fetch_refs:
            log.info("    Fetch refs: %s", list(set(fetch_refs))[:5])
        # Check script srcs
        scripts = re.findall(r'<script[^>]*src="([^"]+)"', body)
        log.info("    Scripts: %s", [s.split("/")[-1] for s in scripts][:5])


# ─── 3. Warm-up: homepage → cookies → search ───
def probe_warmup():
    log.info("── [3] Warm-up: homepage → search ──")
    from curl_cffi import requests as cffi_req
    session_cookies = {}

    # 3a. Fetch homepage to get session cookies
    log.info("  3a. Fetching ru.aliexpress.com homepage")
    resp_home = cffi_req.get(
        "https://ru.aliexpress.com/",
        impersonate="chrome",
        timeout=(15, 30),
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        },
        cookies={
            "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
            "aep_usuc": "f_region=RU&f_locale=ru-RU&f_currency=RUB",
        },
    )
    log.info("  Homepage: HTTP %d, %dB", resp_home.status_code, len(resp_home.text))

    # Collect Set-Cookie
    set_cookies = resp_home.headers.get("set-cookie", "")
    if set_cookies:
        log.info("  Set-Cookie (first 300): %s", set_cookies[:300])
        # Parse cookies
        for sc in re.findall(r'([^=]+)=([^;]*)', set_cookies):
            session_cookies[sc[0].strip()] = sc[1].strip()
    log.info("  Session cookies collected: %s", list(session_cookies.keys())[:10])

    # Check homepage locale
    lang_m = re.search(r'<html[^>]*lang="([^"]*)"', resp_home.text, re.I)
    log.info("  Homepage <html lang=%r>", lang_m.group(1) if lang_m else "NOT FOUND")
    title_m = re.search(r'<title>(.*?)</title>', resp_home.text, re.DOTALL)
    if title_m:
        log.info("  Homepage <title>: %s", title_m.group(1).strip()[:100])

    # 3b. Now fetch search with session cookies
    time.sleep(1)
    query = "наушники"
    eq = quote(query, safe="")
    search_url = (f"https://ru.aliexpress.com/wholesale?SearchText={eq}"
                  f"&page=1&_lang=ru_RU&_currency=RUB&gatewayAdapt=glo2rus")
    all_cookies = {**session_cookies,
                   "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
                   "aep_usuc": "f_region=RU&f_locale=ru-RU&f_currency=RUB"}
    resp_search = cffi_req.get(
        search_url, impersonate="chrome", timeout=(15, 30),
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://ru.aliexpress.com/",
            "Upgrade-Insecure-Requests": "1",
        },
        cookies=all_cookies,
    )
    html = resp_search.text
    log.info("  Search (warm): HTTP %d, %dB", resp_search.status_code, len(html))

    # Check locale in warm search
    lang_m2 = re.search(r'<html[^>]*lang="([^"]*)"', html, re.I)
    log.info("  Warm search <html lang=%r>", lang_m2.group(1) if lang_m2 else "NOT FOUND")

    # pdp_npi currencies
    pdp_currencies = set()
    for pm in re.finditer(r'pdp_npi=([^&"]+)', html):
        parts = pm.group(1).split("!")
        if len(parts) >= 4 and re.match(r'^[A-Z]{3}$', parts[1]):
            pdp_currencies.add(parts[1])
    log.info("  Warm search pdp_npi currencies: %s", pdp_currencies)

    # Title sample
    card_idx = html.find('id="card-list"')
    if card_idx >= 0:
        t_m = re.search(r'<h3[^>]*>(.*?)</h3>', html[card_idx:card_idx+50000], re.DOTALL)
        if t_m:
            t = re.sub(r'<[^>]+>', '', t_m.group(1)).strip()
            log.info("  First title (warm): %s", t[:100])

    # 3c. Try Search API with warm session
    log.info("  3c. Search API with warm session")
    api_url = (f"https://ru.aliexpress.com/aer-webapi/v1/search"
               f"?SearchText={eq}&page=1&pageSize=40&sortType=default"
               f"&_lang=ru_RU&_currency=RUB&gatewayAdapt=glo2rus")
    resp_api = cffi_req.get(
        api_url, impersonate="chrome", timeout=(15, 30),
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={eq}",
            "Origin": "https://ru.aliexpress.com",
            "X-Requested-With": "XMLHttpRequest",
        },
        cookies=all_cookies,
    )
    body = resp_api.text.strip()
    is_json = body.startswith("{") or body.startswith("[")
    log.info("  Warm API: HTTP %d, %dB, JSON=%s", resp_api.status_code, len(body), is_json)
    if is_json:
        try:
            data = json.loads(body)
            log.info("  Warm API keys: %s", list(data.keys())[:10])
        except json.JSONDecodeError:
            log.info("  Warm API parse fail: %s", body[:200])
    else:
        log.info("  Warm API body[:200]: %s", body[:200])


# ─── 4. COM comparison ───
def probe_com():
    log.info("── [4] COM comparison ──")
    from curl_cffi import requests as cffi_req
    query = "headphones"
    url = (f"https://www.aliexpress.com/wholesale?SearchText={quote(query)}"
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
    }
    resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=cookies)
    html = resp.text
    log.info("  COM: HTTP %d, %dB", resp.status_code, len(html))

    lang_m = re.search(r'<html[^>]*lang="([^"]*)"', html, re.I)
    log.info("  COM <html lang=%r>", lang_m.group(1) if lang_m else "NOT FOUND")

    pdp_currencies = set()
    for pm in re.finditer(r'pdp_npi=([^&"]+)', html):
        parts = pm.group(1).split("!")
        if len(parts) >= 4 and re.match(r'^[A-Z]{3}$', parts[1]):
            pdp_currencies.add(parts[1])
    log.info("  COM pdp_npi currencies: %s", pdp_currencies)

    card_idx = html.find('id="card-list"')
    if card_idx >= 0:
        t_m = re.search(r'<h3[^>]*>(.*?)</h3>', html[card_idx:card_idx+50000], re.DOTALL)
        if t_m:
            t = re.sub(r'<[^>]+>', '', t_m.group(1)).strip()
            log.info("  COM first title: %s", t[:100])

    # Check if COM also gets EUR (geo-override)
    ga_m = re.search(r'gatewayAdapt["\s:=]+([a-z0-9_]+)', html, re.I)
    log.info("  COM gatewayAdapt in HTML: %r", ga_m.group(1) if ga_m else "NOT FOUND")


if __name__ == "__main__":
    print(f"=== diag_v7j — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    try:
        probe_ru_wholesale()
    except Exception as e:
        log.exception("Section 1 error: %s", e)
    try:
        probe_search_api()
    except Exception as e:
        log.exception("Section 2 error: %s", e)
    try:
        probe_warmup()
    except Exception as e:
        log.exception("Section 3 error: %s", e)
    try:
        probe_com()
    except Exception as e:
        log.exception("Section 4 error: %s", e)
    print("=== DONE ===")
    log.info("diag_v7j complete")

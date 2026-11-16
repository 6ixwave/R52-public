"""Диагностика v45: новые стратегии для WB, Ozon, Avito.

WB:      batch API, v2/v1, mobile, CDN, HTML с sec-ch-ua
Ozon:    composer API v2, SSR, mobile, api.ozon.ru
Avito:   реальные ID, JSON-LD, mobile, web API, cooldown

Запуск: C:\Python315\python.exe diag_v45_all.py
Результат: diag_v45_results.json
Лог:     diag_v45_run.log
"""

import json
import logging
import re
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

LOG_FILE = PROJECT / "diag_v45_run.log"
RESULT_FILE = PROJECT / "diag_v45_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v45")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()

results = {
    "version": "v45",
    "timestamp": NOW,
    "platforms": {},
}

try:
    from curl_cffi import requests as cffi_req
    CFFI_OK = True
except ImportError:
    CFFI_OK = False
    log.warning("curl_cffi не найден")

import requests as std_req

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
UA_MOBILE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"

FULL_BROWSER_HEADERS = {
    "User-Agent": UA_CHROME,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

JSON_HEADERS = {
    "User-Agent": UA_CHROME,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

def safe_cffi_get(url, headers=None, timeout=15, impersonate="chrome"):
    """curl_cffi GET с impersonation."""
    try:
        h = headers or {}
        resp = cffi_req.get(url, headers=h, timeout=timeout, impersonate=impersonate)
        return resp
    except Exception as e:
        return None

def safe_std_get(url, headers=None, timeout=15):
    """Стандартный requests GET."""
    try:
        h = headers or {}
        resp = std_req.get(url, headers=h, timeout=timeout)
        return resp
    except Exception as e:
        return None

def extract_jsonld(html):
    """Достать все JSON-LD блоки."""
    blocks = []
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(m.group(1).strip())
            blocks.append(data)
        except json.JSONDecodeError:
            pass
    return blocks

def extract_og(html):
    """Open Graph мета."""
    og = {}
    for m in re.finditer(r'<meta[^>]*property=["\']og:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE):
        og[m.group(1)] = m.group(2)
    for m in re.finditer(r'<meta[^>]*content=["\']([^"\']*)["\'][^>]*property=["\']og:([^"\']+)["\']', html, re.IGNORECASE):
        og[m.group(2)] = m.group(1)
    return og

# ============================================================
# WILDBERRIES
# ============================================================
def diag_wb():
    log.info("=== WB (v45) ===")
    wb_results = {"strategies": []}
    nm_ids = ["199298871", "314337853"]

    # --- Стратегия 1: Card API v2 (batch) ---
    log.info("WB S1: card.wb.ru/cards/v2/detail (batch)")
    for nm in nm_ids:
        url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"
        t0 = time.time()
        resp = safe_cffi_get(url, headers=JSON_HEADERS, timeout=15, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "card_v2_batch", "nm": nm, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            entry["has_json"] = resp.status_code == 200 and b"data" in resp.content[:200]
            entry["snippet"] = resp.text[:300]
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    products = data.get("data", {}).get("products", [])
                    entry["products_count"] = len(products)
                    if products:
                        p = products[0]
                        entry["name"] = p.get("name", "")[:100]
                        entry["brand"] = p.get("brand", "")
                        entry["price"] = p.get("salePriceU", {}).get("value", 0) / 100
                        entry["price_full"] = p.get("priceU", {}).get("value", 0) / 100
                        entry["img"] = p.get("imgSizes", [{}])[0].get("big1", "")[:100] if p.get("imgSizes") else ""
                        entry["sku"] = p.get("id", "")
                        entry["options_count"] = len(p.get("options", []))
                except Exception as e:
                    entry["parse_err"] = str(e)[:100]
        else:
            entry["http"] = "ERR"
            entry["error"] = "cffi exception"
        wb_results["strategies"].append(entry)
        log.info(f"  WB v2 nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(2)

    # --- Стратегия 2: Card API v1 ---
    log.info("WB S2: card.wb.ru/cards/v1/detail")
    for nm in nm_ids[:1]:
        url = f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&nm={nm}"
        t0 = time.time()
        resp = safe_cffi_get(url, headers=JSON_HEADERS, timeout=15, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "card_v1", "nm": nm, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            entry["snippet"] = resp.text[:300]
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    products = data.get("data", {}).get("products", [])
                    entry["products_count"] = len(products)
                    if products:
                        p = products[0]
                        entry["name"] = p.get("name", "")[:100]
                        entry["price"] = p.get("salePriceU", {}).get("value", 0) / 100
                except Exception as e:
                    entry["parse_err"] = str(e)[:100]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  WB v1 nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(2)

    # --- Стратегия 3: HTML detail page с cffi ---
    log.info("WB S3: HTML detail.aspx (cffi chrome)")
    for nm in nm_ids[:1]:
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        t0 = time.time()
        resp = safe_cffi_get(url, headers=FULL_BROWSER_HEADERS, timeout=20, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "html_detail", "nm": nm, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            html = resp.text
            entry["has_wbaas"] = "wbaas" in html.lower() or "WBAAS" in html
            entry["has_challenge"] = "challenge" in html.lower() or "captcha" in html.lower()
            entry["has_jsonld"] = "application/ld+json" in html
            entry["has_state"] = "__STATE__" in html or "data-state" in html or "window.__" in html
            entry["has_og"] = "og:title" in html or "og:description" in html
            entry["title_tag"] = ""
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
            if tm:
                entry["title_tag"] = tm.group(1).strip()[:150]
            jsonld = extract_jsonld(html)
            if jsonld:
                entry["jsonld_count"] = len(jsonld)
                for j in jsonld[:2]:
                    if isinstance(j, dict):
                        entry["jsonld_type"] = j.get("@type", "")
                        entry["jsonld_name"] = str(j.get("name", ""))[:100]
                        entry["jsonld_price"] = str(j.get("offers", {}))[:200]
            og = extract_og(html)
            if og:
                entry["og_title"] = og.get("title", "")[:100]
                entry["og_desc"] = og.get("description", "")[:100]
                entry["og_image"] = og.get("image", "")[:100]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  WB HTML nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s) jsonld={entry.get('has_jsonld')}")
        time.sleep(3)

    # --- Стратегия 4: CDN basket (фото) ---
    log.info("WB S4: CDN wbbasket.ru (проверка доступности)")
    cdn_urls = [
        "https://basket-01.wbbasket.ru/vol0/part0/199298871/images/199298871-1.webp",
        "https://basket-02.wbbasket.ru/vol0/part0/199298871/images/199298871-1.webp",
        "https://wbx-content-v1.wbbasket.ru/vol0/part0/199298871/content/199298871.json",
    ]
    for u in cdn_urls:
        t0 = time.time()
        resp = safe_std_get(u, timeout=10)
        dt = time.time() - t0
        entry = {"strategy": "cdn", "url": u, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            entry["content_type"] = resp.headers.get("Content-Type", "")[:50]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  WB CDN: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(1)

    # --- Стратегия 5: web API v1 (новый путь) ---
    log.info("WB S5: www.wildberries.ru/web/api/v1/cards/detail")
    for nm in nm_ids[:1]:
        url = f"https://www.wildberries.ru/web/api/v1/cards/detail?nm={nm}&curr=rub&dest=-1257786"
        t0 = time.time()
        resp = safe_cffi_get(url, headers=JSON_HEADERS, timeout=15, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "web_api_v1", "nm": nm, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  WB web_api nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(2)

    # --- Стратегия 6: batch.wb.ru ---
    log.info("WB S6: batch.wb.ru")
    url = f"https://batch.wb.ru/batch/{nm_ids[0]}/info"
    t0 = time.time()
    resp = safe_cffi_get(url, headers=JSON_HEADERS, timeout=15, impersonate="chrome")
    dt = time.time() - t0
    entry = {"strategy": "batch_info", "nm": nm_ids[0], "url": url, "time_s": round(dt, 2)}
    if resp:
        entry["http"] = resp.status_code
        entry["bytes"] = len(resp.content)
        entry["snippet"] = resp.text[:300]
    else:
        entry["http"] = "ERR"
    wb_results["strategies"].append(entry)
    log.info(f"  WB batch: HTTP {entry.get('http')} ({dt:.1f}s)")

    results["platforms"]["WB"] = wb_results

# ============================================================
# OZON
# ============================================================
def diag_ozon():
    log.info("=== OZON (v45) ===")
    oz_results = {"strategies": []}
    oz_ids = ["1600562267", "1317678381"]

    # --- Стратегия 1: Composer API v2 ---
    log.info("OZON S1: composer-api.bx/page/json/v2")
    for oid in oz_ids:
        url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/"
        t0 = time.time()
        resp = safe_cffi_get(url, headers=JSON_HEADERS, timeout=20, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "composer_v2", "id": oid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    entry["top_keys"] = list(data.keys())[:10]
                    # Ищем состояние
                    state = data.get("state", {})
                    entry["state_keys"] = list(state.keys())[:10] if isinstance(state, dict) else []
                    widgets = data.get("widgets", [])
                    entry["widgets_count"] = len(widgets) if isinstance(widgets, list) else 0
                    # Ищем product info
                    for w in (widgets if isinstance(widgets, list) else []):
                        if isinstance(w, dict):
                            wid = w.get("widgetState", {}).get("id", "") if isinstance(w.get("widgetState"), dict) else ""
                            if "product" in str(wid).lower() or "title" in str(wid).lower():
                                entry["product_widget"] = str(w.get("widgetState", ""))[:300]
                                break
                    entry["snippet"] = json.dumps(data, ensure_ascii=False)[:500]
                except Exception as e:
                    entry["parse_err"] = str(e)[:100]
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        oz_results["strategies"].append(entry)
        log.info(f"  OZON composer nm={oid}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(5)

    # --- Стратегия 2: SSR HTML с cffi ---
    log.info("OZON S2: SSR HTML (cffi chrome, data-widget)")
    for oid in oz_ids[:1]:
        url = f"https://www.ozon.ru/product/{oid}/"
        t0 = time.time()
        resp = safe_cffi_get(url, headers=FULL_BROWSER_HEADERS, timeout=25, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "ssr_html", "id": oid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            html = resp.text
            entry["has_data_widget"] = "data-widget" in html
            entry["has_og"] = "og:title" in html
            entry["has_jsonld"] = "application/ld+json" in html
            entry["has_state"] = "__STATE__" in html or "window.__" in html
            entry["title_tag"] = ""
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
            if tm:
                entry["title_tag"] = tm.group(1).strip()[:150]
            og = extract_og(html)
            if og:
                entry["og_title"] = og.get("title", "")[:100]
                entry["og_desc"] = og.get("description", "")[:100]
                entry["og_image"] = og.get("image", "")[:100]
            jsonld = extract_jsonld(html)
            if jsonld:
                entry["jsonld_count"] = len(jsonld)
                for j in jsonld[:2]:
                    if isinstance(j, dict):
                        entry["jsonld_type"] = j.get("@type", "")
                        entry["jsonld_name"] = str(j.get("name", ""))[:100]
                        offers = j.get("offers")
                        if offers:
                            entry["jsonld_offers"] = str(offers)[:200]
            # data-widget парсинг
            widgets = re.findall(r'data-widget="([^"]+)"', html)
            entry["widget_names"] = widgets[:15]
        else:
            entry["http"] = "ERR"
        oz_results["strategies"].append(entry)
        log.info(f"  OZON SSR nm={oid}: HTTP {entry.get('http')} ({dt:.1f}s) widgets={entry.get('has_data_widget')}")
        time.sleep(5)

    # --- Стратегия 3: api.ozon.ru (публичный) ---
    log.info("OZON S3: api.ozon.ru")
    for oid in oz_ids[:1]:
        url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/"
        t0 = time.time()
        resp = safe_cffi_get(url, headers=JSON_HEADERS, timeout=15, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "api_ozon", "id": oid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        oz_results["strategies"].append(entry)
        log.info(f"  OZON api nm={oid}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(3)

    # --- Стратегия 4: Mobile ---
    log.info("OZON S4: Mobile (m.ozon.ru)")
    for oid in oz_ids[:1]:
        url = f"https://m.ozon.ru/product/{oid}/"
        t0 = time.time()
        resp = safe_cffi_get(url, headers={**FULL_BROWSER_HEADERS, "User-Agent": UA_MOBILE, "sec-ch-ua-mobile": "?1", "sec-ch-ua-platform": '"iOS"'}, timeout=20, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "mobile", "id": oid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            html = resp.text
            entry["has_og"] = "og:title" in html
            entry["has_jsonld"] = "application/ld+json" in html
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
            entry["title_tag"] = tm.group(1).strip()[:150] if tm else ""
        else:
            entry["http"] = "ERR"
        oz_results["strategies"].append(entry)
        log.info(f"  OZON mobile nm={oid}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(3)

    results["platforms"]["OZON"] = oz_results

# ============================================================
# AVITO
# ============================================================
def diag_avito():
    log.info("=== AVITO (v45) ===")
    av_results = {"strategies": []}

    # Реальные ID для теста (из diag_v31: 1154321987, 1198765432, 1234567890)
    # + новый поиск
    av_ids = ["1154321987", "1096783029"]

    # --- Стратегия 1: HTML ad page ---
    log.info("AVITO S1: HTML ad page (cffi chrome)")
    for aid in av_ids:
        url = f"https://www.avito.ru/ad/{aid}"
        t0 = time.time()
        resp = safe_cffi_get(url, headers=FULL_BROWSER_HEADERS, timeout=20, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "html_ad", "id": aid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            html = resp.text
            entry["has_captcha"] = "captcha" in html.lower() or "altcha" in html.lower()
            entry["has_ip_limit"] = "ограничен" in html.lower() or "ip-адрес" in html.lower()
            entry["has_jsonld"] = "application/ld+json" in html
            entry["has_state"] = "__PRELOADED_STATE__" in html or "data-state" in html
            entry["has_og"] = "og:title" in html
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
            entry["title_tag"] = tm.group(1).strip()[:150] if tm else ""
            og = extract_og(html)
            if og:
                entry["og_title"] = og.get("title", "")[:100]
                entry["og_desc"] = og.get("description", "")[:100]
                entry["og_image"] = og.get("image", "")[:100]
            jsonld = extract_jsonld(html)
            if jsonld:
                entry["jsonld_count"] = len(jsonld)
                for j in jsonld[:2]:
                    if isinstance(j, dict):
                        entry["jsonld_type"] = j.get("@type", "")
                        entry["jsonld_name"] = str(j.get("name", ""))[:100]
                        offers = j.get("offers")
                        if offers:
                            entry["jsonld_offers"] = str(offers)[:200]
            # PRELOADED_STATE
            ps_match = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
            if ps_match:
                try:
                    ps = json.loads(ps_match.group(1))
                    entry["preloaded_keys"] = list(ps.keys())[:10]
                except:
                    entry["preloaded_parse_err"] = "JSON parse fail"
        else:
            entry["http"] = "ERR"
        av_results["strategies"].append(entry)
        log.info(f"  AVITO ad={aid}: HTTP {entry.get('http')} ({dt:.1f}s) jsonld={entry.get('has_jsonld')}")
        time.sleep(5)

    # --- Стратегия 2: Web API items ---
    log.info("AVITO S2: web/1/js/items (поиск)")
    search_urls = [
        "https://www.avito.ru/web/1/js/items?path=transport&f=0&q=телефон&maxPrice=100000",
        "https://www.avito.ru/web/1/js/items?path=electronics&f=0&q=iphone&maxPrice=50000",
    ]
    for su in search_urls:
        t0 = time.time()
        resp = safe_cffi_get(su, headers=JSON_HEADERS, timeout=15, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "web_api_items", "url": su, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data.get("items", [])
                    entry["items_count"] = len(items)
                    if items:
                        first = items[0]
                        entry["first_id"] = str(first.get("id", ""))
                        entry["first_title"] = str(first.get("title", ""))[:100]
                        entry["first_price"] = str(first.get("price", ""))[:50]
                except Exception as e:
                    entry["parse_err"] = str(e)[:100]
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        av_results["strategies"].append(entry)
        log.info(f"  AVITO API: HTTP {entry.get('http')} items={entry.get('items_count','?')} ({dt:.1f}s)")
        time.sleep(5)

    # --- Стратегия 3: Mobile page ---
    log.info("AVITO S3: Mobile (m.avito.ru)")
    for aid in av_ids[:1]:
        url = f"https://m.avito.ru/ad/{aid}"
        t0 = time.time()
        resp = safe_cffi_get(url, headers={**FULL_BROWSER_HEADERS, "User-Agent": UA_MOBILE, "sec-ch-ua-mobile": "?1", "sec-ch-ua-platform": '"iOS"'}, timeout=20, impersonate="chrome")
        dt = time.time() - t0
        entry = {"strategy": "mobile_ad", "id": aid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            html = resp.text
            entry["has_og"] = "og:title" in html
            entry["has_jsonld"] = "application/ld+json" in html
            entry["has_captcha"] = "captcha" in html.lower()
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
            entry["title_tag"] = tm.group(1).strip()[:150] if tm else ""
        else:
            entry["http"] = "ERR"
        av_results["strategies"].append(entry)
        log.info(f"  AVITO mobile ad={aid}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(3)

    # --- Стратегия 4: Home page (проверка IP-лимита) ---
    log.info("AVITO S4: Home page (IP check)")
    url = "https://www.avito.ru/"
    t0 = time.time()
    resp = safe_cffi_get(url, headers=FULL_BROWSER_HEADERS, timeout=15, impersonate="chrome")
    dt = time.time() - t0
    entry = {"strategy": "home", "url": url, "time_s": round(dt, 2)}
    if resp:
        entry["http"] = resp.status_code
        entry["bytes"] = len(resp.content)
        html = resp.text
        entry["has_ip_limit"] = "ограничен" in html.lower() or "ip-адрес" in html.lower()
        entry["has_captcha"] = "captcha" in html.lower() or "altcha" in html.lower()
        tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        entry["title_tag"] = tm.group(1).strip()[:100] if tm else ""
    else:
        entry["http"] = "ERR"
    av_results["strategies"].append(entry)
    log.info(f"  AVITO home: HTTP {entry.get('http')} ip_limit={entry.get('has_ip_limit')} ({dt:.1f}s)")

    results["platforms"]["AVITO"] = av_results

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    t_start = time.time()
    log.info(f"=== diag_v45 START | cffi={CFFI_OK} ===")

    for diag_fn in [diag_wb, diag_ozon, diag_avito]:
        try:
            diag_fn()
        except Exception as e:
            log.error(f"Section {diag_fn.__name__} failed: {e}\n{traceback.format_exc()}")
            results["platforms"].setdefault(diag_fn.__name__, {"error": str(e)})

    results["total_time_s"] = round(time.time() - t_start, 1)
    log.info(f"=== diag_v45 DONE in {results['total_time_s']}s ===")

    RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Saved: {RESULT_FILE} ({RESULT_FILE.stat().st_size} B)")

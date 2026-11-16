"""Диагностика v46: уточнённые стратегии после v45.

WB:      CDN vol/part расчёт, wbx-content, batch comma, dest=0, static-basket
Ozon:    Composer + Referer, api.ozon.ru, x-o3-device-id, mobile UA
Avito:   Найти живые ID через категорию → fetch ads, cooldown 30s

Запуск: C:\Python315\python.exe diag_v46_all.py
Результат: diag_v46_results.json
Лог:     diag_v46_run.log
"""

import json
import logging
import re
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

LOG_FILE = PROJECT / "diag_v46_run.log"
RESULT_FILE = PROJECT / "diag_v46_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v46")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()

results = {
    "version": "v46",
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

def cffi_get(url, headers=None, timeout=15, impersonate="chrome"):
    try:
        return cffi_req.get(url, headers=headers or {}, timeout=timeout, impersonate=impersonate)
    except Exception as e:
        log.warning(f"cffi_get ERR {url[:60]}: {e}")
        return None

def std_get(url, headers=None, timeout=15):
    try:
        return std_req.get(url, headers=headers or {}, timeout=timeout)
    except Exception as e:
        log.warning(f"std_get ERR {url[:60]}: {e}")
        return None

def wb_cdn_urls(nm):
    """Правильный расчёт CDN-URL для WB."""
    nm_int = int(nm)
    vol = nm_int // 100000
    part = (nm_int // 1000) % 100
    urls = []
    for basket in ["01", "02", "03", "04", "05"]:
        urls.append(f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/{nm}-1.webp")
    # wbx-content
    urls.append(f"https://wbx-content-v1.wbbasket.ru/vol{vol}/part{part}/{nm}/content/{nm}.json")
    # static-basket (альт)
    urls.append(f"https://static-basket-01.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/{nm}-1.webp")
    return urls, vol, part

# ============================================================
# WILDBERRIES
# ============================================================
def diag_wb():
    log.info("=== WB (v46) ===")
    wb_results = {"strategies": []}
    nm_ids = ["199298871", "314337853"]

    # --- S1: CDN с правильным vol/part ---
    log.info("WB S1: CDN basket-XX (vol/part)")
    for nm in nm_ids:
        urls, vol, part = wb_cdn_urls(nm)
        log.info(f"  nm={nm} vol={vol} part={part}")
        for u in urls[:4]:  # 4 basket + wbx
            t0 = time.time()
            resp = std_get(u, timeout=10)
            dt = time.time() - t0
            entry = {"strategy": "cdn", "nm": nm, "url": u, "time_s": round(dt, 2)}
            if resp:
                entry["http"] = resp.status_code
                entry["bytes"] = len(resp.content)
                entry["ct"] = resp.headers.get("Content-Type", "")[:50]
                if resp.status_code == 200 and b"{" in resp.content[:10]:
                    try:
                        data = resp.json()
                        entry["json_keys"] = list(data.keys())[:10]
                        entry["snippet"] = json.dumps(data, ensure_ascii=False)[:300]
                    except:
                        pass
            else:
                entry["http"] = "ERR"
            wb_results["strategies"].append(entry)
            log.info(f"    {u.split('/')[2]}: HTTP {entry.get('http')} {entry.get('bytes',0)}B ({dt:.1f}s)")
            time.sleep(1)

    # --- S2: wbx-content JSON ---
    log.info("WB S2: wbx-content-v1 (product content)")
    for nm in nm_ids:
        urls, vol, part = wb_cdn_urls(nm)
        wbx_url = [u for u in urls if "wbx-content" in u][0]
        t0 = time.time()
        resp = std_get(wbx_url, timeout=10)
        dt = time.time() - t0
        entry = {"strategy": "wbx_content", "nm": nm, "url": wbx_url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    entry["keys"] = list(data.keys())[:15]
                    entry["snippet"] = json.dumps(data, ensure_ascii=False)[:500]
                except:
                    entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  wbx nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(2)

    # --- S3: Card API v2 с dest=0 и batch (comma) ---
    log.info("WB S3: card.wb.ru v2 (dest=0, batch comma)")
    # Single
    for nm in nm_ids:
        url = f"https://card.wb.ru/cards/v2/detail?curr=rub&dest=0&nm={nm}"
        t0 = time.time()
        resp = cffi_get(url, headers=JSON_HEADERS, timeout=15)
        dt = time.time() - t0
        entry = {"strategy": "card_v2_dest0", "nm": nm, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    prods = data.get("data", {}).get("products", [])
                    entry["count"] = len(prods)
                    if prods:
                        p = prods[0]
                        entry["name"] = p.get("name", "")[:100]
                        entry["price"] = p.get("salePriceU", {}).get("value", 0) / 100
                except:
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:200]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  v2 dest0 nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(2)

    # Batch (comma-separated)
    batch_nm = ",".join(nm_ids)
    url = f"https://card.wb.ru/cards/v2/detail?curr=rub&dest=0&nm={batch_nm}"
    t0 = time.time()
    resp = cffi_get(url, headers=JSON_HEADERS, timeout=15)
    dt = time.time() - t0
    entry = {"strategy": "card_v2_batch_comma", "nm": batch_nm, "url": url, "time_s": round(dt, 2)}
    if resp:
        entry["http"] = resp.status_code
        entry["bytes"] = len(resp.content)
        if resp.status_code == 200:
            try:
                data = resp.json()
                prods = data.get("data", {}).get("products", [])
                entry["count"] = len(prods)
                for p in prods[:2]:
                    entry.setdefault("products", []).append({
                        "id": p.get("id"), "name": p.get("name", "")[:80],
                        "price": p.get("salePriceU", {}).get("value", 0) / 100
                    })
            except:
                entry["snippet"] = resp.text[:300]
        else:
            entry["snippet"] = resp.text[:200]
    else:
        entry["http"] = "ERR"
    wb_results["strategies"].append(entry)
    log.info(f"  v2 batch: HTTP {entry.get('http')} ({dt:.1f}s)")
    time.sleep(2)

    # --- S4: HTML с Referer (имитация перехода) ---
    log.info("WB S4: HTML detail (Referer + cookies)")
    for nm in nm_ids[:1]:
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        hdrs = {**FULL_BROWSER_HEADERS, "Referer": "https://www.wildberries.ru/"}
        t0 = time.time()
        resp = cffi_get(url, headers=hdrs, timeout=20)
        dt = time.time() - t0
        entry = {"strategy": "html_referer", "nm": nm, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            html = resp.text
            entry["has_wbaas"] = "wbaas" in html.lower()
            entry["has_jsonld"] = "application/ld+json" in html
            entry["has_og"] = "og:title" in html
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
            entry["title"] = tm.group(1).strip()[:150] if tm else ""
            # JSON-LD
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    j = json.loads(m.group(1).strip())
                    entry["jsonld"] = str(j)[:400]
                    break
                except:
                    pass
            # OG
            og_t = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\']', html)
            if og_t:
                entry["og_title"] = og_t.group(1)[:100]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  HTML ref nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(3)

    # --- S5: session-based (2 запроса: home → detail) ---
    log.info("WB S5: Session (home → detail)")
    try:
        s = cffi_req.Session(impersonate="chrome")
        # Step 1: home
        r1 = s.get("https://www.wildberries.ru/", headers=FULL_BROWSER_HEADERS, timeout=15)
        log.info(f"  Home: HTTP {r1.status_code} ({len(r1.content)}B)")
        time.sleep(2)
        # Step 2: detail
        nm = nm_ids[0]
        r2 = s.get(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                   headers={**FULL_BROWSER_HEADERS, "Referer": "https://www.wildberries.ru/"}, timeout=20)
        entry = {"strategy": "session", "nm": nm, "home_http": r1.status_code,
                 "detail_http": r2.status_code, "bytes": len(r2.content)}
        html = r2.text
        entry["has_wbaas"] = "wbaas" in html.lower()
        entry["has_jsonld"] = "application/ld+json" in html
        entry["has_og"] = "og:title" in html
        tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        entry["title"] = tm.group(1).strip()[:150] if tm else ""
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                j = json.loads(m.group(1).strip())
                entry["jsonld"] = str(j)[:400]
                break
            except:
                pass
        wb_results["strategies"].append(entry)
        log.info(f"  Session detail: HTTP {r2.status_code} ({len(r2.content)}B) jsonld={entry['has_jsonld']}")
    except Exception as e:
        wb_results["strategies"].append({"strategy": "session", "error": str(e)[:200]})
        log.warning(f"  Session failed: {e}")

    results["platforms"]["WB"] = wb_results

# ============================================================
# OZON
# ============================================================
def diag_ozon():
    log.info("=== OZON (v46) ===")
    oz_results = {"strategies": []}
    oz_ids = ["1600562267", "1317678381"]
    device_id = str(uuid.uuid4())

    # --- S1: Composer с Referer + Origin + device-id ---
    log.info("OZON S1: composer + Referer + device-id")
    for oid in oz_ids:
        url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/"
        hdrs = {
            **JSON_HEADERS,
            "Referer": f"https://www.ozon.ru/product/{oid}/",
            "Origin": "https://www.ozon.ru",
            "x-o3-device-id": device_id,
            "x-o3-request-id": str(uuid.uuid4()),
        }
        t0 = time.time()
        resp = cffi_get(url, headers=hdrs, timeout=20)
        dt = time.time() - t0
        entry = {"strategy": "composer_ref", "id": oid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    entry["top_keys"] = list(data.keys())[:10]
                    widgets = data.get("widgets", [])
                    entry["widgets_count"] = len(widgets) if isinstance(widgets, list) else 0
                    # Ищем product
                    for w in (widgets if isinstance(widgets, list) else []):
                        ws = w.get("widgetState", {}) if isinstance(w, dict) else {}
                        if isinstance(ws, dict):
                            wid = ws.get("id", "")
                            if any(k in str(wid).lower() for k in ["product", "title", "price", "sku"]):
                                entry["product_widget"] = str(ws)[:400]
                                break
                    entry["snippet"] = json.dumps(data, ensure_ascii=False)[:500]
                except:
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        oz_results["strategies"].append(entry)
        log.info(f"  composer ref nm={oid}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(5)

    # --- S2: SSR с device-id + cookies ---
    log.info("OZON S2: SSR HTML (device-id + session)")
    try:
        s = cffi_req.Session(impersonate="chrome")
        # Home first
        r1 = s.get("https://www.ozon.ru/", headers=FULL_BROWSER_HEADERS, timeout=15)
        log.info(f"  Home: HTTP {r1.status_code}")
        time.sleep(3)
        # Product
        oid = oz_ids[0]
        hdrs = {**FULL_BROWSER_HEADERS, "Referer": "https://www.ozon.ru/",
                "x-o3-device-id": device_id}
        r2 = s.get(f"https://www.ozon.ru/product/{oid}/", headers=hdrs, timeout=25)
        entry = {"strategy": "ssr_session", "id": oid, "home_http": r1.status_code,
                 "http": r2.status_code, "bytes": len(r2.content)}
        html = r2.text
        entry["has_data_widget"] = "data-widget" in html
        entry["has_jsonld"] = "application/ld+json" in html
        entry["has_og"] = "og:title" in html
        entry["has_state"] = "__STATE__" in html or "window.__" in html
        tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        entry["title"] = tm.group(1).strip()[:150] if tm else ""
        # OG
        og_t = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\']', html)
        if og_t:
            entry["og_title"] = og_t.group(1)[:100]
        # JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                j = json.loads(m.group(1).strip())
                entry["jsonld"] = str(j)[:400]
                break
            except:
                pass
        # data-widget
        widgets = re.findall(r'data-widget="([^"]+)"', html)
        entry["widget_names"] = widgets[:15]
        oz_results["strategies"].append(entry)
        log.info(f"  SSR session nm={oid}: HTTP {r2.status_code} widgets={entry['has_data_widget']} ({len(r2.content)}B)")
    except Exception as e:
        oz_results["strategies"].append({"strategy": "ssr_session", "error": str(e)[:200]})
        log.warning(f"  SSR session failed: {e}")
    time.sleep(3)

    # --- S3: api.ozon.ru (отдельный subdomain) ---
    log.info("OZON S3: api.ozon.ru")
    for oid in oz_ids[:1]:
        url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/"
        hdrs = {**JSON_HEADERS, "Referer": f"https://www.ozon.ru/product/{oid}/",
                "Origin": "https://www.ozon.ru", "x-o3-device-id": device_id}
        t0 = time.time()
        resp = cffi_get(url, headers=hdrs, timeout=15)
        dt = time.time() - t0
        entry = {"strategy": "api_ozon", "id": oid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        oz_results["strategies"].append(entry)
        log.info(f"  api.ozon nm={oid}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(3)

    # --- S4: Mobile с session ---
    log.info("OZON S4: Mobile (m.ozon.ru session)")
    try:
        s = cffi_req.Session(impersonate="chrome")
        r1 = s.get("https://m.ozon.ru/", headers={**FULL_BROWSER_HEADERS, "User-Agent": UA_MOBILE, "sec-ch-ua-mobile": "?1"}, timeout=15)
        log.info(f"  Mobile home: HTTP {r1.status_code}")
        time.sleep(3)
        oid = oz_ids[0]
        r2 = s.get(f"https://m.ozon.ru/product/{oid}/",
                   headers={**FULL_BROWSER_HEADERS, "User-Agent": UA_MOBILE, "sec-ch-ua-mobile": "?1",
                            "Referer": "https://m.ozon.ru/"}, timeout=20)
        entry = {"strategy": "mobile_session", "id": oid, "home_http": r1.status_code,
                 "http": r2.status_code, "bytes": len(r2.content)}
        html = r2.text
        entry["has_og"] = "og:title" in html
        entry["has_jsonld"] = "application/ld+json" in html
        tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        entry["title"] = tm.group(1).strip()[:150] if tm else ""
        og_t = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\']', html)
        if og_t:
            entry["og_title"] = og_t.group(1)[:100]
        oz_results["strategies"].append(entry)
        log.info(f"  Mobile nm={oid}: HTTP {r2.status_code} ({len(r2.content)}B)")
    except Exception as e:
        oz_results["strategies"].append({"strategy": "mobile_session", "error": str(e)[:200]})

    results["platforms"]["OZON"] = oz_results

# ============================================================
# AVITO
# ============================================================
def diag_avito():
    log.info("=== AVITO (v46) ===")
    av_results = {"strategies": []}

    # --- S0: Найти живые ID через категорию ---
    log.info("AVITO S0: Найти живые ID (категория)")
    cat_urls = [
        "https://www.avito.ru/moskvа/transport/avto",
        "https://www.avito.ru/sankt-peterburg/electronics/telefony",
        "https://www.avito.ru/transport/avto",
    ]
    valid_ids = []
    for cu in cat_urls:
        t0 = time.time()
        resp = cffi_get(cu, headers=FULL_BROWSER_HEADERS, timeout=20)
        dt = time.time() - t0
        entry = {"strategy": "category_search", "url": cu, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            html = resp.text
            entry["has_ip_limit"] = "ограничен" in html.lower() or "ip-адрес" in html.lower()
            entry["has_captcha"] = "captcha" in html.lower() or "altcha" in html.lower()
            # Ищем ID объявлений
            ids_found = re.findall(r'/ad/(\d{9,12})', html)
            entry["ids_found"] = list(set(ids_found))[:10]
            valid_ids.extend(list(set(ids_found))[:5])
            # Title
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
            entry["title"] = tm.group(1).strip()[:100] if tm else ""
        else:
            entry["http"] = "ERR"
        av_results["strategies"].append(entry)
        log.info(f"  Cat {cu.split('/')[-1]}: HTTP {entry.get('http')} ids={len(entry.get('ids_found',[]))} ({dt:.1f}s)")
        time.sleep(5)

    if not valid_ids:
        # Fallback: попробуем web API поиск
        log.info("AVITO S0b: Web API поиск (fallback)")
        api_url = "https://www.avito.ru/web/1/js/items?path=electronics&f=0&q=iphone&maxPrice=100000"
        t0 = time.time()
        resp = cffi_get(api_url, headers={**JSON_HEADERS, "Referer": "https://www.avito.ru/"}, timeout=15)
        dt = time.time() - t0
        entry = {"strategy": "web_api_search", "url": api_url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data.get("items", [])
                    entry["items_count"] = len(items)
                    for it in items[:5]:
                        valid_ids.append(str(it.get("id", "")))
                        entry.setdefault("found", []).append({"id": str(it.get("id")), "title": str(it.get("title",""))[:60]})
                except:
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        av_results["strategies"].append(entry)
        log.info(f"  API search: HTTP {entry.get('http')} items={entry.get('items_count',0)}")
        time.sleep(5)

    valid_ids = valid_ids[:3]  # макс 3
    log.info(f"AVITO: valid_ids={valid_ids}")

    # --- S1: Fetch ads ---
    log.info("AVITO S1: Fetch ads")
    for aid in valid_ids:
        url = f"https://www.avito.ru/ad/{aid}"
        t0 = time.time()
        resp = cffi_get(url, headers={**FULL_BROWSER_HEADERS, "Referer": "https://www.avito.ru/"}, timeout=20)
        dt = time.time() - t0
        entry = {"strategy": "ad_page", "id": aid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            html = resp.text
            entry["has_captcha"] = "captcha" in html.lower() or "altcha" in html.lower()
            entry["has_ip_limit"] = "ограничен" in html.lower()
            entry["has_jsonld"] = "application/ld+json" in html
            entry["has_state"] = "__PRELOADED_STATE__" in html or "data-state" in html
            entry["has_og"] = "og:title" in html
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
            entry["title"] = tm.group(1).strip()[:150] if tm else ""
            # OG
            og_t = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\']', html)
            if og_t:
                entry["og_title"] = og_t.group(1)[:100]
            og_d = re.search(r'property=["\']og:description["\'][^>]*content=["\']([^"\']*)["\']', html)
            if og_d:
                entry["og_desc"] = og_d.group(1)[:100]
            og_i = re.search(r'property=["\']og:image["\'][^>]*content=["\']([^"\']*)["\']', html)
            if og_i:
                entry["og_image"] = og_i.group(1)[:100]
            # JSON-LD
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    j = json.loads(m.group(1).strip())
                    entry["jsonld"] = str(j)[:500]
                    break
                except:
                    pass
            # PRELOADED_STATE
            ps = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
            if ps:
                try:
                    p = json.loads(ps.group(1))
                    entry["preloaded_keys"] = list(p.keys())[:10]
                except:
                    entry["preloaded_parse"] = "fail"
        else:
            entry["http"] = "ERR"
        av_results["strategies"].append(entry)
        log.info(f"  ad={aid}: HTTP {entry.get('http')} jsonld={entry.get('has_jsonld')} ({dt:.1f}s)")
        time.sleep(5)

    # --- S2: Web API items (с cooldown) ---
    log.info("AVITO S2: Web API (cooldown 10s)")
    time.sleep(10)
    api_url = "https://www.avito.ru/web/1/js/items?path=transport&f=0&q=iphone&maxPrice=100000"
    t0 = time.time()
    resp = cffi_get(api_url, headers={**JSON_HEADERS, "Referer": "https://www.avito.ru/transport/"}, timeout=15)
    dt = time.time() - t0
    entry = {"strategy": "web_api", "url": api_url, "time_s": round(dt, 2)}
    if resp:
        entry["http"] = resp.status_code
        entry["bytes"] = len(resp.content)
        if resp.status_code == 200:
            try:
                data = resp.json()
                items = data.get("items", [])
                entry["items_count"] = len(items)
                for it in items[:3]:
                    entry.setdefault("items", []).append({
                        "id": str(it.get("id")), "title": str(it.get("title",""))[:60],
                        "price": str(it.get("price",""))[:30]
                    })
            except:
                entry["snippet"] = resp.text[:300]
        else:
            entry["snippet"] = resp.text[:300]
    else:
        entry["http"] = "ERR"
    av_results["strategies"].append(entry)
    log.info(f"  Web API: HTTP {entry.get('http')} items={entry.get('items_count',0)}")

    # --- S3: Home (IP check) ---
    log.info("AVITO S3: Home (IP check)")
    time.sleep(5)
    t0 = time.time()
    resp = cffi_get("https://www.avito.ru/", headers=FULL_BROWSER_HEADERS, timeout=15)
    dt = time.time() - t0
    entry = {"strategy": "home", "time_s": round(dt, 2)}
    if resp:
        entry["http"] = resp.status_code
        entry["bytes"] = len(resp.content)
        html = resp.text
        entry["has_ip_limit"] = "ограничен" in html.lower() or "ip-адрес" in html.lower()
        entry["has_captcha"] = "captcha" in html.lower() or "altcha" in html.lower()
        tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        entry["title"] = tm.group(1).strip()[:100] if tm else ""
    else:
        entry["http"] = "ERR"
    av_results["strategies"].append(entry)
    log.info(f"  Home: HTTP {entry.get('http')} ip_limit={entry.get('has_ip_limit')}")

    results["platforms"]["AVITO"] = av_results

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    t_start = time.time()
    log.info(f"=== diag_v46 START | cffi={CFFI_OK} ===")

    for diag_fn in [diag_wb, diag_ozon, diag_avito]:
        try:
            diag_fn()
        except Exception as e:
            log.error(f"Section {diag_fn.__name__} failed: {e}\n{traceback.format_exc()}")
            results["platforms"].setdefault(diag_fn.__name__, {"error": str(e)})

    results["total_time_s"] = round(time.time() - t_start, 1)
    log.info(f"=== diag_v46 DONE in {results['total_time_s']}s ===")

    RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Saved: {RESULT_FILE} ({RESULT_FILE.stat().st_size} B)")

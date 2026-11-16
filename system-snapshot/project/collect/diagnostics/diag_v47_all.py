"""Диагностика v47: точечные стратегии после v46.

WB:      CDN через cffi (DNS), static-basket, img.wbbasket, vol/part verify
Ozon:    api.ozon.ru verify=False, session+cookie, composer с Origin
Avito:   API (тот что дал 200), разные path/f, cooldown 15s, mobile API

Запуск: C:\\Python315\\python.exe diag_v47_all.py
Результат: diag_v47_results.json
Лог:     diag_v47_run.log
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

LOG_FILE = PROJECT / "diag_v47_run.log"
RESULT_FILE = PROJECT / "diag_v47_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v47")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()

results = {
    "version": "v47",
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

def cffi_get(url, headers=None, timeout=15, impersonate="chrome", verify=True):
    try:
        return cffi_req.get(url, headers=headers or {}, timeout=timeout, impersonate=impersonate, verify=verify)
    except Exception as e:
        log.warning(f"cffi_get ERR {url[:60]}: {e}")
        return None

def std_get(url, headers=None, timeout=15, verify=True):
    try:
        return std_req.get(url, headers=headers or {}, timeout=timeout, verify=verify)
    except Exception as e:
        log.warning(f"std_get ERR {url[:60]}: {e}")
        return None

# ============================================================
# WILDBERRIES
# ============================================================
def diag_wb():
    log.info("=== WB (v47) ===")
    wb_results = {"strategies": []}
    nm_ids = ["199298871", "314337853"]

    # --- S1: CDN через cffi (другой DNS-резолвер) ---
    log.info("WB S1: CDN через cffi (basket-01..05, static-basket)")
    for nm in nm_ids[:1]:
        nm_int = int(nm)
        vol = nm_int // 100000
        part = (nm_int // 1000) % 100
        log.info(f"  nm={nm} vol={vol} part={part}")

        cdn_urls = []
        for b in ["01", "02", "03", "04", "05"]:
            cdn_urls.append(f"https://basket-{b}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/{nm}-1.webp")
        cdn_urls.append(f"https://static-basket-01.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/{nm}-1.webp")
        cdn_urls.append(f"https://img.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/{nm}-1.webp")

        for u in cdn_urls:
            t0 = time.time()
            resp = cffi_get(u, timeout=10)
            dt = time.time() - t0
            entry = {"strategy": "cdn_cffi", "nm": nm, "url": u, "time_s": round(dt, 2)}
            if resp:
                entry["http"] = resp.status_code
                entry["bytes"] = len(resp.content)
                entry["ct"] = resp.headers.get("Content-Type", "")[:60]
            else:
                entry["http"] = "ERR"
            wb_results["strategies"].append(entry)
            host = u.split("/")[2]
            log.info(f"    {host}: HTTP {entry.get('http')} {entry.get('bytes',0)}B ({dt:.1f}s)")
            time.sleep(0.5)

    # --- S2: wbx-content через cffi ---
    log.info("WB S2: wbx-content-v1 через cffi")
    for nm in nm_ids:
        nm_int = int(nm)
        vol = nm_int // 100000
        part = (nm_int // 1000) % 100
        url = f"https://wbx-content-v1.wbbasket.ru/vol{vol}/part{part}/{nm}/content/{nm}.json"
        t0 = time.time()
        resp = cffi_get(url, timeout=12)
        dt = time.time() - t0
        entry = {"strategy": "wbx_cffi", "nm": nm, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    entry["keys"] = list(data.keys())[:15]
                    entry["snippet"] = json.dumps(data, ensure_ascii=False)[:400]
                except:
                    entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  wbx cffi nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(2)

    # --- S3: Card API v2 через cffi (dest=0, минимальные params) ---
    log.info("WB S3: card.wb.ru v2 (мин params)")
    for nm in nm_ids:
        url = f"https://card.wb.ru/cards/v2/detail?curr=rub&nm={nm}"
        t0 = time.time()
        resp = cffi_get(url, headers=JSON_HEADERS, timeout=15)
        dt = time.time() - t0
        entry = {"strategy": "card_v2_min", "nm": nm, "url": url, "time_s": round(dt, 2)}
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
                        entry["brand"] = p.get("brand", "")
                        entry["price_sale"] = p.get("salePriceU", {}).get("value", 0) / 100
                        entry["price_full"] = p.get("priceU", {}).get("value", 0) / 100
                        entry["img"] = str(p.get("imgSizes", [{}])[0].get("big1", ""))[:100] if p.get("imgSizes") else ""
                except:
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:200]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  v2 min nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(2)

    # --- S4: Card API v4 (был 403, пробуем с мин params) ---
    log.info("WB S4: card.wb.ru v4 (мин params)")
    for nm in nm_ids[:1]:
        url = f"https://card.wb.ru/cards/v4/detail?curr=rub&nm={nm}"
        t0 = time.time()
        resp = cffi_get(url, headers=JSON_HEADERS, timeout=15)
        dt = time.time() - t0
        entry = {"strategy": "card_v4_min", "nm": nm, "url": url, "time_s": round(dt, 2)}
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
                        entry["price_sale"] = p.get("salePriceU", {}).get("value", 0) / 100
                except:
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:200]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  v4 min nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(2)

    # --- S5: search.wb.ru (поиск — может быть меньше антибота) ---
    log.info("WB S5: search.wb.ru (поиск)")
    for q in ["телефон", "iphone"]:
        url = f"https://search.wb.ru/exactmatch/ru/common/v5/search?query={q}&resultset=catalog&sort=popular&spp=30&curr=rub"
        t0 = time.time()
        resp = cffi_get(url, headers=JSON_HEADERS, timeout=15)
        dt = time.time() - t0
        entry = {"strategy": "search_wb", "query": q, "url": url[:80], "time_s": round(dt, 2)}
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
                        entry["first_nm"] = p.get("id")
                        entry["first_name"] = p.get("name", "")[:80]
                        entry["first_price"] = p.get("salePriceU", {}).get("value", 0) / 100
                except:
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:200]
        else:
            entry["http"] = "ERR"
        wb_results["strategies"].append(entry)
        log.info(f"  search '{q}': HTTP {entry.get('http')} items={entry.get('count',0)} ({dt:.1f}s)")
        time.sleep(3)

    # --- S6: HTML через cffi (wbaas challenge — проверим body) ---
    log.info("WB S6: HTML detail (body analysis)")
    nm = nm_ids[0]
    url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
    t0 = time.time()
    resp = cffi_get(url, headers=FULL_BROWSER_HEADERS, timeout=20)
    dt = time.time() - t0
    entry = {"strategy": "html_body", "nm": nm, "url": url, "time_s": round(dt, 2)}
    if resp:
        entry["http"] = resp.status_code
        entry["bytes"] = len(resp.content)
        html = resp.text
        entry["body_len"] = len(html)
        # Сохраним body для анализа
        body_file = PROJECT / f"diag_v47_wb_html_{nm}.html"
        body_file.write_text(html, encoding="utf-8")
        entry["body_saved"] = str(body_file.name)
        entry["has_wbaas"] = "wbaas" in html.lower()
        entry["has_jsonld"] = "application/ld+json" in html
        entry["has_og"] = "og:title" in html
        entry["has_state"] = "__STATE__" in html or "data-state" in html
        tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        entry["title"] = tm.group(1).strip()[:150] if tm else ""
        # Ищем script src (wbaas challenge script)
        scripts = re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', html)
        entry["script_srcs"] = scripts[:5]
    else:
        entry["http"] = "ERR"
    wb_results["strategies"].append(entry)
    log.info(f"  HTML body nm={nm}: HTTP {entry.get('http')} ({dt:.1f}s) saved={entry.get('body_saved')}")

    results["platforms"]["WB"] = wb_results

# ============================================================
# OZON
# ============================================================
def diag_ozon():
    log.info("=== OZON (v47) ===")
    oz_results = {"strategies": []}
    oz_ids = ["1600562267", "1317678381"]
    device_id = str(uuid.uuid4())

    # --- S1: api.ozon.ru с verify=False (SSL issue) ---
    log.info("OZON S1: api.ozon.ru (verify=False)")
    for oid in oz_ids[:1]:
        url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/"
        hdrs = {
            **JSON_HEADERS,
            "Referer": f"https://www.ozon.ru/product/{oid}/",
            "Origin": "https://www.ozon.ru",
            "x-o3-device-id": device_id,
        }
        t0 = time.time()
        resp = cffi_get(url, headers=hdrs, timeout=15, verify=False)
        dt = time.time() - t0
        entry = {"strategy": "api_verify_off", "id": oid, "url": url, "time_s": round(dt, 2)}
        if resp:
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    entry["top_keys"] = list(data.keys())[:10]
                    entry["snippet"] = json.dumps(data, ensure_ascii=False)[:500]
                except:
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:300]
        else:
            entry["http"] = "ERR"
        oz_results["strategies"].append(entry)
        log.info(f"  api verify_off nm={oid}: HTTP {entry.get('http')} ({dt:.1f}s)")
        time.sleep(3)

    # --- S2: www.ozon.ru composer с session (home → product) ---
    log.info("OZON S2: Session (home → composer)")
    try:
        s = cffi_req.Session(impersonate="chrome")
        # Step 1: home (получить cookies)
        r1 = s.get("https://www.ozon.ru/", headers=FULL_BROWSER_HEADERS, timeout=15)
        log.info(f"  Home: HTTP {r1.status_code} ({len(r1.content)}B)")
        cookies = dict(s.cookies)
        log.info(f"  Cookies: {list(cookies.keys())[:10]}")
        time.sleep(3)

        # Step 2: composer API
        oid = oz_ids[0]
        url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/"
        hdrs = {
            **JSON_HEADERS,
            "Referer": f"https://www.ozon.ru/product/{oid}/",
            "Origin": "https://www.ozon.ru",
            "x-o3-device-id": device_id,
            "x-o3-request-id": str(uuid.uuid4()),
        }
        r2 = s.get(url, headers=hdrs, timeout=20)
        entry = {"strategy": "session_composer", "id": oid,
                 "home_http": r1.status_code, "http": r2.status_code,
                 "bytes": len(r2.content), "cookies": list(cookies.keys())[:10]}
        if r2.status_code == 200:
            try:
                data = r2.json()
                entry["top_keys"] = list(data.keys())[:10]
                widgets = data.get("widgets", [])
                entry["widgets_count"] = len(widgets) if isinstance(widgets, list) else 0
                entry["snippet"] = json.dumps(data, ensure_ascii=False)[:500]
            except:
                entry["snippet"] = r2.text[:300]
        else:
            entry["snippet"] = r2.text[:300]
        oz_results["strategies"].append(entry)
        log.info(f"  Session composer nm={oid}: HTTP {r2.status_code} ({len(r2.content)}B)")
    except Exception as e:
        oz_results["strategies"].append({"strategy": "session_composer", "error": str(e)[:200]})
        log.warning(f"  Session failed: {e}")
    time.sleep(3)

    # --- S3: SSR HTML (session, с cookies из home) ---
    log.info("OZON S3: SSR HTML (session)")
    try:
        s = cffi_req.Session(impersonate="chrome")
        r1 = s.get("https://www.ozon.ru/", headers=FULL_BROWSER_HEADERS, timeout=15)
        log.info(f"  Home: HTTP {r1.status_code}")
        time.sleep(3)
        oid = oz_ids[0]
        hdrs = {**FULL_BROWSER_HEADERS, "Referer": "https://www.ozon.ru/",
                "x-o3-device-id": device_id}
        r2 = s.get(f"https://www.ozon.ru/product/{oid}/", headers=hdrs, timeout=25)
        entry = {"strategy": "ssr_session", "id": oid,
                 "home_http": r1.status_code, "http": r2.status_code,
                 "bytes": len(r2.content)}
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
        # Сохраним HTML
        html_file = PROJECT / f"diag_v47_ozon_{oid}.html"
        html_file.write_text(html, encoding="utf-8")
        entry["html_saved"] = html_file.name
        oz_results["strategies"].append(entry)
        log.info(f"  SSR session nm={oid}: HTTP {r2.status_code} widgets={entry['has_data_widget']} ({len(r2.content)}B)")
    except Exception as e:
        oz_results["strategies"].append({"strategy": "ssr_session", "error": str(e)[:200]})
    time.sleep(3)

    # --- S4: Mobile (m.ozon.ru) session ---
    log.info("OZON S4: Mobile session")
    try:
        s = cffi_req.Session(impersonate="chrome")
        mob_hdrs = {**FULL_BROWSER_HEADERS, "User-Agent": UA_MOBILE, "sec-ch-ua-mobile": "?1", "sec-ch-ua-platform": '"iOS"'}
        r1 = s.get("https://m.ozon.ru/", headers=mob_hdrs, timeout=15)
        log.info(f"  Mobile home: HTTP {r1.status_code}")
        time.sleep(3)
        oid = oz_ids[0]
        r2 = s.get(f"https://m.ozon.ru/product/{oid}/",
                   headers={**mob_hdrs, "Referer": "https://m.ozon.ru/"}, timeout=20)
        entry = {"strategy": "mobile_session", "id": oid,
                 "home_http": r1.status_code, "http": r2.status_code,
                 "bytes": len(r2.content)}
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
    log.info("=== AVITO (v47) ===")
    av_results = {"strategies": []}

    # --- S1: Web API (тот что дал 200 в v46) — разные params ---
    log.info("AVITO S1: Web API items (разные params)")
    api_urls = [
        # Тот что дал 200
        "https://www.avito.ru/web/1/js/items?path=electronics&f=0&q=iphone&maxPrice=100000",
        # Без maxPrice
        "https://www.avito.ru/web/1/js/items?path=electronics&f=0&q=iphone",
        # Без q
        "https://www.avito.ru/web/1/js/items?path=electronics&f=0",
        # Транспорт
        "https://www.avito.ru/web/1/js/items?path=transport&f=0&q=автомобиль",
        # Недвижимость
        "https://www.avito.ru/web/1/js/items?path=realty&f=0&q=квартира",
    ]
    for au in api_urls:
        t0 = time.time()
        resp = cffi_get(au, headers={**JSON_HEADERS, "Referer": "https://www.avito.ru/"}, timeout=15)
        dt = time.time() - t0
        entry = {"strategy": "web_api", "url": au[:80], "time_s": round(dt, 2)}
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
                            "id": str(it.get("id", "")),
                            "title": str(it.get("title", ""))[:60],
                            "price": str(it.get("price", ""))[:30],
                        })
                except:
                    entry["snippet"] = resp.text[:300]
            else:
                entry["snippet"] = resp.text[:200]
        else:
            entry["http"] = "ERR"
        av_results["strategies"].append(entry)
        log.info(f"  API {au.split('path=')[1][:30] if 'path=' in au else '?'}: HTTP {entry.get('http')} items={entry.get('items_count',0)} ({dt:.1f}s)")
        time.sleep(8)  # cooldown

    # --- S2: Найти живые ID из API ---
    log.info("AVITO S2: Найти живые ID")
    valid_ids = []
    for s in av_results["strategies"]:
        if s.get("items"):
            for it in s["items"]:
                if it.get("id"):
                    valid_ids.append(it["id"])
    valid_ids = list(set(valid_ids))[:3]
    log.info(f"  valid_ids: {valid_ids}")

    # --- S3: Fetch ads (если нашли ID) ---
    if valid_ids:
        log.info("AVITO S3: Fetch ads")
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
                        pass
            else:
                entry["http"] = "ERR"
            av_results["strategies"].append(entry)
            log.info(f"  ad={aid}: HTTP {entry.get('http')} jsonld={entry.get('has_jsonld')} ({dt:.1f}s)")
            time.sleep(8)

    # --- S4: Home (IP check, cooldown 15s) ---
    log.info("AVITO S4: Home (IP check)")
    time.sleep(15)
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
    log.info(f"=== diag_v47 START | cffi={CFFI_OK} ===")

    for diag_fn in [diag_wb, diag_ozon, diag_avito]:
        try:
            diag_fn()
        except Exception as e:
            log.error(f"Section {diag_fn.__name__} failed: {e}\n{traceback.format_exc()}")
            results["platforms"].setdefault(diag_fn.__name__, {"error": str(e)})

    results["total_time_s"] = round(time.time() - t_start, 1)
    log.info(f"=== diag_v47 DONE in {results['total_time_s']}s ===")

    RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Saved: {RESULT_FILE} ({RESULT_FILE.stat().st_size} B)")

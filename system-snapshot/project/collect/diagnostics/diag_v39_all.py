#!/usr/bin/env python3
"""diag_v39_all.py — Быстрая диагностика 4 площадок через curl_cffi.

Изменения vs v38:
- curl_cffi с Chrome impersonation (лучший TLS-отпечаток)
- Throttle 2 с, 1 ретрай, backoff 5 с
- Явный FileHandler для лога (нет проблем с буферизацией stdout)
- Пер-платформ тайминги
- Сохранение HTML при успешном GET
"""

import json
import logging
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

# --- Лог в файл + stdout ---
LOG_FILE = PROJECT / "diag_v39_run.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v39")

# --- curl_cffi ---
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    log.warning("curl_cffi не найден, fallback на requests")

results: dict = {}

# --- Конфиг ---
READ_TIMEOUT = 12
THROTTLE_SEC = 2
MAX_RETRIES = 1
BACKOFF = 5

OZON_IDS = ["229225961", "1317678381"]
WB_IDS = ["1252522762", "1024173669"]
FARPOST_IDS = ["134004859", "132093530"]

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
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

_last_req = 0.0


def throttle():
    global _last_req
    wait = THROTTLE_SEC - (time.monotonic() - _last_req)
    if wait > 0:
        time.sleep(wait)
    _last_req = time.monotonic()


def cffi_get(url: str, impersonate: str = "chrome131"):
    """GET через curl_cffi с браузерным TLS-отпечатком."""
    throttle()
    for attempt in range(MAX_RETRIES + 1):
        try:
            t0 = time.monotonic()
            resp = cffi_requests.get(
                url,
                headers=HEADERS,
                timeout=READ_TIMEOUT,
                impersonate=impersonate,
            )
            latency = time.monotonic() - t0
            log.info("GET %s -> %d (%.1fs) [a%d]", url, resp.status_code, latency, attempt + 1)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 429, 503) and attempt < MAX_RETRIES:
                time.sleep(BACKOFF)
                continue
            return resp
        except Exception as e:
            log.warning("GET %s error: %s [a%d]", url, e, attempt + 1)
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF)
                continue
            return None
    return None


def summarize_media(items):
    s = {"images": 0, "videos": 0, "other": 0}
    for item in items:
        if not isinstance(item, dict):
            s["other"] += 1
            continue
        t = item.get("type", item.get("kind", ""))
        u = item.get("url", item.get("src", item.get("source_url", "")))
        if t == "video" or re.search(r"\.(mp4|webm|ogg)", u, re.I):
            s["videos"] += 1
        elif t in ("image", "photo") or re.search(r"\.(jpg|jpeg|png|webp|avif)", u, re.I):
            s["images"] += 1
        else:
            s["other"] += 1
    return s


# ============================================================
# OZON
# ============================================================
def test_ozon():
    from r52.collectors.ozon import OzonCollector
    log.info("=== OZON (curl_cffi) ===")
    col = OzonCollector(use_proxies=False)
    results["OZON"] = {}

    for oid in OZON_IDS:
        url = col.build_url(oid)
        log.info("OZON %s: %s", oid, url)
        t0 = time.monotonic()
        resp = cffi_get(url)
        el = time.monotonic() - t0

        if resp is None:
            results["OZON"][oid] = {"status": "FETCH_NONE", "url": url, "elapsed": round(el, 1)}
            continue

        html = resp.text
        results["OZON"][oid] = {
            "status": "FETCHED" if resp.status_code == 200 else f"HTTP_{resp.status_code}",
            "http_code": resp.status_code,
            "html_bytes": len(html),
            "elapsed": round(el, 1),
        }
        if resp.status_code == 200:
            hp = PROJECT / f"v39_OZON_{oid}.html"
            hp.write_text(html, encoding="utf-8")
            log.info("OZON %s: %d B -> %s", oid, len(html), hp.name)
            try:
                obs = col.parse_page(resp, oid, url, "")
                if obs:
                    f = {k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100]) for k, v in obs.get("fields", {}).items()}
                    p = obs.get("price_candidates", [])
                    m = obs.get("media", {}).get("items", [])
                    results["OZON"][oid].update({
                        "status": "PARSED", "fields": f, "prices": p[:6],
                        "media_count": len(m), "media_types": summarize_media(m),
                        "availability": obs.get("availability", "UNKNOWN"),
                    })
                    log.info("OZON %s: PARSED title=%s prices=%d media=%d", oid, f.get("title", "?")[:50], len(p), len(m))
                else:
                    results["OZON"][oid]["status"] = "PARSE_NONE"
            except Exception as e:
                results["OZON"][oid]["status"] = f"PARSE_ERROR: {e}"
                log.exception("OZON %s parse err", oid)


# ============================================================
# WILDBERRIES
# ============================================================
def test_wb():
    from r52.collectors.wb import WBCollector
    log.info("=== WB (curl_cffi) ===")
    col = WBCollector(use_proxies=False)
    results["WB"] = {}

    for wid in WB_IDS:
        # A: API
        api_url = f"https://card.wb.ru/cards/v2/detail?nm={wid}"
        log.info("WB API %s: %s", wid, api_url)
        t0 = time.monotonic()
        resp = cffi_get(api_url)
        el = time.monotonic() - t0
        if resp is None:
            results["WB"][f"api_{wid}"] = {"status": "FETCH_NONE", "url": api_url, "elapsed": round(el, 1)}
        else:
            results["WB"][f"api_{wid}"] = {"http_code": resp.status_code, "bytes": len(resp.text), "elapsed": round(el, 1)}
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    prods = data.get("data", {}).get("products", [])
                    results["WB"][f"api_{wid}"]["status"] = "API_OK" if prods else "API_EMPTY"
                    results["WB"][f"api_{wid}"]["product_count"] = len(prods)
                    if prods:
                        p = prods[0]
                        results["WB"][f"api_{wid}"].update({
                            "name": p.get("name", "")[:100], "brand": p.get("brand", ""),
                            "price_basic": p.get("price", {}).get("basic"),
                            "price_total": p.get("price", {}).get("total"),
                            "images": len(p.get("im", [])), "rating": p.get("rating"),
                            "reviews": p.get("feedbacks"),
                            "description": (p.get("description") or "")[:200],
                        })
                        log.info("WB API %s: OK name=%s price=%s", wid, p.get("name", "?")[:50], p.get("price", {}).get("basic"))
                except (json.JSONDecodeError, ValueError):
                    results["WB"][f"api_{wid}"]["status"] = "API_NOT_JSON"
                    results["WB"][f"api_{wid}"]["snippet"] = resp.text[:300]
            else:
                results["WB"][f"api_{wid}"]["status"] = f"HTTP_{resp.status_code}"
                results["WB"][f"api_{wid}"]["snippet"] = resp.text[:300]
        time.sleep(THROTTLE_SEC)

        # B: HTML
        detail_url = col.build_url(wid)
        log.info("WB HTML %s: %s", wid, detail_url)
        t0 = time.monotonic()
        resp = cffi_get(detail_url)
        el = time.monotonic() - t0
        if resp is None:
            results["WB"][f"html_{wid}"] = {"status": "FETCH_NONE", "url": detail_url, "elapsed": round(el, 1)}
        else:
            html = resp.text
            ch = "wbaas" in html.lower() or "browser-check" in html.lower() or "antibot" in html.lower()
            jl = "application/ld+json" in html
            results["WB"][f"html_{wid}"] = {"http_code": resp.status_code, "bytes": len(html), "is_challenge": ch, "has_jsonld": jl, "elapsed": round(el, 1)}
            if resp.status_code == 200:
                results["WB"][f"html_{wid}"]["status"] = "CHALLENGE" if ch else ("CONTENT" if len(html) > 5000 else "THIN")
                if not ch and len(html) > 5000:
                    hp = PROJECT / f"v39_WB_{wid}.html"
                    hp.write_text(html, encoding="utf-8")
                    log.info("WB HTML %s: %d B -> %s", wid, len(html), hp.name)
                    try:
                        obs = col.parse_page(resp, wid, detail_url, "")
                        if obs:
                            results["WB"][f"html_{wid}"]["parsed"] = True
                            results["WB"][f"html_{wid}"]["fields"] = {k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100]) for k, v in obs.get("fields", {}).items()}
                            results["WB"][f"html_{wid}"]["prices"] = obs.get("price_candidates", [])[:4]
                            results["WB"][f"html_{wid}"]["media_count"] = len(obs.get("media", {}).get("items", []))
                        else:
                            results["WB"][f"html_{wid}"]["parsed"] = False
                    except Exception as e:
                        results["WB"][f"html_{wid}"]["parse_error"] = str(e)
            else:
                results["WB"][f"html_{wid}"]["status"] = f"HTTP_{resp.status_code}"


# ============================================================
# AVITO
# ============================================================
def test_avito():
    from r52.collectors.avito import AvitoCollector
    log.info("=== AVITO (curl_cffi) ===")
    col = AvitoCollector(use_proxies=False)
    results["AVITO"] = {}

    cat_urls = [
        "https://www.avito.ru/moskva/tech/communication/cellphones",
        "https://www.avito.ru/moskva/tech/notebooks",
    ]
    ids_found = []
    for cat_url in cat_urls:
        if len(ids_found) >= 2:
            break
        log.info("AVITO disc: %s", cat_url)
        t0 = time.monotonic()
        resp = cffi_get(cat_url)
        el = time.monotonic() - t0
        if resp is None:
            results["AVITO"][f"disc_{cat_url.split('/')[-1]}"] = {"status": "FETCH_NONE", "elapsed": round(el, 1)}
            continue
        html = resp.text
        key = f"disc_{cat_url.split('/')[-1]}"
        results["AVITO"][key] = {"http_code": resp.status_code, "bytes": len(html), "elapsed": round(el, 1)}
        if resp.status_code == 200:
            id_re = re.compile(r'(?:/ad/|data-ad-id=["\']?)(\d{9,12})')
            ids_found.extend(dict.fromkeys(id_re.findall(html)))
            bl = "Доступ ограничен" in html or "проверка безопасности" in html
            results["AVITO"][key]["status"] = "BLOCKED" if bl else "OK"
            results["AVITO"][key]["ids_found"] = ids_found[-5:]
            log.info("AVITO disc %s: %d B, IDs=%s", cat_url.split("/")[-1], len(html), ids_found[-3:])
            if not bl:
                hp = PROJECT / f"v39_AVITO_disc_{cat_url.split('/')[-1]}.html"
                hp.write_text(html, encoding="utf-8")
        else:
            results["AVITO"][key]["status"] = f"HTTP_{resp.status_code}"

    test_ids = ids_found[:2] if ids_found else []
    if not test_ids:
        results["AVITO"]["note"] = "No real IDs discovered"
        log.warning("AVITO: no IDs, skip card parse")
        return

    for aid in test_ids:
        url = col.build_url(aid)
        log.info("AVITO %s: %s", aid, url)
        t0 = time.monotonic()
        resp = cffi_get(url)
        el = time.monotonic() - t0
        if resp is None:
            results["AVITO"][aid] = {"status": "FETCH_NONE", "url": url, "elapsed": round(el, 1)}
            continue
        html = resp.text
        bl = "Доступ ограничен" in html or "проверка безопасности" in html
        jl = "application/ld+json" in html
        results["AVITO"][aid] = {"http_code": resp.status_code, "bytes": len(html), "is_blocked": bl, "has_jsonld": jl, "elapsed": round(el, 1)}
        if resp.status_code == 200:
            results["AVITO"][aid]["status"] = "BLOCKED" if bl else "CONTENT"
            if not bl:
                hp = PROJECT / f"v39_AVITO_{aid}.html"
                hp.write_text(html, encoding="utf-8")
                log.info("AVITO %s: %d B -> %s", aid, len(html), hp.name)
                try:
                    obs = col.parse_page(resp, aid, url, "")
                    if obs:
                        results["AVITO"][aid]["parsed"] = True
                        results["AVITO"][aid]["fields"] = {k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100]) for k, v in obs.get("fields", {}).items()}
                        results["AVITO"][aid]["prices"] = obs.get("price_candidates", [])[:4]
                        results["AVITO"][aid]["media_count"] = len(obs.get("media", {}).get("items", []))
                    else:
                        results["AVITO"][aid]["parsed"] = False
                except Exception as e:
                    results["AVITO"][aid]["parse_error"] = str(e)


# ============================================================
# FARPOST
# ============================================================
def test_farpost():
    from r52.collectors.farpost import FarpostCollector
    log.info("=== FARPOST (curl_cffi) ===")
    col = FarpostCollector(use_proxies=False)
    results["FARPOST"] = {}

    for fid in FARPOST_IDS:
        url = col.build_url(fid)
        log.info("FARPOST %s: %s", fid, url)
        t0 = time.monotonic()
        resp = cffi_get(url)
        el = time.monotonic() - t0
        if resp is None:
            results["FARPOST"][fid] = {"status": "FETCH_NONE", "url": url, "elapsed": round(el, 1)}
            continue
        html = resp.text
        results["FARPOST"][fid] = {"http_code": resp.status_code, "bytes": len(html), "elapsed": round(el, 1)}
        if resp.status_code == 200:
            results["FARPOST"][fid]["status"] = "FETCHED"
            hp = PROJECT / f"v39_FARPOST_{fid}.html"
            hp.write_text(html, encoding="utf-8")
            log.info("FARPOST %s: %d B -> %s", fid, len(html), hp.name)
            try:
                obs = col.parse_page(resp, fid, url, "")
                if obs:
                    results["FARPOST"][fid]["parsed"] = True
                    results["FARPOST"][fid]["fields"] = {k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100]) for k, v in obs.get("fields", {}).items()}
                    results["FARPOST"][fid]["prices"] = obs.get("price_candidates", [])[:4]
                    m = obs.get("media", {}).get("items", [])
                    results["FARPOST"][fid]["media_count"] = len(m)
                    results["FARPOST"][fid]["media_types"] = summarize_media(m)
                    results["FARPOST"][fid]["availability"] = obs.get("availability", "UNKNOWN")
                else:
                    results["FARPOST"][fid]["parsed"] = False
            except Exception as e:
                results["FARPOST"][fid]["parse_error"] = str(e)
        else:
            results["FARPOST"][fid]["status"] = f"HTTP_{resp.status_code}"
            results["FARPOST"][fid]["snippet"] = html[:300]


# ============================================================
# MAIN
# ============================================================
def main():
    t0 = time.monotonic()
    log.info("=== DIAG V39: curl_cffi, 4 platforms ===")
    log.info("curl_cffi: %s", HAS_CFFI)
    if not HAS_CFFI:
        log.error("curl_cffi missing — abort")
        return 1
    log.info("Ozon: %s | WB: %s | Farpost: %s | Avito: discovery", OZON_IDS, WB_IDS, FARPOST_IDS)

    for name, fn in [("OZON", test_ozon), ("WB", test_wb), ("AVITO", test_avito), ("FARPOST", test_farpost)]:
        pt = time.monotonic()
        try:
            fn()
        except Exception as e:
            log.exception("%s: error: %s", name, e)
            results.setdefault(name, {})["_error"] = str(e)
        log.info("%s done in %.1fs", name, time.monotonic() - pt)

    el = time.monotonic() - t0
    results["_meta"] = {
        "version": "v39", "elapsed_sec": round(el, 1),
        "timestamp": datetime.now(timezone(timedelta(hours=10))).isoformat(),
        "engine": "curl_cffi", "ids": {"OZON": OZON_IDS, "WB": WB_IDS, "FARPOST": FARPOST_IDS},
    }
    out = PROJECT / "diag_v39_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("=== V39 done %.1fs -> %s (%d B) ===", el, out.name, out.stat().st_size)

    log.info("--- SUMMARY ---")
    for plat in ("OZON", "WB", "AVITO", "FARPOST"):
        data = results.get(plat, {})
        st = {k: v.get("status", "?") if isinstance(v, dict) else "?" for k, v in data.items()}
        log.info("%s: %s", plat, json.dumps(st, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

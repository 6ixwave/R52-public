"""diag19 — точечная диагностика 3 проблемных площадок.

1. WB: дамп card.json (все ключи, типы, значения) — найти цену
2. Ozon: requests (не cffi) + mobile URL + другой товар
3. Avito: curl_cffi impersonate=chrome + конкретное объявление
"""
import sys, os, json, time, logging, traceback
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("diag19")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()
out = {"ts": NOW, "wb": {}, "ozon": {}, "avito": {}}

# ============================================================
# 1. WB: dump card.json
# ============================================================
log.info("=" * 60)
log.info("WB: dump card.json for 1097975275")
log.info("=" * 60)
try:
    from curl_cffi import requests as cffi_req
    nm_id = 1097975275
    vol = nm_id // 100000
    part = nm_id // 1000
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    # Используем известный хост из кэша (basket-42)
    host = "basket-42.wbbasket.ru"
    url = f"https://{host}{path}"
    log.info("WB: GET %s", url)
    resp = cffi_req.get(url, impersonate="chrome", timeout=15, headers={
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.wildberries.ru/",
    })
    log.info("WB: HTTP %d, %dB", resp.status_code, len(resp.content))
    if resp.status_code == 200:
        data = resp.json()
        log.info("WB: %d keys: %s", len(data), list(data.keys()))
        # Дамп каждого ключа: тип + значение (обрезанное)
        dump = {}
        for k, v in data.items():
            if isinstance(v, str):
                dump[k] = {"type": "str", "len": len(v), "val": v[:200]}
            elif isinstance(v, (int, float)):
                dump[k] = {"type": type(v).__name__, "val": v}
            elif isinstance(v, bool):
                dump[k] = {"type": "bool", "val": v}
            elif isinstance(v, list):
                dump[k] = {"type": "list", "len": len(v), "first": str(v[0])[:300] if v else None}
            elif isinstance(v, dict):
                dump[k] = {"type": "dict", "keys": list(v.keys())[:30], "sample": str(v)[:400]}
            else:
                dump[k] = {"type": type(v).__name__, "val": str(v)[:200]}
        out["wb"]["card_json_dump"] = dump
        # Ищем все числовые поля > 100 (потенциальные цены)
        numeric_fields = []
        def find_numeric(obj, prefix=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    find_numeric(v, f"{prefix}.{k}" if prefix else k)
            elif isinstance(obj, list):
                for i, v in enumerate(obj[:5]):
                    find_numeric(v, f"{prefix}[{i}]")
            elif isinstance(obj, (int, float)) and not isinstance(obj, bool) and obj > 100:
                numeric_fields.append({"path": prefix, "val": obj})
        find_numeric(data)
        out["wb"]["numeric_fields_gt_100"] = numeric_fields
        log.info("WB: numeric fields > 100: %s", json.dumps(numeric_fields[:20], ensure_ascii=False))
    else:
        out["wb"]["error"] = f"HTTP {resp.status_code}"
        out["wb"]["body"] = resp.text[:500]
except Exception as e:
    log.exception("WB dump error: %s", e)
    out["wb"]["error"] = str(e)

# ============================================================
# 2. OZON: requests + mobile + different product
# ============================================================
log.info("=" * 60)
log.info("OZON: multi-strategy")
log.info("=" * 60)
try:
    import requests as req
    session = req.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })

    # Стратегия A: desktop SSR (requests, не cffi)
    urls = [
        ("desktop", "https://www.ozon.ru/product/2067947177/"),
        ("mobile", "https://m.ozon.ru/product/2067947177/"),
        ("desktop2", "https://www.ozon.ru/product/2067947177"),
    ]
    for label, u in urls:
        try:
            log.info("OZON [%s]: GET %s", label, u)
            r = session.get(u, timeout=(5, 20), allow_redirects=True)
            log.info("OZON [%s]: HTTP %d, len=%d, final_url=%s", label, r.status_code, len(r.text), r.url[:80])
            out["ozon"][label] = {"status": r.status_code, "len": len(r.text), "url": r.url}
            if r.status_code == 200 and len(r.text) > 1000:
                # Быстрый парсинг
                import re
                title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', r.text, re.I)
                price_m = re.search(r'"price"\s*:\s*"?(\d+)', r.text)
                widget_m = re.search(r'data-widget=["\']([^"\']+)', r.text)
                out["ozon"][label]["og_title"] = title_m.group(1)[:100] if title_m else None
                out["ozon"][label]["price_raw"] = price_m.group(1) if price_m else None
                out["ozon"][label]["widget"] = widget_m.group(1)[:80] if widget_m else None
                log.info("OZON [%s]: title=%s price=%s widget=%s",
                         label,
                         out["ozon"][label].get("og_title", "?")[:50],
                         out["ozon"][label].get("price_raw"),
                         out["ozon"][label].get("widget", "?")[:40])
                if title_m or price_m:
                    break
            elif r.status_code in (403, 429):
                log.info("OZON [%s]: blocked", label)
            time.sleep(2)
        except Exception as e2:
            log.warning("OZON [%s] error: %s", label, e2)
            out["ozon"][label] = {"error": str(e2)}

    # Стратегия B: composer API через requests
    try:
        log.info("OZON: composer via requests")
        comp_url = "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/2067947177/"
        r = session.get(comp_url, timeout=(5, 15), headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.ozon.ru/",
        })
        log.info("OZON: composer HTTP %d, len=%d", r.status_code, len(r.text))
        out["ozon"]["composer_req"] = {"status": r.status_code, "len": len(r.text)}
        if r.status_code == 200:
            d = r.json()
            out["ozon"]["composer_req"]["keys"] = list(d.keys())[:20]
            out["ozon"]["composer_req"]["sample"] = json.dumps(d, ensure_ascii=False)[:500]
    except Exception as e2:
        log.warning("OZON composer error: %s", e2)
        out["ozon"]["composer_req"] = {"error": str(e2)}

    # Стратегия C: другой товар (популярный, может быть менее защищён)
    try:
        log.info("OZON: try product 113692656 (popular item)")
        r = session.get("https://www.ozon.ru/product/113692656/", timeout=(5, 20))
        log.info("OZON: alt product HTTP %d, len=%d", r.status_code, len(r.text))
        out["ozon"]["alt_product"] = {"status": r.status_code, "len": len(r.text)}
        if r.status_code == 200 and len(r.text) > 1000:
            import re
            title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', r.text, re.I)
            out["ozon"]["alt_product"]["og_title"] = title_m.group(1)[:100] if title_m else None
    except Exception as e2:
        out["ozon"]["alt_product"] = {"error": str(e2)}

except Exception as e:
    log.exception("OZON fatal: %s", e)
    out["ozon"]["fatal"] = str(e)

# ============================================================
# 3. AVITO: curl_cffi + known ad
# ============================================================
log.info("=" * 60)
log.info("AVITO: curl_cffi + known ads")
log.info("=" * 60)
try:
    from curl_cffi import requests as cffi_req

    # Стратегия A: curl_cffi на category page
    cat_urls = [
        "https://www.avito.ru/moskva/smartfony-i-telefony",
        "https://www.avito.ru/spb/smartfony-i-telefony",
        "https://www.avito.ru/moskva/noutbuki-i-ulegkie-pc",
    ]
    for u in cat_urls:
        try:
            log.info("AVITO cffi: GET %s", u)
            r = cffi_req.get(u, impersonate="chrome", timeout=(5, 20), headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9",
            })
            log.info("AVITO cffi: HTTP %d, len=%d", r.status_code, len(r.text))
            label = u.split("/")[-1]
            out["avito"][f"cat_{label}"] = {"status": r.status_code, "len": len(r.text)}
            if r.status_code == 200 and len(r.text) > 2000:
                import re
                ids = re.findall(r'/(\d{9,15})(?:[/?]|$)', r.text)
                ids = [i for i in ids if len(i) >= 9]
                out["avito"][f"cat_{label}"]["ids"] = ids[:10]
                log.info("AVITO cffi: IDs: %s", ids[:5])
                if ids:
                    break
            time.sleep(2)
        except Exception as e2:
            log.warning("AVITO cffi cat error: %s", e2)
            out["avito"][f"cat_{u.split('/')[-1]}"] = {"error": str(e2)}

    # Стратегия B: curl_cffi на конкретное объявление (короткий URL)
    # Ищем ID из category или используем known
    test_ids = []
    for k, v in out["avito"].items():
        if isinstance(v, dict) and v.get("ids"):
            test_ids = v["ids"][:3]
            break
    if not test_ids:
        # Known Avito ad IDs (short format /ad/ID)
        test_ids = ["1097975275", "1234567890"]
        log.info("AVITO: using fallback IDs: %s", test_ids)

    for aid in test_ids[:3]:
        try:
            ad_url = f"https://www.avito.ru/ad/{aid}"
            log.info("AVITO cffi: GET %s", ad_url)
            r = cffi_req.get(ad_url, impersonate="chrome", timeout=(5, 20), headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9",
            })
            log.info("AVITO cffi ad: HTTP %d, len=%d", r.status_code, len(r.text))
            out["avito"][f"ad_{aid}"] = {"status": r.status_code, "len": len(r.text)}
            if r.status_code == 200 and len(r.text) > 1000:
                import re
                title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', r.text, re.I)
                price_m = re.search(r'"price"\s*:\s*"?(\d+)', r.text)
                jsonld_m = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', r.text, re.DOTALL)
                out["avito"][f"ad_{aid}"]["og_title"] = title_m.group(1)[:100] if title_m else None
                out["avito"][f"ad_{aid}"]["price_raw"] = price_m.group(1) if price_m else None
                out["avito"][f"ad_{aid}"]["jsonld_count"] = len(jsonld_m)
                if jsonld_m:
                    out["avito"][f"ad_{aid}"]["jsonld_sample"] = jsonld_m[0][:300]
                log.info("AVITO cffi ad: title=%s price=%s jsonld=%d",
                         out["avito"][f"ad_{aid}"].get("og_title", "?")[:50],
                         out["avito"][f"ad_{aid}"].get("price_raw"),
                         len(jsonld_m))
        except Exception as e2:
            log.warning("AVITO cffi ad error: %s", e2)
            out["avito"][f"ad_{aid}"] = {"error": str(e2)}
        time.sleep(2)

    # Стратегия C: mobile cffi
    try:
        log.info("AVITO cffi mobile: GET https://m.avito.ru/moskva/smartfony-i-telefony")
        r = cffi_req.get("https://m.avito.ru/moskva/smartfony-i-telefony", impersonate="chrome",
                         timeout=(5, 20), headers={
            "Accept": "text/html",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        log.info("AVITO cffi mobile: HTTP %d, len=%d", r.status_code, len(r.text))
        out["avito"]["mobile_cffi"] = {"status": r.status_code, "len": len(r.text)}
        if r.status_code == 200:
            import re
            ids = re.findall(r'/ad/(\d{9,15})', r.text)
            out["avito"]["mobile_cffi"]["ids"] = ids[:10]
    except Exception as e2:
        out["avito"]["mobile_cffi"] = {"error": str(e2)}

except Exception as e:
    log.exception("AVITO fatal: %s", e)
    out["avito"]["fatal"] = str(e)

# ============================================================
# SAVE
# ============================================================
out_path = os.path.join(HERE, "diag19_result.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
log.info("Result → %s", out_path)
log.info("DONE")

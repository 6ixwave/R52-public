"""Мульти-стратегийный пробник: WB, Ozon, Avito.
Каждая стратегия — короткий таймаут (8-15с), результат в JSON.
"""
import json, logging, re, sys, time, traceback
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("PROBE")

RESULTS = {}

def now_iso():
    return datetime.now(timezone(timedelta(hours=10))).isoformat()

def record(platform, strategy, ok, detail, dt):
    key = f"{platform}:{strategy}"
    RESULTS[key] = {"ok": ok, "detail": detail, "seconds": round(dt, 1)}
    status = "✓" if ok else "✗"
    log.info("[%s] %s %s (%.1fs) %s", platform, strategy, status, dt, str(detail)[:120])

# ================================================================
# WB PROBE
# ================================================================
def probe_wb():
    nm_id = 1097975275
    vol = nm_id // 100000
    part = nm_id // 1000
    log.info("=== WB PROBE nm_id=%d ===", nm_id)

    # --- W1: basket CDN price.json ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        # Найденный хост: basket-42
        host = "basket-42.wbbasket.ru"
        url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/price.json"
        r = cffi.get(url, impersonate="chrome", timeout=10,
                     headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        if r.status_code == 200:
            data = r.json()
            record("WB", "basket_price.json", True, f"keys={list(data.keys())[:8]} sizes={len(data.get('sizes',[]))}", time.time()-t0)
            # Извлекаем цены
            prices = []
            for sz in data.get("sizes", []):
                p = sz.get("price", {})
                for k in ("basic","product","old","card","wallet","crossed"):
                    v = p.get(k)
                    if v and v > 0:
                        prices.append({"role": k, "minor": v})
            if prices:
                record("WB", "basket_price.json_PRICES", True, prices[:5], time.time()-t0)
            else:
                record("WB", "basket_price.json_PRICES", False, f"sizes={len(data.get('sizes',[]))} but no price keys", time.time()-t0)
        else:
            record("WB", "basket_price.json", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("WB", "basket_price.json", False, str(e)[:80], time.time()-t0)

    # --- W2: card.wb.ru v2 с dest=1257786 (без минуса) ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        for dest in ("-1257786", "1257786", "25"):
            url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest={dest}&spp=30&nm={nm_id}"
            r = cffi.get(url, impersonate="chrome", timeout=10,
                         headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/", "Origin": "https://www.wildberries.ru"})
            if r.status_code == 200:
                data = r.json()
                prods = data.get("data", {}).get("items") or data.get("products", [])
                if prods:
                    p = prods[0]
                    sizes = p.get("sizes", [])
                    prices = []
                    for sz in sizes:
                        po = sz.get("price", {})
                        for k in ("basic","product","old","card"):
                            v = po.get(k)
                            if v and v > 0:
                                prices.append({"role": k, "minor": v})
                    record("WB", f"card_v2_dest{dest}", True, f"items={len(prods)} prices={prices[:3]}", time.time()-t0)
                    break
                else:
                    record("WB", f"card_v2_dest{dest}", False, f"200 but no items, keys={list(data.keys())}", time.time()-t0)
            else:
                record("WB", f"card_v2_dest{dest}", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("WB", "card_v2", False, str(e)[:80], time.time()-t0)

    # --- W3: card.wb.ru v1 (старый, иногда жив) ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        url = f"https://card.wb.ru/cards/v1/detail?curr=rub&nm={nm_id}"
        r = cffi.get(url, impersonate="chrome", timeout=10,
                     headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        if r.status_code == 200:
            data = r.json()
            prods = data.get("data", {}).get("items") or data.get("products", [])
            prices = []
            if prods:
                for sz in prods[0].get("sizes", []):
                    po = sz.get("price", {})
                    for k in ("basic","product","old"):
                        v = po.get(k)
                        if v and v > 0:
                            prices.append({"role": k, "minor": v})
            record("WB", "card_v1", True, f"items={len(prods)} prices={prices[:3]}", time.time()-t0)
        else:
            record("WB", "card_v1", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("WB", "card_v1", False, str(e)[:80], time.time()-t0)

    # --- W4: basket CDN card.json — ищем цены внутри ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        host = "basket-42.wbbasket.ru"
        url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        r = cffi.get(url, impersonate="chrome", timeout=10,
                     headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        if r.status_code == 200:
            data = r.json()
            # Ищем ВСЕ числовые поля с "price" в имени
            price_fields = []
            def find_prices(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if "price" in k.lower() or "priceu" in k.lower() or "priced" in k.lower():
                            if isinstance(v, (int, float)) and v > 0:
                                price_fields.append({"path": f"{path}.{k}", "val": v})
                            elif isinstance(v, dict):
                                find_prices(v, f"{path}.{k}")
                        else:
                            find_prices(v, f"{path}.{k}")
                elif isinstance(obj, list):
                    for i, item in enumerate(obj[:5]):
                        find_prices(item, f"{path}[{i}]")
            find_prices(data)
            record("WB", "card.json_price_fields", len(price_fields) > 0, price_fields[:8], time.time()-t0)
        else:
            record("WB", "card.json_price_fields", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("WB", "card.json_price_fields", False, str(e)[:80], time.time()-t0)

    # --- W5: HTML detail.aspx с mobile UA ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        r = cffi.get(url, impersonate="chrome", timeout=15,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        if r.status_code == 200:
            html = r.text
            # Ищем цены
            prices = []
            m = re.search(r'"price"\s*:\s*\{[^}]*"basic"\s*:\s*(\d+)', html)
            if m:
                prices.append({"role": "basic", "minor": int(m.group(1)), "src": "embedded_json"})
            for m in re.finditer(r'>(\d[\d\s]{2,9})\s*₽\s*<', html):
                amt = int(m.group(1).replace(" ", ""))
                if amt > 100:
                    prices.append({"role": "html", "minor": amt * 100, "src": "visible_text"})
                    break
            # og:price
            m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
            if m:
                prices.append({"role": "og", "minor": int(float(m.group(1)) * 100), "src": "og:price"})
            record("WB", "html_mobile", len(prices) > 0, f"len={len(html)} prices={prices[:4]}", time.time()-t0)
        else:
            record("WB", "html_mobile", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("WB", "html_mobile", False, str(e)[:80], time.time()-t0)

    # --- W6: basket CDN price.json (другой хост, перебор) ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        found = False
        for n in range(1, 61):
            host = f"basket-{n:02d}.wbbasket.ru"
            url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/price.json"
            r = cffi.get(url, impersonate="chrome", timeout=5,
                         headers={"Accept": "application/json"})
            if r.status_code == 200:
                data = r.json()
                sizes = data.get("sizes", [])
                prices = []
                for sz in sizes:
                    po = sz.get("price", {})
                    for k in ("basic","product","old","card"):
                        v = po.get(k)
                        if v and v > 0:
                            prices.append({"role": k, "minor": v})
                record("WB", f"basket_price_scan_{n:02d}", True, f"host={host} sizes={len(sizes)} prices={prices[:3]}", time.time()-t0)
                found = True
                break
        if not found:
            record("WB", "basket_price_scan", False, "no host responded 200", time.time()-t0)
    except Exception as e:
        record("WB", "basket_price_scan", False, str(e)[:80], time.time()-t0)

    # --- W7: search.wb.ru (иногда отдельный CDN) ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        url = f"https://search.wb.ru/cards/detail?curr=rub&nm={nm_id}&spp=30"
        r = cffi.get(url, impersonate="chrome", timeout=10,
                     headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        if r.status_code == 200:
            data = r.json()
            prods = data.get("data", {}).get("items") or data.get("products", [])
            prices = []
            if prods:
                for sz in prods[0].get("sizes", []):
                    po = sz.get("price", {})
                    for k in ("basic","product","old"):
                        v = po.get(k)
                        if v and v > 0:
                            prices.append({"role": k, "minor": v})
            record("WB", "search_wb", True, f"items={len(prods)} prices={prices[:3]}", time.time()-t0)
        else:
            record("WB", "search_wb", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("WB", "search_wb", False, str(e)[:80], time.time()-t0)


# ================================================================
# OZON PROBE
# ================================================================
def probe_ozon():
    log.info("=== OZON PROBE ===")
    # Живые ID — попробуем несколько
    ozon_ids = ["2067947177", "3123456789", "2567890123"]

    # --- O1: Composer API с SSL fix ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        url = f"https://www.ozon.ru/composer-api.bx/page/json/v2?url=/product/{ozon_ids[0]}/"
        r = cffi.get(url, impersonate="chrome", timeout=12, verify=False,
                     headers={
                         "Accept": "application/json",
                         "Referer": f"https://www.ozon.ru/product/{ozon_ids[0]}/",
                         "X-Request-Sign": "",
                         "X-Request-Timestamp": str(int(time.time() * 1000)),
                     })
        if r.status_code == 200:
            data = r.json()
            # Ищем цены в state
            state = data.get("state", {})
            prices = []
            def find_ozon_prices(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k in ("price", "priceU", "priceD", "oldPrice", "amount") and isinstance(v, (int, float)) and v > 0:
                            prices.append({"path": f"{path}.{k}", "val": v})
                        find_ozon_prices(v, f"{path}.{k}")
                elif isinstance(obj, list):
                    for i, item in enumerate(obj[:3]):
                        find_ozon_prices(item, f"{path}[{i}]")
            find_ozon_prices(state)
            record("OZON", "composer_api", True, f"state_keys={list(state.keys())[:6]} prices={prices[:4]}", time.time()-t0)
        else:
            record("OZON", "composer_api", False, f"HTTP {r.status_code} body[:100]={r.text[:100]}", time.time()-t0)
    except Exception as e:
        record("OZON", "composer_api", False, str(e)[:100], time.time()-t0)

    # --- O2: Composer API v1 (старый) ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        url = f"https://www.ozon.ru/composer-api.bx/page/json/v1?url=/product/{ozon_ids[0]}/"
        r = cffi.get(url, impersonate="chrome", timeout=12, verify=False,
                     headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/"})
        if r.status_code == 200:
            data = r.json()
            record("OZON", "composer_v1", True, f"keys={list(data.keys())[:6]}", time.time()-t0)
        else:
            record("OZON", "composer_v1", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("OZON", "composer_v1", False, str(e)[:80], time.time()-t0)

    # --- O3: HTML с verify=False ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        url = f"https://www.ozon.ru/product/{ozon_ids[0]}/"
        r = cffi.get(url, impersonate="chrome", timeout=15, verify=False,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        if r.status_code == 200:
            html = r.text
            prices = []
            m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
            if m:
                prices.append({"role": "og", "minor": int(float(m.group(1)) * 100)})
            for m in re.finditer(r'(\d[\d\s]{2,9})\s*₽', html[:50000]):
                amt = int(m.group(1).replace(" ", ""))
                if 100 < amt < 10_000_000:
                    prices.append({"role": "html", "minor": amt * 100})
                    break
            # JSON-LD
            m = re.search(r'"price"\s*:\s*([\d.]+)', html)
            if m:
                prices.append({"role": "ld", "minor": int(float(m.group(1)) * 100)})
            record("OZON", "html_ssl_fix", len(prices) > 0, f"len={len(html)} prices={prices[:4]}", time.time()-t0)
        else:
            record("OZON", "html_ssl_fix", False, f"HTTP {r.status_code} len={len(r.text)}", time.time()-t0)
    except Exception as e:
        record("OZON", "html_ssl_fix", False, str(e)[:100], time.time()-t0)

    # --- O4: Mобильный Ozon ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        url = f"https://m.ozon.ru/product/{ozon_ids[0]}/"
        r = cffi.get(url, impersonate="chrome", timeout=12, verify=False,
                     headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"})
        if r.status_code == 200:
            html = r.text
            prices = []
            m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
            if m:
                prices.append({"role": "og", "minor": int(float(m.group(1)) * 100)})
            record("OZON", "mobile", len(prices) > 0, f"len={len(html)} prices={prices[:3]}", time.time()-t0)
        else:
            record("OZON", "mobile", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("OZON", "mobile", False, str(e)[:80], time.time()-t0)

    # --- O5: api.ozon.ru (backend) ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{ozon_ids[0]}/"
        r = cffi.get(url, impersonate="chrome", timeout=12, verify=False,
                     headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/"})
        if r.status_code == 200:
            data = r.json()
            record("OZON", "api_ozon", True, f"keys={list(data.keys())[:6]}", time.time()-t0)
        else:
            record("OZON", "api_ozon", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("OZON", "api_ozon", False, str(e)[:80], time.time()-t0)

    # --- O6: Разные ID (возможно 2067947177 мёртвый) ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        for oid in ozon_ids[1:]:
            url = f"https://www.ozon.ru/product/{oid}/"
            r = cffi.get(url, impersonate="chrome", timeout=10, verify=False,
                         headers={"Accept": "text/html"})
            status = r.status_code
            title = ""
            m = re.search(r'<title[^>]*>([^<]+)', r.text, re.I)
            if m:
                title = m.group(1)[:60]
            record("OZON", f"id_{oid}", status == 200, f"HTTP {status} title={title}", time.time()-t0)
    except Exception as e:
        record("OZON", "id_scan", False, str(e)[:80], time.time()-t0)


# ================================================================
# AVITO PROBE
# ================================================================
def probe_avito():
    log.info("=== AVITO PROBE ===")
    # Живые ID — попробуем несколько форматов
    avito_ids = ["1045231001", "1050000001", "1060000001", "1070000001"]

    # --- A1: www.avito.ru ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        for aid in avito_ids:
            url = f"https://www.avito.ru/ad/{aid}"
            r = cffi.get(url, impersonate="chrome", timeout=10,
                         headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
            if r.status_code == 200:
                html = r.text
                prices = []
                m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
                if m:
                    prices.append({"role": "og", "minor": int(float(m.group(1)) * 100)})
                m = re.search(r'"price"\s*:\s*(\d+)', html)
                if m:
                    prices.append({"role": "json", "minor": int(m.group(1)) * 100})
                captcha = "captcha" in html[:5000].lower()
                record("AVITO", f"www_{aid}", len(prices) > 0, f"len={len(html)} prices={prices[:3]} captcha={captcha}", time.time()-t0)
            else:
                record("AVITO", f"www_{aid}", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("AVITO", "www", False, str(e)[:80], time.time()-t0)

    # --- A2: m.avito.ru ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        for aid in avito_ids:
            url = f"https://m.avito.ru/ad/{aid}"
            r = cffi.get(url, impersonate="chrome", timeout=10,
                         headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"})
            if r.status_code == 200:
                html = r.text
                prices = []
                m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
                if m:
                    prices.append({"role": "og", "minor": int(float(m.group(1)) * 100)})
                record("AVITO", f"m_{aid}", len(prices) > 0, f"len={len(html)} prices={prices[:3]}", time.time()-t0)
            else:
                record("AVITO", f"m_{aid}", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("AVITO", "mobile", False, str(e)[:80], time.time()-t0)

    # --- A3: Avito API (api.avito.ru) ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        aid = avito_ids[0]
        url = f"https://api.avito.ru/v1/items/{aid}"
        r = cffi.get(url, impersonate="chrome", timeout=10,
                     headers={"Accept": "application/json"})
        if r.status_code == 200:
            data = r.json()
            record("AVITO", "api_v1", True, f"keys={list(data.keys())[:6]}", time.time()-t0)
        else:
            record("AVITO", "api_v1", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        record("AVITO", "api_v1", False, str(e)[:80], time.time()-t0)

    # --- A4: Avito с cookie warmup ---
    t0 = time.time()
    try:
        from curl_cffi import requests as cffi
        s = cffi.Session(impersonate="chrome")
        # Warmup: главная
        r1 = s.get("https://www.avito.ru/", timeout=10,
                   headers={"Accept": "text/html"})
        time.sleep(2)
        # Затем объявление
        aid = avito_ids[0]
        r2 = s.get(f"https://www.avito.ru/ad/{aid}", timeout=12,
                   headers={"Accept": "text/html", "Referer": "https://www.avito.ru/"})
        if r2.status_code == 200:
            html = r2.text
            prices = []
            m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
            if m:
                prices.append({"role": "og", "minor": int(float(m.group(1)) * 100)})
            record("AVITO", "warmup_session", len(prices) > 0, f"main={r1.status_code} ad={r2.status_code} len={len(html)} prices={prices[:3]}", time.time()-t0)
        else:
            record("AVITO", "warmup_session", False, f"main={r1.status_code} ad={r2.status_code}", time.time()-t0)
    except Exception as e:
        record("AVITO", "warmup_session", False, str(e)[:80], time.time()-t0)


# ================================================================
# MAIN
# ================================================================
def main():
    t_start = time.time()
    log.info("PROBE START %s", now_iso())

    probe_wb()
    probe_ozon()
    probe_avito()

    dt_total = time.time() - t_start
    log.info("PROBE DONE in %.1fs", dt_total)

    out = {
        "time": now_iso(),
        "total_seconds": round(dt_total, 1),
        "results": RESULTS,
    }
    out_path = "probe_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    log.info("Saved: %s", out_path)

    # Краткая сводка
    log.info("=" * 50)
    log.info("SUMMARY")
    log.info("=" * 50)
    for plat in ("WB", "OZON", "AVITO"):
        ok_count = sum(1 for k, v in RESULTS.items() if k.startswith(plat + ":") and v["ok"])
        total = sum(1 for k in RESULTS if k.startswith(plat + ":"))
        log.info("  %-6s %d/%d strategies OK", plat, ok_count, total)
    log.info("DONE")


if __name__ == "__main__":
    main()

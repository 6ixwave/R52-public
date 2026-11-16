"""Probe2: сфокусированные тесты WB prices, Ozon (реальные ID), Avito (поиск живого)."""
import json, logging, re, sys, time, traceback
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("P2")

RESULTS = {}
def rec(plat, strat, ok, detail, dt):
    RESULTS[f"{plat}:{strat}"] = {"ok": ok, "detail": str(detail)[:200], "s": round(dt,1)}
    log.info("[%s] %s %s (%.1fs) %s", plat, strat, "OK" if ok else "FAIL", dt, str(detail)[:100])

def now():
    return datetime.now(timezone(timedelta(hours=10))).isoformat()

# ============================================================
# WB: найти где лежат цены
# ============================================================
def wb_probe():
    nm = 1097975275
    vol = nm // 100000
    part = nm // 1000
    log.info("=== WB nm=%d vol=%d part=%d ===", nm, vol, part)
    from curl_cffi import requests as cr

    # W-A: Dump card.json structure (top-level keys + sample)
    t0 = time.time()
    try:
        host = "basket-42.wbbasket.ru"
        url = f"https://{host}/vol{vol}/part{part}/{nm}/info/ru/card.json"
        r = cr.get(url, impersonate="chrome", timeout=10,
                   headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"})
        if r.status_code == 200:
            d = r.json()
            keys = list(d.keys())
            # Ищем ВСЁ числовое > 100 (возможно цены в копейках)
            nums = []
            def walk(o, p=""):
                if isinstance(o, dict):
                    for k,v in o.items():
                        walk(v, f"{p}.{k}")
                elif isinstance(o, list):
                    for i,v in enumerate(o[:3]):
                        walk(v, f"{p}[{i}]")
                elif isinstance(o, (int,float)) and 100 < o < 100000000:
                    nums.append({"p": p, "v": o})
            walk(d)
            # Фильтруем: ищем поля с "price", "prc", "cost", "amount" в пути
            price_like = [x for x in nums if any(w in x["p"].lower() for w in ("price","prc","cost","amount","basic","product","old","card","wallet"))]
            rec("WB","card_dump", True, f"keys={keys[:15]} price_like={price_like[:8]} total_nums={len(nums)}", time.time()-t0)
            # Сохраним полный dump для анализа
            with open("wb_card_dump.json","w",encoding="utf-8") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
        else:
            rec("WB","card_dump", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        rec("WB","card_dump", False, str(e)[:100], time.time()-t0)

    # W-B: price.json — разные пути
    t0 = time.time()
    paths = [
        f"/vol{vol}/part{part}/{nm}/info/ru/price.json",
        f"/vol{vol}/part{part}/{nm}/info/price.json",
        f"/vol{vol}/part{part}/{nm}/price.json",
        f"/vol{vol}/part{part}/{nm}/info/ru/prices.json",
        f"/vol{vol}/part{part}/{nm}/info/ru/card_price.json",
    ]
    found = False
    for p in paths:
        try:
            url = f"https://basket-42.wbbasket.ru{p}"
            r = cr.get(url, impersonate="chrome", timeout=6,
                       headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"})
            if r.status_code == 200:
                d = r.json()
                rec("WB", f"price_path_{p.split('/')[-1]}", True, f"keys={list(d.keys())[:10]}", time.time()-t0)
                found = True
                break
            elif r.status_code != 404:
                rec("WB", f"price_path_{p.split('/')[-1]}", False, f"HTTP {r.status_code}", time.time()-t0)
        except Exception as e:
            pass
    if not found:
        rec("WB","price_paths", False, f"all 404, tried {len(paths)} paths", time.time()-t0)

    # W-C: catalog.wb.ru (mirror)
    t0 = time.time()
    try:
        for ver in ["v1","v2","v4","v5"]:
            url = f"https://catalog.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"
            r = cr.get(url, impersonate="chrome", timeout=8,
                       headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/","Origin":"https://www.wildberries.ru"})
            if r.status_code == 200:
                d = r.json()
                prods = d.get("products") or d.get("data",{}).get("items",[])
                if prods:
                    sz = prods[0].get("sizes",[])
                    pr = sz[0].get("price",{}) if sz else {}
                    rec("WB", f"catalog_{ver}", True, f"items={len(prods)} price={pr}", time.time()-t0)
                    break
            elif r.status_code in (403,498):
                continue
    except Exception as e:
        rec("WB","catalog", False, str(e)[:80], time.time()-t0)

    # W-D: web API
    t0 = time.time()
    try:
        url = f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}&curr=rub"
        r = cr.get(url, impersonate="chrome", timeout=10,
                   headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"})
        if r.status_code == 200:
            d = r.json()
            rec("WB","web_api", True, f"keys={list(d.keys())[:10]}", time.time()-t0)
        else:
            rec("WB","web_api", False, f"HTTP {r.status_code} body[:80]={r.text[:80]}", time.time()-t0)
    except Exception as e:
        rec("WB","web_api", False, str(e)[:80], time.time()-t0)

    # W-E: card.wb.ru v2 с appType=1&curr=rub БЕЗ dest (или dest=25)
    t0 = time.time()
    try:
        for params in [
            f"appType=1&curr=rub&nm={nm}",
            f"appType=1&curr=rub&dest=25&spp=30&nm={nm}",
            f"appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}&lang=RU",
            f"curr=rub&nm={nm}&spp=30",
        ]:
            url = f"https://card.wb.ru/cards/v2/detail?{params}"
            r = cr.get(url, impersonate="chrome", timeout=8,
                       headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/","Origin":"https://www.wildberries.ru","X-Requested-With":"XMLHttpRequest"})
            if r.status_code == 200:
                d = r.json()
                prods = d.get("products") or d.get("data",{}).get("items",[])
                if prods:
                    sz = prods[0].get("sizes",[])
                    pr = sz[0].get("price",{}) if sz else {}
                    rec("WB","card_v2_nodest", True, f"params={params[:40]} price={pr}", time.time()-t0)
                    break
            elif r.status_code == 200:
                continue
    except Exception as e:
        rec("WB","card_v2_nodest", False, str(e)[:80], time.time()-t0)

    # W-F: basket CDN — info/ru/ с другим суффиксом
    t0 = time.time()
    try:
        # Попробуем info/ru/ с разными именами
        for suffix in ["price","prices","card_price","info_price","price_info"]:
            url = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/{suffix}.json"
            r = cr.get(url, impersonate="chrome", timeout=5,
                       headers={"Accept":"application/json"})
            if r.status_code == 200:
                rec("WB", f"basket_{suffix}", True, f"keys={list(r.json().keys())[:8]}", time.time()-t0)
                break
        else:
            # Попробуем без /ru/
            url = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm}/info/price.json"
            r = cr.get(url, impersonate="chrome", timeout=5,
                       headers={"Accept":"application/json"})
            if r.status_code == 200:
                rec("WB","basket_info_price_noru", True, f"keys={list(r.json().keys())[:8]}", time.time()-t0)
            else:
                rec("WB","basket_suffix_scan", False, f"all tried, last HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        rec("WB","basket_suffix_scan", False, str(e)[:80], time.time()-t0)

    # W-G: HTML detail.aspx — ищем embedded state / __NEXT_DATA__ / window.__
    t0 = time.time()
    try:
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        r = cr.get(url, impersonate="chrome", timeout=15,
                   headers={"Accept":"text/html","Accept-Language":"ru-RU,ru;q=0.9"})
        if r.status_code == 200:
            html = r.text
            # Ищем window.__STATE__, __NEXT_DATA__, application/json script
            embedded = []
            m = re.search(r'window\.__STATE__\s*=\s*(\{.+?\});?\s*</script>', html, re.S)
            if m:
                embedded.append("window.__STATE__")
            m = re.search(r'__NEXT_DATA__\s*=\s*(\{.+?\})\s*</script>', html, re.S)
            if m:
                embedded.append("__NEXT_DATA__")
            m = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(\{.+?\})</script>', html, re.S)
            if m:
                embedded.append("ld+json")
                try:
                    ld = json.loads(m.group(1))
                    if isinstance(ld, dict) and "offers" in ld:
                        offers = ld["offers"]
                        if isinstance(offers, list):
                            embedded.append(f"offers[0]={json.dumps(offers[0],ensure_ascii=False)[:100]}")
                        elif isinstance(offers, dict):
                            embedded.append(f"offers={json.dumps(offers,ensure_ascii=False)[:100]}")
                except: pass
            # og:price
            m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
            if m:
                embedded.append(f"og_price={m.group(1)}")
            # data-price, data-cost
            for pat in [r'data-price=["\']([\d.]+)', r'data-cost=["\']([\d.]+)', r'"price"\s*:\s*\{[^}]*"basic"\s*:\s*(\d+)']:
                m = re.search(pat, html)
                if m:
                    embedded.append(f"re:{pat[:20]}={m.group(1)}")
            rec("WB","html_embed", len(embedded)>0, f"len={len(html)} found={embedded[:6]}", time.time()-t0)
        else:
            rec("WB","html_embed", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        rec("WB","html_embed", False, str(e)[:80], time.time()-t0)


# ============================================================
# OZON: реальные ID + разные подходы
# ============================================================
def ozon_probe():
    log.info("=== OZON ===")
    from curl_cffi import requests as cr
    # Реальные ID из web_search
    ids = ["3481320800", "761659557", "1848574753"]

    # O-A: Composer API v2 с реальным ID
    t0 = time.time()
    try:
        oid = ids[0]
        url = f"https://www.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/"
        r = cr.get(url, impersonate="chrome", timeout=12, verify=False,
                   headers={"Accept":"application/json","Referer":f"https://www.ozon.ru/product/{oid}/","X-Request-Sign":"","X-Request-Timestamp":str(int(time.time()*1000))})
        if r.status_code == 200:
            d = r.json()
            state = d.get("state",{})
            # Ищем price
            prices = []
            def find_p(o,p=""):
                if isinstance(o,dict):
                    for k,v in o.items():
                        if k in ("price","priceU","priceD","oldPrice","amount","priceValue") and isinstance(v,(int,float)) and v>0:
                            prices.append(f"{p}.{k}={v}")
                        find_p(v,f"{p}.{k}")
                elif isinstance(o,list):
                    for i,v in enumerate(o[:3]):
                        find_p(v,f"{p}[{i}]")
            find_p(state)
            rec("OZON","composer_v2_real", True, f"state_keys={list(state.keys())[:8]} prices={prices[:6]}", time.time()-t0)
        else:
            body = r.text[:150]
            rec("OZON","composer_v2_real", False, f"HTTP {r.status_code} body={body}", time.time()-t0)
    except Exception as e:
        rec("OZON","composer_v2_real", False, str(e)[:100], time.time()-t0)

    # O-B: HTML с реальным ID
    t0 = time.time()
    try:
        oid = ids[0]
        url = f"https://www.ozon.ru/product/{oid}/"
        r = cr.get(url, impersonate="chrome", timeout=15, verify=False,
                   headers={"Accept":"text/html","Accept-Language":"ru-RU,ru;q=0.9"})
        if r.status_code == 200:
            html = r.text
            prices = []
            m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
            if m: prices.append(f"og={m.group(1)}")
            m = re.search(r'"price"\s*:\s*([\d.]+)', html)
            if m: prices.append(f"json={m.group(1)}")
            m = re.search(r'(\d[\d\s]{2,9})\s*₽', html[:80000])
            if m: prices.append(f"html={m.group(1).strip()}")
            # FAB challenge?
            fab = "fab_chlg" in html or "Antibot" in html or "challenge" in html.lower()[:5000]
            rec("OZON","html_real", len(prices)>0, f"len={len(html)} prices={prices[:4]} fab={fab}", time.time()-t0)
        else:
            rec("OZON","html_real", False, f"HTTP {r.status_code} len={len(r.text)}", time.time()-t0)
    except Exception as e:
        rec("OZON","html_real", False, str(e)[:100], time.time()-t0)

    # O-C: Разные ID — какой живой?
    t0 = time.time()
    try:
        for oid in ids:
            url = f"https://www.ozon.ru/product/{oid}/"
            r = cr.get(url, impersonate="chrome", timeout=8, verify=False,
                       headers={"Accept":"text/html"})
            title = ""
            m = re.search(r'<title[^>]*>([^<]+)', r.text, re.I)
            if m: title = m.group(1)[:50]
            rec("OZON", f"id_{oid}", r.status_code==200, f"HTTP {r.status_code} title={title}", time.time()-t0)
    except Exception as e:
        rec("OZON","id_scan", False, str(e)[:80], time.time()-t0)

    # O-D: Playwright (FAB challenge требует реального браузера)
    t0 = time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width":1920,"height":1080},
                locale="ru-RU",
            )
            page = ctx.new_page()
            oid = ids[0]
            page.goto(f"https://www.ozon.ru/product/{oid}/", wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)
            html = page.content()
            prices = []
            m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
            if m: prices.append(f"og={m.group(1)}")
            m = re.search(r'"price"\s*:\s*([\d.]+)', html)
            if m: prices.append(f"json={m.group(1)}")
            # Ищем в state
            m = re.search(r'"priceU"\s*:\s*"([\d.]+)"', html)
            if m: prices.append(f"priceU={m.group(1)}")
            m = re.search(r'"priceD"\s*:\s*"([\d.]+)"', html)
            if m: prices.append(f"priceD={m.group(1)}")
            fab = "fab_chlg" in html or "Antibot" in html
            rec("OZON","playwright", len(prices)>0, f"len={len(html)} prices={prices[:5]} fab={fab}", time.time()-t0)
            browser.close()
    except Exception as e:
        rec("OZON","playwright", False, str(e)[:120], time.time()-t0)


# ============================================================
# AVITO: найти живое объявление
# ============================================================
def avito_probe():
    log.info("=== AVITO ===")
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")

    # A-A: Главная → категория → живые ID
    t0 = time.time()
    try:
        r = s.get("https://www.avito.ru/", timeout=12,
                  headers={"Accept":"text/html","Accept-Language":"ru-RU,ru;q=0.9"})
        log.info("Avito main: HTTP %d, len=%d", r.status_code, len(r.text))
        time.sleep(2)
    except Exception as e:
        log.warning("Avito main error: %s", e)

    # A-B: Категория (электроника, Москва) — ищем ссылки /ad/
    t0 = time.time()
    try:
        r = s.get("https://www.avito.ru/moskva/elektronika", timeout=15,
                  headers={"Accept":"text/html","Referer":"https://www.avito.ru/"})
        if r.status_code == 200:
            # Ищем /ad/(\d+) или /mск/.../ad/(\d+)
            ids = list(set(re.findall(r'/ad/(\d{8,12})', r.text)))[:5]
            rec("AVITO","category_ids", len(ids)>0, f"HTTP {r.status_code} ids={ids}", time.time()-t0)
            if ids:
                # Тестируем первый найденный
                time.sleep(2)
                aid = ids[0]
                r2 = s.get(f"https://www.avito.ru/ad/{aid}", timeout=12,
                           headers={"Accept":"text/html","Referer":"https://www.avito.ru/moskva/elektronika"})
                if r2.status_code == 200:
                    html = r2.text
                    prices = []
                    m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
                    if m: prices.append(f"og={m.group(1)}")
                    m = re.search(r'"price"\s*:\s*(\d+)', html)
                    if m: prices.append(f"json={m.group(1)}")
                    m = re.search(r'(\d[\d\s]{2,10})\s*₽', html[:60000])
                    if m: prices.append(f"html={m.group(1).strip()}")
                    fab = "captcha" in html[:5000].lower() or "antibot" in html.lower()[:3000]
                    rec("AVITO","live_ad", len(prices)>0, f"ad={aid} len={len(html)} prices={prices[:4]} captcha={fab}", time.time()-t0)
                else:
                    rec("AVITO","live_ad", False, f"ad={aid} HTTP {r2.status_code}", time.time()-t0)
        else:
            rec("AVITO","category_ids", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        rec("AVITO","category_ids", False, str(e)[:100], time.time()-t0)

    # A-C: m.avito.ru (mobile, иногда проще)
    t0 = time.time()
    try:
        r = s.get("https://m.avito.ru/moskva/elektronika", timeout=12,
                  headers={"Accept":"text/html","User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"})
        if r.status_code == 200:
            ids = list(set(re.findall(r'/ad/(\d{8,12})', r.text)))[:3]
            rec("AVITO","m_category", len(ids)>0, f"HTTP {r.status_code} ids={ids}", time.time()-t0)
        else:
            rec("AVITO","m_category", False, f"HTTP {r.status_code}", time.time()-t0)
    except Exception as e:
        rec("AVITO","m_category", False, str(e)[:80], time.time()-t0)

    # A-D: Avito API (api.avito.ru) — если есть
    t0 = time.time()
    try:
        r = s.get("https://api.avito.ru/v1/items/1045231001", timeout=8,
                  headers={"Accept":"application/json"})
        rec("AVITO","api_v1", r.status_code==200, f"HTTP {r.status_code} body[:80]={r.text[:80]}", time.time()-t0)
    except Exception as e:
        rec("AVITO","api_v1", False, str(e)[:80], time.time()-t0)


# ============================================================
def main():
    t0 = time.time()
    log.info("PROBE2 START %s", now())
    wb_probe()
    ozon_probe()
    avito_probe()
    dt = time.time()-t0
    log.info("PROBE2 DONE %.1fs", dt)

    out = {"time": now(), "total_s": round(dt,1), "results": RESULTS}
    with open("probe2_results.json","w",encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    log.info("Saved probe2_results.json")

    log.info("="*50)
    for plat in ("WB","OZON","AVITO"):
        ok = sum(1 for k,v in RESULTS.items() if k.startswith(plat+":") and v["ok"])
        tot = sum(1 for k in RESULTS if k.startswith(plat+":"))
        log.info("  %-6s %d/%d OK", plat, ok, tot)
    log.info("DONE")

if __name__ == "__main__":
    main()

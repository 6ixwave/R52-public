"""Probe4: WB Playwright+photos, Ozon JSON-LD parse, Avito deep JS."""
import json, logging, re, sys, time
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("P4")
RESULTS = {}
def rec(p,s,ok,d,dt):
    RESULTS[f"{p}:{s}"]={"ok":ok,"detail":str(d)[:300],"s":round(dt,1)}
    log.info("[%s] %s %s (%.1fs) %s",p,s,"OK" if ok else "FAIL",dt,str(d)[:120])
def now(): return datetime.now(timezone(timedelta(hours=10))).isoformat()

# ============================================================
# WB: Playwright (fixed) + photo CDN
# ============================================================
def wb_probe():
    nm = 1097975275
    vol, part = nm//100000, nm//1000
    log.info("=== WB nm=%d ===", nm)
    from curl_cffi import requests as cr

    # W1: Photo CDN — try common patterns
    t0=time.time()
    try:
        # Pattern 1: basket CDN /1/images/big/
        photo_urls = []
        for img_n in range(1, 8):
            for sz in ["big","medium","small"]:
                url = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm}/1/images/{sz}/{img_n}/"
                r = cr.get(url, impersonate="chrome", timeout=4,
                           headers={"Accept":"image/*","Referer":"https://www.wildberries.ru/"})
                ct = r.headers.get("content-type","")
                if r.status_code == 200 and "image" in ct:
                    photo_urls.append(url)
                    break
            if len(photo_urls) >= 3:
                break
        # Pattern 2: ir.wbstatic.net
        if not photo_urls:
            for img_n in range(1, 5):
                url = f"https://ir1.wbstatic.net/large/1/{nm}/{img_n}.jpg"
                r = cr.get(url, impersonate="chrome", timeout=4)
                if r.status_code == 200 and len(r.content) > 1000:
                    photo_urls.append(url)
        # Pattern 3: wbstatic with hash
        if not photo_urls:
            url = f"https://ir.wbstatic.net/large/1/{nm}/1.jpg"
            r = cr.get(url, impersonate="chrome", timeout=4)
            rec("WB","photo_wbstatic",r.status_code==200,f"HTTP {r.status_code} len={len(r.content)} ct={r.headers.get('content-type','')}",time.time()-t0)
        rec("WB","photo_cdn",len(photo_urls)>0,f"urls={photo_urls[:3]} count={len(photo_urls)}",time.time()-t0)
    except Exception as e:
        rec("WB","photo_cdn",False,str(e)[:100],time.time()-t0)

    # W2: Playwright with networkidle + XHR intercept
    t0=time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx = br.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width":1920,"height":1080}, locale="ru-RU")
            pg = ctx.new_page()
            # Intercept XHR to catch price API calls
            xhr_data = []
            def on_response(resp):
                url = resp.url
                if any(x in url for x in ("card.wb.ru","catalog.wb.ru","search.wb.ru","web/api")):
                    try:
                        body = resp.text()
                        xhr_data.append({"url": url[:120], "status": resp.status, "body": body[:500]})
                    except:
                        xhr_data.append({"url": url[:120], "status": resp.status, "body": "ERR"})
            pg.on("response", lambda r: on_response(r) if r.status in (200,403,498) else None)
            
            pg.goto(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                    wait_until="networkidle", timeout=30000)
            time.sleep(3)
            html = pg.content()
            with open("wb_pw4.html","w",encoding="utf-8") as f: f.write(html)
            
            # Extract prices from HTML
            prices = []
            m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
            if m: prices.append(f"og={m.group(1)}")
            for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.S):
                try:
                    ld = json.loads(m.group(1))
                    s = json.dumps(ld, ensure_ascii=False)
                    if "offers" in s or "price" in s.lower():
                        prices.append(f"ld={s[:200]}")
                except: pass
            m = re.search(r'"basic"\s*:\s*(\d+)', html)
            if m: prices.append(f"basic={m.group(1)}")
            m = re.search(r'"price"\s*:\s*\{[^}]*\}', html)
            if m: prices.append(f"price_obj={m.group(0)[:100]}")
            # Visible
            for m in re.finditer(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:120000]):
                amt = m.group(1).replace(" ","")
                if len(amt) >= 4: prices.append(f"vis={amt}"); break
            # Photos in HTML
            photos = re.findall(r'(https://[^"]*wbbasket[^"]*images[^"]*)', html)
            photos2 = re.findall(r'(https://ir[^"]*wbstatic[^"]*)', html)
            rec("WB","pw4_html",len(prices)>0,
                f"len={len(html)} prices={prices[:5]} photos={photos[:2]+photos2[:2]} xhr={len(xhr_data)}",
                time.time()-t0)
            # Save XHR
            with open("wb_pw4_xhr.json","w",encoding="utf-8") as f:
                json.dump(xhr_data[:10], f, indent=2, ensure_ascii=False)
            br.close()
    except Exception as e:
        rec("WB","pw4_html",False,str(e)[:200],time.time()-t0)

    # W3: card.wb.ru via Playwright (direct API call in browser context)
    t0=time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx = br.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",locale="ru-RU")
            pg = ctx.new_page()
            # Navigate to WB first (warmup cookies)
            pg.goto("https://www.wildberries.ru/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
            # Then call card.wb.ru API in browser context
            result = pg.evaluate("""
                async () => {
                    const nm = 1097975275;
                    const urls = [
                        `https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm=${nm}`,
                        `https://card.wb.ru/cards/v1/detail?curr=rub&nm=${nm}`,
                        `https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm=${nm}`,
                    ];
                    for (const url of urls) {
                        try {
                            const r = await fetch(url, {headers:{"Accept":"application/json"}});
                            if (r.status === 200) {
                                const d = await r.json();
                                return {url: url, status: 200, data: JSON.stringify(d).substring(0, 1000)};
                            }
                        } catch(e) { continue; }
                    }
                    return {url: "all", status: 0, data: "all failed"};
                }
            """)
            rec("WB","pw_api_call",result.get("status")==200,
                f"url={result.get('url','?')[:60]} data={result.get('data','')[:200]}",time.time()-t0)
            br.close()
    except Exception as e:
        rec("WB","pw_api_call",False,str(e)[:150],time.time()-t0)


# ============================================================
# OZON: Parse existing HTML + Playwright XHR
# ============================================================
def ozon_probe():
    log.info("=== OZON ===")
    oid = "3481320800"

    # O1: Parse existing ozon_pw_html.html for JSON-LD
    t0=time.time()
    try:
        with open("ozon_pw_html.html","r",encoding="utf-8") as f:
            html = f.read()
        log.info("Ozon HTML loaded: %d bytes", len(html))
        
        product_data = {}
        # JSON-LD blocks
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S):
            try:
                ld = json.loads(m.group(1))
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    product_data = ld
                    break
                elif isinstance(ld, list):
                    for item in ld:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            product_data = item
                            break
            except: pass
        
        if product_data:
            name = product_data.get("name","")
            brand = product_data.get("brand",{}).get("name","") if isinstance(product_data.get("brand"),dict) else product_data.get("brand","")
            desc = product_data.get("description","")[:200]
            images = product_data.get("image",[])
            if isinstance(images, str): images = [images]
            offers = product_data.get("offers",{})
            if isinstance(offers, list): offers = offers[0] if offers else {}
            price = offers.get("price","")
            currency = offers.get("priceCurrency","")
            avail = offers.get("availability","")
            rec("OZON","jsonld",True,
                f"name={name[:50]} brand={brand} price={price}{currency} avail={avail} imgs={len(images)} desc={desc[:80]}",
                time.time()-t0)
            # Save structured
            with open("ozon_jsonld.json","w",encoding="utf-8") as f:
                json.dump(product_data, f, indent=2, ensure_ascii=False)
        else:
            # Try embedded state
            m = re.search(r'window\.__STATE__\s*=\s*(\{.+?\})\s*;?\s*</script>', html, re.S)
            if not m:
                m = re.search(r'"priceU"\s*:\s*"?([\d.]+)"?', html)
            rec("OZON","jsonld",False,f"no Product JSON-LD found, len={len(html)}",time.time()-t0)
    except Exception as e:
        rec("OZON","jsonld",False,str(e)[:100],time.time()-t0)

    # O2: Playwright with XHR intercept (catch composer-api calls)
    t0=time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx = br.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width":1920,"height":1080}, locale="ru-RU")
            pg = ctx.new_page()
            xhrs = []
            def on_resp(r):
                if "composer-api" in r.url or "api.ozon" in r.url or "json/v2" in r.url:
                    try:
                        body = r.text()
                        xhrs.append({"url": r.url[:150], "status": r.status, "len": len(body), "body": body[:800]})
                    except:
                        xhrs.append({"url": r.url[:150], "status": r.status, "body": "ERR"})
            pg.on("response", lambda r: on_resp(r))
            
            pg.goto(f"https://www.ozon.ru/product/{oid}/", wait_until="networkidle", timeout=30000)
            time.sleep(4)
            
            # Also try to get state from page
            state = pg.evaluate("() => { try { return window.__STATE__ || window.__INITIAL_STATE__ || null; } catch(e) { return null; } }")
            
            # Extract from DOM
            price_el = pg.query_selector('[data-widget-id="price"]') or pg.query_selector('.price__current') or pg.query_selector('[class*="price"]')
            price_text = price_el.inner_text() if price_el else ""
            
            # Images
            imgs = pg.query_selector_all('[class*="gallery"] img, [class*="photo"] img, .tile__img')
            img_urls = []
            for img in imgs[:6]:
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                if src: img_urls.append(src)
            
            rec("OZON","pw_xhr",len(xhrs)>0 or bool(state),
                f"xhrs={len(xhrs)} state={bool(state)} price_dom={price_text[:50]} imgs={img_urls[:3]}",
                time.time()-t0)
            with open("ozon_pw4_xhr.json","w",encoding="utf-8") as f:
                json.dump(xhrs[:5], f, indent=2, ensure_ascii=False)
            if state:
                with open("ozon_pw4_state.json","w",encoding="utf-8") as f:
                    json.dump(state, f, indent=2, ensure_ascii=False, default=str)
            br.close()
    except Exception as e:
        rec("OZON","pw_xhr",False,str(e)[:200],time.time()-t0)

    # O3: Try Ozon API endpoint directly (api.ozon.ru)
    t0=time.time()
    try:
        from curl_cffi import requests as cr
        endpoints = [
            f"https://api.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/",
            f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/",
            f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/",
        ]
        for url in endpoints:
            r = cr.get(url, impersonate="chrome", timeout=10, verify=False,
                       headers={"Accept":"application/json","Referer":f"https://www.ozon.ru/product/{oid}/"})
            if r.status_code == 200:
                d = r.json()
                rec("OZON","api_direct",True,f"url={url[:60]} keys={list(d.keys())[:8]}",time.time()-t0)
                break
        else:
            rec("OZON","api_direct",False,f"all {len(endpoints)} failed, last={r.status_code}",time.time()-t0)
    except Exception as e:
        rec("OZON","api_direct",False,str(e)[:100],time.time()-t0)


# ============================================================
# AVITO: Deep Playwright + API discovery
# ============================================================
def avito_probe():
    log.info("=== AVITO ===")

    # A1: Playwright with long wait + scroll + XHR
    t0=time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx = br.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width":1920,"height":1080}, locale="ru-RU")
            pg = ctx.new_page()
            xhrs = []
            def on_resp(r):
                url = r.url
                if any(x in url for x in ("api","search","catalog","/ad/","listing")) and r.status == 200:
                    try:
                        body = r.text()
                        if len(body) > 100:
                            xhrs.append({"url": url[:150], "status": r.status, "len": len(body), "body": body[:600]})
                    except: pass
            pg.on("response", lambda r: on_resp(r))
            
            pg.goto("https://www.avito.ru/moskva/elektronika", wait_until="networkidle", timeout=30000)
            time.sleep(5)
            # Scroll to trigger lazy load
            pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(3)
            
            html = pg.content()
            with open("avito_pw4.html","w",encoding="utf-8") as f: f.write(html)
            
            # Find ad links (various patterns)
            ad_ids = set(re.findall(r'/ad/(\d{8,12})', html))
            ad_ids2 = set(re.findall(r'data-ad-id=["\']?(\d+)', html))
            ad_ids3 = set(re.findall(r'"id"\s*:\s*(\d{9,12})', html))
            # Titles
            titles = re.findall(r'<h2[^>]*>([^<]{10,100})</h2>', html)
            # Prices
            prices = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:100000])
            
            rec("AVITO","pw4_deep",len(ad_ids)+len(ad_ids2)>0,
                f"len={len(html)} ad_ids={list(ad_ids)[:5]} data_ids={list(ad_ids2)[:5]} json_ids={list(ad_ids3)[:5]} titles={titles[:3]} prices={prices[:5]} xhrs={len(xhrs)}",
                time.time()-t0)
            with open("avito_pw4_xhr.json","w",encoding="utf-8") as f:
                json.dump(xhrs[:10], f, indent=2, ensure_ascii=False)
            br.close()
    except Exception as e:
        rec("AVITO","pw4_deep",False,str(e)[:200],time.time()-t0)

    # A2: Try Avito search API (internal)
    t0=time.time()
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        # Try internal API endpoints
        apis = [
            "https://www.avito.ru/api/v1/search?query=iphone&city=1&limit=5",
            "https://www.avito.ru/api/search?query=iphone&city_id=1",
            "https://api.avito.ru/v1/search?query=iphone",
            "https://www.avito.ru/moskva/elektronika?api=1",
        ]
        for url in apis:
            r = s.get(url, timeout=10, headers={"Accept":"application/json, text/html","Referer":"https://www.avito.ru/"})
            ct = r.headers.get("content-type","")
            if r.status_code == 200 and "json" in ct:
                d = r.json()
                rec("AVITO","api_search",True,f"url={url[:60]} keys={list(d.keys())[:8]}",time.time()-t0)
                break
            elif r.status_code == 200 and len(r.text) > 500:
                # Maybe HTML with embedded JSON
                m = re.search(r'window\.__STATE__\s*=\s*(\{.+?\})\s*;', r.text, re.S)
                if m:
                    rec("AVITO","api_search",True,f"url={url[:60]} state_found=True",time.time()-t0)
                    break
        else:
            rec("AVITO","api_search",False,f"tried {len(apis)}, last HTTP {r.status_code} ct={ct[:30]}",time.time()-t0)
    except Exception as e:
        rec("AVITO","api_search",False,str(e)[:100],time.time()-t0)

    # A3: m.avito.ru via Playwright (mobile might be simpler)
    t0=time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx = br.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                viewport={"width":390,"height":844}, locale="ru-RU",
                is_mobile=True, has_touch=True)
            pg = ctx.new_page()
            pg.goto("https://m.avito.ru/moskva/elektronika", wait_until="networkidle", timeout=25000)
            time.sleep(4)
            html = pg.content()
            with open("avito_m_pw4.html","w",encoding="utf-8") as f: f.write(html)
            ad_ids = set(re.findall(r'/ad/(\d{8,12})', html))
            prices = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:80000])
            rec("AVITO","m_pw",len(ad_ids)>0,
                f"len={len(html)} ids={list(ad_ids)[:5]} prices={prices[:5]}",time.time()-t0)
            br.close()
    except Exception as e:
        rec("AVITO","m_pw",False,str(e)[:150],time.time()-t0)


def main():
    t0=time.time()
    log.info("PROBE4 START %s",now())
    wb_probe()
    ozon_probe()
    avito_probe()
    dt=time.time()-t0
    log.info("PROBE4 DONE %.1fs",dt)
    out={"time":now(),"total_s":round(dt,1),"results":RESULTS}
    with open("probe4_results.json","w",encoding="utf-8") as f:
        json.dump(out,f,indent=2,ensure_ascii=False)
    log.info("Saved probe4_results.json")
    for p in ("WB","OZON","AVITO"):
        ok=sum(1 for k,v in RESULTS.items() if k.startswith(p+":") and v["ok"])
        tot=sum(1 for k in RESULTS if k.startswith(p+":"))
        log.info("  %-6s %d/%d OK",p,ok,tot)

if __name__=="__main__":
    main()

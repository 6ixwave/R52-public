"""Probe3: WB selling+Playwright, Ozon Playwright HTML dump, Avito category dump."""
import json, logging, re, sys, time
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("P3")
RESULTS = {}
def rec(p,s,ok,d,dt):
    RESULTS[f"{p}:{s}"]={"ok":ok,"detail":str(d)[:300],"s":round(dt,1)}
    log.info("[%s] %s %s (%.1fs) %s",p,s,"OK" if ok else "FAIL",dt,str(d)[:120])
def now(): return datetime.now(timezone(timedelta(hours=10))).isoformat()

# ============================================================
# WB: selling field + Playwright HTML
# ============================================================
def wb_probe():
    nm = 1097975275
    vol, part = nm//100000, nm//1000
    log.info("=== WB ===")
    from curl_cffi import requests as cr

    # W1: Parse selling from card.json
    t0=time.time()
    try:
        url=f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json"
        r=cr.get(url,impersonate="chrome",timeout=10,headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"})
        if r.status_code==200:
            d=r.json()
            selling=d.get("selling",{})
            rec("WB","selling",True,f"selling={json.dumps(selling,ensure_ascii=False)[:200]}",time.time()-t0)
            # Also check full_colors, colors for price hints
            fc=d.get("full_colors",[])
            colors=d.get("colors",[])
            rec("WB","colors",True,f"full_colors[0:2]={json.dumps(fc[:2],ensure_ascii=False)[:150]} colors[0:2]={json.dumps(colors[:2],ensure_ascii=False)[:150]}",time.time()-t0)
            # media
            media=d.get("media",{})
            rec("WB","media",True,f"media_keys={list(media.keys())[:5]} photos={len(media.get('photos',[]))}",time.time()-t0)
    except Exception as e:
        rec("WB","selling",False,str(e)[:100],time.time()-t0)

    # W2: Playwright HTML (curl gets 498, browser might not)
    t0=time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br=pw.chromium.launch(headless=True,args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx=br.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",viewport={"width":1920,"height":1080},locale="ru-RU")
            pg=ctx.new_page()
            pg.goto(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",wait_until="domcontentloaded",timeout=25000)
            time.sleep(4)
            html=pg.content()
            # Dump
            with open("wb_pw_html.html","w",encoding="utf-8") as f: f.write(html)
            # Extract prices
            prices=[]
            # og:price
            m=re.search(r'og:price:amount[^>]*content=["\']([\d.]+)',html,re.I)
            if m: prices.append(f"og={m.group(1)}")
            # JSON-LD
            for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>',html,re.S):
                try:
                    ld=json.loads(m.group(1))
                    if isinstance(ld,dict) and "offers" in ld:
                        prices.append(f"ld={json.dumps(ld['offers'],ensure_ascii=False)[:150]}")
                except: pass
            # Embedded state
            m=re.search(r'"price"\s*:\s*\{[^}]*"basic"\s*:\s*(\d+)',html)
            if m: prices.append(f"state_basic={m.group(1)}")
            m=re.search(r'"basic"\s*:\s*(\d+)',html)
            if m: prices.append(f"basic={m.group(1)}")
            # Visible text
            for m in re.finditer(r'>(\d[\d\s]{2,10})\s*(?:₽|руб)',html[:100000]):
                amt=m.group(1).replace(" ","")
                if len(amt)>=3: prices.append(f"vis={amt}₽"); break
            # data attributes
            for pat in [r'data-price=["\']?([\d.]+)',r'data-cost=["\']?([\d.]+)',r'"priceU"\s*:\s*"?(\d+)']:
                m=re.search(pat,html)
                if m: prices.append(f"attr:{pat[:15]}={m.group(1)}")
            rec("WB","pw_html",len(prices)>0,f"len={len(html)} prices={prices[:6]}",time.time()-t0)
            br.close()
    except Exception as e:
        rec("WB","pw_html",False,str(e)[:150],time.time()-t0)

    # W3: basket CDN — try info/ru/ with different vol/part calc
    t0=time.time()
    try:
        # Sometimes the path uses different vol/part
        for host_n in [42, 1, 10, 20, 30, 50]:
            host=f"basket-{host_n:02d}.wbbasket.ru"
            # Try: /vol{vol}/part{part}/{nm}/info/ru/card.json
            url=f"https://{host}/vol{vol}/part{part}/{nm}/info/ru/card.json"
            r=cr.get(url,impersonate="chrome",timeout=5,headers={"Accept":"application/json"})
            if r.status_code==200:
                d=r.json()
                # Check if this host has price info
                selling=d.get("selling",{})
                rec("WB",f"host_{host_n:02d}",True,f"host={host} selling={json.dumps(selling,ensure_ascii=False)[:100]}",time.time()-t0)
                break
        else:
            rec("WB","host_scan",False,"only 42 worked or none",time.time()-t0)
    except Exception as e:
        rec("WB","host_scan",False,str(e)[:80],time.time()-t0)

    # W4: Try WB price via mobile API or app endpoint
    t0=time.time()
    try:
        endpoints=[
            f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm}&curr=rub&dest=-1257786",
            f"https://www.wildberries.ru/web/api/catalog/cards/v3/detail?nm={nm}&curr=rub",
            f"https://app.wildberries.ru/cards/v2/detail?nm={nm}&curr=rub",
            f"https://m.wildberries.ru/catalog/{nm}/detail.aspx",
        ]
        for i,url in enumerate(endpoints):
            r=cr.get(url,impersonate="chrome",timeout=8,headers={"Accept":"application/json, text/html","Referer":"https://www.wildberries.ru/","Origin":"https://www.wildberries.ru"})
            if r.status_code==200:
                ct=r.headers.get("content-type","")
                if "json" in ct:
                    d=r.json()
                    prods=d.get("products") or d.get("data",{}).get("items",[])
                    if prods:
                        sz=prods[0].get("sizes",[])
                        pr=sz[0].get("price",{}) if sz else {}
                        rec("WB",f"api_{i}",True,f"url={url[:60]} price={pr}",time.time()-t0)
                        break
                else:
                    # HTML response
                    prices=[]
                    m=re.search(r'og:price:amount[^>]*content=["\']([\d.]+)',r.text,re.I)
                    if m: prices.append(m.group(1))
                    m=re.search(r'(\d[\d\s]{2,10})\s*₽',r.text[:50000])
                    if m: prices.append(m.group(1).strip())
                    rec("WB",f"api_{i}",len(prices)>0,f"url={url[:60]} ct={ct[:30]} prices={prices[:3]}",time.time()-t0)
                    break
            elif r.status_code not in (403,498):
                continue
        else:
            rec("WB","api_scan",False,f"all {len(endpoints)} endpoints failed",time.time()-t0)
    except Exception as e:
        rec("WB","api_scan",False,str(e)[:80],time.time()-t0)


# ============================================================
# OZON: Playwright HTML dump + deep price search
# ============================================================
def ozon_probe():
    log.info("=== OZON ===")
    from curl_cffi import requests as cr
    oid="3481320800"

    t0=time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br=pw.chromium.launch(headless=True,args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx=br.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",viewport={"width":1920,"height":1080},locale="ru-RU")
            pg=ctx.new_page()
            pg.goto(f"https://www.ozon.ru/product/{oid}/",wait_until="domcontentloaded",timeout=25000)
            time.sleep(5)
            html=pg.content()
            with open("ozon_pw_html.html","w",encoding="utf-8") as f: f.write(html)
            log.info("Ozon HTML dumped: %d bytes",len(html))

            # Deep price search
            prices=[]
            # 1. og:price
            for m in re.finditer(r'og:price[^>]*content=["\']([\d.]+)',html,re.I):
                prices.append(f"og={m.group(1)}")
            # 2. JSON-LD
            for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>',html,re.S):
                try:
                    ld=json.loads(m.group(1))
                    s=json.dumps(ld,ensure_ascii=False)
                    if "price" in s.lower() or "offers" in s.lower():
                        prices.append(f"ld={s[:200]}")
                except: pass
            # 3. window.__STATE__ or similar
            for pat in [r'window\.__STATE__\s*=\s*(\{.{100,5000}?\})\s*[;]',
                        r'window\.__INITIAL_STATE__\s*=\s*(\{.{100,5000}?\})\s*[;]',
                        r'"priceU"\s*:\s*"?([\d.]+)"?',
                        r'"priceD"\s*:\s*"?([\d.]+)"?',
                        r'"oldPrice"\s*:\s*"?([\d.]+)"?',
                        r'"amount"\s*:\s*"?([\d.]+)"?',
                        r'"price"\s*:\s*\{[^}]*"value"\s*:\s*"?([\d.]+)"?',
                        r'"price"\s*:\s*"?([\d.]+)"?']:
                m=re.search(pat,html,re.S)
                if m: prices.append(f"re:{pat[:20]}={m.group(1)[:50]}")
            # 4. Visible text with ₽
            for m in re.finditer(r'>(\d[\d\s]{2,10})\s*(?:₽|руб\.?)\s*<',html[:150000]):
                amt=m.group(1).replace(" ","")
                if 100<int(amt)<10000000:
                    prices.append(f"vis={amt}₽")
                    break
            # 5. data attributes
            for pat in [r'data-price=["\']?([\d.]+)',r'data-amount=["\']?([\d.]+)']:
                m=re.search(pat,html)
                if m: prices.append(f"data:{m.group(1)}")
            # 6. Search for "262" (known price from search)
            if "262" in html:
                idx=html.index("262")
                prices.append(f"ctx_262=...{html[max(0,idx-50):idx+50]}...")
            # 7. Check if it's still a challenge
            fab="fab_chlg" in html or "Antibot" in html or "challenge" in html[:3000].lower()
            rec("OZON","pw_deep",len(prices)>0,f"len={len(html)} fab={fab} prices={prices[:8]}",time.time()-t0)
            br.close()
    except Exception as e:
        rec("OZON","pw_deep",False,str(e)[:200],time.time()-t0)

    # O2: Try Ozon with different product (cheaper, simpler)
    t0=time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br=pw.chromium.launch(headless=True,args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx=br.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",viewport={"width":1920,"height":1080},locale="ru-RU")
            pg=ctx.new_page()
            oid2="761659557"
            pg.goto(f"https://www.ozon.ru/product/{oid2}/",wait_until="domcontentloaded",timeout=25000)
            time.sleep(4)
            html=pg.content()
            prices=[]
            for m in re.finditer(r'og:price[^>]*content=["\']([\d.]+)',html,re.I):
                prices.append(f"og={m.group(1)}")
            for m in re.finditer(r'>(\d[\d\s]{2,10})\s*₽\s*<',html[:100000]):
                amt=m.group(1).replace(" ","")
                if 50<int(amt)<1000000: prices.append(f"vis={amt}₽"); break
            fab="fab_chlg" in html or "Antibot" in html
            rec("OZON","pw_id2",len(prices)>0,f"oid={oid2} len={len(html)} fab={fab} prices={prices[:5]}",time.time()-t0)
            br.close()
    except Exception as e:
        rec("OZON","pw_id2",False,str(e)[:150],time.time()-t0)


# ============================================================
# AVITO: category HTML dump + find real ad URLs
# ============================================================
def avito_probe():
    log.info("=== AVITO ===")
    from curl_cffi import requests as cr
    s=cr.Session(impersonate="chrome")

    # A1: Category page — dump and find URLs
    t0=time.time()
    try:
        r=s.get("https://www.avito.ru/moskva/elektronika",timeout=15,
                headers={"Accept":"text/html","Accept-Language":"ru-RU,ru;q=0.9","Referer":"https://www.avito.ru/"})
        log.info("Avito cat: HTTP %d len=%d",r.status_code,len(r.text))
        if r.status_code==200:
            with open("avito_cat.html","w",encoding="utf-8") as f: f.write(r.text)
            # Find ALL href patterns
            hrefs=re.findall(r'href="([^"]*ad[^"]*)"',r.text,re.I)
            # Also try /мск/ pattern
            hrefs2=re.findall(r'href="([^"]*/\d{8,12}[^"]*)"',r.text)
            # data attributes
            data_ids=re.findall(r'data-id=["\']?(\d{8,12})',r.text)
            # JSON embedded
            json_ids=re.findall(r'"id"\s*:\s*(\d{9,12})',r.text)
            rec("AVITO","cat_dump",True,f"len={len(r.text)} hrefs_ad={hrefs[:5]} hrefs_num={hrefs2[:5]} data_ids={data_ids[:5]} json_ids={json_ids[:5]}",time.time()-t0)
    except Exception as e:
        rec("AVITO","cat_dump",False,str(e)[:100],time.time()-t0)

    # A2: Try specific known category with different URL
    t0=time.time()
    try:
        urls=[
            "https://www.avito.ru/moskva/telefony",
            "https://www.avito.ru/moskva/elektronika/telefony-i-smartfony",
            "https://www.avito.ru/search?q=iphone&city=1",
        ]
        for url in urls:
            r=s.get(url,timeout=12,headers={"Accept":"text/html","Referer":"https://www.avito.ru/"})
            if r.status_code==200:
                hrefs=re.findall(r'href="([^"]*(?:/ad/|/мск/)[^"]*"\d{8,12}[^"]*)"',r.text,re.I)
                hrefs2=re.findall(r'/(\d{9,12})"',r.text)
                data_ids=re.findall(r'data-ad-id=["\']?(\d+)',r.text)
                if hrefs or hrefs2 or data_ids:
                    rec("AVITO",f"url_{url.split('/')[-1][:20]}",True,f"HTTP 200 hrefs={hrefs[:3]} nums={hrefs2[:5]} data={data_ids[:3]}",time.time()-t0)
                    break
            time.sleep(1)
        else:
            rec("AVITO","url_scan",False,f"tried {len(urls)} urls",time.time()-t0)
    except Exception as e:
        rec("AVITO","url_scan",False,str(e)[:80],time.time()-t0)

    # A3: Playwright for Avito (main page 439, maybe browser handles it)
    t0=time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br=pw.chromium.launch(headless=True,args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx=br.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",viewport={"width":1920,"height":1080},locale="ru-RU")
            pg=ctx.new_page()
            pg.goto("https://www.avito.ru/moskva/elektronika",wait_until="domcontentloaded",timeout=20000)
            time.sleep(4)
            html=pg.content()
            with open("avito_pw_cat.html","w",encoding="utf-8") as f: f.write(html)
            # Find ad links
            ad_links=re.findall(r'href="([^"]*(?:/ad/|/мск/)[^"]*"\d{8,12}[^"]*)"',html,re.I)
            ad_ids=re.findall(r'/ad/(\d{8,12})',html)
            data_ids=re.findall(r'data-ad-id=["\']?(\d+)',html)
            # Also look for JSON state
            state_m=re.search(r'window\.__STATE__\s*=\s*(\{.{100,10000}?\})\s*[;]',html,re.S)
            rec("AVITO","pw_cat",len(ad_links)+len(ad_ids)+len(data_ids)>0,
                f"len={len(html)} ad_links={ad_links[:3]} ad_ids={ad_ids[:5]} data={data_ids[:5]} state={bool(state_m)}",time.time()-t0)
            br.close()
    except Exception as e:
        rec("AVITO","pw_cat",False,str(e)[:150],time.time()-t0)


def main():
    t0=time.time()
    log.info("PROBE3 START %s",now())
    wb_probe()
    ozon_probe()
    avito_probe()
    dt=time.time()-t0
    log.info("PROBE3 DONE %.1fs",dt)
    out={"time":now(),"total_s":round(dt,1),"results":RESULTS}
    with open("probe3_results.json","w",encoding="utf-8") as f:
        json.dump(out,f,indent=2,ensure_ascii=False)
    log.info("Saved probe3_results.json")
    for p in ("WB","OZON","AVITO"):
        ok=sum(1 for k,v in RESULTS.items() if k.startswith(p+":") and v["ok"])
        tot=sum(1 for k in RESULTS if k.startswith(p+":"))
        log.info("  %-6s %d/%d OK",p,ok,tot)

if __name__=="__main__":
    main()

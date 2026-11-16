r"""
diag21.py — WB price + Avito live ad + Ozon Playwright
Run: C:\Python315\python.exe -u diag21.py
"""
import json, re, time, logging, sys, os
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=10))
OUT = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12"
RESULT = os.path.join(OUT, "diag21_result.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(OUT, "diag21_log.txt"), mode="w", encoding="utf-8"),
    ],
)
log = logging.getLogger("diag21")

result = {"ts": datetime.now(TZ).isoformat()}

# ============================================================
# WB: price via card.wb.ru / www.wildberries.ru (curl_cffi)
# ============================================================
log.info("=" * 60)
log.info("WB: price endpoints (curl_cffi chrome)")
log.info("=" * 60)

from curl_cffi import requests as cffi_req

WB_NM = "1097975275"
wb_results = {}

wb_urls = [
    ("card_wb_shot", f"https://card.wb.ru/cards/v2/detail.shot?nm={WB_NM}"),
    ("card_wb_v2", f"https://card.wb.ru/cards/v2/detail?nm={WB_NM}"),
    ("card_wb_v1", f"https://card.wb.ru/cards/detail?nm={WB_NM}"),
    ("www_wb_v2", f"https://www.wildberries.ru/cards/v2/detail?nm={WB_NM}"),
    ("www_wb_shot", f"https://www.wildberries.ru/cards/v2/detail.shot?nm={WB_NM}"),
    ("static_basket", f"https://static-basket-42.wbbasket.ru/vol10979/part1097975/1097975275/info/ru/card.json"),
]

for tag, url in wb_urls:
    try:
        r = cffi_req.get(url, impersonate="chrome", timeout=12)
        body = r.text[:2000]
        wb_results[tag] = {"status": r.status_code, "len": len(r.text), "body_head": body}
        log.info(f"WB [{tag}]: HTTP {r.status_code}, len={len(r.text)}")
        if r.status_code == 200 and "price" in r.text.lower():
            log.info(f"WB [{tag}]: PRICE FOUND!")
            try:
                data = r.json()
                wb_results[tag]["json_keys"] = list(data.keys()) if isinstance(data, dict) else f"list[{len(data)}]"
                wb_results[tag]["json_head"] = json.dumps(data, ensure_ascii=False)[:1500]
            except Exception as e:
                wb_results[tag]["json_err"] = str(e)
        time.sleep(1.5)
    except Exception as e:
        wb_results[tag] = {"status": "ERR", "err": str(e)}
        log.info(f"WB [{tag}]: ERR {e}")
        time.sleep(1)

result["wb"] = wb_results

# ============================================================
# AVITO: parse search → extract IDs → try live ads
# ============================================================
log.info("=" * 60)
log.info("AVITO: search parse + live ad hunt")
log.info("=" * 60)

avito_results = {}

# Step 1: fetch search page
try:
    r = cffi_req.get(
        "https://www.avito.ru/moskva/search?query=iphone",
        impersonate="chrome",
        timeout=15,
    )
    avito_results["search"] = {"status": r.status_code, "len": len(r.text)}
    log.info(f"AVITO search: HTTP {r.status_code}, len={len(r.text)}")
    html = r.text

    # Extract ad IDs: patterns
    # 1) data-ad-id="123456"
    ids_data = re.findall(r'data-ad-id=["\'](\d{6,})["\']', html)
    # 2) /moskva/.../123456789 (URL pattern)
    ids_url = re.findall(r'/moskva/[a-z0-9\-]+/(\d{6,})', html)
    # 3) "adId":123456 or "id":123456 in JSON
    ids_json = re.findall(r'"(?:adId|ad_id|id)"[:\s]+(\d{7,})', html)
    # 4) href with numeric ending
    ids_href = re.findall(r'href="[^"]*?/(\d{7,})[^"]*?"', html)

    all_ids = list(dict.fromkeys(ids_data + ids_url + ids_json + ids_href))
    avito_results["search"]["ids_found"] = len(all_ids)
    avito_results["search"]["ids_sample"] = all_ids[:10]
    log.info(f"AVITO search: IDs found={len(all_ids)}, sample={all_ids[:5]}")

    # Step 2: try first 5 unique IDs
    live_ad = None
    for i, ad_id in enumerate(all_ids[:5]):
        ad_url = f"https://www.avito.ru/moskva/{ad_id}"
        try:
            ra = cffi_req.get(ad_url, impersonate="chrome", timeout=12)
            avito_results[f"ad_{i}_{ad_id}"] = {"status": ra.status_code, "len": len(ra.text)}
            log.info(f"AVITO ad {ad_id}: HTTP {ra.status_code}, len={len(ra.text)}")
            if ra.status_code == 200 and len(ra.text) > 5000:
                # Extract JSON-LD
                ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', ra.text, re.DOTALL)
                preloaded = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>', ra.text, re.DOTALL)
                og_price = re.search(r'property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', ra.text)
                og_title = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', ra.text)
                
                ad_info = {"id": ad_id, "status": ra.status_code}
                if ld_blocks:
                    ad_info["ld_count"] = len(ld_blocks)
                    for lb in ld_blocks[:2]:
                        try:
                            ld = json.loads(lb.strip())
                            ad_info[f"ld_{len(ad_info)}"] = json.dumps(ld, ensure_ascii=False)[:800]
                        except:
                            ad_info[f"ld_raw_{len(ad_info)}"] = lb.strip()[:400]
                if preloaded:
                    ad_info["preloaded_head"] = preloaded.group(1)[:600]
                if og_price:
                    ad_info["og_price"] = og_price.group(1)
                if og_title:
                    ad_info["og_title"] = og_title.group(1)
                
                avito_results[f"ad_{i}_{ad_id}"]["extracted"] = ad_info
                live_ad = ad_info
                log.info(f"AVITO ad {ad_id}: LIVE! price={og_price.group(1) if og_price else 'N/A'}")
                break
            time.sleep(2)
        except Exception as e:
            avito_results[f"ad_{i}_{ad_id}"] = {"status": "ERR", "err": str(e)}
            log.info(f"AVITO ad {ad_id}: ERR {e}")
            time.sleep(1)

    avito_results["live_ad"] = live_ad
except Exception as e:
    avito_results["error"] = str(e)
    log.info(f"AVITO: ERR {e}")

result["avito"] = avito_results

# ============================================================
# OZON: Playwright product page
# ============================================================
log.info("=" * 60)
log.info("OZON: Playwright product page")
log.info("=" * 60)

ozon_results = {}
OZON_URL = "https://www.ozon.ru/product/2067947177/"

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = ctx.new_page()
        
        # Intercept network to catch API calls
        api_captured = []
        def on_response(resp):
            url = resp.url
            if any(k in url for k in ["composer-api", "pdp", "api/entrypoint", "price"]):
                try:
                    body = resp.text()
                    api_captured.append({"url": url[:200], "status": resp.status, "len": len(body), "head": body[:500]})
                except:
                    api_captured.append({"url": url[:200], "status": resp.status, "len": 0})
        page.on("response", on_response)

        log.info(f"OZON: navigating to {OZON_URL}")
        page.goto(OZON_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)  # wait for JS render

        # Get page title
        title = page.title()
        ozon_results["title"] = title
        log.info(f"OZON: title={title}")

        # Try to extract from DOM
        price_el = page.query_selector("[data-widget='Price'] .price__current, .tile-layout__brick--price .price__current, [class*='price'] [class*='current']")
        if price_el:
            ozon_results["dom_price"] = price_el.inner_text()
            log.info(f"OZON: DOM price={ozon_results['dom_price']}")

        # Try window.__INITIAL_STATE__ or similar
        state = page.evaluate("""
            () => {
                if (window.__INITIAL_STATE__) return JSON.stringify(window.__INITIAL_STATE__).substring(0, 3000);
                if (window.__NEXT_DATA__) return JSON.stringify(window.__NEXT_DATA__).substring(0, 3000);
                if (window.__NUXT__) return JSON.stringify(window.__NUXT__).substring(0, 3000);
                return null;
            }
        """)
        if state:
            ozon_results["state_head"] = state[:2000]
            log.info(f"OZON: state found, len={len(state)}")

        # Get meta tags
        metas = page.evaluate("""
            () => {
                const ms = document.querySelectorAll('meta[property], meta[name]');
                const out = {};
                ms.forEach(m => {
                    const k = m.getAttribute('property') || m.getAttribute('name');
                    const v = m.getAttribute('content');
                    if (k && v) out[k] = v.substring(0, 200);
                });
                return out;
            }
        """)
        ozon_results["metas"] = metas
        log.info(f"OZON: metas={json.dumps(metas, ensure_ascii=False)[:500]}")

        # Check for challenge/bot page
        body_html = page.content()
        ozon_results["body_len"] = len(body_html)
        if "challenge" in body_html.lower() or "antibot" in body_html.lower():
            ozon_results["bot_challenge"] = True
            log.info("OZON: BOT CHALLENGE detected")
        else:
            ozon_results["bot_challenge"] = False

        # API captures
        ozon_results["api_captured"] = api_captured[:5]
        log.info(f"OZON: API calls captured={len(api_captured)}")

        browser.close()

except Exception as e:
    ozon_results["error"] = str(e)
    log.info(f"OZON: ERR {e}")

result["ozon"] = ozon_results

# ============================================================
# Save
# ============================================================
with open(RESULT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
log.info(f"Result → {RESULT}")
log.info("DONE")

#!/usr/bin/env python3
"""diag_ali_pw49.py — AliExpress region + full MTOP capture.
Goal: get FULL product data (price, SKU, stock) by:
  1. Capturing entire MTOP body (not 3000-char preview)
  2. Trying US region (us.aliexpress.com)
  3. Trying global (www.aliexpress.com, no gatewayAdapt)
  4. Parsing full MTOP JSON for price/sku/stock
  5. Testing second product for comparison
"""

import json, re, time, traceback, sys
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
PRODUCT_ID   = "1005011813323763"
PRODUCT_ID2  = "1005007597544632"  # second product from recommendations
OUT_DIR      = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
OUT_JSON     = OUT_DIR / "diag_ali_pw49_result.json"
OUT_LOG      = OUT_DIR / "diag_ali_pw49_log.txt"
PROFILE      = "pw_ali_profile_pw49"

# ── Logging ─────────────────────────────────────────────────────────
log_lines = []
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    log_lines.append(line)
    print(line, flush=True)
    try:
        OUT_LOG.write_text("\n".join(log_lines), encoding="utf-8")
    except Exception:
        pass

# ── MTOP parser ─────────────────────────────────────────────────────
def parse_mtop_body(body_str):
    """Parse full MTOP JSONP/JSON body into structured dict."""
    out = {"raw_len": len(body_str), "parsed": False, "data": {}}
    try:
        # Strip JSONP wrapper: mtopjsonp1({...})
        m = re.match(r'\s*\w+\((\{.*\})\)\s*$', body_str, re.DOTALL)
        if m:
            body_str = m.group(1)
        parsed = json.loads(body_str)
        out["parsed"] = True
        out["api"] = parsed.get("api", "")
        out["ret"] = parsed.get("ret", [])
        data = parsed.get("data", {})
        result = data.get("result", data)

        # GLOBAL_DATA
        gd = result.get("GLOBAL_DATA", {}).get("globalData", {})
        out["data"]["bigBossBan"] = gd.get("bigBossBan", False)
        out["data"]["errorCode"] = gd.get("errorCode", "")
        out["data"]["banTip"] = gd.get("bigBossBanTip", "")

        # PRODUCT_DATA
        pd = result.get("PRODUCT_DATA", {})
        if pd:
            out["data"]["product"] = pd

        # SKU_DATA
        sd = result.get("SKU_DATA", {})
        if sd:
            out["data"]["sku"] = sd

        # PRICE
        price_data = result.get("PRICE_DATA", result.get("PRICE", {}))
        if price_data:
            out["data"]["price"] = price_data

        # SELL_POINT
        sp = result.get("SELL_POINT", {})
        if sp:
            out["data"]["sellPoint"] = sp

        # ALL top-level keys in result
        out["data"]["result_keys"] = list(result.keys())

        # Try to find price in any nested structure
        price_found = []
        def find_prices(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in ("price", "salePrice", "originalPrice", "minPrice", "maxPrice",
                                     "formattedPrice", "priceText", "sellPrice") and isinstance(v, (str, int, float)):
                        price_found.append(f"{path}.{k} = {v}")
                    elif k.lower() in ("price", "priceData", "priceModule") and isinstance(v, dict):
                        find_prices(v, f"{path}.{k}")
                    else:
                        find_prices(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:20]):
                    find_prices(item, f"{path}[{i}]")
        find_prices(result)
        out["data"]["prices_found"] = price_found[:30]

        # Find SKU info
        sku_found = []
        def find_skus(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in ("skuId", "sku_id", "skuAmount", "stock", "quantity", "available") and isinstance(v, (str, int, float, bool)):
                        sku_found.append(f"{path}.{k} = {v}")
                    elif k.lower() in ("skuList", "skuArray", "skus", "skuData") and isinstance(v, (list, dict)):
                        find_skus(v, f"{path}.{k}")
                    else:
                        find_skus(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:10]):
                    find_skus(item, f"{path}[{i}]")
        find_skus(result)
        out["data"]["sku_found"] = sku_found[:30]

    except Exception as e:
        out["parse_error"] = str(e)
        out["raw_preview"] = body_str[:500]
    return out

# ── Region test ─────────────────────────────────────────────────────
def test_region(ctx, page, region_name, base_url, product_id, cookies_to_set=None):
    """Navigate to product in given region, capture MTOP."""
    log(f"  Region [{region_name}]: {base_url}")
    captured_mtop = []

    def on_resp(resp):
        try:
            url = resp.url
            if "mtop.aliexpress.pdp" in url.lower() or "mtop.aliexpress.pdp.pc.query" in url.lower():
                body = resp.text()
                captured_mtop.append({
                    "url": url[:500],
                    "status": resp.status,
                    "body_len": len(body),
                    "body": body,  # FULL body
                })
                log(f"    MTOP: {resp.status} len={len(body)}")
        except Exception:
            pass

    page.on("response", on_resp)

    # Set region cookies before navigation
    if cookies_to_set:
        for c in cookies_to_set:
            try:
                ctx.add_cookies([c])
            except Exception:
                pass

    try:
        page.goto(f"{base_url}/item/{product_id}.html", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(15000)  # 15s for CSR render + MTOP
    except Exception as e:
        log(f"    goto error: {e}")

    # Extract DOM basics
    dom = {}
    try:
        dom["final_url"] = page.url
        dom["title"] = page.title()
        dom["html_len"] = len(page.content())
        # h1
        try:
            dom["h1"] = page.locator("h1").first.inner_text(timeout=2000).strip()
        except Exception:
            dom["h1"] = ""
        # Price elements
        prices = set()
        for sel in ["[class*='price']", "[class*='Price']", ".pdp-price",
                     "[data-role='price']", "[class*='sku-price']", "[class*='sale-price']",
                     "[class*='price-current']", "[class*='price-original']"]:
            try:
                for el in page.locator(sel).all()[:10]:
                    txt = el.inner_text(timeout=800).strip()
                    if txt and re.search(r'[\d.,]+', txt) and len(txt) < 100:
                        prices.add(txt)
            except Exception:
                pass
        dom["prices"] = list(prices)
        # Images
        imgs = []
        try:
            for el in page.locator("img").all()[:30]:
                src = el.get_attribute("src") or el.get_attribute("data-src") or ""
                if "ae-pic" in src or "aliexpress-media" in src:
                    imgs.append(src)
        except Exception:
            pass
        dom["images_count"] = len(imgs)
        # TMD
        tmd = 0
        for fr in page.frames:
            u = (fr.url or "").lower()
            if "tmd" in u or "punish" in u:
                tmd += 1
        dom["tmd_iframes"] = tmd
    except Exception as e:
        dom["error"] = str(e)

    page.remove_listener("response", on_resp)

    # Parse MTOP
    mtop_parsed = []
    for mt in captured_mtop:
        parsed = parse_mtop_body(mt["body"])
        parsed["url"] = mt["url"]
        parsed["status"] = mt["status"]
        mtop_parsed.append(parsed)
        # Save full body to file for inspection
        safe_id = product_id[-8:]
        mtop_file = OUT_DIR / f"mtop_{region_name}_{safe_id}.json"
        mtop_file.write_text(mt["body"], encoding="utf-8")
        log(f"    MTOP saved → {mtop_file.name} ({len(mt['body'])} bytes)")

    return {"dom": dom, "mtop": mtop_parsed, "mtop_count": len(captured_mtop)}

# ── Main ────────────────────────────────────────────────────────────
def main():
    from playwright.sync_api import sync_playwright

    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "product_id": PRODUCT_ID,
        "product_id2": PRODUCT_ID2,
        "phase": "init",
        "regions": {},
        "final_data": {},
        "errors": [],
    }

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(OUT_DIR / PROFILE),
            headless=False,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ── Warmup on US homepage ──
        log("Warmup: us.aliexpress.com")
        try:
            page.goto("https://us.aliexpress.com", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            log(f"  cookies: {len(ctx.cookies())}")
        except Exception as e:
            log(f"  warmup error: {e}")

        # ── Test 1: US region, product 1 ──
        log("=== TEST 1: US region, product 1 ===")
        us_cookies = [
            {"name": "aep_usuc_f", "value": "ae_u_m=undefined&&ae_u_s=2&&ae_us_uc_f=us-EN-US-USD-15&&ae_us_f=us-EN-US-USD-15", "domain": ".aliexpress.com", "path": "/", "secure": True},
            {"name": "intl_locale", "value": "en_US", "domain": ".aliexpress.com", "path": "/"},
            {"name": "aep_country", "value": "US", "domain": ".aliexpress.com", "path": "/"},
        ]
        r1 = test_region(ctx, page, "US", "https://us.aliexpress.com", PRODUCT_ID, us_cookies)
        result["regions"]["us_p1"] = r1

        # ── Test 2: Global (www), product 1 ──
        log("=== TEST 2: Global (www), product 1 ===")
        # Clear IT-specific cookies
        for c in ctx.cookies():
            if c["name"] in ("aep_usuc_f", "intl_locale", "aep_country"):
                pass  # keep, will overwrite
        global_cookies = [
            {"name": "aep_usuc_f", "value": "ae_u_m=undefined&&ae_u_s=2&&ae_us_uc_f=us-EN-US-USD-15&&ae_us_f=us-EN-US-USD-15", "domain": ".aliexpress.com", "path": "/", "secure": True},
            {"name": "intl_locale", "value": "en_US", "domain": ".aliexpress.com", "path": "/"},
        ]
        r2 = test_region(ctx, page, "global", "https://www.aliexpress.com", PRODUCT_ID, global_cookies)
        result["regions"]["global_p1"] = r2

        # ── Test 3: US region, product 2 ──
        log("=== TEST 3: US region, product 2 ===")
        r3 = test_region(ctx, page, "US_p2", "https://us.aliexpress.com", PRODUCT_ID2, us_cookies)
        result["regions"]["us_p2"] = r3

        # ── Test 4: IT region (control), product 1 ──
        log("=== TEST 4: IT region (control), product 1 ===")
        it_cookies = [
            {"name": "aep_usuc_f", "value": "ae_u_m=undefined&&ae_u_s=2&&ae_us_uc_f=it-IT-IT-EUR-15&&ae_us_f=it-IT-IT-EUR-15", "domain": ".aliexpress.com", "path": "/", "secure": True},
            {"name": "intl_locale", "value": "it_IT", "domain": ".aliexpress.com", "path": "/"},
            {"name": "aep_country", "value": "IT", "domain": ".aliexpress.com", "path": "/"},
        ]
        r4 = test_region(ctx, page, "IT", "https://it.aliexpress.com", PRODUCT_ID, it_cookies)
        result["regions"]["it_p1"] = r4

        # ── Merge best data ──
        log("Merging best data…")
        best = {"title": "", "price": "", "currency": "", "stock": "",
                "category": "", "seller": "", "images": [], "sku_count": 0,
                "source": "", "region": ""}

        for rname, rdata in result["regions"].items():
            mtops = rdata.get("mtop", [])
            for mt in mtops:
                if not mt.get("parsed"):
                    continue
                d = mt.get("data", {})
                # Title from PRODUCT_DATA
                pd = d.get("product", {})
                if isinstance(pd, dict):
                    title = pd.get("title", pd.get("subject", pd.get("name", "")))
                    if isinstance(title, dict):
                        title = title.get("displayTitle", title.get("seoTitle", ""))
                    if title and len(title) > len(best["title"]):
                        best["title"] = title
                        best["region"] = rname
                # Price
                pf = d.get("prices_found", [])
                for p in pf:
                    if best["price"] == "":
                        m = re.search(r'= ?([\d.,]+)', str(p))
                        if m and float(m.group(1).replace(",", "")) > 0.5:
                            best["price"] = m.group(1)
                            best["currency"] = "USD" if "US" in rname else "EUR"
                            best["source"] = f"mtop_{rname}"
                # SKU
                sf = d.get("sku_found", [])
                if sf:
                    for s in sf:
                        if "amount" in s.lower() or "stock" in s.lower() or "quantity" in s.lower():
                            best["stock"] = s.split("=")[-1].strip()
                            break
                # Images from DCData or PRODUCT_DATA
                imgs = pd.get("imagePathList", pd.get("images", [])) if isinstance(pd, dict) else []
                if isinstance(imgs, list):
                    for img in imgs[:15]:
                        if img and img not in best["images"]:
                            best["images"].append(img)

            # DOM fallback
            dom = rdata.get("dom", {})
            if dom.get("h1") and len(dom["h1"]) > len(best["title"]):
                best["title"] = dom["h1"]
                best["source"] = f"dom_{rname}"
            if dom.get("prices") and not best["price"]:
                for p in dom["prices"]:
                    m = re.search(r'([\d.,]+)', p)
                    if m and float(m.group(1).replace(",", "")) > 0.5:
                        best["price"] = m.group(1)
                        best["currency"] = "USD" if "US" in rname else "EUR"
                        best["source"] = f"dom_{rname}"
                        break

        best["sku_count"] = len([s for s in result["regions"].get("us_p1", {}).get("mtop", [{}])[0].get("data", {}).get("sku_found", [])]) if result["regions"].get("us_p1") else 0
        result["final_data"] = best
        result["phase"] = "done"

        # ── Save ──
        OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        sz = OUT_JSON.stat().st_size
        log(f"Saved → {OUT_JSON} ({sz} bytes)")

        # ── Summary ──
        log("=== FINAL ===")
        log(f"  title:    {best['title'][:100]}")
        log(f"  price:    {best['price']} {best['currency']}")
        log(f"  stock:    {best['stock'][:60]}")
        log(f"  images:   {len(best['images'])}")
        log(f"  region:   {best['region']}")
        log(f"  source:   {best['source']}")
        for rname, rdata in result["regions"].items():
            mtop_lens = [m.get("body_len", 0) for m in rdata.get("mtop", [])]
            log(f"  [{rname}] mtop_count={rdata.get('mtop_count',0)}, lens={mtop_lens}, "
                f"tmd={rdata['dom'].get('tmd_iframes',0)}, prices={rdata['dom'].get('prices',[])}")

        ctx.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(f"FATAL:\n{traceback.format_exc()}")
        sys.exit(1)

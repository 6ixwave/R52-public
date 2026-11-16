#!/usr/bin/env python3
"""diag_ali_pw50.py — Fix pw49: unique MTOP filenames, US redirect handling, crash fix."""

import json, re, time, traceback, sys
from datetime import datetime, timezone
from pathlib import Path

PRODUCT_ID   = "1005011813323763"
PRODUCT_ID2  = "1005007597544632"
OUT_DIR      = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
OUT_JSON     = OUT_DIR / "diag_ali_pw50_result.json"
OUT_LOG      = OUT_DIR / "diag_ali_pw50_log.txt"
PROFILE      = "pw_ali_profile_pw50"

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

def parse_mtop_body(body_str):
    """Parse full MTOP JSONP/JSON body."""
    out = {"raw_len": len(body_str), "parsed": False, "data": {}}
    try:
        m = re.match(r'\s*\w+\((\{.*\})\)\s*$', body_str, re.DOTALL)
        if m:
            body_str = m.group(1)
        parsed = json.loads(body_str)
        out["parsed"] = True
        out["api"] = parsed.get("api", "")
        out["ret"] = parsed.get("ret", [])
        data = parsed.get("data", {})
        result = data.get("result", data)

        gd = result.get("GLOBAL_DATA", {}).get("globalData", {})
        out["data"]["bigBossBan"] = gd.get("bigBossBan", False)
        out["data"]["errorCode"] = gd.get("errorCode", "")
        out["data"]["banTip"] = gd.get("bigBossBanTip", "")

        pd = result.get("PRODUCT_DATA", {})
        if pd:
            out["data"]["product"] = pd
        sd = result.get("SKU_DATA", {})
        if sd:
            out["data"]["sku"] = sd
        price_data = result.get("PRICE_DATA", result.get("PRICE", {}))
        if price_data:
            out["data"]["price"] = price_data
        sp = result.get("SELL_POINT", {})
        if sp:
            out["data"]["sellPoint"] = sp

        out["data"]["result_keys"] = list(result.keys())

        # Deep price search
        price_found = []
        def find_prices(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in ("price","salePrice","originalPrice","minPrice","maxPrice",
                                     "formattedPrice","priceText","sellPrice","displayPrice") \
                       and isinstance(v, (str, int, float)):
                        price_found.append(f"{path}.{k} = {v}")
                    elif isinstance(v, (dict, list)):
                        find_prices(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:20]):
                    find_prices(item, f"{path}[{i}]")
        find_prices(result)
        out["data"]["prices_found"] = price_found[:40]

        sku_found = []
        def find_skus(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in ("skuId","sku_id","skuAmount","stock","quantity","available",
                                     "skuStock","totalStock") and isinstance(v, (str,int,float,bool)):
                        sku_found.append(f"{path}.{k} = {v}")
                    elif k.lower() in ("skuList","skuArray","skus","skuData","skuModule") \
                       and isinstance(v, (list, dict)):
                        find_skus(v, f"{path}.{k}")
                    else:
                        find_skus(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:10]):
                    find_skus(item, f"{path}[{i}]")
        find_skus(result)
        out["data"]["sku_found"] = sku_found[:40]

        # Title
        title = ""
        if isinstance(pd, dict):
            t = pd.get("title", pd.get("subject", pd.get("name", "")))
            if isinstance(t, dict):
                title = t.get("displayTitle", t.get("seoTitle", ""))
            elif isinstance(t, str):
                title = t
        out["data"]["title"] = title

    except Exception as e:
        out["parse_error"] = str(e)
        out["raw_preview"] = body_str[:500]
    return out

def test_region(ctx, page, region_name, base_url, product_id, cookies_to_set=None, wait_ms=18000):
    """Navigate to product, capture ALL MTOP responses with unique filenames."""
    log(f"  Region [{region_name}]: {base_url}")
    captured_mtop = []
    mtop_idx = 0

    def on_resp(resp):
        nonlocal mtop_idx
        try:
            url = resp.url
            if "mtop.aliexpress.pdp" not in url.lower():
                return
            body = resp.text()
            mtop_idx += 1
            captured_mtop.append({
                "url": url[:500],
                "status": resp.status,
                "body_len": len(body),
                "body": body,
                "idx": mtop_idx,
            })
            log(f"    MTOP[{mtop_idx}]: {resp.status} len={len(body)}")
        except Exception:
            pass

    page.on("response", on_resp)

    if cookies_to_set:
        for c in cookies_to_set:
            try:
                ctx.add_cookies([c])
            except Exception:
                pass

    try:
        page.goto(f"{base_url}/item/{product_id}.html", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(wait_ms)
    except Exception as e:
        log(f"    goto error: {e}")

    dom = {}
    try:
        dom["final_url"] = page.url
        dom["title"] = page.title()
        dom["html_len"] = len(page.content())
        try:
            dom["h1"] = page.locator("h1").first.inner_text(timeout=2000).strip()
        except Exception:
            dom["h1"] = ""
        prices = set()
        for sel in ["[class*='price']","[class*='Price']",".pdp-price",
                     "[data-role='price']","[class*='sku-price']","[class*='sale-price']",
                     "[class*='price-current']","[class*='price-original']",
                     "[class*='Price--']","[class*='price--']"]:
            try:
                for el in page.locator(sel).all()[:12]:
                    txt = el.inner_text(timeout=800).strip()
                    if txt and re.search(r'[\d.,]+', txt) and len(txt) < 100:
                        prices.add(txt)
            except Exception:
                pass
        dom["prices"] = list(prices)
        imgs = []
        try:
            for el in page.locator("img").all()[:30]:
                src = el.get_attribute("src") or el.get_attribute("data-src") or ""
                if "ae-pic" in src or "aliexpress-media" in src:
                    imgs.append(src)
        except Exception:
            pass
        dom["images_count"] = len(imgs)
        tmd = 0
        for fr in page.frames:
            u = (fr.url or "").lower()
            if "tmd" in u or "punish" in u:
                tmd += 1
        dom["tmd_iframes"] = tmd
    except Exception as e:
        dom["error"] = str(e)

    page.remove_listener("response", on_resp)

    # Parse + save each MTOP with unique name
    mtop_parsed = []
    safe_id = product_id[-8:]
    for mt in captured_mtop:
        parsed = parse_mtop_body(mt["body"])
        parsed["url"] = mt["url"]
        parsed["status"] = mt["status"]
        mtop_parsed.append(parsed)
        mtop_file = OUT_DIR / f"mtop_pw50_{region_name}_{safe_id}_{mt['idx']}.json"
        mtop_file.write_text(mt["body"], encoding="utf-8")
        log(f"    MTOP saved → {mtop_file.name} ({len(mt['body'])} bytes)")

    return {"dom": dom, "mtop": mtop_parsed, "mtop_count": len(captured_mtop)}

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

        # Warmup: www first (most stable)
        log("Warmup: www.aliexpress.com")
        try:
            page.goto("https://www.aliexpress.com", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            log(f"  cookies: {len(ctx.cookies())}")
        except Exception as e:
            log(f"  warmup error: {e}")

        # ── TEST 1: Global (www) ──
        log("=== TEST 1: Global (www), product 1 ===")
        g_cookies = [
            {"name": "aep_usuc_f", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
            {"name": "intl_locale", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/"},
        ]
        r1 = test_region(ctx, page, "global", "https://www.aliexpress.com", PRODUCT_ID, g_cookies)
        result["regions"]["global_p1"] = r1

        # ── TEST 2: US ──
        log("=== TEST 2: US, product 1 ===")
        us_cookies = [
            {"name": "aep_usuc_f", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
            {"name": "intl_locale", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/"},
            {"name": "aep_country", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/"},
        ]
        r2 = test_region(ctx, page, "us", "https://us.aliexpress.com", PRODUCT_ID, us_cookies, wait_ms=20000)
        result["regions"]["us_p1"] = r2

        # ── TEST 3: IT (control) ──
        log("=== TEST 3: IT, product 1 ===")
        it_cookies = [
            {"name": "aep_usuc_f", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
            {"name": "intl_locale", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/"},
            {"name": "aep_country", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/"},
        ]
        r3 = test_region(ctx, page, "it", "https://it.aliexpress.com", PRODUCT_ID, it_cookies)
        result["regions"]["it_p1"] = r3

        # ── TEST 4: US, product 2 ──
        log("=== TEST 4: US, product 2 ===")
        r4 = test_region(ctx, page, "us_p2", "https://us.aliexpress.com", PRODUCT_ID2, us_cookies, wait_ms=20000)
        result["regions"]["us_p2"] = r4

        # ── Merge ──
        log("Merging best data…")
        best = {"title": "", "price": "", "currency": "", "stock": "",
                "category": "", "seller": "", "images": [], "sku_count": 0,
                "source": "", "region": "", "mtop_ban": False, "ban_tip": ""}

        for rname, rdata in result["regions"].items():
            mtops = rdata.get("mtop", [])
            for mt in mtops:
                if not mt.get("parsed"):
                    continue
                d = mt.get("data", {})

                # Ban check
                if d.get("bigBossBan"):
                    best["mtop_ban"] = True
                    best["ban_tip"] = d.get("banTip", "")
                    log(f"  [{rname}] BAN: {d.get('errorCode')} — {d.get('banTip','')[:60]}")

                # Title
                t = d.get("title", "")
                if t and len(t) > len(best["title"]):
                    best["title"] = t
                    best["region"] = rname
                    best["source"] = f"mtop_{rname}"

                # Price
                pf = d.get("prices_found", [])
                for p in pf:
                    if best["price"] == "":
                        m = re.search(r'= ?([\d.,]+)', str(p))
                        if m:
                            try:
                                val = float(m.group(1).replace(",", ""))
                                if val > 0.5:
                                    best["price"] = m.group(1)
                                    best["currency"] = "USD" if "us" in rname.lower() or "global" in rname else "EUR"
                                    best["source"] = f"mtop_{rname}"
                                    break
                            except ValueError:
                                pass

                # SKU / Stock
                sf = d.get("sku_found", [])
                for s in sf:
                    sl = s.lower()
                    if any(k in sl for k in ("amount","stock","quantity","available")):
                        best["stock"] = s.split("=")[-1].strip()
                        break

                # Images from product data
                pd = d.get("product", {})
                if isinstance(pd, dict):
                    imgs = pd.get("imagePathList", pd.get("images", []))
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
                    if m:
                        try:
                            val = float(m.group(1).replace(",", ""))
                            if val > 0.5:
                                best["price"] = m.group(1)
                                best["currency"] = "USD" if "us" in rname.lower() else "EUR"
                                best["source"] = f"dom_{rname}"
                                break
                        except ValueError:
                            pass

        # SKU count from largest MTOP
        all_skus = []
        for rname, rdata in result["regions"].items():
            for mt in rdata.get("mtop", []):
                all_skus.extend(mt.get("data", {}).get("sku_found", []))
        best["sku_count"] = len(all_skus)

        result["final_data"] = best
        result["phase"] = "done"

        OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        sz = OUT_JSON.stat().st_size
        log(f"Saved → {OUT_JSON} ({sz} bytes)")

        log("=== FINAL ===")
        log(f"  title:    {best['title'][:100]}")
        log(f"  price:    {best['price']} {best['currency']}")
        log(f"  stock:    {best['stock'][:60]}")
        log(f"  images:   {len(best['images'])}")
        log(f"  region:   {best['region']}")
        log(f"  source:   {best['source']}")
        log(f"  ban:      {best['mtop_ban']} — {best['ban_tip'][:60]}")
        for rname, rdata in result["regions"].items():
            mtop_lens = [m.get("raw_len", 0) for m in rdata.get("mtop", [])]
            log(f"  [{rname}] mtop={rdata.get('mtop_count',0)}, lens={mtop_lens}, "
                f"tmd={rdata['dom'].get('tmd_iframes',0)}, prices={rdata['dom'].get('prices',[])}")

        ctx.close()
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(f"FATAL:\n{traceback.format_exc()}")
        sys.exit(1)

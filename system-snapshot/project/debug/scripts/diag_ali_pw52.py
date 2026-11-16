#!/usr/bin/env python3
"""
diag_ali_pw52.py — AliExpress PDP: обход TMD/reCAPTCHA + надёжный перехват MTOP
Исправления vs pw51:
  - resp.body() + decode (вместо resp.text() который молча падал)
  - После reCAPTCHA: reload + ожидание нового PDP с SUCCESS
  - DOM: IT-селекторы, __INIT_DATA__, JSON-LD
  - Скриншоты для отладки
"""
import json, re, time, os, sys, traceback
from pathlib import Path
from datetime import datetime

BASE = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
PROFILE = BASE / "pw_ali_profile_pw52"
RESULT = BASE / "diag_ali_pw52_result.json"
LOG = BASE / "diag_ali_pw52_log.txt"
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"

log_lines = []
def log(msg):
    ts = datetime.now().strftime("[%H:%M:%S]")
    line = f"{ts} {msg}"
    log_lines.append(line)
    print(line, flush=True)

def save_log():
    LOG.write_text("\n".join(log_lines), encoding="utf-8")

def strip_jsonp(text):
    if not text:
        return None
    text = text.strip()
    m = re.match(r'^\s*mtopjsonp\d+\((.*)\)\s*$', text, re.DOTALL)
    if m:
        return m.group(1)
    if text.startswith("{"):
        return text
    return None

def parse_mtop_body(body_text):
    try:
        json_str = strip_jsonp(body_text)
        if not json_str:
            return None
        return json.loads(json_str)
    except Exception as e:
        log(f"  parse_mtop error: {e}")
        return None

def extract_pdp_data(mtop_json):
    if not mtop_json:
        return {}
    data = {}
    try:
        data["ret"] = mtop_json.get("ret", [])
        d = mtop_json.get("data", {})
        result = d.get("result", d)

        gd = result.get("GLOBAL_DATA", {})
        gdata = gd.get("globalData", gd)
        data["global"] = {
            "title": gdata.get("title", ""),
            "productId": gdata.get("productId", ""),
            "sellerId": gdata.get("sellerId", ""),
            "sellerName": gdata.get("sellerName", ""),
            "errorCode": gdata.get("errorCode", ""),
        }

        sku_data = result.get("SKU", result.get("sku", {}))
        skus = []
        if isinstance(sku_data, dict):
            sku_list = sku_data.get("skuPaths", sku_data.get("skuList", []))
            if isinstance(sku_list, list):
                for s in sku_list[:30]:
                    skus.append({
                        "skuId": str(s.get("skuId", s.get("id", ""))),
                        "price": s.get("price", s.get("salePrice", "")),
                        "stock": s.get("stock", s.get("quantity", -1)),
                    })
            sku_props = sku_data.get("skuProperties", sku_data.get("properties", []))
            data["sku_properties"] = sku_props[:10] if isinstance(sku_props, list) else []
        data["skus"] = skus
        data["sku_count"] = len(skus)

        price_info = result.get("PRICE", result.get("price", {}))
        if isinstance(price_info, dict):
            data["price"] = {
                "salePrice": price_info.get("salePrice", price_info.get("price", "")),
                "originalPrice": price_info.get("originalPrice", price_info.get("listPrice", "")),
                "currency": price_info.get("currency", price_info.get("currencyCode", "")),
                "formatPrice": price_info.get("formatPrice", price_info.get("displayPrice", "")),
            }

        imgs = result.get("IMAGE", result.get("image", result.get("images", [])))
        if isinstance(imgs, dict):
            imgs = imgs.get("imageList", imgs.get("images", []))
        if isinstance(imgs, list):
            data["images"] = [
                i if isinstance(i, str) else i.get("url", i.get("imageUrl", ""))
                for i in imgs[:20]
            ]

        ship = result.get("SHIPPING", result.get("shipping", result.get("delivery", {})))
        data["shipping"] = ship if isinstance(ship, dict) else str(ship)[:300]

        specs = result.get("SPECIFICATION", result.get("specification", result.get("attributes", {})))
        data["specs"] = specs if isinstance(specs, (dict, list)) else str(specs)[:500]

        stock = result.get("STOCK", result.get("stock", result.get("inventory", {})))
        data["stock"] = stock if isinstance(stock, (dict, int)) else str(stock)[:100]

    except Exception as e:
        data["extract_error"] = str(e)
    return data

def screenshot(page, name):
    try:
        path = BASE / f"diag_ali_pw52_{name}.png"
        page.screenshot(path=str(path))
        log(f"  Screenshot: {path.name}")
    except Exception as e:
        log(f"  Screenshot error: {e}")

def main():
    log(f"=== pw52: AliExpress TMD bypass + MTOP PDP (fixed) ===")
    log(f"Product: {PRODUCT_ID}")
    log(f"URL: {PRODUCT_URL}")

    result = {
        "product_id": PRODUCT_ID,
        "url": PRODUCT_URL,
        "timestamp": datetime.now().isoformat(),
        "phase": "start",
        "mtop_responses": [],
        "pdp_data": {},
        "dom_data": {},
        "tmd_handled": False,
        "recaptcha_solved": False,
        "errors": [],
    }

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            viewport={"width": 1366, "height": 900},
            locale="it-IT",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_https_errors=True,
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # --- Перехват MTOP (исправленный) ---
        mtop_captured = []

        def on_response(resp):
            try:
                url = resp.url
                if "mtop" not in url.lower():
                    return
                status = resp.status
                entry = {"url": url, "status": status}
                log(f"  MTOP: {status} {url[:120]}")
                try:
                    raw = resp.body(timeout=8000)
                    body = raw.decode("utf-8", errors="replace")
                    entry["body_len"] = len(body)
                    entry["body_head"] = body[:600]
                    # Сохранить полный файл для PDP и больших ответов
                    if len(body) > 300:
                        idx = len(mtop_captured) + 1
                        fname = BASE / f"mtop_pw52_{idx}.json"
                        fname.write_text(body, encoding="utf-8")
                        entry["saved_to"] = str(fname)
                        log(f"    saved → {fname.name} ({len(body)} B)")
                    mtop_captured.append(entry)
                except Exception as e:
                    entry["body_error"] = str(e)
                    log(f"    body_error: {e}")
                    mtop_captured.append(entry)
            except Exception:
                pass

        page.on("response", on_response)

        # --- Phase 1: Warm-up ---
        log("=== Phase 1: Warm-up ===")
        try:
            page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            cookies = browser.cookies()
            log(f"  Cookies: {len(cookies)}")
        except Exception as e:
            log(f"  Warm-up error: {e}")
            result["errors"].append(f"warmup: {e}")

        # --- Phase 2: Load product page ---
        log("=== Phase 2: Load product page ===")
        mtop_captured.clear()
        try:
            page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
            time.sleep(8)
            log(f"  Title: {page.title()[:100]}")
            log(f"  MTOP so far: {len(mtop_captured)}")
            screenshot(page, "after_load")
        except Exception as e:
            log(f"  Load error: {e}")
            result["errors"].append(f"load: {e}")

        # --- Phase 3: TMD detection ---
        log("=== Phase 3: TMD detection ===")
        tmd_found = False

        try:
            tmd_iframes = page.query_selector_all("iframe[src*='tmd'], iframe[src*='punish'], iframe[src*='recaptcha']")
            log(f"  TMD iframes: {len(tmd_iframes)}")
            for i, fr in enumerate(tmd_iframes):
                src = fr.get_attribute("src") or ""
                log(f"    [{i}] {src[:150]}")
                if "tmd" in src or "punish" in src or "recaptcha" in src:
                    tmd_found = True
        except Exception as e:
            log(f"  TMD detect error: {e}")

        mtop_tmd = False
        for m in mtop_captured:
            head = m.get("body_head", "")
            if "FAIL_SYS_USER_VALIDATE" in head or "RGV587" in head:
                mtop_tmd = True
                log(f"  MTOP TMD: {m['url'][:100]}")

        if tmd_found or mtop_tmd:
            log("  → TMD active")
            result["tmd_handled"] = True
        else:
            log("  → No TMD")

        # --- Phase 4: reCAPTCHA bypass ---
        if tmd_found or mtop_tmd:
            log("=== Phase 4: reCAPTCHA bypass ===")
            recaptcha_frame = None

            # Ищем Google reCAPTCHA frame (самый надёжный)
            try:
                for fr in page.frames:
                    fr_url = fr.url or ""
                    if "google.com/recaptcha" in fr_url:
                        recaptcha_frame = fr
                        log(f"  → Google reCAPTCHA frame: {fr_url[:120]}")
                        break
            except Exception as e:
                log(f"  Frame scan: {e}")

            # Fallback: TMD punish frame
            if not recaptcha_frame:
                try:
                    for fr in page.frames:
                        fr_url = fr.url or ""
                        if "punish" in fr_url or "tmd" in fr_url:
                            recaptcha_frame = fr
                            log(f"  → TMD frame: {fr_url[:120]}")
                            break
                except:
                    pass

            if recaptcha_frame:
                log("  Waiting for reCAPTCHA widget...")
                try:
                    recaptcha_frame.wait_for_selector(
                        "#recaptcha-anchor, .recaptcha-checkbox, input[type='checkbox']",
                        timeout=15000
                    )
                    time.sleep(1)

                    clicked = False
                    for sel in ["#recaptcha-anchor", ".recaptcha-checkbox", "input[type='checkbox']"]:
                        try:
                            el = recaptcha_frame.query_selector(sel)
                            if el:
                                el.click()
                                clicked = True
                                log(f"  → Clicked: {sel}")
                                break
                        except:
                            pass

                    if not clicked:
                        try:
                            recaptcha_frame.click("body", position={"x": 30, "y": 30})
                            clicked = True
                            log("  → Clicked at (30,30)")
                        except Exception as e2:
                            log(f"  Click error: {e2}")

                    if clicked:
                        log("  Waiting 20s for reCAPTCHA resolution...")
                        time.sleep(20)
                        screenshot(page, "after_recaptcha")

                        # Проверка sessionStorage
                        try:
                            ss_token = page.evaluate("() => sessionStorage.getItem('bx-recaptcha-token')")
                            log(f"  SS token: {str(ss_token)[:80] if ss_token else 'none'}")
                        except:
                            pass

                        result["recaptcha_solved"] = True
                    else:
                        log("  No checkbox found")
                        result["errors"].append("recaptcha: no checkbox")
                except Exception as e:
                    log(f"  reCAPTCHA error: {e}")
                    result["errors"].append(f"recaptcha: {e}")
            else:
                log("  reCAPTCHA frame NOT found")
                result["errors"].append("recaptcha: frame not found")

        # --- Phase 5: Reload + capture NEW PDP ---
        log("=== Phase 5: Reload & capture PDP ===")

        # Перезагружаем страницу — после TMD cookies должны пропустить
        mtop_captured.clear()
        try:
            page.reload(wait_until="domcontentloaded", timeout=45000)
            time.sleep(10)
            log(f"  After reload: {len(mtop_captured)} MTOP")
            screenshot(page, "after_reload")
        except Exception as e:
            log(f"  Reload error: {e}")
            result["errors"].append(f"reload: {e}")

        # Если мало MTOP — ещё ждём
        if len(mtop_captured) < 3:
            log("  Waiting more...")
            time.sleep(10)
            log(f"  Now: {len(mtop_captured)} MTOP")

        # Найти лучший PDP
        best_pdp = None
        best_pdp_len = 0
        for m in mtop_captured:
            url = m.get("url", "")
            if ("pdp" in url.lower() or "detail" in url.lower()) and "punish" not in url.lower():
                if m.get("status") == 200:
                    body = m.get("body_head", "")
                    blen = m.get("body_len", 0) or 0
                    # Успех: нет FAIL_SYS, есть data
                    if "FAIL_SYS" not in body and "RGV587" not in body:
                        if blen > best_pdp_len:
                            best_pdp = m
                            best_pdp_len = blen
                            log(f"  PDP candidate: len={blen} {url[:100]}")

        # Если не нашли — попробуем все PDP (даже с punish в URL, но без TMD в body)
        if not best_pdp:
            for m in mtop_captured:
                url = m.get("url", "")
                if "pdp" in url.lower():
                    body = m.get("body_head", "")
                    if "SUCCESS" in body or "globalData" in body:
                        best_pdp = m
                        log(f"  PDP (loose): {url[:100]}")
                        break

        # Разобрать PDP
        if best_pdp and best_pdp.get("saved_to"):
            try:
                full_body = Path(best_pdp["saved_to"]).read_text(encoding="utf-8")
                mtop_json = parse_mtop_body(full_body)
                if mtop_json:
                    result["pdp_data"] = extract_pdp_data(mtop_json)
                    log(f"  PDP extracted: {list(result['pdp_data'].keys())}")
                else:
                    result["pdp_data"]["raw_head"] = full_body[:500]
                    log(f"  PDP parse fail: {full_body[:200]}")
            except Exception as e:
                log(f"  PDP read error: {e}")
        elif best_pdp:
            # Может body_head достаточно
            log(f"  PDP no file, using body_head: {best_pdp.get('body_head','')[:200]}")
            mtop_json = parse_mtop_body(best_pdp.get("body_head", ""))
            if mtop_json:
                result["pdp_data"] = extract_pdp_data(mtop_json)

        # --- Phase 6: DOM extraction (улучшенный) ---
        log("=== Phase 6: DOM extraction ===")
        try:
            dom = {}

            # og: tags
            for prop in ["og:title", "og:image", "og:price:amount", "og:price:currency"]:
                el = page.query_selector(f'meta[property="{prop}"]')
                if el:
                    dom[prop] = el.get_attribute("content") or ""

            # Title
            title_el = page.query_selector("title")
            if title_el:
                dom["title_tag"] = title_el.inner_text()[:200]

            # Цена — много селекторов (IT + EN + generic)
            price_selectors = [
                ".price-current", ".price--current",
                "[class*='price-current']", "[class*='Price--current']",
                ".product-price", "[data-role='price']",
                ".ae-price-current", ".price-box .price",
                "[class*='price'] [class*='current']",
                ".sell-price", ".price-item--current",
                "[class*='PriceCurrent']", "[class*='price-current'] .price-text",
                ".pdp-price", ".price-wrap .price",
                "span[class*='price']", "div[class*='Price']",
            ]
            for sel in price_selectors:
                try:
                    el = page.query_selector(sel)
                    if el:
                        txt = el.inner_text().strip()
                        if txt and any(c.isdigit() for c in txt):
                            dom["price_dom"] = txt
                            dom["price_sel"] = sel
                            log(f"  Price [{sel}]: {txt}")
                            break
                except:
                    pass

            # Если не нашли — ищем по regex в HTML
            if "price_dom" not in dom:
                try:
                    html = page.content()
                    # Ищем паттерны цен: €12,34 / $12.34 / 12,34 €
                    price_matches = re.findall(r'[\d.,]+[\s]*[€$£¥]|€[\s]?[\d.,]+|\$[\s]?[\d.,]+', html[:50000])
                    if price_matches:
                        dom["price_regex"] = price_matches[:10]
                        log(f"  Price regex: {price_matches[:5]}")
                except:
                    pass

            # __INIT_DATA__ / window.__INIT_DATA__
            try:
                init_data = page.evaluate("""() => {
                    if (window.__INIT_DATA__) return JSON.stringify(window.__INIT_DATA__).substring(0, 5000);
                    if (window.__INITIAL_DATA__) return JSON.stringify(window.__INITIAL_DATA__).substring(0, 5000);
                    if (window.__NEXT_DATA__) return JSON.stringify(window.__NEXT_DATA__).substring(0, 5000);
                    return null;
                }""")
                if init_data:
                    dom["init_data_head"] = init_data[:2000]
                    log(f"  __INIT_DATA__: {init_data[:200]}")
            except Exception as e:
                log(f"  InitData error: {e}")

            # JSON-LD
            ld_els = page.query_selector_all('script[type="application/ld+json"]')
            for ld in ld_els:
                try:
                    ld_text = ld.inner_text()
                    ld_data = json.loads(ld_text)
                    if isinstance(ld_data, dict):
                        offers = ld_data.get("offers", {})
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        dom["jsonld"] = {
                            "name": ld_data.get("name", ""),
                            "price": offers.get("price", ""),
                            "currency": offers.get("priceCurrency", ""),
                            "sku": ld_data.get("sku", ""),
                            "mpn": ld_data.get("mpn", ""),
                            "image": ld_data.get("image", ""),
                            "availability": offers.get("availability", ""),
                        }
                        log(f"  JSON-LD: {dom['jsonld']}")
                        break
                except:
                    pass

            # Изображения
            imgs = page.query_selector_all("img[src*='alicdn'], img[src*='aliexpress'], img[src*='ae-pic']")
            dom["img_count"] = len(imgs)
            dom["images"] = []
            for img in imgs[:20]:
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                if src and "logo" not in src.lower() and "icon" not in src.lower() and "sprite" not in src.lower():
                    dom["images"].append(src)

            result["dom_data"] = dom
            log(f"  DOM: title={dom.get('og:title','')[:60]}, price={dom.get('price_dom', dom.get('price_regex','none'))}, imgs={dom.get('img_count',0)}")

        except Exception as e:
            log(f"  DOM error: {e}")
            result["errors"].append(f"dom: {e}")

        # --- Phase 7: Screenshot final ---
        screenshot(page, "final")

        # --- Собрать MTOP список ---
        result["mtop_responses"] = [
            {
                "url": m.get("url", "")[:200],
                "status": m.get("status"),
                "body_len": m.get("body_len"),
                "saved_to": m.get("saved_to", ""),
                "body_head": m.get("body_head", "")[:400],
                "body_error": m.get("body_error", ""),
            }
            for m in mtop_captured
        ]

        # --- Финал ---
        result["phase"] = "done"
        log(f"=== DONE ===")
        log(f"  MTOP total: {len(mtop_captured)}")
        log(f"  TMD: {result['tmd_handled']}, reCAPTCHA: {result['recaptcha_solved']}")
        log(f"  PDP keys: {list(result['pdp_data'].keys()) if result['pdp_data'] else 'empty'}")
        log(f"  DOM title: {result['dom_data'].get('og:title', '')[:80]}")
        log(f"  DOM price: {result['dom_data'].get('price_dom', result['dom_data'].get('price_regex', 'none'))}")
        log(f"  Errors: {result['errors']}")

        browser.close()

    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result: {RESULT} ({RESULT.stat().st_size} B)")
    save_log()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        log(traceback.format_exc())
        save_log()
        sys.exit(1)

#!/usr/bin/env python3
"""
diag_ali_pw51.py — AliExpress PDP: обход TMD/reCAPTCHA + перехват MTOP
Стратегия:
  1. Прогрев cookies на главной
  2. Загрузка страницы товара
  3. Перехват всех MTOP-ответов
  4. Если TMD → найти reCAPTCHA iframe → кликнуть чекбокс → ждать redirect
  5. После redirect перехватить успешный PDP-ответ
  6. DOM-фоллбэк: og_title, цена из HTML, изображения
  7. Сохранить результат в JSON
"""
import json, re, time, os, sys, traceback
from pathlib import Path
from datetime import datetime

BASE = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
PROFILE = BASE / "pw_ali_profile_pw51"
RESULT = BASE / "diag_ali_pw51_result.json"
LOG = BASE / "diag_ali_pw51_log.txt"
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
    """Убрать JSONP-обёртку mtopjsonpN(...) → вернуть JSON-строку."""
    if not text:
        return None
    text = text.strip()
    m = re.match(r'^\s*mtopjsonp\d+\((.*)\)\s*$', text, re.DOTALL)
    if m:
        return m.group(1)
    # Может быть просто JSON
    if text.startswith("{"):
        return text
    return None

def parse_mtop_body(body_text):
    """Разобрать тело MTOP-ответа → dict или None."""
    try:
        json_str = strip_jsonp(body_text)
        if not json_str:
            return None
        return json.loads(json_str)
    except Exception as e:
        log(f"  parse_mtop error: {e}")
        return None

def extract_pdp_data(mtop_json):
    """Извлечь товарные данные из успешного PDP-ответа."""
    if not mtop_json:
        return {}
    data = {}
    try:
        ret = mtop_json.get("ret", [])
        data["ret"] = ret
        d = mtop_json.get("data", {})
        
        # PDP структура: data.result.GLOBAL_DATA / data.result.SKU / etc.
        result = d.get("result", d)
        
        # GLOBAL_DATA
        gd = result.get("GLOBAL_DATA", {})
        gdata = gd.get("globalData", gd)
        data["global"] = {
            "title": gdata.get("title", ""),
            "productId": gdata.get("productId", ""),
            "sellerId": gdata.get("sellerId", ""),
            "sellerName": gdata.get("sellerName", ""),
            "categoryId": gdata.get("categoryId", ""),
            "categoryName": gdata.get("categoryName", ""),
            "errorCode": gdata.get("errorCode", ""),
        }
        
        # SKU / variants
        sku_data = result.get("SKU", result.get("sku", {}))
        skus = []
        if isinstance(sku_data, dict):
            sku_list = sku_data.get("skuPaths", sku_data.get("skuList", []))
            if isinstance(sku_list, list):
                for s in sku_list[:20]:
                    skus.append({
                        "skuId": s.get("skuId", s.get("id", "")),
                        "price": s.get("price", s.get("salePrice", "")),
                        "stock": s.get("stock", s.get("quantity", -1)),
                        "attrs": s.get("attrs", s.get("properties", "")),
                    })
            # SKU properties (варианты: цвет, размер)
            sku_props = sku_data.get("skuProperties", sku_data.get("properties", []))
            data["sku_properties"] = sku_props[:10] if isinstance(sku_props, list) else []
        data["skus"] = skus
        data["sku_count"] = len(skus)
        
        # Price
        price_info = result.get("PRICE", result.get("price", {}))
        if isinstance(price_info, dict):
            data["price"] = {
                "salePrice": price_info.get("salePrice", price_info.get("price", "")),
                "originalPrice": price_info.get("originalPrice", price_info.get("listPrice", "")),
                "currency": price_info.get("currency", price_info.get("currencyCode", "")),
                "formatPrice": price_info.get("formatPrice", price_info.get("displayPrice", "")),
            }
        
        # Images
        imgs = result.get("IMAGE", result.get("image", result.get("images", [])))
        if isinstance(imgs, dict):
            imgs = imgs.get("imageList", imgs.get("images", []))
        if isinstance(imgs, list):
            data["images"] = [
                i if isinstance(i, str) else i.get("url", i.get("imageUrl", ""))
                for i in imgs[:20]
            ]
        
        # Shipping / delivery
        ship = result.get("SHIPPING", result.get("shipping", result.get("delivery", {})))
        data["shipping"] = ship if isinstance(ship, dict) else str(ship)[:200]
        
        # Specs / attributes
        specs = result.get("SPECIFICATION", result.get("specification", result.get("attributes", {})))
        data["specs"] = specs if isinstance(specs, (dict, list)) else str(specs)[:500]
        
        # Stock
        stock = result.get("STOCK", result.get("stock", result.get("inventory", {})))
        data["stock"] = stock if isinstance(stock, (dict, int)) else str(stock)[:100]
        
    except Exception as e:
        data["extract_error"] = str(e)
    
    return data

def main():
    log(f"=== pw51: AliExpress TMD bypass + MTOP PDP ===")
    log(f"Product: {PRODUCT_ID}")
    log(f"URL: {PRODUCT_URL}")
    log(f"Profile: {PROFILE}")
    
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
            locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_https_errors=True,
        )
        
        page = browser.pages[0] if browser.pages else browser.new_page()
        
        # --- Перехват MTOP ---
        mtop_captured = []
        
        def on_response(resp):
            try:
                url = resp.url
                if "mtop" not in url.lower():
                    return
                status = resp.status
                log(f"  MTOP: {status} len=? {url[:120]}")
                entry = {"url": url, "status": status}
                try:
                    body = resp.text(timeout=5000)
                    entry["body_len"] = len(body)
                    entry["body_head"] = body[:500]
                    # Сохранить полные тела больших ответов
                    if len(body) > 200:
                        idx = len(mtop_captured) + 1
                        fname = BASE / f"mtop_pw51_{idx}.json"
                        fname.write_text(body, encoding="utf-8")
                        entry["saved_to"] = str(fname)
                    mtop_captured.append(entry)
                except Exception as e:
                    entry["body_error"] = str(e)
                    mtop_captured.append(entry)
            except Exception:
                pass
        
        page.on("response", on_response)
        
        # --- Шаг 1: Прогрев cookies ---
        log("=== Phase 1: Warm-up cookies ===")
        try:
            page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            cookies = browser.cookies()
            log(f"  Cookies after warm-up: {len(cookies)}")
        except Exception as e:
            log(f"  Warm-up error: {e}")
            result["errors"].append(f"warmup: {e}")
        
        # --- Шаг 2: Загрузка страницы товара ---
        log("=== Phase 2: Load product page ===")
        mtop_captured.clear()
        try:
            page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
            time.sleep(6)  # Дать MTOP запросам уйти
            log(f"  Page title: {page.title()[:100]}")
            log(f"  MTOP so far: {len(mtop_captured)}")
        except Exception as e:
            log(f"  Load error: {e}")
            result["errors"].append(f"load: {e}")
        
        # --- Шаг 3: Проверка TMD ---
        log("=== Phase 3: TMD / reCAPTCHA detection ===")
        tmd_found = False
        
        # Ищем TMD iframe или overlay
        try:
            tmd_iframes = page.query_selector_all("iframe[src*='tmd'], iframe[src*='punish'], iframe[src*='recaptcha']")
            log(f"  TMD iframes found: {len(tmd_iframes)}")
            for i, fr in enumerate(tmd_iframes):
                src = fr.get_attribute("src") or ""
                log(f"    [{i}] {src[:150]}")
                if "tmd" in src or "punish" in src or "recaptcha" in src:
                    tmd_found = True
        except Exception as e:
            log(f"  TMD detect error: {e}")
        
        # Также проверим body на TMD-маркеры
        try:
            body_html = page.content()[:5000]
            if "tmd" in body_html.lower() or "punish" in body_html.lower() or "recaptcha" in body_html.lower():
                tmd_found = True
                log("  TMD markers in body HTML")
        except:
            pass
        
        # Проверим MTOP-ответы на FAIL_SYS
        mtop_tmd = False
        for m in mtop_captured:
            head = m.get("body_head", "")
            if "FAIL_SYS_USER_VALIDATE" in head or "RGV587" in head:
                mtop_tmd = True
                log(f"  MTOP TMD block detected: {m['url'][:100]}")
        
        if tmd_found or mtop_tmd:
            log("  → TMD active, attempting reCAPTCHA bypass...")
            result["tmd_handled"] = True
        else:
            log("  → No TMD detected, checking MTOP for success...")
        
        # --- Шаг 4: Обход reCAPTCHA ---
        if tmd_found or mtop_tmd:
            log("=== Phase 4: reCAPTCHA bypass ===")
            
            # Ищем reCAPTCHA iframe (может быть вложенным)
            recaptcha_frame = None
            
            # Вариант A: прямой iframe с recaptcha
            try:
                frames = page.frames
                log(f"  Total frames: {len(frames)}")
                for fr in frames:
                    url = fr.url or ""
                    log(f"    frame: {url[:120]}")
                    if "recaptcha" in url.lower() or "punish" in url.lower() or "tmd" in url.lower():
                        recaptcha_frame = fr
                        log(f"  → Selected frame: {url[:120]}")
            except Exception as e:
                log(f"  Frame scan error: {e}")
            
            # Вариант B: ищем через content_frame
            if not recaptcha_frame:
                try:
                    for iframe_el in page.query_selector_all("iframe"):
                        src = iframe_el.get_attribute("src") or ""
                        if "recaptcha" in src.lower() or "punish" in src.lower():
                            try:
                                recaptcha_frame = iframe_el.content_frame()
                                if recaptcha_frame:
                                    log(f"  → content_frame from iframe: {src[:100]}")
                                    break
                            except:
                                pass
                except Exception as e:
                    log(f"  iframe scan error: {e}")
            
            # Вариант C: Google reCAPTCHA iframe (bframe)
            if not recaptcha_frame:
                try:
                    for fr in page.frames:
                        if "google.com/recaptcha" in (fr.url or ""):
                            recaptcha_frame = fr
                            log(f"  → Google reCAPTCHA frame: {fr.url[:100]}")
                            break
                except:
                    pass
            
            if recaptcha_frame:
                log("  reCAPTCHA frame found, waiting for widget...")
                try:
                    # Ждём появление чекбокса
                    recaptcha_frame.wait_for_selector(
                        "#recaptcha-anchor, .recaptcha-checkbox, input[type='checkbox'], #slider-anchor",
                        timeout=15000
                    )
                    time.sleep(1)
                    
                    # Клик по чекбоксу
                    clicked = False
                    for sel in ["#recaptcha-anchor", ".recaptcha-checkbox", "input[type='checkbox']", "#slider-anchor", ".recaptcha-checkbox-spinner-label"]:
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
                        # Попробовать координаты (чекбокс обычно слева сверху)
                        try:
                            recaptcha_frame.click("body", position={"x": 30, "y": 30})
                            log("  → Clicked at position (30,30)")
                            clicked = True
                        except Exception as e2:
                            log(f"  → Position click error: {e2}")
                    
                    if clicked:
                        log("  Waiting for reCAPTCHA resolution (30s)...")
                        time.sleep(15)
                        
                        # Проверка: появился ли токен или изменился URL
                        try:
                            ss_token = page.evaluate("() => sessionStorage.getItem('bx-recaptcha-token')")
                            log(f"  sessionStorage token: {str(ss_token)[:80] if ss_token else 'none'}")
                        except:
                            pass
                        
                        # Дождаться redirect
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=20000)
                            log(f"  After redirect URL: {page.url[:120]}")
                        except:
                            log("  No redirect detected, continuing...")
                        
                        result["recaptcha_solved"] = True
                        log("  reCAPTCHA: clicked and waited")
                    else:
                        log("  reCAPTCHA: could not find checkbox")
                        result["errors"].append("recaptcha: no checkbox found")
                        
                except Exception as e:
                    log(f"  reCAPTCHA error: {e}")
                    result["errors"].append(f"recaptcha: {e}")
            else:
                log("  reCAPTCHA frame NOT found")
                result["errors"].append("recaptcha: frame not found")
                
                # Попробовать: может TMD уже ушёл сам?
                time.sleep(5)
                log(f"  Page after wait: {page.url[:120]}")
        
        # --- Шаг 5: Перехват успешного MTOP PDP ---
        log("=== Phase 5: Capture PDP MTOP ===")
        
        # Если после TMD страница перезагрузилась — ждём новые MTOP
        time.sleep(5)
        
        # Если MTOP ещё не пришёл — попробуем reload
        pdp_found = False
        for m in mtop_captured:
            if "pdp" in m.get("url", "").lower() and m.get("status") == 200:
                head = m.get("body_head", "")
                if "FAIL_SYS" not in head and "RGV587" not in head:
                    pdp_found = True
                    log(f"  PDP MTOP success: {m['url'][:100]}")
        
        if not pdp_found:
            log("  PDP not found yet, trying reload...")
            try:
                mtop_captured.clear()
                page.reload(wait_until="domcontentloaded", timeout=45000)
                time.sleep(8)
                log(f"  After reload, MTOP: {len(mtop_captured)}")
            except Exception as e:
                log(f"  Reload error: {e}")
        
        # Найти лучший PDP-ответ
        best_pdp = None
        for m in mtop_captured:
            url = m.get("url", "")
            if "pdp" in url.lower() or "detail" in url.lower():
                if m.get("status") == 200:
                    body = m.get("body_head", "")
                    if "FAIL_SYS" not in body and "RGV587" not in body:
                        best_pdp = m
                        log(f"  Best PDP: len={m.get('body_len', '?')} {url[:100]}")
        
        # Если есть сохранённый файл — прочитай полный текст
        if best_pdp and best_pdp.get("saved_to"):
            try:
                full_body = Path(best_pdp["saved_to"]).read_text(encoding="utf-8")
                mtop_json = parse_mtop_body(full_body)
                if mtop_json:
                    result["pdp_data"] = extract_pdp_data(mtop_json)
                    log(f"  PDP data extracted: {list(result['pdp_data'].keys())}")
                else:
                    # Может быть TMD-HTML
                    if "<html" in full_body[:100].lower():
                        log("  PDP response is HTML (TMD page), not JSON")
                        result["pdp_data"]["note"] = "TMD HTML page"
                    else:
                        log(f"  PDP parse failed, head: {full_body[:200]}")
                        result["pdp_data"]["raw_head"] = full_body[:500]
            except Exception as e:
                log(f"  PDP file read error: {e}")
        
        # --- Шаг 6: DOM-фоллбэк ---
        log("=== Phase 6: DOM extraction ===")
        try:
            dom = {}
            # og:tag
            og_title = page.query_selector('meta[property="og:title"]')
            if og_title:
                dom["og_title"] = og_title.get_attribute("content") or ""
            og_img = page.query_selector('meta[property="og:image"]')
            if og_img:
                dom["og_image"] = og_img.get_attribute("content") or ""
            
            # Title
            title_el = page.query_selector("title")
            if title_el:
                dom["title_tag"] = title_el.inner_text()[:200]
            
            # Цена из DOM
            price_selectors = [
                ".price-current", ".price--current", "[class*='price-current']",
                ".product-price", "[data-role='price']", ".ae-price-current",
                "[class*='Price--current']", "[class*='price'] [class*='current']",
            ]
            for sel in price_selectors:
                try:
                    el = page.query_selector(sel)
                    if el:
                        txt = el.inner_text().strip()
                        if txt and any(c.isdigit() for c in txt):
                            dom["price_dom"] = txt
                            log(f"  Price from '{sel}': {txt}")
                            break
                except:
                    pass
            
            # Изображения
            imgs = page.query_selector_all("img[src*='alicdn'], img[src*='aliexpress']")
            dom["img_count"] = len(imgs)
            dom["images"] = []
            for img in imgs[:20]:
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                if src and "logo" not in src.lower() and "icon" not in src.lower():
                    dom["images"].append(src)
            
            # JSON-LD
            ld_els = page.query_selector_all('script[type="application/ld+json"]')
            for ld in ld_els:
                try:
                    ld_text = ld.inner_text()
                    ld_data = json.loads(ld_text)
                    if isinstance(ld_data, dict):
                        dom["jsonld"] = {
                            "name": ld_data.get("name", ""),
                            "price": ld_data.get("offers", {}).get("price", "") if isinstance(ld_data.get("offers"), dict) else "",
                            "currency": ld_data.get("offers", {}).get("priceCurrency", "") if isinstance(ld_data.get("offers"), dict) else "",
                            "sku": ld_data.get("sku", ""),
                            "image": ld_data.get("image", ""),
                        }
                        log(f"  JSON-LD: {dom['jsonld']}")
                        break
                except:
                    pass
            
            result["dom_data"] = dom
            log(f"  DOM: title={dom.get('og_title','')[:60]}, price={dom.get('price_dom','none')}, imgs={dom.get('img_count',0)}")
            
        except Exception as e:
            log(f"  DOM error: {e}")
            result["errors"].append(f"dom: {e}")
        
        # --- Шаг 7: Собрать MTOP-список ---
        result["mtop_responses"] = [
            {
                "url": m.get("url", "")[:200],
                "status": m.get("status"),
                "body_len": m.get("body_len"),
                "saved_to": m.get("saved_to", ""),
                "body_head": m.get("body_head", "")[:300],
            }
            for m in mtop_captured
        ]
        
        # --- Финал ---
        result["phase"] = "done"
        log(f"=== DONE ===")
        log(f"  MTOP total: {len(mtop_captured)}")
        log(f"  TMD handled: {result['tmd_handled']}")
        log(f"  reCAPTCHA solved: {result['recaptcha_solved']}")
        log(f"  PDP data keys: {list(result['pdp_data'].keys()) if result['pdp_data'] else 'empty'}")
        log(f"  DOM title: {result['dom_data'].get('og_title', '')[:80]}")
        log(f"  DOM price: {result['dom_data'].get('price_dom', 'none')}")
        log(f"  Errors: {result['errors']}")
        
        browser.close()
    
    # Сохранить
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result saved: {RESULT} ({RESULT.stat().st_size} bytes)")
    save_log()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        log(traceback.format_exc())
        save_log()
        sys.exit(1)

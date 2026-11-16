"""pw58: US-регион + gatewayAdapt + вложенный reCAPTCHA Enterprise + PDP MTOP."""
import asyncio, json, os, sys, time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(BASE, "pw_ali_profile_pw58")
OUT = os.path.join(BASE, "diag_ali_pw58_result.json")
PDP_OUT = os.path.join(BASE, "mtop_pw58_pdp_474088.json")

PRODUCT_ID = "1005010487474088"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html?gatewayAdapt=glo2usa"
HOME_URL = "https://www.aliexpress.com/"

US_COOKIES = [
    {"name": "aep_usuc_f", "value": "US|en_US|USD|10001", "domain": ".aliexpress.com", "path": "/", "secure": True},
    {"name": "_lang", "value": "en_US", "domain": ".aliexpress.com", "path": "/", "secure": True},
    {"name": "intl_locale", "value": "en_US", "domain": ".aliexpress.com", "path": "/", "secure": True},
    {"name": "xman_us_f", "value": "us_f=US&us_g=10001&us_l=en_US", "domain": ".aliexpress.com", "path": "/", "secure": True},
]

async def find_tmd_frame(page, log):
    """Находит TMD-iframe по URL-паттерну."""
    for frame in page.frames:
        url = frame.url.lower()
        if "_____tmd_____" in url or "punish" in url:
            return frame
    return None

async def solve_recaptcha_in_frame(tmd_frame, page, log):
    """
    В TMD-iframe ищем вложенный reCAPTCHA Enterprise iframe.
    Структура: page → tmd_frame → recaptcha_iframe → checkbox
    """
    log("Looking for reCAPTCHA inside TMD frame...")
    await asyncio.sleep(3)  # даём reCAPTCHA загрузиться

    # Шаг 1: найти reCAPTCHA iframe внутри TMD
    recaptcha_frame = None
    for child in tmd_frame.child_frames:
        if "recaptcha" in child.url.lower() or "google.com" in child.url.lower():
            recaptcha_frame = child
            log(f"Found reCAPTCHA frame: {child.url[:120]}")
            break

    if not recaptcha_frame:
        # Может быть вложен глубже или ещё грузится
        log("No reCAPTCHA child frame yet, waiting 5s and retrying...")
        await asyncio.sleep(5)
        for child in tmd_frame.child_frames:
            if "recaptcha" in child.url.lower() or "google.com" in child.url.lower():
                recaptcha_frame = child
                log(f"Found reCAPTCHA frame (retry): {child.url[:120]}")
                break

    if not recaptcha_frame:
        log("WARNING: reCAPTCHA frame not found. Checking all frames...")
        for f in page.frames:
            log(f"  frame: {f.url[:100]}")
        return "no_recaptcha_frame"

    # Шаг 2: внутри reCAPTCHA ищем чекбокс
    # reCAPTCHA Enterprise (size=normal) имеет:
    #   - span.recaptcha-checkbox / label / input[type=checkbox]
    #   - .rc-button (кнопка-чекбокс)
    #   - .recaptcha-checkbox
    clicked = False
    selectors = [
        ".rc-button",
        "span.recaptcha-checkbox",
        "label",
        "input[type='checkbox']",
        ".recaptcha-checkbox",
        "span[role='checkbox']",
        ".rcbxd",  # newer reCAPTCHA
    ]

    for sel in selectors:
        try:
            el = await recaptcha_frame.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                clicked = True
                log(f"Clicked '{sel}' in reCAPTCHA frame")
                break
        except Exception as e:
            log(f"  sel '{sel}': {e}")

    if not clicked:
        # Fallback: клик по координатам (чекбокс обычно слева, ~30px от края)
        try:
            box = await recaptcha_frame.bounding_box()
            if box:
                cx = box["x"] + 30
                cy = box["y"] + box["height"] / 2
                log(f"Clicking reCAPTCHA by coords: ({cx:.0f}, {cy:.0f})")
                await page.mouse.click(cx, cy)
                clicked = True
        except Exception as e:
            log(f"Coord click failed: {e}")

    if not clicked:
        log("WARNING: Could not click reCAPTCHA checkbox")
        return "no_click"

    # Шаг 3: ждём разрешение (checkbox → возможно картинка → auto-submit)
    log("Waiting 15s for reCAPTCHA to resolve...")
    await asyncio.sleep(15)

    # Проверка: исчез ли TMD?
    tmd_gone = True
    for f in page.frames:
        if "_____tmd_____" in f.url.lower() or "punish" in f.url.lower():
            tmd_gone = False
            break

    if tmd_gone:
        log("TMD frame gone! reCAPTCHA resolved.")
        return "resolved"
    else:
        log("TMD still present after reCAPTCHA click. Might need image challenge.")
        return "still_tmd"

async def handle_tmd_full(page, log, stage):
    """Полная обработка TMD: поиск → reCAPTCHA → wait → verify."""
    log(f"[{stage}] Checking for TMD...")
    for attempt in range(3):
        tmd_frame = await find_tmd_frame(page, log)
        if not tmd_frame:
            log(f"[{stage}] No TMD frame found")
            return False

        log(f"[{stage}] TMD frame (attempt {attempt+1}): {tmd_frame.url[:120]}")

        # Скриншот TMD
        try:
            await page.screenshot(path=os.path.join(BASE, f"diag_ali_pw58_tmd_{stage}_{attempt+1}.png"))
        except Exception:
            pass

        # Решаем reCAPTCHA
        status = await solve_recaptcha_in_frame(tmd_frame, page, log)
        log(f"[{stage}] reCAPTCHA status: {status}")

        if status == "resolved":
            await asyncio.sleep(5)
            return True

        if status == "still_tmd":
            # Может быть image challenge — ждём ещё
            log(f"[{stage}] Waiting 10s more (possible image challenge)...")
            await asyncio.sleep(10)
            tmd_frame2 = await find_tmd_frame(page, log)
            if not tmd_frame2:
                log(f"[{stage}] TMD resolved after extra wait!")
                return True

        await asyncio.sleep(5)

    log(f"[{stage}] TMD not resolved after 3 attempts")
    return False

async def main():
    log_lines = []
    def log(msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        log_lines.append(line)

    from playwright.async_api import async_playwright

    result = {
        "product_id": PRODUCT_ID,
        "url": PRODUCT_URL,
        "timestamp": datetime.now().isoformat(),
        "phase": "init",
        "region": "US",
        "gatewayAdapt": "glo2usa",
        "tmd_homepage": False,
        "tmd_product": False,
        "tmd_handled": False,
        "mtop_pdp_captured": False,
        "mtop_responses": [],
        "pdp_data": None,
        "dom_data": None,
        "errors": [],
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        # ── 1. US cookies ──
        log("Setting US cookies...")
        await page.context.add_cookies(US_COOKIES)
        await asyncio.sleep(1)

        # ── 2. Прогрев на главной (15 сек) ──
        log("Warming up: homepage (15s)...")
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"Homepage goto: {e}")

        # Ждём подзапросы, scroll
        await asyncio.sleep(5)
        try:
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(3)
            await page.evaluate("window.scrollBy(0, -800)")
            await asyncio.sleep(3)
            await page.evaluate("window.scrollBy(0, 400)")
            await asyncio.sleep(4)
        except Exception:
            pass
        log("Homepage warm-up done")

        # ── 3. TMD на главной ──
        tmd_home = await handle_tmd_full(page, log, "home")
        result["tmd_homepage"] = tmd_home
        if tmd_home:
            log("Homepage TMD resolved, waiting 5s...")
            await asyncio.sleep(5)

        # ── 4. Навигация на товар ──
        log(f"Navigating to: {PRODUCT_URL}")

        mtop_bodies = []

        async def on_response(response):
            try:
                url = response.url
                if "pdp.pc.query" in url:
                    body = await response.text()
                    mtop_bodies.append({
                        "url": url[:300],
                        "status": response.status,
                        "body_len": len(body),
                        "body_head": body[:500],
                        "body_full": body,
                    })
                    log(f"MTOP PDP CAPTURED! len={len(body)}")
            except Exception:
                pass

        page.on("response", on_response)

        try:
            await page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
            log("Product page DOM loaded")
        except Exception as e:
            log(f"Product goto: {e}")

        # Ждём MTOP
        log("Waiting 10s for MTOP...")
        await asyncio.sleep(10)

        # ── 5. TMD на товаре ──
        tmd_prod = await handle_tmd_full(page, log, "product")
        result["tmd_product"] = tmd_prod
        if tmd_prod:
            log("Product TMD resolved! Reloading...")
            await asyncio.sleep(3)
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                log("Reload done, waiting 12s for MTOP...")
                await asyncio.sleep(12)
            except Exception as e:
                log(f"Reload: {e}")

        # ── 6. Проверка MTOP ──
        result["mtop_pdp_captured"] = len(mtop_bodies) > 0
        result["mtop_responses"] = [
            {k: v for k, v in m.items() if k != "body_full"}
            for m in mtop_bodies
        ]

        if mtop_bodies:
            pdp_body = mtop_bodies[0]["body_full"]
            with open(PDP_OUT, "w", encoding="utf-8") as f:
                f.write(pdp_body)
            log(f"PDP saved → {PDP_OUT} ({len(pdp_body)} bytes)")

            try:
                raw = pdp_body.strip()
                if raw.startswith("mtopjsonp"):
                    s = raw.index("(")
                    e = raw.rindex(")")
                    raw = raw[s+1:e]
                data = json.loads(raw)
                res = data.get("data", {}).get("result", {})
                gd = res.get("GLOBAL_DATA", {}).get("globalData", {})

                result["pdp_data"] = {
                    "api": data.get("api"),
                    "ret": data.get("ret"),
                    "result_keys": list(res.keys()),
                    "globalData": {
                        "bigBossBan": gd.get("bigBossBan"),
                        "errorCode": gd.get("errorCode"),
                        "tip": gd.get("bigBossBanTip"),
                    },
                }

                # Цена
                ts = res.get("targetSkuPriceInfo", {})
                result["pdp_data"]["price"] = {
                    "salePriceString": ts.get("salePriceString"),
                    "originalPrice": ts.get("originalPrice"),
                    "currency": ts.get("currency"),
                    "salePriceLocal": ts.get("salePriceLocal"),
                }

                # Изображения
                hip = res.get("HEADER_IMAGE_PC", {})
                imgs = hip.get("currentSkuImages", [])
                result["pdp_data"]["images"] = {
                    "count": len(imgs),
                    "first_3": [i.get("imageUrl", "")[:100] for i in imgs[:3]],
                }

                # SKU
                sku = res.get("SKUModule", {})
                if isinstance(sku, dict):
                    paths = sku.get("skuPaths", [])
                    result["pdp_data"]["sku"] = {
                        "keys": list(sku.keys())[:15],
                        "paths_count": len(paths),
                    }

                # Категория
                result["pdp_data"]["category"] = {
                    "categoryPath": res.get("categoryPath"),
                    "category1": res.get("category1"),
                    "category3": res.get("category3"),
                }

                # Название
                result["pdp_data"]["title"] = res.get("title") or res.get("subject")

                # Доставка
                deliv = res.get("deliveryOptionPanelDisplayList", [])
                result["pdp_data"]["delivery_count"] = len(deliv) if isinstance(deliv, list) else 0

                log(f"PDP parsed. Keys: {result['pdp_data']['result_keys']}")
                log(f"  bigBossBan={gd.get('bigBossBan')}, err={gd.get('errorCode')}")
                log(f"  price={ts.get('salePriceString')}, imgs={len(imgs)}")
            except Exception as e:
                result["errors"].append(f"PDP parse: {e}")
                log(f"PDP parse error: {e}")
        else:
            log("No PDP MTOP. Trying DOM fallback...")
            try:
                title = await page.title()
                h1_el = await page.query_selector("h1")
                h1_text = (await h1_el.inner_text()).strip() if h1_el else None
                price_el = await page.query_selector(
                    "[class*='price'], [class*='Price'], .pdp-price"
                )
                price_text = (await price_el.inner_text()).strip() if price_el else None

                # JSON-LD
                jsonld = None
                try:
                    scripts = await page.query_selector_all("script[type='application/ld+json']")
                    for s in scripts:
                        txt = (await s.inner_text()).strip()
                        if txt and len(txt) > 50:
                            jsonld = txt[:2000]
                            break
                except Exception:
                    pass

                result["dom_data"] = {
                    "title": title,
                    "h1": h1_text,
                    "price_text": price_text,
                    "jsonld_head": jsonld[:500] if jsonld else None,
                }
                log(f"DOM: title={title[:80]!r}, h1={h1_text[:80] if h1_text else None!r}, price={price_text!r}")
            except Exception as e:
                result["errors"].append(f"DOM: {e}")

        # ── 7. Скриншот финальный ──
        try:
            await page.screenshot(path=os.path.join(BASE, "diag_ali_pw58_final.png"))
            log("Final screenshot saved")
        except Exception:
            pass

        # ── 8. Cookies после ──
        cookies = await page.context.cookies()
        result["cookies_after"] = {
            c["name"]: c["value"][:100]
            for c in cookies
            if c["name"] in ("aep_usuc_f", "_lang", "intl_locale", "xman_us_f", "country", "xman_us_t")
        }

        await browser.close()

    result["phase"] = "done"
    result["log"] = log_lines[-40:]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n[pw58] Done → {OUT}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())

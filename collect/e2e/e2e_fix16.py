"""e2e_fix16 v2: WB (Playwright, safe_content), Avito (Playwright DOM), SQLite verify.
Fix: page.content() retry + evaluate fallback when page still navigating.
"""
import json, re, sys, time, os, traceback
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=10))
def ts(): return datetime.now(TZ).strftime("%H:%M:%S")

BASE = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12"
DB_PATH = r"C:\Users\aleks\AppData\Local\R52\data\r52.sqlite3"

results = {"ts": datetime.now(TZ).isoformat(), "s": 0, "pass": 0, "d": {}}
t0 = time.monotonic()
print(f"[{ts()}] e2e_fix16 v2 start", flush=True)

from playwright.sync_api import sync_playwright

wb_data = {"title": None, "price_minor": None, "price_label": None, "html_len": 0, "status": "FAIL"}
avito_data = {"ad_id": None, "title": None, "price_minor": None, "price_label": None,
              "html_len": 0, "status": "FAIL", "images": []}

def safe_content(page, retries=10, delay=3):
    """Get page.content() with retry + evaluate fallback."""
    for i in range(retries):
        try:
            return page.content()
        except Exception as e:
            if i < retries - 1:
                print(f"    content() retry {i+1}/{retries}: {e}", flush=True)
                time.sleep(delay)
            else:
                # Last resort: evaluate outerHTML
                print(f"    content() all retries failed, trying evaluate...", flush=True)
                try:
                    return page.evaluate("() => document.documentElement.outerHTML")
                except Exception as e2:
                    print(f"    evaluate also failed: {e2}", flush=True)
                    raise
    return ""

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
        extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
    )

    # ==========================================================
    # WB
    # ==========================================================
    print(f"\n[{ts()}] === WB: Playwright ===", flush=True)
    page = ctx.new_page()
    NM = 1097975275
    wb_url = f"https://www.wildberries.ru/catalog/{NM}/detail.aspx"

    try:
        page.goto(wb_url, wait_until="domcontentloaded", timeout=40000)
        # Wait for JS to settle: try selector first, then networkidle
        try:
            page.wait_for_selector("h1, [data-auto='price'], .c-price", timeout=15000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        time.sleep(4)

        html = safe_content(page)
        wb_data["html_len"] = len(html)
        print(f"  WB page: len={len(html)} url={page.url}", flush=True)

        with open(os.path.join(BASE, "wb_page16.html"), "w", encoding="utf-8") as f:
            f.write(html)

        # DOM extraction
        dom_info = page.evaluate("""
            () => {
                const r = {};
                r.h1 = (document.querySelector('h1') || {}).textContent || null;
                r.ogTitle = (document.querySelector('meta[property="og:title"]') || {}).content || null;
                r.ogPrice = (document.querySelector('meta[property="og:price:amount"]') || {}).content || null;
                r.ogImage = (document.querySelector('meta[property="og:image"]') || {}).content || null;
                const sels = [
                    '[data-auto="price"]', '[data-testid="price"]',
                    '.c-price', '.w-price', '[class*="Price"]',
                    '[class*="price"]', '.price-value',
                    '[data-auto="product-price"]',
                ];
                r.selPrices = {};
                for (const sel of sels) {
                    try {
                        const els = document.querySelectorAll(sel);
                        if (els.length > 0) {
                            r.selPrices[sel] = Array.from(els).slice(0, 4).map(e =>
                                ({text: e.textContent.trim().substring(0, 100), html: e.outerHTML.substring(0, 200)}));
                        }
                    } catch(e) {}
                }
                r.priceTexts = [];
                try {
                    const walker = document.createTreeWalker(document.body, 4);
                    let node;
                    while (node = walker.nextNode()) {
                        const t = node.textContent.trim();
                        if (/^\\d[\\d\\s\\u00a0]{2,12}\\s*(?:\\u20bd|\\u20ac|руб)/.test(t) && t.length < 40) {
                            r.priceTexts.push(t);
                            if (r.priceTexts.length >= 15) break;
                        }
                    }
                } catch(e) {}
                r.scripts = [];
                const scripts = document.querySelectorAll('script:not([src])');
                for (const s of scripts) {
                    const t = s.textContent || '';
                    if (t.includes('"price"') || t.includes('"basic"') || t.includes('"priceU"')) {
                        r.scripts.push(t.substring(0, 800));
                    }
                    if (t.includes('__INITIAL_STATE__') || t.includes('__APP_DATA__') || t.includes('__NEXT_DATA__')) {
                        r.scripts.push('STATE: ' + t.substring(0, 800));
                    }
                    if (r.scripts.length >= 4) break;
                }
                return r;
            }
        """)
        print(f"  WB DOM: {json.dumps(dom_info, ensure_ascii=False)[:2500]}", flush=True)

        if dom_info:
            wb_data["title"] = dom_info.get("h1") or dom_info.get("ogTitle")
            if dom_info.get("ogPrice"):
                try:
                    wb_data["price_minor"] = int(round(float(dom_info["ogPrice"]) * 100))
                    wb_data["price_label"] = "og:price:amount"
                except (ValueError, TypeError):
                    pass
            if not wb_data["price_minor"]:
                for sel, els in (dom_info.get("selPrices") or {}).items():
                    if not isinstance(els, list):
                        continue
                    for el in els:
                        m = re.search(r'(\d[\d\s\u00a0]{2,12})\s*(?:\u20bd|руб)', el.get("text", ""))
                        if m:
                            val = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
                            wb_data["price_minor"] = int(val * 100)
                            wb_data["price_label"] = f"dom.{sel}"
                            break
                    if wb_data["price_minor"]:
                        break
            if not wb_data["price_minor"]:
                for pt in (dom_info.get("priceTexts") or []):
                    m = re.search(r'(\d[\d\s\u00a0]{2,12})\s*(?:\u20bd|руб)', pt)
                    if m:
                        val = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
                        wb_data["price_minor"] = int(val * 100)
                        wb_data["price_label"] = "dom.text_node"
                        break

        # In-page fetch to card.wb.ru
        print(f"  WB: in-page fetch card.wb.ru...", flush=True)
        try:
            fetch_res = page.evaluate("""
                async () => {
                    const urls = [
                        'https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm=1097975275',
                        'https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm=1097975275',
                        'https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm=1097975275',
                    ];
                    const results = [];
                    for (const u of urls) {
                        try {
                            const resp = await fetch(u, {headers: {'Accept': 'application/json'}});
                            const text = await resp.text();
                            results.push({host: u.split('/')[2], status: resp.status, body: text.substring(0, 500)});
                            if (resp.status === 200) break;
                        } catch(e) {
                            results.push({host: u.split('/')[2], error: e.message});
                        }
                    }
                    return results;
                }
            """)
            print(f"  WB in-page fetch: {json.dumps(fetch_res, ensure_ascii=False)[:1500]}", flush=True)
            if not wb_data["price_minor"]:
                for fr in (fetch_res or []):
                    if fr.get("status") == 200:
                        try:
                            body = json.loads(fr["body"])
                        except:
                            continue
                        prods = body.get("products") or body.get("data", {}).get("items", [])
                        if prods:
                            p = prods[0]
                            for sz in p.get("sizes", []):
                                pr = sz.get("price", {})
                                basic = pr.get("basic") or pr.get("product")
                                if basic and basic > 0:
                                    wb_data["price_minor"] = int(basic)
                                    wb_data["price_label"] = f"inpage.{fr['host']}"
                                    break
                            if wb_data["price_minor"]:
                                break
        except Exception as e:
            print(f"  WB in-page fetch error: {e}", flush=True)

        print(f"  WB FINAL: title={wb_data['title']} price={wb_data['price_minor']} label={wb_data['price_label']}", flush=True)

    except Exception as e:
        print(f"  WB page error: {e}", flush=True)
        traceback.print_exc()

    # ==========================================================
    # AVITO
    # ==========================================================
    print(f"\n[{ts()}] === AVITO: Playwright ===", flush=True)
    page2 = ctx.new_page()

    avito_urls = [
        "https://www.avito.ru/spb/smartfony-i-telefony",
        "https://www.avito.ru/spb/search?q=iphone",
        "https://www.avito.ru/ekb/smartfony-i-telefony",
        "https://www.avito.ru/moskva/smartfony-i-telefony",
    ]

    for list_url in avito_urls:
        try:
            page2.goto(list_url, wait_until="domcontentloaded", timeout=35000)
            try:
                page2.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            time.sleep(4)
            html2 = safe_content(page2)
            city = list_url.split('/')[3]
            print(f"  AVITO [{city}]: len={len(html2)} url={page2.url}", flush=True)

            with open(os.path.join(BASE, "avito_list16.html"), "w", encoding="utf-8") as f:
                f.write(html2)

            # Check if 404 or captcha
            page_title = page2.evaluate("() => document.title")
            print(f"  AVITO title: {page_title}", flush=True)
            if "404" in page_title or "не найдена" in page_title.lower():
                print(f"  AVITO: 404 page, trying next", flush=True)
                continue
            if "captcha" in page_title.lower() or "подтверждение" in page_title.lower():
                print(f"  AVITO: captcha, trying next", flush=True)
                continue

            # DOM extraction of ad IDs
            ad_ids = page2.evaluate("""
                () => {
                    const ids = new Set();
                    const links = document.querySelectorAll('a[href]');
                    for (const a of links) {
                        const h = a.getAttribute('href') || '';
                        let m = h.match(/\\/ad\\/(\\d{6,})/);
                        if (m) ids.add(m[1]);
                        m = h.match(/\\/\\w+\\/[^/]+\\/(\\d{9,})/);
                        if (m) ids.add(m[1]);
                        m = h.match(/avito\\.ru\\/\\w+\\/[^/]+\\/(\\d{6,})/);
                        if (m) ids.add(m[1]);
                    }
                    for (const el of document.querySelectorAll('[data-ad-id],[data-id],[data-adid]')) {
                        for (const attr of ['data-ad-id','data-id','data-adid']) {
                            const v = el.getAttribute(attr);
                            if (v && /^\\d{6,}$/.test(v)) ids.add(v);
                        }
                    }
                    return Array.from(ids).slice(0, 20);
                }
            """)
            print(f"  AVITO ad IDs: {ad_ids}", flush=True)

            if ad_ids:
                avito_data["ad_id"] = ad_ids[0]
                break
            else:
                structure = page2.evaluate("""
                    () => {
                        const r = {};
                        r.title = document.title;
                        r.h1 = (document.querySelector('h1') || {}).textContent || null;
                        r.links = document.querySelectorAll('a[href]').length;
                        r.imgs = document.querySelectorAll('img').length;
                        r.sampleHrefs = Array.from(document.querySelectorAll('a[href]')).slice(0, 25)
                            .map(a => a.getAttribute('href')).filter(h => h && !h.startsWith('#') && !h.startsWith('javascript'));
                        return r;
                    }
                """)
                print(f"  AVITO structure: {json.dumps(structure, ensure_ascii=False)[:2000]}", flush=True)

        except Exception as e:
            print(f"  AVITO listing error: {e}", flush=True)
        time.sleep(2)

    # Load ad page
    if avito_data["ad_id"]:
        aid = avito_data["ad_id"]
        ad_url = f"https://www.avito.ru/ad/{aid}"
        print(f"\n  AVITO ad: {ad_url}", flush=True)
        try:
            page2.goto(ad_url, wait_until="domcontentloaded", timeout=35000)
            try:
                page2.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            time.sleep(4)
            html3 = safe_content(page2)
            avito_data["html_len"] = len(html3)
            print(f"  AVITO ad page: len={len(html3)}", flush=True)

            with open(os.path.join(BASE, "avito_ad16.html"), "w", encoding="utf-8") as f:
                f.write(html3)

            ad_dom = page2.evaluate("""
                () => {
                    const r = {};
                    r.h1 = (document.querySelector('h1') || {}).textContent || null;
                    r.ogTitle = (document.querySelector('meta[property="og:title"]') || {}).content || null;
                    r.ogPrice = (document.querySelector('meta[property="og:price:amount"]') || {}).content || null;
                    r.ogImage = (document.querySelector('meta[property="og:image"]') || {}).content || null;
                    const priceSels = ['[class*="price"]', '[data-auto*="price"]', '.price', '.c-price'];
                    r.selPrices = {};
                    for (const sel of priceSels) {
                        try {
                            const els = document.querySelectorAll(sel);
                            if (els.length > 0) {
                                r.selPrices[sel] = Array.from(els).slice(0, 5).map(e =>
                                    ({text: e.textContent.trim().substring(0, 100), cls: e.className.substring(0, 60)}));
                            }
                        } catch(e) {}
                    }
                    r.priceTexts = [];
                    try {
                        const walker = document.createTreeWalker(document.body, 4);
                        let node;
                        while (node = walker.nextNode()) {
                            const t = node.textContent.trim();
                            if (/^\\d[\\d\\s\\u00a0]{1,12}\\s*(?:\\u20bd|руб)/.test(t) && t.length < 40) {
                                r.priceTexts.push(t);
                                if (r.priceTexts.length >= 10) break;
                            }
                        }
                    } catch(e) {}
                    r.images = Array.from(document.querySelectorAll('img[src*="avito.st"], img[src*="mmedia"], img[src*="l16"], img[src*="l26"]'))
                        .slice(0, 8).map(i => i.src);
                    r.desc = (document.querySelector('[class*="description"], [data-auto*="description"]') || {}).textContent || null;
                    return r;
                }
            """)
            print(f"  AVITO ad DOM: {json.dumps(ad_dom, ensure_ascii=False)[:2500]}", flush=True)

            if ad_dom:
                avito_data["title"] = ad_dom.get("h1") or ad_dom.get("ogTitle")
                if ad_dom.get("ogPrice"):
                    try:
                        avito_data["price_minor"] = int(round(float(ad_dom["ogPrice"]) * 100))
                        avito_data["price_label"] = "og:price:amount"
                    except (ValueError, TypeError):
                        pass
                if not avito_data["price_minor"]:
                    for sel, els in (ad_dom.get("selPrices") or {}).items():
                        if not isinstance(els, list):
                            continue
                        for el in els:
                            m = re.search(r'(\d[\d\s\u00a0]{1,12})\s*(?:\u20bd|руб)', el.get("text", ""))
                            if m:
                                avito_data["price_minor"] = int(float(m.group(1).replace(" ", "").replace("\u00a0", "")) * 100)
                                avito_data["price_label"] = f"dom.{sel}"
                                break
                        if avito_data["price_minor"]:
                            break
                if not avito_data["price_minor"]:
                    for pt in (ad_dom.get("priceTexts") or []):
                        m = re.search(r'(\d[\d\s\u00a0]{1,12})\s*(?:\u20bd|руб)', pt)
                        if m:
                            avito_data["price_minor"] = int(float(m.group(1).replace(" ", "").replace("\u00a0", "")) * 100)
                            avito_data["price_label"] = "dom.text"
                            break
                avito_data["images"] = (ad_dom.get("images") or [])[:5]

        except Exception as e:
            print(f"  AVITO ad error: {e}", flush=True)
            traceback.print_exc()

    browser.close()

# ============================================================
# SQLITE verify
# ============================================================
print(f"\n[{ts()}] === SQLITE verify ===", flush=True)
import sqlite3
con = sqlite3.connect(DB_PATH)
cur = con.cursor()
for src in ["OZON", "FARPOST"]:
    p = cur.execute("SELECT id, listing_id, title FROM product WHERE source=?", (src,)).fetchall()
    o = cur.execute("""
        SELECT o.id, o.product_id, o.observed_at, o.availability
        FROM observation o JOIN product p ON o.product_id = p.id
        WHERE p.source=? ORDER BY o.rowid DESC LIMIT 3
    """, (src,)).fetchall()
    pp = cur.execute("""
        SELECT pp.id, pp.product_id, pp.role, pp.amount_minor, pp.currency, pp.label
        FROM price_point pp JOIN product p ON pp.product_id = p.id
        WHERE p.source=? ORDER BY pp.rowid DESC LIMIT 5
    """, (src,)).fetchall()
    print(f"  {src}: products={len(p)}, obs={len(o)}, pp={len(pp)}", flush=True)
    for r in p[:3]:
        print(f"    P: {r[0]} {r[1]} {str(r[2])[:50]}", flush=True)
    for r in pp[:3]:
        print(f"    PP: prod={r[1]} role={r[2]} amt={r[3]} {r[4]} label={r[5]}", flush=True)
con.close()

# ============================================================
# Summary
# ============================================================
elapsed = round(time.monotonic() - t0, 1)
results["s"] = elapsed

wb_has_title = bool(wb_data.get("title"))
wb_has_price = bool(wb_data.get("price_minor"))
results["d"]["WB"] = {
    "verdict": "DATA_OK" if (wb_has_title and wb_has_price) else "DATA_FAIL",
    "reason": f"title={wb_has_title} price={wb_data.get('price_minor')} label={wb_data.get('price_label')}",
    "detail": wb_data,
}

av_has_title = bool(avito_data.get("title"))
av_has_price = bool(avito_data.get("price_minor"))
results["d"]["AVITO"] = {
    "verdict": "DATA_OK" if (av_has_title and av_has_price) else "DATA_FAIL",
    "reason": f"ad={avito_data.get('ad_id')} title={av_has_title} price={avito_data.get('price_minor')}",
    "detail": avito_data,
}

results["pass"] = sum(1 for v in results["d"].values() if v["verdict"] == "DATA_OK")

out_path = os.path.join(BASE, "e2e_fix16_result.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n[{ts()}] e2e_fix16 done in {elapsed}s", flush=True)
print(f"  WB: {results['d']['WB']['verdict']} — {results['d']['WB']['reason']}", flush=True)
print(f"  AVITO: {results['d']['AVITO']['verdict']} — {results['d']['AVITO']['reason']}", flush=True)
print(f"  Result → {out_path}", flush=True)

"""Диагностика v49: оффлайн-парсинг Ozon + WB WBAAS auto-reload + Avito mobile.

Стратегии:
- OZON:  разбор сохранённого HTML (1MB) оффлайн — JSON-LD, цены, фото, характеристики
- WB:    Playwright → WBAAS → ждать 65s → reload → извлечь (2 попытки)
- AVITO: Playwright mobile UA + m.avito.ru + /ad/{id} + viewport 390x844
- FARPOST: cffi re-check (подтверждение)

Запуск: C:\Python315\python.exe diag_v49_all.py
Результат: diag_v49_results.json
Лог:     diag_v49_run.log
"""

import json
import logging
import re
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

LOG_FILE = PROJECT / "diag_v49_run.log"
RESULT_FILE = PROJECT / "diag_v49_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v49")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()

results = {
    "version": "v49",
    "timestamp": NOW,
    "platforms": {},
}

UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)


# ── helpers ──────────────────────────────────────────────
def extract_jsonld(html):
    blocks = []
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            blocks.append(json.loads(m.group(1).strip()))
        except Exception:
            pass
    return blocks


def extract_og(html):
    og = {}
    for m in re.finditer(
        r'<meta[^>]*property=["\']og:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    ):
        og[m.group(1)] = m.group(2)
    for m in re.finditer(
        r'<meta[^>]*content=["\']([^"\']*)["\'][^>]*property=["\']og:([^"\']+)["\']',
        html, re.IGNORECASE,
    ):
        og.setdefault(m.group(2), m.group(1))
    return og


def extract_h1(html):
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    if m:
        return re.sub(r'<[^>]+>', '', m.group(1)).strip()[:200]
    return ""


def extract_prices_text(html):
    """Извлекает все вхождения цены (число + ₽/руб)."""
    prices = []
    seen = set()
    for m in re.finditer(r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб(?:\.|лей)?)', html):
        raw = m.group(1).replace(" ", "").replace("\u00a0", "").replace("\xa0", "")
        try:
            amount = float(raw)
            if 1 <= amount <= 10_000_000:
                minor = int(round(amount * 100))
                if minor not in seen:
                    seen.add(minor)
                    prices.append({"amount": amount, "minor": minor, "raw": m.group(0)[:50]})
        except ValueError:
            pass
    return prices[:20]


def extract_images(html, cdn_patterns):
    """Извлекает URL картинок по CDN-паттернам."""
    items = []
    seen = set()
    for pat in cdn_patterns:
        for m in re.finditer(pat, html, re.I):
            url = m.group(1) if m.lastindex else m.group(0)
            if url not in seen and len(url) > 20:
                seen.add(url)
                items.append(url)
    return items[:40]


def save_html(html, name):
    p = PROJECT / name
    p.write_text(html, encoding="utf-8")
    return p.name


# ── OZON: offline parse of saved HTML ───────────────────
def run_ozon_offline():
    log.info("=== OZON offline parse (saved HTML) ===")
    oz = {"strategies": []}
    html_file = PROJECT / "diag_v48_ozon_1600562267.html"
    if not html_file.exists():
        oz["strategies"].append({"strategy": "offline", "error": "HTML file not found"})
        results["platforms"]["OZON"] = oz
        return

    html = html_file.read_text(encoding="utf-8")
    log.info(f"  Loaded {len(html)} bytes from {html_file.name}")

    e = {
        "strategy": "offline_parse",
        "id": "1600562267",
        "html_len": len(html),
        "html_file": html_file.name,
    }

    # JSON-LD
    jld = extract_jsonld(html)
    e["jsonld_count"] = len(jld)
    product_ld = None
    for b in jld:
        if isinstance(b, dict) and b.get("@type") == "Product":
            product_ld = b
            break
    if product_ld:
        e["product_name"] = product_ld.get("name", "")[:200]
        e["product_brand"] = product_ld.get("brand", "")
        e["product_desc"] = (product_ld.get("description", "") or "")[:300]
        e["product_img"] = product_ld.get("image", "")[:200]
        offers = product_ld.get("offers", {})
        if isinstance(offers, dict):
            offers = [offers]
        if isinstance(offers, list) and offers:
            o = offers[0]
            e["price"] = o.get("price", "")
            e["price_currency"] = o.get("priceCurrency", "")
            e["availability"] = o.get("availability", "")
        ar = product_ld.get("aggregateRating", {})
        if ar:
            e["rating"] = ar.get("ratingValue", "")
            e["review_count"] = ar.get("reviewCount", "")
        e["jsonld_full"] = json.dumps(product_ld, ensure_ascii=False)[:2000]

    # OG
    og = extract_og(html)
    e["og"] = {k: v[:150] for k, v in list(og.items())[:12]}

    # H1
    e["h1"] = extract_h1(html)

    # Prices from text
    prices = extract_prices_text(html)
    e["prices_text"] = prices[:10]

    # Images: ir.ozone.ru, ozone.ru/s3, ozoncdn, ozonusercontent
    img_pats = [
        r'(?:src|data-src|data-original|href)=["\']([^"\']*ir\.ozone\.ru/[^"\']+)["\']',
        r'(?:src|data-src|data-original|href)=["\']([^"\']*ozone\.ru/s3/[^"\']+)["\']',
        r'(?:src|data-src|data-original|href)=["\']([^"\']*ozoncdn\.net/[^"\']+)["\']',
        r'(?:src|data-src|data-original|href)=["\']([^"\']*ozonusercontent\.com/[^"\']+)["\']',
        r'"(https?://ir\.ozone\.ru/s3/[^"]+)"',
        r'"(https?://[a-z0-9]+\.ozone\.ru/s3/[^"]+)"',
    ]
    imgs = extract_images(html, img_pats)
    e["images_count"] = len(imgs)
    e["images_sample"] = imgs[:8]

    # Videos
    vid_pats = [
        r'(?:src|data-src|data-url)=["\']([^"\']*\.(?:mp4|webm)(?:\?[^"\']*)?)["\']',
        r'"(https?://[^"]*\.(?:mp4|webm)[^"]*)"',
    ]
    vids = extract_images(html, vid_pats)
    e["videos_count"] = len(vids)
    e["videos_sample"] = vids[:5]

    # data-widget (might be different attribute name)
    widgets = list(set(re.findall(r'data-widget(?:-name)?=["\']([^"\']+)["\']', html)))
    e["widgets"] = widgets[:20]

    # data-hid attributes
    hids = list(set(re.findall(r'data-hid=["\']([^"\']+)["\']', html)))
    e["data_hids"] = hids[:20]

    # Breadcrumb / category
    bc = re.findall(r'class=["\'][^"\']*breadcrumb[^"\']*["\'][^>]*>(.*?)</(?:nav|div|ul)>', html, re.DOTALL | re.I)
    if bc:
        e["breadcrumb_raw"] = re.sub(r'<[^>]+>', ' > ', bc[0]).strip()[:200]

    # Characteristics / aspects
    aspects = re.findall(r'class=["\'][^"\']*(?:aspect|characteristic|spec)[^"\']*["\'][^>]*>(.*?)</', html, re.DOTALL | re.I)
    if aspects:
        e["aspects_sample"] = [re.sub(r'<[^>]+>', '', a).strip()[:80] for a in aspects[:10]]

    # State / JSON blobs in scripts
    state_blobs = []
    for m in re.finditer(r'<script[^>]*>(\{.{200,}?\})</script>', html, re.DOTALL):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                state_blobs.append(list(obj.keys())[:15])
        except Exception:
            pass
    e["state_blobs"] = state_blobs[:5]

    # Title tag
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    e["title_tag"] = m.group(1).strip()[:200] if m else ""

    oz["strategies"].append(e)
    log.info(f"  OZON: jsonld={len(jld)} prices={len(prices)} imgs={len(imgs)} "
             f"widgets={len(widgets)} h1={e.get('h1','?')[:50]}")
    log.info(f"  OZON product: {e.get('product_name','?')[:80]} "
             f"price={e.get('price','?')} brand={e.get('product_brand','?')}")

    results["platforms"]["OZON"] = oz


# ── WB: WBAAS auto-reload ────────────────────────────────
def run_wb():
    log.info("=== WB Playwright (WBAAS auto-reload) ===")
    wb = {"strategies": []}
    nm_ids = ["199298871"]
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-features=IsolateOrigins,site-per-process"],
            )
            ctx = browser.new_context(
                user_agent=UA_CHROME,
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
                extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
            )
            page = ctx.new_page()

            for nm in nm_ids:
                url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
                t0 = time.time()
                e = {"strategy": "pw_wbaas_reload", "nm": nm, "url": url}

                # Attempt 1: goto, wait for WBAAS JS to execute + auto-reload
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception as ge:
                    e["goto_err"] = str(ge)
                    wb["strategies"].append(e)
                    continue

                # WBAAS: JS runs, sets cookie, auto-reloads after ~55s
                log.info(f"  WB {nm}: waiting 65s for WBAAS auto-reload...")
                time.sleep(65)

                # Check if page auto-reloaded (title changed)
                title1 = page.title()
                html1 = page.content()
                e["after_wait"] = {
                    "title": title1[:200],
                    "html_len": len(html1),
                    "wbaas": "wbaas" in html1.lower(),
                    "countdown": "countdown" in html1.lower(),
                }
                log.info(f"  WB {nm} after 65s: title={title1[:60]} "
                         f"wbaas={e['after_wait']['wbaas']} html={len(html1)}B")

                # If still WBAAS, try manual reload
                if "wbaas" in html1.lower() or len(html1) < 20000:
                    log.info(f"  WB {nm}: still WBAAS, manual reload...")
                    time.sleep(3)
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                    except Exception as re2:
                        e["reload_err"] = str(re2)
                    time.sleep(12)
                    title2 = page.title()
                    html2 = page.content()
                    e["after_reload"] = {
                        "title": title2[:200],
                        "html_len": len(html2),
                        "wbaas": "wbaas" in html2.lower(),
                    }
                    log.info(f"  WB {nm} after reload: title={title2[:60]} "
                             f"wbaas={e['after_reload']['wbaas']} html={len(html2)}B")
                    html_final = html2
                    title_final = title2
                else:
                    html_final = html1
                    title_final = title1

                dt = time.time() - t0
                e["time_s"] = round(dt, 1)
                e["final_title"] = title_final[:200]
                e["final_html_len"] = len(html_final)

                # Extract data
                jld = extract_jsonld(html_final)
                e["jsonld_count"] = len(jld)
                for i, b in enumerate(jld[:3]):
                    e[f"jsonld_{i}"] = json.dumps(b, ensure_ascii=False)[:500]

                og = extract_og(html_final)
                e["og"] = {k: v[:100] for k, v in list(og.items())[:10]}
                e["h1"] = extract_h1(html_final)
                e["prices"] = extract_prices_text(html_final)[:8]

                # WB state
                for pat in [r'window\.__STATE__\s*=\s*(\{.+?\})\s*;',
                            r'<script[^>]*id=["\']state["\'][^>]*>(\{.+?\})</script>',
                            r'data-state=["\']([^"\']+)["\']']:
                    m = re.search(pat, html_final, re.DOTALL)
                    if m:
                        e["state_found"] = pat[:40]
                        e["state_len"] = len(m.group(1))
                        try:
                            st = json.loads(m.group(1))
                            e["state_keys"] = list(st.keys())[:20]
                        except Exception:
                            e["state_snippet"] = m.group(1)[:300]
                        break

                # Images
                wb_imgs = extract_images(html_final, [
                    r'(?:src|data-src|data-original)=["\']([^"\']*wbbasket\.ru[^"\']*)["\']',
                    r'(?:src|data-src|data-original)=["\']([^"\']*wbstatic\.net[^"\']*)["\']',
                    r'"(https?://[^"]*wbbasket\.ru/[^"]+)"',
                ])
                e["images_count"] = len(wb_imgs)
                e["images_sample"] = wb_imgs[:6]

                e["html_saved"] = save_html(html_final, f"diag_v49_wb_{nm}.html")
                wb["strategies"].append(e)
                log.info(f"  WB {nm}: {dt:.0f}s jsonld={len(jld)} "
                         f"prices={len(e.get('prices',[]))} imgs={len(wb_imgs)} "
                         f"h1={e.get('h1','?')[:50]}")
                time.sleep(3)

            browser.close()
    except Exception as ex:
        log.error(f"  WB PW ERR: {ex}\n{traceback.format_exc()}")
        wb["strategies"].append({"strategy": "pw_wbaas_reload", "error": str(ex)})
    results["platforms"]["WB"] = wb


# ── AVITO: mobile + /ad/{id} ─────────────────────────────
def run_avito():
    log.info("=== Avito Playwright (mobile + /ad/{id}) ===")
    av = {"strategies": []}

    # Strategy 1: mobile UA + m.avito.ru
    # Strategy 2: desktop UA + /ad/{id} (canonical)
    strategies = [
        {
            "name": "mobile_ad",
            "url": "https://m.avito.ru/moscow/elektronika/iphone-1154321987",
            "ua": UA_MOBILE,
            "viewport": {"width": 390, "height": 844},
        },
        {
            "name": "desktop_ad",
            "url": "https://www.avito.ru/ad/1154321987",
            "ua": UA_CHROME,
            "viewport": {"width": 1920, "height": 1080},
        },
    ]

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            for strat in strategies:
                t0 = time.time()
                e = {
                    "strategy": strat["name"],
                    "url": strat["url"],
                }
                try:
                    browser = pw.chromium.launch(
                        headless=True,
                        args=["--disable-blink-features=AutomationControlled",
                              "--no-sandbox", "--disable-dev-shm-usage"],
                    )
                    ctx = browser.new_context(
                        user_agent=strat["ua"],
                        viewport=strat["viewport"],
                        locale="ru-RU",
                    )
                    page = ctx.new_page()
                    resp = page.goto(strat["url"], wait_until="domcontentloaded",
                                     timeout=30000)
                    time.sleep(8)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    time.sleep(3)

                    dt = time.time() - t0
                    html = page.content()
                    title = page.title()
                    status = resp.status if resp else 0

                    e["time_s"] = round(dt, 1)
                    e["http"] = status
                    e["title"] = title[:200]
                    e["html_len"] = len(html)
                    e["ip_limit"] = ("Доступ ограничен" in html
                                     or "ip_limit" in html[:5000].lower()
                                     or "проблем с IP" in html)
                    e["redirected_url"] = page.url[:200]

                    jld = extract_jsonld(html)
                    e["jsonld_count"] = len(jld)
                    if jld:
                        e["jsonld_0"] = json.dumps(jld[0], ensure_ascii=False)[:500]

                    og = extract_og(html)
                    e["og"] = {k: v[:100] for k, v in list(og.items())[:10]}
                    e["h1"] = extract_h1(html)
                    e["prices"] = extract_prices_text(html)[:8]

                    # Avito state
                    m = re.search(
                        r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\})\s*;',
                        html, re.DOTALL)
                    if m:
                        try:
                            st = json.loads(m.group(1))
                            e["preloaded_keys"] = list(st.keys())[:15]
                        except Exception:
                            e["preloaded_snippet"] = m.group(1)[:300]

                    # Images
                    av_imgs = extract_images(html, [
                        r'(?:src|data-src|data-original)=["\']([^"\']*avito\.st[^"\']*)["\']',
                        r'(?:src|data-src|data-original)=["\']([^"\']*mmedia\.avito\.st[^"\']*)["\']',
                        r'"(https?://[^"]*avito\.st/[^"]+)"',
                    ])
                    e["images_count"] = len(av_imgs)
                    e["images_sample"] = av_imgs[:6]

                    slug = strat["url"].rstrip("/").split("/")[-1][:30]
                    e["html_saved"] = save_html(html, f"diag_v49_avito_{strat['name']}_{slug}.html")
                    av["strategies"].append(e)
                    log.info(f"  Avito [{strat['name']}]: {dt:.1f}s HTTP {status} "
                             f"ip_limit={e['ip_limit']} jsonld={len(jld)} "
                             f"h1={e.get('h1','?')[:50]} url={e['redirected_url'][:60]}")

                    browser.close()
                except Exception as ex:
                    e["error"] = str(ex)
                    av["strategies"].append(e)
                    log.error(f"  Avito [{strat['name']}] ERR: {ex}")
                time.sleep(5)
    except Exception as ex:
        log.error(f"  Avito PW ERR: {ex}\n{traceback.format_exc()}")
        av["strategies"].append({"strategy": "pw", "error": str(ex)})
    results["platforms"]["AVITO"] = av


# ── FARPOST: quick re-check ──────────────────────────────
def run_farpost():
    log.info("=== FARPOST re-check ===")
    fp = {"strategies": []}
    url = (
        "https://www.farpost.ru/vladivostok/zapchasti/"
        "datchik-kisloroda-lynxauto-art-dm-2350-g16879823067.html"
    )
    try:
        from curl_cffi import requests as cffi_req
        t0 = time.time()
        resp = cffi_req.get(url, headers={"User-Agent": UA_CHROME},
                            timeout=15, impersonate="chrome")
        dt = time.time() - t0
        html = resp.text
        e = {
            "strategy": "cffi", "id": "16879823067",
            "http": resp.status_code, "html_len": len(html),
            "time_s": round(dt, 2),
        }
        jld = extract_jsonld(html)
        e["jsonld_count"] = len(jld)
        for b in jld:
            if isinstance(b, dict) and b.get("@type") == "Product":
                e["name"] = b.get("name", "")[:150]
                offers = b.get("offers", {})
                if isinstance(offers, dict):
                    e["price"] = offers.get("price", "")
                    e["currency"] = offers.get("priceCurrency", "")
                elif isinstance(offers, list) and offers:
                    e["price"] = offers[0].get("price", "")
        og = extract_og(html)
        e["og_title"] = og.get("og:title", "")[:150]
        e["h1"] = extract_h1(html)
        e["prices"] = extract_prices_text(html)[:5]
        e["images_count"] = len(extract_images(html, [
            r'(?:src|data-src)=["\']([^"\']*farpost\.ru/[^"\']*\.jpg[^"\']*)["\']',
            r'(?:src|data-src)=["\']([^"\']*farpost\.ru/[^"\']*\.png[^"\']*)["\']',
        ]))
        fp["strategies"].append(e)
        log.info(f"  FP: {dt:.1f}s HTTP {resp.status_code} "
                 f"jsonld={len(jld)} name={e.get('name','?')[:60]} price={e.get('price','?')}")
    except Exception as ex:
        log.error(f"  FP ERR: {ex}")
        fp["strategies"].append({"strategy": "cffi", "error": str(ex)})
    results["platforms"]["FARPOST"] = fp


# ── MAIN ─────────────────────────────────────────────────
if __name__ == "__main__":
    t_start = time.time()
    log.info("=== diag_v49 START ===")
    run_ozon_offline()
    run_farpost()
    run_wb()
    run_avito()
    results["total_time_s"] = round(time.time() - t_start, 1)
    RESULT_FILE.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"=== diag_v49 DONE {results['total_time_s']}s ===")
    log.info(f"Saved: {RESULT_FILE} ({RESULT_FILE.stat().st_size} B)")

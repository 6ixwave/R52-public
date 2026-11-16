"""Диагностика v48: Playwright для WB, Ozon, Avito + Farpost re-check.

Стратегия: headless Chromium решает JS-челленджи (WBAAS, Antibot, 439),
после чего извлекаем JSON-LD, state-объекты, OG из рендеренного HTML.

Запуск: C:\Python315\python.exe diag_v48_all.py
Результат: diag_v48_results.json
Лог:     diag_v48_run.log
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

LOG_FILE = PROJECT / "diag_v48_run.log"
RESULT_FILE = PROJECT / "diag_v48_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v48")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()

results = {
    "version": "v48",
    "timestamp": NOW,
    "platforms": {},
}

UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
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


def save_html(html, name):
    p = PROJECT / name
    p.write_text(html, encoding="utf-8")
    return p.name


# ── FARPOST re-check ─────────────────────────────────────
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
            "strategy": "cffi_recheck", "id": "16879823067",
            "url": url, "time_s": round(dt, 2),
            "http": resp.status_code, "html_len": len(html),
        }
        jld = extract_jsonld(html)
        e["jsonld_count"] = len(jld)
        for b in jld:
            if isinstance(b, dict) and b.get("@type") == "Product":
                e["product_name"] = b.get("name", "")[:150]
                offers = b.get("offers", {})
                if isinstance(offers, dict):
                    e["price"] = offers.get("price", "")
                    e["price_currency"] = offers.get("priceCurrency", "")
                elif isinstance(offers, list) and offers:
                    e["price"] = offers[0].get("price", "")
        og = extract_og(html)
        e["og_title"] = og.get("og:title", "")[:150]
        e["h1"] = extract_h1(html)
        e["html_saved"] = save_html(html, "diag_v48_fp_16879823067.html")
        fp["strategies"].append(e)
        log.info(f"  FP: {dt:.1f}s HTTP {resp.status_code} "
                 f"jsonld={len(jld)} name={e.get('product_name','?')[:60]}")
    except Exception as ex:
        log.error(f"  FP ERR: {ex}")
        fp["strategies"].append({"strategy": "cffi_recheck", "error": str(ex)})
    results["platforms"]["FARPOST"] = fp


# ── WILDBERRIES via Playwright ───────────────────────────
def run_wb():
    log.info("=== WB Playwright (WBAAS) ===")
    wb = {"strategies": []}
    nm_ids = ["199298871", "314337853"]
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = browser.new_context(
                user_agent=UA_CHROME,
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
            )
            page = ctx.new_page()
            for nm in nm_ids:
                url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
                t0 = time.time()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception as ge:
                    log.warning(f"  WB goto {nm}: {ge}")
                    wb["strategies"].append(
                        {"strategy": "pw_wbaas", "nm": nm, "error": str(ge)})
                    continue
                # WBAAS: JS executes, may set cookie + reload
                time.sleep(6)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                time.sleep(3)
                dt = time.time() - t0
                html = page.content()
                title = page.title()
                e = {
                    "strategy": "pw_wbaas", "nm": nm, "url": url,
                    "time_s": round(dt, 2), "title": title[:200],
                    "html_len": len(html),
                    "has_wbaas": "__wbaas" in html,
                }
                jld = extract_jsonld(html)
                e["jsonld_count"] = len(jld)
                for i, b in enumerate(jld[:3]):
                    e[f"jsonld_{i}"] = json.dumps(b, ensure_ascii=False)[:300]
                og = extract_og(html)
                e["og"] = {k: v[:100] for k, v in list(og.items())[:10]}
                e["h1"] = extract_h1(html)
                # window.__STATE__
                m = re.search(
                    r'window\.__STATE__\s*=\s*(\{.+?\})\s*;', html, re.DOTALL)
                if m:
                    try:
                        st = json.loads(m.group(1))
                        e["state_top_keys"] = list(st.keys())[:20]
                    except Exception:
                        e["state_snippet"] = m.group(1)[:300]
                # data-state
                m2 = re.search(r'data-state=["\']([^"\']+)["\']', html)
                if m2:
                    e["data_state_len"] = len(m2.group(1))
                e["html_saved"] = save_html(html, f"diag_v48_wb_{nm}.html")
                wb["strategies"].append(e)
                log.info(
                    f"  WB {nm}: {dt:.1f}s title={title[:60]} "
                    f"wbaas={e['has_wbaas']} jsonld={len(jld)} "
                    f"h1={e.get('h1','?')[:50]} html={len(html)}B")
                time.sleep(2)
            browser.close()
    except Exception as ex:
        log.error(f"  WB PW ERR: {ex}\n{traceback.format_exc()}")
        wb["strategies"].append({"strategy": "pw_wbaas", "error": str(ex)})
    results["platforms"]["WB"] = wb


# ── OZON via Playwright ──────────────────────────────────
def run_ozon():
    log.info("=== Ozon Playwright (Antibot) ===")
    oz = {"strategies": []}
    pid = "1600562267"
    url = f"https://www.ozon.ru/product/{pid}/"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = browser.new_context(
                user_agent=UA_CHROME,
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
            )
            page = ctx.new_page()
            t0 = time.time()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Antibot may need more time
            time.sleep(10)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            time.sleep(3)
            dt = time.time() - t0
            html = page.content()
            title = page.title()
            e = {
                "strategy": "pw_antibot", "id": pid, "url": url,
                "time_s": round(dt, 2), "title": title[:200],
                "html_len": len(html),
                "has_antibot": "ntibot" in title or "antibot" in html[:3000].lower(),
            }
            widgets = list(set(re.findall(
                r'data-widget-name=["\']([^"\']+)["\']', html)))
            e["widgets"] = widgets[:15]
            jld = extract_jsonld(html)
            e["jsonld_count"] = len(jld)
            for i, b in enumerate(jld[:3]):
                e[f"jsonld_{i}"] = json.dumps(b, ensure_ascii=False)[:300]
            og = extract_og(html)
            e["og"] = {k: v[:100] for k, v in list(og.items())[:10]}
            e["h1"] = extract_h1(html)
            e["html_saved"] = save_html(html, f"diag_v48_ozon_{pid}.html")
            oz["strategies"].append(e)
            log.info(
                f"  Ozon: {dt:.1f}s title={title[:60]} "
                f"antibot={e['has_antibot']} widgets={len(widgets)} "
                f"jsonld={len(jld)} html={len(html)}B")
            browser.close()
    except Exception as ex:
        log.error(f"  Ozon PW ERR: {ex}\n{traceback.format_exc()}")
        oz["strategies"].append({"strategy": "pw_antibot", "error": str(ex)})
    results["platforms"]["OZON"] = oz


# ── AVITO via Playwright ─────────────────────────────────
def run_avito():
    log.info("=== Avito Playwright (439) ===")
    av = {"strategies": []}
    urls = [
        "https://www.avito.ru/moscow/elektronika/iphone-1154321987",
        "https://www.avito.ru/moscow/elektronika/iphone-1198765432",
    ]
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = browser.new_context(
                user_agent=UA_CHROME,
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
            )
            page = ctx.new_page()
            for lurl in urls:
                t0 = time.time()
                try:
                    resp = page.goto(lurl, wait_until="domcontentloaded",
                                     timeout=30000)
                except Exception as ge:
                    log.warning(f"  Avito goto: {ge}")
                    av["strategies"].append(
                        {"strategy": "pw_439", "url": lurl, "error": str(ge)})
                    continue
                time.sleep(7)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                time.sleep(2)
                dt = time.time() - t0
                html = page.content()
                title = page.title()
                status = resp.status if resp else 0
                e = {
                    "strategy": "pw_439", "url": lurl,
                    "time_s": round(dt, 2), "http": status,
                    "title": title[:200], "html_len": len(html),
                    "has_ip_limit": ("Доступ ограничен" in html
                                     or "ip_limit" in html.lower()[:3000]),
                }
                m = re.search(
                    r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\})\s*;',
                    html, re.DOTALL)
                if m:
                    try:
                        st = json.loads(m.group(1))
                        e["preloaded_keys"] = list(st.keys())[:15]
                    except Exception:
                        e["preloaded_snippet"] = m.group(1)[:300]
                jld = extract_jsonld(html)
                e["jsonld_count"] = len(jld)
                if jld:
                    e["jsonld_0"] = json.dumps(jld[0], ensure_ascii=False)[:300]
                og = extract_og(html)
                e["og"] = {k: v[:100] for k, v in list(og.items())[:10]}
                e["h1"] = extract_h1(html)
                slug = lurl.rstrip("/").split("/")[-1]
                e["html_saved"] = save_html(html, f"diag_v48_avito_{slug}.html")
                av["strategies"].append(e)
                log.info(
                    f"  Avito {slug}: {dt:.1f}s HTTP {status} "
                    f"ip_limit={e['has_ip_limit']} jsonld={len(jld)} "
                    f"h1={e.get('h1','?')[:50]}")
                time.sleep(3)
            browser.close()
    except Exception as ex:
        log.error(f"  Avito PW ERR: {ex}\n{traceback.format_exc()}")
        av["strategies"].append({"strategy": "pw_439", "error": str(ex)})
    results["platforms"]["AVITO"] = av


# ── MAIN ─────────────────────────────────────────────────
if __name__ == "__main__":
    t_start = time.time()
    log.info("=== diag_v48 START ===")
    run_farpost()
    run_wb()
    run_ozon()
    run_avito()
    results["total_time_s"] = round(time.time() - t_start, 1)
    RESULT_FILE.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"=== diag_v48 DONE {results['total_time_s']}s ===")
    log.info(f"Saved: {RESULT_FILE} ({RESULT_FILE.stat().st_size} B)")

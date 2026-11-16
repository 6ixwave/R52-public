#!/usr/bin/env python3
"""diag_v41_all.py — WB (WBAAS warmup), Avito (stealth), Farpost (discovery).

Ozon уже PARSED в v40. Здесь доводим оставшиеся 3 площадки.
"""

import json
import logging
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

LOG_FILE = PROJECT / "diag_v41_run.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v41")

results: dict = {}

OZON_IDS = ["1600562267"]  # контрольный (уже работал)
WB_IDS = ["199298871", "1252522762"]
FARPOST_CAT = "https://www.farpost.ru/telefony"
FARPOST_CAT2 = "https://www.farpost.ru/obshchie"

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = {runtime: {}};
"""


def summarize_media(items):
    s = {"images": 0, "videos": 0, "other": 0}
    for item in items:
        if not isinstance(item, dict):
            s["other"] += 1
            continue
        t = item.get("type", item.get("kind", ""))
        u = item.get("url", item.get("src", item.get("source_url", "")))
        if t == "video" or re.search(r"\.(mp4|webm|ogg)", u, re.I):
            s["videos"] += 1
        elif t in ("image", "photo") or re.search(r"\.(jpg|jpeg|png|webp|avif)", u, re.I):
            s["images"] += 1
        else:
            s["other"] += 1
    return s


def extract_obs_summary(obs):
    if not obs:
        return None
    f = {k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100])
         for k, v in obs.get("fields", {}).items()}
    p = obs.get("price_candidates", [])
    m = obs.get("media", {}).get("items", [])
    return {
        "fields": f, "prices": p[:6],
        "media_count": len(m), "media_types": summarize_media(m),
        "availability": obs.get("availability", "UNKNOWN"),
    }


def parse_with_collector(collector, html_text, listing_id, url):
    class FakeResp:
        def __init__(self, text, status=200):
            self.text = text
            self.status_code = status
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
        def json(self):
            return json.loads(self.text)
    try:
        return collector.parse_page(FakeResp(html_text), listing_id, url, "")
    except Exception as e:
        log.warning("parse_page(%s %s): %s", type(collector).__name__, listing_id, e)
        return None


def main():
    t0 = time.monotonic()
    log.info("=== DIAG V41: WB warmup + Avito stealth + Farpost discovery ===")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
                "--window-size=1920,1080",
            ],
        )

        # ============================================================
        # WILDBERRIES — warmup + WBAAS challenge
        # ============================================================
        log.info("=== WB: warmup + WBAAS ===")
        results["WB"] = {}
        from r52.collectors.wb import WBCollector
        wb_col = WBCollector(use_proxies=False)

        ctx_wb = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
        )
        ctx_wb.add_init_script(STEALTH_JS)
        page_wb = ctx_wb.new_page()

        # Шаг 1: warmup на главной
        log.info("WB warmup: https://www.wildberries.ru/")
        try:
            page_wb.goto("https://www.wildberries.ru/", wait_until="domcontentloaded", timeout=25000)
            page_wb.wait_for_timeout(8000)
            warmup_html = page_wb.content()
            log.info("WB warmup: %d B, title=%s", len(warmup_html), page_wb.title()[:60])
        except Exception as e:
            log.warning("WB warmup error: %s", e)

        # Шаг 2: API через context.request (общие cookies + TLS)
        for wid in WB_IDS:
            api_url = f"https://card.wb.ru/cards/v2/detail?nm={wid}"
            log.info("WB API(ctx) %s: %s", wid, api_url)
            t1 = time.monotonic()
            try:
                resp = ctx_wb.request.get(api_url, timeout=15000, headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.wildberries.ru/",
                })
                el = time.monotonic() - t1
                body = resp.text()
                results["WB"][f"api_{wid}"] = {
                    "url": api_url, "http_code": resp.status,
                    "bytes": len(body), "elapsed": round(el, 1),
                }
                log.info("WB API(ctx) %s: %d, %d B, %.1fs", wid, resp.status, len(body), el)
                if resp.status == 200:
                    try:
                        data = json.loads(body)
                        prods = data.get("data", {}).get("products", [])
                        results["WB"][f"api_{wid}"]["status"] = "API_OK" if prods else "API_EMPTY"
                        results["WB"][f"api_{wid}"]["count"] = len(prods)
                        if prods:
                            p = prods[0]
                            results["WB"][f"api_{wid}"].update({
                                "name": p.get("name", "")[:100],
                                "brand": p.get("brand", ""),
                                "price_basic": p.get("price", {}).get("basic"),
                                "price_total": p.get("price", {}).get("total"),
                                "images": len(p.get("im", [])),
                                "rating": p.get("rating"),
                                "reviews": p.get("feedbacks"),
                                "description": (p.get("description") or "")[:200],
                            })
                            log.info("WB API %s: OK %s", wid, p.get("name", "?")[:50])
                    except (json.JSONDecodeError, ValueError):
                        results["WB"][f"api_{wid}"]["status"] = "NOT_JSON"
                        results["WB"][f"api_{wid}"]["snippet"] = body[:300]
                else:
                    results["WB"][f"api_{wid}"]["status"] = f"HTTP_{resp.status}"
                    results["WB"][f"api_{wid}"]["snippet"] = body[:300]
            except Exception as e:
                el = time.monotonic() - t1
                results["WB"][f"api_{wid}"] = {"url": api_url, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("WB API(ctx) %s: %s", wid, e)
            time.sleep(2)

        # Шаг 3: HTML product page (WBAAS challenge → wait → content)
        for wid in WB_IDS:
            detail_url = wb_col.build_url(wid)
            log.info("WB HTML %s: %s", wid, detail_url)
            t1 = time.monotonic()
            try:
                page_wb.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                # WBAAS: ждём до 20 с, пока не станет > 30 KB
                html = ""
                for wait_round in range(4):
                    page_wb.wait_for_timeout(5000)
                    html = page_wb.content()
                    el = time.monotonic() - t1
                    is_ch = any(s in html[:15000].lower() for s in ("wbaas", "browser-check", "antibot", "challenge"))
                    log.info("WB HTML %s round%d: %d B, ch=%s, %.1fs", wid, wait_round + 1, len(html), is_ch, el)
                    if not is_ch and len(html) > 30000:
                        break
                el = time.monotonic() - t1
                is_ch = any(s in html[:15000].lower() for s in ("wbaas", "browser-check", "antibot", "challenge"))
                has_jl = "application/ld+json" in html[:40000]
                results["WB"][f"html_{wid}"] = {
                    "url": detail_url, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page_wb.title()[:100],
                    "is_challenge": is_ch, "has_jsonld": has_jl,
                    "status": "CHALLENGE" if is_ch else ("CONTENT" if len(html) > 30000 else "THIN"),
                }
                log.info("WB HTML %s final: %d B, ch=%s, jl=%s, %.1fs", wid, len(html), is_ch, has_jl, el)
                hp = PROJECT / f"v41_WB_{wid}.html"
                hp.write_text(html[:800000], encoding="utf-8")

                if not is_ch and len(html) > 30000:
                    obs = parse_with_collector(wb_col, html, wid, detail_url)
                    if obs:
                        results["WB"][f"html_{wid}"]["parsed"] = True
                        results["WB"][f"html_{wid}"].update(extract_obs_summary(obs))
                        log.info("WB HTML %s: PARSED OK", wid)
                    else:
                        results["WB"][f"html_{wid}"]["parsed"] = False
            except Exception as e:
                el = time.monotonic() - t1
                results["WB"][f"html_{wid}"] = {"url": detail_url, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("WB HTML %s: %s", wid, e)
            time.sleep(3)

        ctx_wb.close()

        # ============================================================
        # AVITO — stealth + mobile + API
        # ============================================================
        log.info("=== AVITO: stealth + mobile ===")
        results["AVITO"] = {}
        from r52.collectors.avito import AvitoCollector
        av_col = AvitoCollector(use_proxies=False)

        ctx_av = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        ctx_av.add_init_script(STEALTH_JS)
        page_av = ctx_av.new_page()

        # A: Desktop search
        av_urls = [
            ("disc_desktop", "https://www.avito.ru/moskva/tech/notebooks"),
            ("disc_mobile", "https://m.avito.ru/moskva/tech/notebooks"),
        ]
        ids_found = []
        for key, au in av_urls:
            log.info("AVITO %s: %s", key, au)
            t1 = time.monotonic()
            try:
                page_av.goto(au, wait_until="domcontentloaded", timeout=25000)
                page_av.wait_for_timeout(7000)
                html = page_av.content()
                el = time.monotonic() - t1
                is_blocked = ("Доступ ограничен" in html or "проверка безопасности" in html
                              or "firewall" in html.lower()[:5000])
                id_re = re.compile(r'(?:/ad/|data-ad-id=["\']?|href="[^"]*/)(\d{9,12})')
                new_ids = list(dict.fromkeys(id_re.findall(html)))
                ids_found.extend(new_ids)
                results["AVITO"][key] = {
                    "url": au, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page_av.title()[:100],
                    "is_blocked": is_blocked,
                    "status": "BLOCKED" if is_blocked else "OK",
                    "ids_found": ids_found[-10:],
                }
                log.info("AVITO %s: %d B, blocked=%s, IDs=%s", key, len(html), is_blocked, ids_found[-5:])
                hp = PROJECT / f"v41_AVITO_{key}.html"
                hp.write_text(html[:600000], encoding="utf-8")
            except Exception as e:
                el = time.monotonic() - t1
                results["AVITO"][key] = {"url": au, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("AVITO %s: %s", key, e)
            time.sleep(3)

        # B: Если не нашли ID — пробуем API через context.request
        if not ids_found:
            log.info("AVITO: trying API via context.request")
            api_tests = [
                ("api_search", "https://www.avito.ru/api/v3/items/search?query=iphone&locationId=1&limit=10"),
                ("api_mobile", "https://m.avito.ru/api/v3/items/search?query=iphone&limit=5"),
            ]
            for key, api_url in api_tests:
                t1 = time.monotonic()
                try:
                    resp = ctx_av.request.get(api_url, timeout=15000, headers={
                        "Accept": "application/json",
                        "Referer": "https://www.avito.ru/",
                    })
                    el = time.monotonic() - t1
                    body = resp.text()
                    results["AVITO"][key] = {
                        "url": api_url, "http_code": resp.status,
                        "bytes": len(body), "elapsed": round(el, 1),
                    }
                    log.info("AVITO %s: %d, %d B", key, resp.status, len(body))
                    if resp.status == 200:
                        try:
                            data = json.loads(body)
                            items = data.get("items", data.get("data", {}).get("items", []))
                            if isinstance(items, list):
                                for it in items[:5]:
                                    iid = str(it.get("id", it.get("itemId", "")))
                                    if iid and iid not in ids_found:
                                        ids_found.append(iid)
                                results["AVITO"][key]["ids_from_api"] = ids_found[-10:]
                                results["AVITO"][key]["status"] = "API_OK"
                        except (json.JSONDecodeError, ValueError):
                            results["AVITO"][key]["status"] = "NOT_JSON"
                    else:
                        results["AVITO"][key]["status"] = f"HTTP_{resp.status}"
                        results["AVITO"][key]["snippet"] = body[:300]
                except Exception as e:
                    el = time.monotonic() - t1
                    results["AVITO"][key] = {"url": api_url, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                    log.warning("AVITO %s: %s", key, e)
                time.sleep(2)

        # C: Парсинг карточек
        test_ids = ids_found[:2] if ids_found else ["1096783029"]
        if not ids_found:
            results["AVITO"]["note"] = "No IDs, using fallback 1096783029"
            log.warning("AVITO: fallback ID 1096783029")

        for aid in test_ids:
            url = av_col.build_url(aid)
            log.info("AVITO card %s: %s", aid, url)
            t1 = time.monotonic()
            try:
                page_av.goto(url, wait_until="domcontentloaded", timeout=25000)
                page_av.wait_for_timeout(7000)
                html = page_av.content()
                el = time.monotonic() - t1
                is_blocked = "Доступ ограничен" in html or "проверка безопасности" in html
                is_404 = "Страница не найдена" in html or "Объявление не найдено" in html
                has_jl = "application/ld+json" in html[:40000]
                has_pre = "__PRELOADED_STATE__" in html or "__INITIAL_STATE__" in html
                results["AVITO"][aid] = {
                    "url": url, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page_av.title()[:100],
                    "is_blocked": is_blocked, "is_404": is_404,
                    "has_jsonld": has_jl, "has_preloaded": has_pre,
                    "status": "BLOCKED" if is_blocked else ("404" if is_404 else "CONTENT"),
                }
                log.info("AVITO %s: %d B, bl=%s 404=%s jl=%s pre=%s", aid, len(html), is_blocked, is_404, has_jl, has_pre)
                hp = PROJECT / f"v41_AVITO_{aid}.html"
                hp.write_text(html[:600000], encoding="utf-8")

                if not is_blocked and not is_404:
                    obs = parse_with_collector(av_col, html, aid, url)
                    if obs:
                        results["AVITO"][aid]["parsed"] = True
                        results["AVITO"][aid].update(extract_obs_summary(obs))
                        log.info("AVITO %s: PARSED OK", aid)
                    else:
                        results["AVITO"][aid]["parsed"] = False
            except Exception as e:
                el = time.monotonic() - t1
                results["AVITO"][aid] = {"url": url, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("AVITO %s: %s", aid, e)
            time.sleep(3)

        ctx_av.close()

        # ============================================================
        # FARPOST — discovery → parse
        # ============================================================
        log.info("=== FARPOST: discovery + parse ===")
        results["FARPOST"] = {}
        from r52.collectors.farpost import FarpostCollector
        fp_col = FarpostCollector(use_proxies=False)

        ctx_fp = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
        )
        ctx_fp.add_init_script(STEALTH_JS)
        page_fp = ctx_fp.new_page()

        # A: Discovery — категория
        fp_ids = []
        for cat_url in [FARPOST_CAT, FARPOST_CAT2]:
            if len(fp_ids) >= 3:
                break
            log.info("FARPOST cat: %s", cat_url)
            t1 = time.monotonic()
            try:
                page_fp.goto(cat_url, wait_until="domcontentloaded", timeout=25000)
                page_fp.wait_for_timeout(5000)
                html = page_fp.content()
                el = time.monotonic() - t1
                # Farpost: ссылки вида /bulletin/123456789
                id_re = re.compile(r'/bulletin/(\d{8,12})')
                new_ids = list(dict.fromkeys(id_re.findall(html)))
                fp_ids.extend(new_ids)
                results["FARPOST"][f"cat_{cat_url.split('/')[-1]}"] = {
                    "url": cat_url, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page_fp.title()[:100],
                    "ids_found": fp_ids[-10:],
                    "status": "OK" if new_ids else "NO_IDS",
                }
                log.info("FARPOST cat %s: %d B, IDs=%s", cat_url.split("/")[-1], len(html), fp_ids[-5:])
                hp = PROJECT / f"v41_FARPOST_cat_{cat_url.split('/')[-1]}.html"
                hp.write_text(html[:600000], encoding="utf-8")
            except Exception as e:
                el = time.monotonic() - t1
                results["FARPOST"][f"cat_{cat_url.split('/')[-1]}"] = {"url": cat_url, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("FARPOST cat %s: %s", cat_url, e)
            time.sleep(3)

        # B: Parse найденных карточек
        test_fp = fp_ids[:3] if fp_ids else []
        if not test_fp:
            results["FARPOST"]["note"] = "No IDs discovered from categories"
            log.warning("FARPOST: no IDs, trying old ones as fallback")
            test_fp = ["137440009"]

        for fid in test_fp:
            url = fp_col.build_url(fid)
            log.info("FARPOST %s: %s", fid, url)
            t1 = time.monotonic()
            try:
                page_fp.goto(url, wait_until="domcontentloaded", timeout=25000)
                page_fp.wait_for_timeout(5000)
                html = page_fp.content()
                el = time.monotonic() - t1
                is_404 = ("Страница не найдена" in html or "404" in html[:500]
                          or "не существует" in html.lower()[:3000])
                has_jl = "application/ld+json" in html[:40000]
                results["FARPOST"][fid] = {
                    "url": url, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page_fp.title()[:100],
                    "is_404": is_404, "has_jsonld": has_jl,
                    "status": "404" if is_404 else "CONTENT",
                }
                log.info("FARPOST %s: %d B, 404=%s, jl=%s, title=%s", fid, len(html), is_404, has_jl, page_fp.title()[:50])
                hp = PROJECT / f"v41_FARPOST_{fid}.html"
                hp.write_text(html[:600000], encoding="utf-8")

                if not is_404:
                    obs = parse_with_collector(fp_col, html, fid, url)
                    if obs:
                        results["FARPOST"][fid]["parsed"] = True
                        results["FARPOST"][fid].update(extract_obs_summary(obs))
                        log.info("FARPOST %s: PARSED OK", fid)
                    else:
                        results["FARPOST"][fid]["parsed"] = False
                        log.info("FARPOST %s: fetched, parse None", fid)
            except Exception as e:
                el = time.monotonic() - t1
                results["FARPOST"][fid] = {"url": url, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("FARPOST %s: %s", fid, e)
            time.sleep(3)

        ctx_fp.close()

        browser.close()

    # ============================================================
    # ИТОГ
    # ============================================================
    el = time.monotonic() - t0
    results["_meta"] = {
        "version": "v41",
        "elapsed_sec": round(el, 1),
        "timestamp": datetime.now(timezone(timedelta(hours=10))).isoformat(),
        "engine": "playwright",
        "note": "Ozon done in v40 (PARSED). This run: WB, Avito, Farpost.",
    }
    out = PROJECT / "diag_v41_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("=== V41 done %.1fs -> %s (%d B) ===", el, out.name, out.stat().st_size)

    log.info("--- SUMMARY ---")
    for plat in ("WB", "AVITO", "FARPOST"):
        data = results.get(plat, {})
        st = {k: v.get("status", "?") if isinstance(v, dict) else "?" for k, v in data.items()}
        log.info("%s: %s", plat, json.dumps(st, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())

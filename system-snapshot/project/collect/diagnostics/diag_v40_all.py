#!/usr/bin/env python3
"""diag_v40_all.py — Playwright для 4 площадок + парсинг через коллекторы.

Изменения vs v39:
- Playwright (Chromium headless) — единственный проверенный способ
- Farpost: исправленные IDs (137440009, 137723592, 139122082)
- Avito: m.avito.ru + поиск + desktop
- WB: API (requests с Referer) + HTML (Playwright)
- Ozon: Playwright + parse_page()
- Лог в файл + stdout
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

LOG_FILE = PROJECT / "diag_v40_run.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v40")

results: dict = {}

# --- IDs (проверенные) ---
OZON_IDS = ["1600562267", "229225961"]  # 1600562267 работал в v35
WB_IDS = ["199298871", "1252522762"]    # 199298871 использовался в v35
FARPOST_IDS = ["137440009", "137723592", "139122082"]  # из v27/v28
AVITO_SEARCH = "https://www.avito.ru/moskva/tech/notebooks"
AVITO_MOBILE = "https://m.avito.ru/moskva/tech/notebooks"


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


def parse_with_collector(collector, html_text, listing_id, url):
    """Парсит HTML через parse_page() коллектора (нужен Response-like)."""
    class FakeResp:
        def __init__(self, text, status=200):
            self.text = text
            self.status_code = status
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
        def json(self):
            return json.loads(self.text)
    try:
        obs = collector.parse_page(FakeResp(html_text), listing_id, url, "")
        return obs
    except Exception as e:
        log.warning("parse_page(%s %s) error: %s", type(collector).__name__, listing_id, e)
        return None


def extract_obs_summary(obs):
    """Краткая сводка из observation."""
    if not obs:
        return None
    f = {k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100])
         for k, v in obs.get("fields", {}).items()}
    p = obs.get("price_candidates", [])
    m = obs.get("media", {}).get("items", [])
    return {
        "fields": f,
        "prices": p[:6],
        "media_count": len(m),
        "media_types": summarize_media(m),
        "availability": obs.get("availability", "UNKNOWN"),
        "category_path": obs.get("category_path", ""),
    }


def main():
    t0 = time.monotonic()
    log.info("=== DIAG V40: Playwright, 4 platforms ===")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        )
        page = ctx.new_page()

        # ============================================================
        # OZON
        # ============================================================
        log.info("=== OZON ===")
        results["OZON"] = {}
        from r52.collectors.ozon import OzonCollector
        oz_col = OzonCollector(use_proxies=False)

        for oid in OZON_IDS:
            url = oz_col.build_url(oid)
            log.info("OZON %s: %s", oid, url)
            t1 = time.monotonic()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(5000)
                html = page.content()
                el = time.monotonic() - t1
                results["OZON"][oid] = {
                    "url": url, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page.title()[:100],
                    "has_jsonld": "application/ld+json" in html[:30000],
                    "has_state": any(s in html[:30000] for s in ("__INITIAL_STATE__", "__NEXT_DATA__", "data-state")),
                }
                log.info("OZON %s: %d B, %.1fs, title=%s", oid, len(html), el, page.title()[:60])
                hp = PROJECT / f"v40_OZON_{oid}.html"
                hp.write_text(html[:800000], encoding="utf-8")

                obs = parse_with_collector(oz_col, html, oid, url)
                if obs:
                    results["OZON"][oid]["status"] = "PARSED"
                    results["OZON"][oid].update(extract_obs_summary(obs))
                    log.info("OZON %s: PARSED OK", oid)
                else:
                    results["OZON"][oid]["status"] = "FETCHED_PARSE_NONE"
                    log.info("OZON %s: fetched but parse returned None", oid)
            except Exception as e:
                el = time.monotonic() - t1
                results["OZON"][oid] = {"url": url, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("OZON %s: %s", oid, e)
            time.sleep(3)

        # ============================================================
        # WILDBERRIES
        # ============================================================
        log.info("=== WB ===")
        results["WB"] = {}
        from r52.collectors.wb import WBCollector
        wb_col = WBCollector(use_proxies=False)

        for wid in WB_IDS:
            # --- A: API через requests (быстро, без Playwright) ---
            api_url = f"https://card.wb.ru/cards/v2/detail?nm={wid}"
            log.info("WB API %s: %s", wid, api_url)
            t1 = time.monotonic()
            try:
                import requests as req
                hdrs = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "Referer": "https://www.wildberries.ru/",
                    "Origin": "https://www.wildberries.ru",
                }
                r = req.get(api_url, headers=hdrs, timeout=15)
                el = time.monotonic() - t1
                results["WB"][f"api_{wid}"] = {
                    "url": api_url, "http_code": r.status_code,
                    "bytes": len(r.text), "elapsed": round(el, 1),
                }
                log.info("WB API %s: %d, %d B, %.1fs", wid, r.status_code, len(r.text), el)
                if r.status_code == 200:
                    try:
                        data = r.json()
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
                            })
                            log.info("WB API %s: OK %s", wid, p.get("name", "?")[:50])
                    except (json.JSONDecodeError, ValueError):
                        results["WB"][f"api_{wid}"]["status"] = "NOT_JSON"
                        results["WB"][f"api_{wid}"]["snippet"] = r.text[:200]
                else:
                    results["WB"][f"api_{wid}"]["status"] = f"HTTP_{r.status_code}"
                    results["WB"][f"api_{wid}"]["snippet"] = r.text[:200]
            except Exception as e:
                el = time.monotonic() - t1
                results["WB"][f"api_{wid}"] = {"url": api_url, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("WB API %s: %s", wid, e)
            time.sleep(2)

            # --- B: HTML через Playwright ---
            detail_url = wb_col.build_url(wid)
            log.info("WB HTML %s: %s", wid, detail_url)
            t1 = time.monotonic()
            try:
                page.goto(detail_url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(5000)
                html = page.content()
                el = time.monotonic() - t1
                is_ch = any(s in html[:20000].lower() for s in ("wbaas", "browser-check", "antibot", "challenge"))
                has_jl = "application/ld+json" in html[:30000]
                results["WB"][f"html_{wid}"] = {
                    "url": detail_url, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page.title()[:100],
                    "is_challenge": is_ch, "has_jsonld": has_jl,
                    "status": "CHALLENGE" if is_ch else ("CONTENT" if len(html) > 5000 else "THIN"),
                }
                log.info("WB HTML %s: %d B, %.1fs, ch=%s jl=%s", wid, len(html), el, is_ch, has_jl)
                hp = PROJECT / f"v40_WB_{wid}.html"
                hp.write_text(html[:800000], encoding="utf-8")

                if not is_ch and len(html) > 5000:
                    obs = parse_with_collector(wb_col, html, wid, detail_url)
                    if obs:
                        results["WB"][f"html_{wid}"]["parsed"] = True
                        results["WB"][f"html_{wid}"].update(extract_obs_summary(obs))
                    else:
                        results["WB"][f"html_{wid}"]["parsed"] = False
            except Exception as e:
                el = time.monotonic() - t1
                results["WB"][f"html_{wid}"] = {"url": detail_url, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("WB HTML %s: %s", wid, e)
            time.sleep(3)

        # ============================================================
        # AVITO
        # ============================================================
        log.info("=== AVITO ===")
        results["AVITO"] = {}
        from r52.collectors.avito import AvitoCollector
        av_col = AvitoCollector(use_proxies=False)

        # Шаг 1: поиск (desktop + mobile) для обнаружения ID
        disc_urls = [AVITO_SEARCH, AVITO_MOBILE]
        ids_found = []
        for i, du in enumerate(disc_urls):
            key = f"disc_{i}"
            log.info("AVITO disc[%d]: %s", i, du)
            t1 = time.monotonic()
            try:
                page.goto(du, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(6000)
                html = page.content()
                el = time.monotonic() - t1
                is_blocked = "Доступ ограничен" in html or "проверка безопасности" in html or "firewall" in html.lower()
                id_re = re.compile(r'(?:/ad/|data-ad-id=["\']?|href="/[^"]*/)(\d{9,12})')
                new_ids = list(dict.fromkeys(id_re.findall(html)))
                ids_found.extend(new_ids)
                results["AVITO"][key] = {
                    "url": du, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page.title()[:100],
                    "is_blocked": is_blocked,
                    "status": "BLOCKED" if is_blocked else "OK",
                    "ids_found": ids_found[-10:],
                }
                log.info("AVITO disc[%d]: %d B, blocked=%s, IDs=%s", i, len(html), is_blocked, ids_found[-5:])
                hp = PROJECT / f"v40_AVITO_disc{i}.html"
                hp.write_text(html[:600000], encoding="utf-8")
            except Exception as e:
                el = time.monotonic() - t1
                results["AVITO"][key] = {"url": du, "status": f"ERROR: {e}", "elapsed": round(el, 1)}
                log.warning("AVITO disc[%d]: %s", i, e)
            time.sleep(3)

        # Шаг 2: парсинг найденных карточек
        test_ids = ids_found[:2] if ids_found else []
        if not test_ids:
            results["AVITO"]["note"] = "No real IDs discovered from search"
            log.warning("AVITO: no IDs found, trying known ID 1096783029")
            test_ids = ["1096783029"]  # из v35

        for aid in test_ids:
            url = av_col.build_url(aid)
            log.info("AVITO card %s: %s", aid, url)
            t1 = time.monotonic()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(6000)
                html = page.content()
                el = time.monotonic() - t1
                is_blocked = "Доступ ограничен" in html or "проверка безопасности" in html
                is_404 = "Страница не найдена" in html or "404" in html[:500]
                has_jl = "application/ld+json" in html[:30000]
                has_pre = "__PRELOADED_STATE__" in html or "__INITIAL_STATE__" in html
                results["AVITO"][aid] = {
                    "url": url, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page.title()[:100],
                    "is_blocked": is_blocked, "is_404": is_404,
                    "has_jsonld": has_jl, "has_preloaded": has_pre,
                    "status": "BLOCKED" if is_blocked else ("404" if is_404 else "CONTENT"),
                }
                log.info("AVITO %s: %d B, blocked=%s, 404=%s, jl=%s", aid, len(html), is_blocked, is_404, has_jl)
                hp = PROJECT / f"v40_AVITO_{aid}.html"
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

        # ============================================================
        # FARPOST
        # ============================================================
        log.info("=== FARPOST ===")
        results["FARPOST"] = {}
        from r52.collectors.farpost import FarpostCollector
        fp_col = FarpostCollector(use_proxies=False)

        for fid in FARPOST_IDS:
            url = fp_col.build_url(fid)
            log.info("FARPOST %s: %s", fid, url)
            t1 = time.monotonic()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(4000)
                html = page.content()
                el = time.monotonic() - t1
                is_404 = "Страница не найдена" in html or "404" in html[:500] or "не существует" in html.lower()[:2000]
                has_jl = "application/ld+json" in html[:30000]
                results["FARPOST"][fid] = {
                    "url": url, "bytes": len(html), "elapsed": round(el, 1),
                    "title": page.title()[:100],
                    "is_404": is_404, "has_jsonld": has_jl,
                    "status": "404" if is_404 else "CONTENT",
                }
                log.info("FARPOST %s: %d B, %.1fs, 404=%s, title=%s", fid, len(html), el, is_404, page.title()[:50])
                hp = PROJECT / f"v40_FARPOST_{fid}.html"
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

        browser.close()

    # ============================================================
    # ИТОГ
    # ============================================================
    el = time.monotonic() - t0
    results["_meta"] = {
        "version": "v40",
        "elapsed_sec": round(el, 1),
        "timestamp": datetime.now(timezone(timedelta(hours=10))).isoformat(),
        "engine": "playwright",
        "ids": {"OZON": OZON_IDS, "WB": WB_IDS, "FARPOST": FARPOST_IDS},
    }
    out = PROJECT / "diag_v40_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("=== V40 done %.1fs -> %s (%d B) ===", el, out.name, out.stat().st_size)

    log.info("--- SUMMARY ---")
    for plat in ("OZON", "WB", "AVITO", "FARPOST"):
        data = results.get(plat, {})
        st = {k: v.get("status", "?") if isinstance(v, dict) else "?" for k, v in data.items()}
        log.info("%s: %s", plat, json.dumps(st, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())

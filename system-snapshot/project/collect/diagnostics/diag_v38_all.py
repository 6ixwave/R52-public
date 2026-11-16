#!/usr/bin/env python3
"""diag_v38_all.py — Диагностика всех 4 площадок с РЕАЛЬными ID.

Особенности v38 (vs v37):
- Реальные ID товаров (не 123456/789012).
- Ozon: онлайн GET + парсинг (не только офлайн HTML).
- WB: API v2 + detail.aspx с реальными nm.
- Avito: шаг обнаружения реальных ID из каталога, затем парсинг.
- Farpost: реальные bulletin ID + проверка формата intake.
- Сохранение HTML каждого успешного GET для офлайн-анализа.
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("diag_v38")

# ============================================================
# РЕАЛЬНЫЕ ID (найдены через web_search 11.11.2026)
# ============================================================
OZON_IDS = ["229225961", "1317678381"]
WB_IDS = ["1252522762", "1024173669"]
FARPOST_IDS = ["134004859", "132093530"]
# AVITO: обнаруживаем из каталога

results: dict = {}


# ============================================================
# OZON
# ============================================================
def test_ozon():
    from r52.collectors.ozon import OzonCollector

    log.info("=== OZON: онлайн-сбор ===")
    col = OzonCollector(use_proxies=False)
    results["OZON"] = {}

    for oid in OZON_IDS:
        url = col.build_url(oid)
        log.info("OZON %s: GET %s", oid, url)
        try:
            resp = col.fetch(url)
        except Exception as e:
            results["OZON"][oid] = {"status": f"FETCH_ERROR: {e}"}
            continue

        if resp is None:
            results["OZON"][oid] = {"status": "FETCH_NONE", "url": url}
            continue

        html = resp.text
        # Сохранить HTML
        html_path = PROJECT / f"v38_OZON_{oid}.html"
        html_path.write_text(html, encoding="utf-8")
        log.info("OZON %s: %d байт → %s", oid, len(html), html_path.name)

        try:
            obs = col.parse_page(resp, oid, url, "")
            if obs:
                fields = {
                    k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100])
                    for k, v in obs.get("fields", {}).items()
                }
                prices = obs.get("price_candidates", [])
                media = obs.get("media", {}).get("items", [])
                results["OZON"][oid] = {
                    "status": "PARSED",
                    "http_code": resp.status_code,
                    "html_bytes": len(html),
                    "fields": fields,
                    "prices": prices[:6],
                    "media_count": len(media),
                    "media_types": _summarize_media(media),
                    "availability": obs.get("availability", "UNKNOWN"),
                    "category_path": obs.get("category_path", ""),
                }
                log.info(
                    "OZON %s: PARSED title=%s prices=%d media=%d avail=%s",
                    oid, fields.get("title", "?")[:50], len(prices), len(media),
                    obs.get("availability", "?"),
                )
            else:
                results["OZON"][oid] = {
                    "status": "PARSE_NONE",
                    "http_code": resp.status_code,
                    "html_bytes": len(html),
                }
                log.warning("OZON %s: PARSE_NONE (%d байт)", oid, len(html))
        except Exception as e:
            results["OZON"][oid] = {"status": f"PARSE_ERROR: {e}", "html_bytes": len(html)}
            log.exception("OZON %s: parse error", oid)

        time.sleep(3)


# ============================================================
# WILDBERRIES
# ============================================================
def test_wb():
    from r52.collectors.wb import WBCollector

    log.info("=== WB: онлайн-сбор (API + HTML) ===")
    col = WBCollector(use_proxies=False)
    results["WB"] = {}

    for wid in WB_IDS:
        # --- Стратегия A: API v2 ---
        api_url = f"https://card.wb.ru/cards/v2/detail?nm={wid}"
        log.info("WB API %s: GET %s", wid, api_url)
        try:
            resp = col.fetch(api_url)
            if resp:
                try:
                    data = resp.json()
                    products = data.get("data", {}).get("products", [])
                    results["WB"][f"api_{wid}"] = {
                        "status": "API_OK" if products else "API_EMPTY",
                        "http_code": resp.status_code,
                        "bytes": len(resp.text),
                        "product_count": len(products),
                    }
                    if products:
                        p = products[0]
                        results["WB"][f"api_{wid}"]["name"] = p.get("name", "")[:100]
                        results["WB"][f"api_{wid}"]["brand"] = p.get("brand", "")
                        results["WB"][f"api_{wid}"]["price"] = p.get("price", {}).get("basic")
                        results["WB"][f"api_{wid}"]["old_price"] = p.get("price", {}).get("total")
                        results["WB"][f"api_{wid}"]["images"] = len(p.get("im", []))
                        results["WB"][f"api_{wid}"]["rating"] = p.get("rating")
                        results["WB"][f"api_{wid}"]["reviews"] = p.get("feedbacks")
                        results["WB"][f"api_{wid}"]["description"] = (p.get("description") or "")[:200]
                        results["WB"][f"api_{wid}"]["options"] = p.get("options", [])[:10]
                        results["WB"][f"api_{wid}"]["sizes"] = p.get("sizes", [])
                        results["WB"][f"api_{wid}"]["withOptions"] = p.get("withOptions", [])[:5]
                except (json.JSONDecodeError, ValueError):
                    results["WB"][f"api_{wid}"] = {
                        "status": "API_NOT_JSON",
                        "http_code": resp.status_code,
                        "bytes": len(resp.text),
                        "snippet": resp.text[:300],
                    }
            else:
                results["WB"][f"api_{wid}"] = {"status": "FETCH_NONE", "url": api_url}
        except Exception as e:
            results["WB"][f"api_{wid}"] = {"status": f"ERROR: {e}"}
        time.sleep(3)

        # --- Стратегия B: detail.aspx (HTML) ---
        detail_url = col.build_url(wid)
        log.info("WB HTML %s: GET %s", wid, detail_url)
        try:
            resp = col.fetch(detail_url)
            if resp:
                html = resp.text
                is_challenge = (
                    "wbaas" in html.lower()
                    or "browser-check" in html.lower()
                    or "antibot" in html.lower()
                )
                has_jsonld = "application/ld+json" in html
                has_state = "__STATE__" in html or "data-state" in html
                results["WB"][f"html_{wid}"] = {
                    "status": "CHALLENGE" if is_challenge else ("CONTENT" if len(html) > 5000 else "THIN"),
                    "http_code": resp.status_code,
                    "bytes": len(html),
                    "is_challenge": is_challenge,
                    "has_jsonld": has_jsonld,
                    "has_state": has_state,
                    "snippet": html[:300],
                }
                if not is_challenge and len(html) > 5000:
                    html_path = PROJECT / f"v38_WB_{wid}.html"
                    html_path.write_text(html, encoding="utf-8")
                    log.info("WB HTML %s: saved %d байт → %s", wid, len(html), html_path.name)
                    try:
                        obs = col.parse_page(resp, wid, detail_url, "")
                        if obs:
                            results["WB"][f"html_{wid}"]["parsed"] = True
                            results["WB"][f"html_{wid}"]["fields"] = {
                                k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100])
                                for k, v in obs.get("fields", {}).items()
                            }
                            results["WB"][f"html_{wid}"]["prices"] = obs.get("price_candidates", [])[:4]
                            results["WB"][f"html_{wid}"]["media_count"] = len(obs.get("media", {}).get("items", []))
                        else:
                            results["WB"][f"html_{wid}"]["parsed"] = False
                    except Exception as e:
                        results["WB"][f"html_{wid}"]["parse_error"] = str(e)
            else:
                results["WB"][f"html_{wid}"] = {"status": "FETCH_NONE", "url": detail_url}
        except Exception as e:
            results["WB"][f"html_{wid}"] = {"status": f"ERROR: {e}"}
        time.sleep(3)


# ============================================================
# AVITO
# ============================================================
def test_avito():
    from r52.collectors.avito import AvitoCollector

    log.info("=== AVITO: обнаружение + онлайн-сбор ===")
    col = AvitoCollector(use_proxies=False)
    results["AVITO"] = {}

    # --- Шаг 1: Обнаружение реальных ID из каталога ---
    cat_urls = [
        "https://www.avito.ru/moskva/tech/communication/cellphones",
        "https://www.avito.ru/moskva/tech/notebooks",
    ]
    ids_found = []
    for cat_url in cat_urls:
        if len(ids_found) >= 2:
            break
        log.info("AVITO discovery: GET %s", cat_url)
        try:
            resp = col.fetch(cat_url)
            if resp:
                html = resp.text
                # Паттерны: /ad/1234567890, /moskva/.../1234567890, data-ad-id
                id_re = re.compile(r'(?:/ad/|data-ad-id=["\']?)(\d{9,12})')
                ids_found.extend(dict.fromkeys(id_re.findall(html)))
                is_blocked = "Доступ ограничен" in html or "проверка безопасности" in html
                results["AVITO"][f"discovery_{cat_url.split('/')[-1]}"] = {
                    "status": "BLOCKED" if is_blocked else "OK",
                    "http_code": resp.status_code,
                    "bytes": len(html),
                    "ids_found": ids_found[-5:],
                }
                log.info("AVITO discovery %s: %d байт, IDs=%s", cat_url.split("/")[-1], len(html), ids_found[-3:])
            else:
                results["AVITO"][f"discovery_{cat_url.split('/')[-1]}"] = {"status": "FETCH_NONE"}
        except Exception as e:
            results["AVITO"][f"discovery_{cat_url.split('/')[-1]}"] = {"status": f"ERROR: {e}"}
        time.sleep(3)

    # --- Шаг 2: Парсинг найденных ID ---
    test_ids = ids_found[:2] if ids_found else []
    if not test_ids:
        log.warning("AVITO: не найдены реальные ID, пропуск парсинга карточек")
        results["AVITO"]["note"] = "No real IDs discovered"
        return

    for aid in test_ids:
        url = col.build_url(aid)
        log.info("AVITO %s: GET %s", aid, url)
        try:
            resp = col.fetch(url)
            if resp:
                html = resp.text
                is_blocked = "Доступ ограничен" in html or "проверка безопасности" in html
                is_404 = "Страница не найдена" in html or "404" in html[:500]
                has_jsonld = "application/ld+json" in html
                has_preloaded = "__PRELOADED_STATE__" in html
                results["AVITO"][aid] = {
                    "status": "BLOCKED" if is_blocked else ("404" if is_404 else "CONTENT"),
                    "http_code": resp.status_code,
                    "bytes": len(html),
                    "is_blocked": is_blocked,
                    "has_jsonld": has_jsonld,
                    "has_preloaded": has_preloaded,
                    "url": url,
                }
                if not is_blocked and not is_404:
                    html_path = PROJECT / f"v38_AVITO_{aid}.html"
                    html_path.write_text(html, encoding="utf-8")
                    log.info("AVITO %s: saved %d байт → %s", aid, len(html), html_path.name)
                    try:
                        obs = col.parse_page(resp, aid, url, "")
                        if obs:
                            results["AVITO"][aid]["parsed"] = True
                            results["AVITO"][aid]["fields"] = {
                                k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100])
                                for k, v in obs.get("fields", {}).items()
                            }
                            results["AVITO"][aid]["prices"] = obs.get("price_candidates", [])[:4]
                            results["AVITO"][aid]["media_count"] = len(obs.get("media", {}).get("items", []))
                            results["AVITO"][aid]["availability"] = obs.get("availability", "UNKNOWN")
                        else:
                            results["AVITO"][aid]["parsed"] = False
                    except Exception as e:
                        results["AVITO"][aid]["parse_error"] = str(e)
            else:
                results["AVITO"][aid] = {"status": "FETCH_NONE", "url": url}
        except Exception as e:
            results["AVITO"][aid] = {"status": f"ERROR: {e}"}
        time.sleep(3)


# ============================================================
# FARPOST
# ============================================================
def test_farpost():
    from r52.collectors.farpost import FarpostCollector

    log.info("=== FARPOST: онлайн-сбор ===")
    col = FarpostCollector(use_proxies=False)
    results["FARPOST"] = {}

    for fid in FARPOST_IDS:
        url = col.build_url(fid)
        log.info("FARPOST %s: GET %s", fid, url)
        try:
            resp = col.fetch(url)
            if resp:
                html = resp.text
                results["FARPOST"][fid] = {
                    "status": "FETCHED",
                    "http_code": resp.status_code,
                    "bytes": len(html),
                    "url": url,
                }
                html_path = PROJECT / f"v38_FARPOST_{fid}.html"
                html_path.write_text(html, encoding="utf-8")
                log.info("FARPOST %s: saved %d байт → %s", fid, len(html), html_path.name)
                try:
                    obs = col.parse_page(resp, fid, url, "")
                    if obs:
                        results["FARPOST"][fid]["parsed"] = True
                        results["FARPOST"][fid]["fields"] = {
                            k: (v.get("value", "")[:100] if isinstance(v, dict) else str(v)[:100])
                            for k, v in obs.get("fields", {}).items()
                        }
                        results["FARPOST"][fid]["prices"] = obs.get("price_candidates", [])[:4]
                        media = obs.get("media", {}).get("items", [])
                        results["FARPOST"][fid]["media_count"] = len(media)
                        results["FARPOST"][fid]["media_types"] = _summarize_media(media)
                        results["FARPOST"][fid]["availability"] = obs.get("availability", "UNKNOWN")
                        results["FARPOST"][fid]["category_path"] = obs.get("category_path", "")
                    else:
                        results["FARPOST"][fid]["parsed"] = False
                except Exception as e:
                    results["FARPOST"][fid]["parse_error"] = str(e)
            else:
                results["FARPOST"][fid] = {"status": "FETCH_NONE", "url": url}
        except Exception as e:
            results["FARPOST"][fid] = {"status": f"ERROR: {e}"}
        time.sleep(3)

    # --- Проверка формата intake ---
    log.info("FARPOST: проверка формата intake")
    try:
        from r52.collectors.farpost import FarpostCollector as FC
        fake_obs = {
            "fields": {"title": {"status": "KNOWN", "value": "test", "method": "TEST"}},
            "price_candidates": [{"role": "regular", "amount_minor": 1000, "label": "test"}],
            "media": {"items": []},
            "availability": "IN_STOCK",
        }
        fc = FC(use_proxies=False)
        fc._ensure_evidence(fake_obs)
        fc._ensure_price_amounts(fake_obs)
        fc._ensure_availability_in_fields(fake_obs)
        # Проверка: есть ли evidence, amount, visible_label
        ev_ok = fake_obs["fields"]["title"].get("evidence") is not None
        amt_ok = "amount" in fake_obs["price_candidates"][0]
        vl_ok = "visible_label" in fake_obs["price_candidates"][0]
        results["FARPOST"]["intake_format_match"] = ev_ok and amt_ok and vl_ok
        log.info("FARPOST: intake format match = %s (ev=%s amt=%s vl=%s)",
                 results["FARPOST"]["intake_format_match"], ev_ok, amt_ok, vl_ok)
    except Exception as e:
        results["FARPOST"]["intake_format_match"] = False
        results["FARPOST"]["intake_error"] = str(e)


# ============================================================
# ХЕЛПЕР
# ============================================================
def _summarize_media(items: list) -> dict:
    """Краткая сводка по типам медиа."""
    summary = {"images": 0, "videos": 0, "other": 0}
    for item in items:
        if not isinstance(item, dict):
            summary["other"] += 1
            continue
        t = item.get("type", item.get("kind", ""))
        url = item.get("url", item.get("src", ""))
        if t == "video" or re.search(r"\.(mp4|webm|ogg)", url, re.I):
            summary["videos"] += 1
        elif t in ("image", "photo") or re.search(r"\.(jpg|jpeg|png|webp|avif)", url, re.I):
            summary["images"] += 1
        else:
            summary["other"] += 1
    return summary


# ============================================================
# MAIN
# ============================================================
def main():
    t0 = time.monotonic()
    log.info("=== DIAG V38: все 4 площадки, РЕАЛЬНЫЕ ID ===")
    log.info("Ozon: %s", OZON_IDS)
    log.info("WB:   %s", WB_IDS)
    log.info("Farpost: %s", FARPOST_IDS)
    log.info("Avito: discovery → parse")

    test_ozon()
    test_wb()
    test_avito()
    test_farpost()

    elapsed = time.monotonic() - t0
    results["_meta"] = {
        "version": "v38",
        "elapsed_sec": round(elapsed, 1),
        "timestamp": datetime.now(timezone(timedelta(hours=10))).isoformat(),
        "ids": {"OZON": OZON_IDS, "WB": WB_IDS, "FARPOST": FARPOST_IDS},
    }

    out = PROJECT / "diag_v38_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("=== V38 завершено за %.1fs → %s (%d байт) ===", elapsed, out.name, out.stat().st_size)

    # Краткая сводка в лог
    log.info("--- СВОДКА ---")
    for plat in ("OZON", "WB", "AVITO", "FARPOST"):
        data = results.get(plat, {})
        statuses = {k: v.get("status", "?") if isinstance(v, dict) else "?" for k, v in data.items()}
        log.info("%s: %s", plat, json.dumps(statuses, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Диагностика v37: все четыре площадки за один запуск.

Офлайн: Ozon (2 HTML), Farpost (3 карточки).
Онлайн: WB (4 стратегии), Avito (3 стратегии).
Результат: diag_v37_results.json + diag_v37_out.txt
"""

import json
import logging
import re
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

# --- Setup ---
BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(BASE / "diag_v37_out.txt", mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v37")

results = {}

# ====================================================================
# 1. OZON — офлайн-парсинг двух HTML
# ====================================================================
def test_ozon_offline():
    log.info("=== OZON: офлайн-парсинг ===")
    from r52.collectors.ozon import OzonCollector

    collector = OzonCollector(use_proxies=False)
    ozon_results = {}

    html_files = {
        "prod1": BASE / "v35_OZON_prod1.html",
        "prod2": BASE / "v35_OZON_prod2.html",
    }

    for name, path in html_files.items():
        log.info("OZON %s: читаю %s", name, path.name)
        if not path.exists():
            ozon_results[name] = {"status": "FILE_MISSING"}
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        log.info("OZON %s: %d байт, %d строк", name, len(html), html.count("\n"))

        # Фейковый Response
        class FakeResp:
            def __init__(self, text):
                self.text = text
                self.status_code = 200

        listing_id = "7" if name == "prod1" else "2"
        url = f"https://www.ozon.ru/product/test-{listing_id}/"

        try:
            obs = collector.parse_page(FakeResp(html), listing_id, url, "test")
            if obs is None:
                ozon_results[name] = {"status": "PARSE_NONE"}
                continue

            # Сводка
            fields_summary = {}
            for k, v in obs.get("fields", {}).items():
                if isinstance(v, dict):
                    val = v.get("value", "")
                    fields_summary[k] = val[:120] if isinstance(val, str) else str(val)[:120]

            media = obs.get("media", {}).get("items", [])
            img_count = sum(1 for m in media if m.get("type") == "image")
            vid_count = sum(1 for m in media if m.get("type") == "video")

            # Фильтрация: какие медиа реально товарные
            ozon_product_imgs = []
            payment_icons = []
            video_covers = []
            rec_imgs = []
            for m in media:
                u = m.get("source_url", "")
                if "payments-cdn" in u:
                    payment_icons.append(u)
                elif "/video-" in u and "cover" in u:
                    video_covers.append(u)
                elif "multimedia" in u:
                    # Разделяем по wc-размеру: wc50/wc140/wc500 = товар, wc1000 = og, wc600/c600 = og
                    if "wc50/" in u or "wc140/" in u or "wc500/" in u:
                        ozon_product_imgs.append(u)
                    else:
                        rec_imgs.append(u)
                elif "cms" in u:
                    rec_imgs.append(u)
                elif "marketing-api" in u:
                    rec_imgs.append(u)
                else:
                    ozon_product_imgs.append(u)

            prices = obs.get("price_candidates", [])
            price_summary = [{"role": p.get("role"), "amount_minor": p.get("amount_minor"),
                              "label": p.get("label")} for p in prices[:10]]

            ozon_results[name] = {
                "status": "PARSED",
                "html_bytes": len(html),
                "fields": fields_summary,
                "prices": price_summary,
                "media_total": len(media),
                "media_images": img_count,
                "media_videos": vid_count,
                "media_product_imgs": len(ozon_product_imgs),
                "media_payment_icons": len(payment_icons),
                "media_video_covers": len(video_covers),
                "media_other": len(rec_imgs),
                "availability": obs.get("availability"),
                "category": obs.get("category_path"),
            }
            log.info("OZON %s: title=%s, prices=%d, imgs=%d (product=%d, icons=%d, covers=%d, other=%d)",
                     name, fields_summary.get("title", "?")[:50],
                     len(prices), img_count, len(ozon_product_imgs),
                     len(payment_icons), len(video_covers), len(rec_imgs))
        except Exception as e:
            ozon_results[name] = {"status": "ERROR", "error": str(e), "tb": traceback.format_exc()}
            log.error("OZON %s: %s", name, e)

    results["OZON"] = ozon_results


# ====================================================================
# 2. WILDBERRIES — 4 стратегии онлайн
# ====================================================================
def test_wb_online():
    log.info("=== WB: онлайн-стратегии ===")
    import requests

    # Тестовый nm (наименование) — берём популярный
    TEST_NMS = ["199298871", "123456789"]
    headers_base = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    headers_mobile = {
        **headers_base,
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    }

    wb_results = {}

    # --- Стратегия A: API card.wb.ru ---
    log.info("WB A: card.wb.ru/cards/v2/detail")
    for nm in TEST_NMS:
        url = f"https://card.wb.ru/cards/v2/detail?nm={nm}"
        try:
            r = requests.get(url, headers=headers_base, timeout=15)
            body = r.text[:2000]
            wb_results[f"A_api_{nm}"] = {
                "status_code": r.status_code,
                "bytes": len(r.text),
                "snippet": body[:500],
            }
            log.info("WB A nm=%s: HTTP %d, %d байт", nm, r.status_code, len(r.text))
        except Exception as e:
            wb_results[f"A_api_{nm}"] = {"error": str(e)}
        time.sleep(3)

    # --- Стратегия B: mobile info ---
    log.info("WB B: mobile/catalog/{nm}/info.aspx")
    for nm in TEST_NMS:
        url = f"https://www.wildberries.ru/mobile/catalog/{nm}/info.aspx"
        try:
            r = requests.get(url, headers=headers_mobile, timeout=15)
            wb_results[f"B_mobile_{nm}"] = {
                "status_code": r.status_code,
                "bytes": len(r.text),
                "is_challenge": "wbaas" in r.text[:3000].lower() or "browser-check" in r.text[:3000],
                "snippet": r.text[:500],
            }
            log.info("WB B nm=%s: HTTP %d, %d байт, challenge=%s",
                     nm, r.status_code, len(r.text),
                     "wbaas" in r.text[:3000].lower())
        except Exception as e:
            wb_results[f"B_mobile_{nm}"] = {"error": str(e)}
        time.sleep(3)

    # --- Стратегия C: detail.aspx с mobile UA ---
    log.info("WB C: detail.aspx mobile UA")
    for nm in TEST_NMS:
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        try:
            r = requests.get(url, headers=headers_mobile, timeout=15)
            is_challenge = ("wbaas" in r.text[:3000].lower()
                           or "browser-check" in r.text[:3000]
                           or "antibot" in r.text[:3000])
            wb_results[f"C_detail_{nm}"] = {
                "status_code": r.status_code,
                "bytes": len(r.text),
                "is_challenge": is_challenge,
                "has_jsonld": "application/ld+json" in r.text,
                "has_state": "__STATE__" in r.text or "data-state" in r.text,
                "snippet": r.text[:300],
            }
            log.info("WB C nm=%s: HTTP %d, %d байт, challenge=%s, jsonld=%s",
                     nm, r.status_code, len(r.text), is_challenge,
                     "application/ld+json" in r.text)
        except Exception as e:
            wb_results[f"C_detail_{nm}"] = {"error": str(e)}
        time.sleep(3)

    # --- Стратегия D: batch API (cards/v2/batch) ---
    log.info("WB D: card.wb.ru/cards/v2/batch")
    for nm in TEST_NMS:
        url = "https://card.wb.ru/cards/v2/batch"
        payload = json.dumps({"nm": [int(nm)]})
        try:
            r = requests.post(url, headers={**headers_base, "Content-Type": "application/json"},
                              data=payload, timeout=15)
            wb_results[f"D_batch_{nm}"] = {
                "status_code": r.status_code,
                "bytes": len(r.text),
                "snippet": r.text[:500],
            }
            log.info("WB D nm=%s: HTTP %d, %d байт", nm, r.status_code, len(r.text))
        except Exception as e:
            wb_results[f"D_batch_{nm}"] = {"error": str(e)}
        time.sleep(3)

    results["WB"] = wb_results


# ====================================================================
# 3. AVITO — 3 стратегии онлайн
# ====================================================================
def test_avito_online():
    log.info("=== AVITO: онлайн-стратегии ===")
    import requests

    # Тестовый ID объявления (большой, реальный)
    TEST_IDS = ["1000000000", "1234567890"]
    headers_base = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    headers_mobile = {
        **headers_base,
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    }

    avito_results = {}

    # --- Стратегия A: mobile URL ---
    log.info("AVITO A: m.avito.ru")
    for ad_id in TEST_IDS:
        url = f"https://m.avito.ru/ad/{ad_id}"
        try:
            r = requests.get(url, headers=headers_mobile, timeout=15)
            is_blocked = ("Доступ ограничен" in r.text[:5000]
                         or "проверка безопасности" in r.text[:5000]
                         or "firewall" in r.text[:3000].lower())
            avito_results[f"A_mobile_{ad_id}"] = {
                "status_code": r.status_code,
                "bytes": len(r.text),
                "is_blocked": is_blocked,
                "has_jsonld": "application/ld+json" in r.text,
                "has_preloaded": "__PRELOADED_STATE__" in r.text,
                "title_match": (lambda m: m.group(1) if m else None)(re.search(r"<title>([^<]+)</title>", r.text)),
                "snippet": r.text[:400],
            }
            log.info("AVITO A id=%s: HTTP %d, %d байт, blocked=%s",
                     ad_id, r.status_code, len(r.text), is_blocked)
        except Exception as e:
            avito_results[f"A_mobile_{ad_id}"] = {"error": str(e)}
        time.sleep(5)

    # --- Стратегия B: www.avito.ru с mobile UA ---
    log.info("AVITO B: www.avito.ru mobile UA")
    for ad_id in TEST_IDS:
        url = f"https://www.avito.ru/ad/{ad_id}"
        try:
            r = requests.get(url, headers=headers_mobile, timeout=15)
            is_blocked = ("Доступ ограничен" in r.text[:5000]
                         or "проверка безопасности" in r.text[:5000])
            avito_results[f"B_www_mobile_{ad_id}"] = {
                "status_code": r.status_code,
                "bytes": len(r.text),
                "is_blocked": is_blocked,
                "has_jsonld": "application/ld+json" in r.text,
                "snippet": r.text[:400],
            }
            log.info("AVITO B id=%s: HTTP %d, %d байт, blocked=%s",
                     ad_id, r.status_code, len(r.text), is_blocked)
        except Exception as e:
            avito_results[f"B_www_mobile_{ad_id}"] = {"error": str(e)}
        time.sleep(5)

    # --- Стратегия C: RSS-фид Avito ---
    log.info("AVITO C: RSS")
    for ad_id in TEST_IDS:
        url = f"https://www.avito.ru/rss/all/{ad_id}"
        try:
            r = requests.get(url, headers=headers_base, timeout=15)
            avito_results[f"C_rss_{ad_id}"] = {
                "status_code": r.status_code,
                "bytes": len(r.text),
                "is_xml": r.text.strip().startswith("<?xml") or r.text.strip().startswith("<rss"),
                "snippet": r.text[:500],
            }
            log.info("AVITO C id=%s: HTTP %d, %d байт, xml=%s",
                     ad_id, r.status_code, len(r.text),
                     r.text.strip()[:5] in ("<?xm", "<rss"))
        except Exception as e:
            avito_results[f"C_rss_{ad_id}"] = {"error": str(e)}
        time.sleep(5)

    results["AVITO"] = avito_results


# ====================================================================
# 4. FARPOST — интеграционный тест со штатным intake
# ====================================================================
def test_farpost_integration():
    log.info("=== FARPOST: интеграция со штатным intake ===")
    from r52.collectors.farpost import FarpostCollector

    collector = FarpostCollector(use_proxies=False)
    fp_results = {}

    # Тестовые карточки Farpost
    test_ids = ["123456", "789012"]

    for lid in test_ids:
        url = collector.build_url(lid)
        log.info("FARPOST %s: %s", lid, url)
        try:
            # Пытаемся fetch (может быть офлайн или онлайн)
            obs = collector.collect_product(lid, url, "test")
            if obs is None:
                fp_results[lid] = {"status": "FETCH_NONE"}
                continue

            # Проверка структуры для intake
            fmt = obs.get("format", "")
            source = obs.get("collector_source", "")
            inner = obs.get("observation", {})
            fields = inner.get("fields", {})
            media = inner.get("media", {}).get("items", [])
            prices = inner.get("price_candidates", [])

            fp_results[lid] = {
                "status": "OK",
                "format": fmt,
                "source": source,
                "fields_count": len(fields),
                "media_count": len(media),
                "prices_count": len(prices),
                "has_evidence": all(
                    f.get("evidence") for f in fields.values()
                    if isinstance(f, dict) and f.get("status") == "KNOWN"
                ),
            }
            log.info("FARPOST %s: format=%s, fields=%d, media=%d, prices=%d",
                     lid, fmt, len(fields), len(media), len(prices))
        except Exception as e:
            fp_results[lid] = {"status": "ERROR", "error": str(e)}

    # Проверка: intake/eye.py принимает ли наш формат
    log.info("FARPOST: проверка формата intake")
    try:
        eye_path = BASE / "r52" / "intake" / "eye.py"
        if eye_path.exists():
            eye_src = eye_path.read_text(encoding="utf-8")
            # Ищем FORMATS или ACCEPTED
            has_format = "JARVIS_BR02_EYE_USER_CAPTURE" in eye_src
            fp_results["intake_format_match"] = has_format
            log.info("FARPOST: intake format match = %s", has_format)
        else:
            fp_results["intake_format_match"] = "eye.py not found"
            log.warning("FARPOST: eye.py не найден")
    except Exception as e:
        fp_results["intake_format_match"] = f"error: {e}"

    results["FARPOST"] = fp_results


# ====================================================================
# MAIN
# ====================================================================
def main():
    log.info("=== DIAG V37: все 4 площадки ===")
    start = time.monotonic()

    # 1. Ozon (офлайн — быстрый)
    try:
        test_ozon_offline()
    except Exception as e:
        results["OZON"] = {"error": str(e), "tb": traceback.format_exc()}
        log.error("OZON section: %s", e)

    # 2. WB (онлайн — медленный)
    try:
        test_wb_online()
    except Exception as e:
        results["WB"] = {"error": str(e), "tb": traceback.format_exc()}
        log.error("WB section: %s", e)

    # 3. Avito (онлайн — медленный)
    try:
        test_avito_online()
    except Exception as e:
        results["AVITO"] = {"error": str(e), "tb": traceback.format_exc()}
        log.error("AVITO section: %s", e)

    # 4. Farpost (интеграция)
    try:
        test_farpost_integration()
    except Exception as e:
        results["FARPOST"] = {"error": str(e), "tb": traceback.format_exc()}
        log.error("FARPOST section: %s", e)

    elapsed = time.monotonic() - start
    results["_meta"] = {
        "version": "v37",
        "elapsed_sec": round(elapsed, 1),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Сохранение
    out_path = BASE / "diag_v37_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("=== V37 завершено за %.1fс → %s ===", elapsed, out_path.name)

    # Краткая сводка
    log.info("--- СВОДКА ---")
    for src in ("OZON", "WB", "AVITO", "FARPOST"):
        data = results.get(src, {})
        log.info("%s: %s", src, json.dumps(data, ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()

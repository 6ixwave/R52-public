#!/usr/bin/env python3
"""v128: Фокусный тест — WB media/price + Farpost (включая 98836529).

Цели:
1. WB: проверить, есть ли цены в card.json (priceU/priceD/price на топ-уровне).
2. WB: построить галерею фото (не только 1, а по photo_count).
3. FP: проверить 98836529 (тайм-аут в v127) + 129606872.
4. Общий лимит: 120 сек.
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("v128")

OUT_DIR = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12"
DEADLINE = time.monotonic() + 115  # 115 сек буфер


def remaining():
    return max(0, DEADLINE - time.monotonic())


def save_json(data, fname):
    path = os.path.join(OUT_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("  saved -> %s", fname)


# ================================================================
# WILDBERRIES
# ================================================================
def test_wb():
    log.info("=" * 60)
    log.info("WILDBERRIES (media + price from card.json)")
    log.info("=" * 60)

    from r52.collectors.wb import WBCollector, JSON_HEADERS
    wb = WBCollector(data_dir=Path(OUT_DIR), use_proxies=False)

    test_ids = ["199298871", "259671175"]
    results = []

    for nm_id_str in test_ids:
        nm_id = int(nm_id_str)
        t0 = time.monotonic()
        log.info("\n--- WB %s ---", nm_id_str)

        # 1. Fetch card.json directly
        vol = nm_id // 100000
        part = nm_id // 1000
        path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        host = wb._find_basket_host(nm_id, vol, path)
        if not host:
            log.info("  basket host not found, skip")
            results.append({"id": nm_id_str, "status": "NO_HOST"})
            continue

        card_url = f"https://{host}{path}"
        log.info("  card_url: %s", card_url)
        resp = wb._http_get(card_url, headers=JSON_HEADERS, timeout=15, fast=True)

        card_data = None
        if resp and resp.status_code == 200:
            try:
                card_data = resp.json()
                log.info("  card.json: %d keys, %dB", len(card_data), len(resp.content))
            except Exception as e:
                log.info("  card.json parse error: %s", e)

        # 2. Check price fields in card.json
        prices_found = []
        if card_data:
            # Top-level price fields
            for pk in ("price", "priceU", "priceD", "oldPrice", "priceTotal"):
                pv = card_data.get(pk)
                if isinstance(pv, (int, float)) and pv > 0:
                    prices_found.append(f"{pk}={pv}")
            # selling sub-object
            selling = card_data.get("selling", {})
            if isinstance(selling, dict):
                for pk in ("price", "priceU", "priceD", "oldPrice"):
                    pv = selling.get(pk)
                    if isinstance(pv, (int, float)) and pv > 0:
                        prices_found.append(f"selling.{pk}={pv}")
            # media info
            media = card_data.get("media", {})
            if isinstance(media, dict):
                log.info("  media: photo_count=%s has_video=%s",
                         media.get("photo_count"), media.get("has_video"))
            # All top-level keys for debugging
            log.info("  card.json keys: %s", sorted(card_data.keys()))

        if prices_found:
            log.info("  PRICES in card.json: %s", prices_found)
        else:
            log.info("  PRICES in card.json: NONE (need API or HTML)")

        # 3. Try catalog.wb.ru as alternative price source
        cat_url = (f"https://catalog.wb.ru/cards/v1/detail"
                   f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}")
        log.info("  trying catalog.wb.ru...")
        resp2 = wb._http_get(cat_url, headers=JSON_HEADERS, timeout=12, fast=True)
        cat_price = None
        if resp2:
            log.info("  catalog.wb.ru -> HTTP %d", resp2.status_code)
            if resp2.status_code == 200:
                try:
                    cd = resp2.json()
                    prods = cd.get("data", {}).get("items") or cd.get("products", [])
                    if prods:
                        p = prods[0]
                        sizes = p.get("sizes", [])
                        if sizes:
                            pr = sizes[0].get("price", {})
                            cat_price = pr
                            qty = sizes[0].get("qty")
                            log.info("  catalog price: %s qty=%s", pr, qty)
                except Exception as e:
                    log.info("  catalog parse: %s", e)
        if not cat_price:
            log.info("  catalog.wb.ru: no price")

        # 4. Build media gallery (multiple photos)
        media_items = []
        if host:
            for sz in ("S800", "S1200", "S500"):
                u = (f"https://{host}/vol{vol}/part{part}/{nm_id}"
                     f"/images/{sz}/{sz}.jpg")
                media_items.append({"type": "image", "source_url": u, "image_data": None})
        log.info("  media gallery: %d URLs built", len(media_items))

        # 5. Full collect_product (for JSON output)
        url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        obs = wb.collect_product(nm_id_str, url, title_hint="")
        elapsed = time.monotonic() - t0

        if obs:
            o = obs["observation"]
            status = o.get("status", "?")
            title = o.get("fields", {}).get("title", {}).get("value", "?")
            n_prices = len(o.get("price_candidates", []))
            n_media = len(o.get("media", {}).get("items", []))
            avail = o.get("availability", "UNKNOWN")
            log.info("  [%.1fs] status=%s title=%s", elapsed, status, title[:60])
            log.info("  prices=%d media=%d avail=%s", n_prices, n_media, avail)
            save_json(obs, f"v128_wb_{nm_id_str}.json")
            results.append({"id": nm_id_str, "status": status,
                            "prices": n_prices, "media": n_media})
        else:
            log.info("  [%.1fs] collect_product -> None", elapsed)
            results.append({"id": nm_id_str, "status": "NONE"})

        if remaining() < 30:
            log.info("  time budget low, stopping WB")
            break

    log.info("\nWB results: %s", results)
    log.info("WB stats: %s", wb.stats)
    return results


# ================================================================
# FARPOST
# ================================================================
def test_farpost():
    log.info("\n" + "=" * 60)
    log.info("FARPOST (98836529 timeout fix + 129606872)")
    log.info("=" * 60)

    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(data_dir=Path(OUT_DIR), use_proxies=False)

    test_ids = ["98836529", "129606872"]
    results = []

    for lid in test_ids:
        t0 = time.monotonic()
        log.info("\n--- FP %s ---", lid)

        url = fp.build_url(lid)
        log.info("  URL: %s", url)

        # Warm-up
        fp._warmup()

        # Fetch with explicit timeout
        try:
            resp = fp.session.get(url, timeout=(10, 45))
            elapsed_fetch = time.monotonic() - t0
            log.info("  fetch: HTTP %d, %dB, [%.1fs]",
                     resp.status_code, len(resp.content), elapsed_fetch)

            if resp.status_code == 200:
                parsed = fp.parse_page(resp, lid, url, "")
                if parsed:
                    fields = parsed["fields"]
                    title = fields.get("title", {}).get("value", "?")
                    n_prices = len(parsed.get("price_candidates", []))
                    n_media = len(parsed.get("media", {}).get("items", []))
                    avail = parsed.get("availability", "UNKNOWN")
                    n_specs = len(fields.get("specs", {}).get("value", {}))
                    seller = fields.get("seller", {}).get("value", "?")

                    log.info("  status=PARTIAL_SUCCESS title=%s", title[:60])
                    log.info("  prices=%d media=%d avail=%s seller=%s specs=%d",
                             n_prices, n_media, avail, seller[:30], n_specs)

                    for pc in parsed.get("price_candidates", []):
                        log.info("    P: %s %d %s (%s)",
                                 pc["role"], pc["amount_minor"], pc["currency"], pc["label"])
                    for mi in parsed.get("media", {}).get("items", [])[:3]:
                        log.info("    M: %s %s", mi["type"], mi["source_url"][:60])

                    # Build full observation JSON
                    obs = {
                        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
                        "capture_action": "EXPLICIT_POPUP_CLICK",
                        "fully_autonomous": False,
                        "collector_source": "FARPOST",
                        "observation": {
                            **parsed,
                            "source": "FARPOST",
                            "id": lid,
                            "url": url,
                            "status": "PARTIAL_SUCCESS",
                            "product_content": True,
                            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                            "http_status": 200,
                        },
                    }
                    save_json(obs, f"v128_fp_{lid}.json")
                    results.append({"id": lid, "status": "PARTIAL_SUCCESS",
                                    "prices": n_prices, "media": n_media})
                else:
                    log.info("  parse_page -> None")
                    results.append({"id": lid, "status": "PARSE_NONE"})
            else:
                log.info("  HTTP %d (not 200)", resp.status_code)
                results.append({"id": lid, "status": f"HTTP_{resp.status_code}"})

        except Exception as e:
            elapsed = time.monotonic() - t0
            log.info("  ERROR [%.1fs]: %s: %s", elapsed, type(e).__name__, e)
            results.append({"id": lid, "status": f"ERROR:{type(e).__name__}"})

        if remaining() < 20:
            log.info("  time budget low, stopping FP")
            break

    log.info("\nFP results: %s", results)
    log.info("FP stats: %s", fp.stats)
    return results


# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    t_start = time.monotonic()
    log.info("v128 started, budget=115s")

    try:
        wb_res = test_wb()
    except Exception as e:
        log.exception("WB section error: %s", e)
        wb_res = [{"status": "SECTION_ERROR"}]

    if remaining() > 25:
        try:
            fp_res = test_farpost()
        except Exception as e:
            log.exception("FP section error: %s", e)
            fp_res = [{"status": "SECTION_ERROR"}]
    else:
        log.info("Skipping FP (time budget)")
        fp_res = []

    total = time.monotonic() - t_start
    log.info("\n" + "=" * 60)
    log.info("v128 DONE in %.1fs", total)
    log.info("WB: %s", wb_res)
    log.info("FP: %s", fp_res)
    log.info("=" * 60)

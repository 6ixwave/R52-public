#!/usr/bin/env python3
"""Диагностика v36: Ozon новый фронтенд, WB API с заголовками, Avito mobile.

Цель:
  1. OZON — офлайн-парсинг v35_OZON_prod1.html (новый data-widget).
  2. WB   — card.wb.ru с Origin/Referer/X-Requested-With.
  3. AVITO — mobile API / m.avito.ru с Playwright + cookies.
"""

import json
import re
import time
import logging
import requests
from pathlib import Path
from html import unescape

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v36")

BASE = Path(__file__).parent
RESULTS = []

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def record(key, data: dict):
    data["key"] = key
    RESULTS.append(data)
    log.info(f"  {key:30s} {data.get('verdict','?'):15s} {str(data.get('note',''))[:80]}")


# =====================================================================
# 1. OZON — офлайн-парсинг нового фронтенда
# =====================================================================
log.info("=" * 60)
log.info("OZON: offline parse of v35_OZON_prod1.html (new data-widget)")
log.info("=" * 60)

ozon_html_path = BASE / "v35_OZON_prod1.html"
if ozon_html_path.exists():
    html = ozon_html_path.read_text(encoding="utf-8")
    log.info(f"  Loaded {len(html)} chars, {html.count(chr(10))} lines")

    extracted = {}

    # --- Title: <h1> ---
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    if m:
        title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        extracted["title"] = title
        log.info(f"  Title: {title[:100]}")

    # --- Price: ищем паттерн "N NNN ₽" ---
    # Ozon: "2 164 ₽ за 1 шт" или "1 299 ₽"
    prices = []
    # Паттерн 1: число с пробелами + ₽
    for m in re.finditer(r'([\d\s\u00a0]{3,12})\s*₽\s*(?:за\s+\d+\s*шт)?', html):
        raw = m.group(1).replace(" ", "").replace("\u00a0", "")
        try:
            val = int(raw)
            if 10 < val < 10_000_000:
                prices.append({"value": val, "context": m.group(0)[:60]})
        except ValueError:
            pass
    # Паттерн 2: data-атрибуты
    for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
        prices.append({"value": int(m.group(1)), "context": "data-price"})
    # Паттерн 3: "price" в JSON-подобных блоках
    for m in re.finditer(r'"price"\s*:\s*(\d+)', html):
        prices.append({"value": int(m.group(1)), "context": "json price"})
    extracted["prices"] = prices[:10]
    log.info(f"  Prices found: {len(prices)}")
    for p in prices[:5]:
        log.info(f"    {p['value']:>10,} ₽  [{p['context']}]")

    # --- Category: breadcrumbs ---
    breadcrumbs = []
    bc_match = re.search(r'data-widget="breadCrumbs".*?</ol>', html, re.DOTALL)
    if bc_match:
        for li in re.finditer(r'<li[^>]*>.*?<span>([^<]+)</span>', bc_match.group(0), re.DOTALL):
            breadcrumbs.append(li.group(1).strip())
    extracted["category"] = " > ".join(breadcrumbs) if breadcrumbs else None
    log.info(f"  Category: {extracted['category']}")

    # --- Rating ---
    m = re.search(r'([\d.]+)\s*•\s*([\d\s\u00a0]+)\s*(?:отзыва|отзывов|отзыв)', html)
    if m:
        extracted["rating"] = m.group(1)
        extracted["reviews_count"] = m.group(2).replace(" ", "").replace("\u00a0", "")
        log.info(f"  Rating: {extracted['rating']}, Reviews: {extracted['reviews_count']}")

    # --- Images: ir.ozone.ru / cdn1.ozonusercontent.com / io.ozone.ru ---
    img_urls = set()
    for m in re.finditer(r'(?:src|data-src|data-original|href)=["\']([^"\']*(?:ir\.ozone\.ru|io\.ozone\.ru|cdn1\.ozonusercontent\.com|ozoncdn\.net)[^"\']*)["\']', html, re.I):
        url = m.group(1)
        if any(ext in url.lower() for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif")):
            img_urls.add(url)
    # Также ищем в JSON-подобных структурах
    for m in re.finditer(r'"(?:url|src|image|photo)"\s*:\s*"([^"]*(?:ozone|ozoncdn)[^"]*)"', html, re.I):
        url = m.group(1)
        if any(ext in url.lower() for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif")):
            img_urls.add(url)
    extracted["images_count"] = len(img_urls)
    extracted["images_sample"] = list(img_urls)[:5]
    log.info(f"  Images: {len(img_urls)}")
    for u in list(img_urls)[:3]:
        log.info(f"    {u[:120]}")

    # --- Videos ---
    vid_urls = set()
    for m in re.finditer(r'(?:src|data-src|data-url)=["\']([^"\']*\.(?:mp4|webm)(?:\?[^"\']*)?)["\']', html, re.I):
        vid_urls.add(m.group(1))
    extracted["videos_count"] = len(vid_urls)
    log.info(f"  Videos: {len(vid_urls)}")

    # --- Description: ищем длинный текст в data-widget="customHtml" или description ---
    desc = None
    # Паттерн: <div data-widget="...description..."> или "description" в JSON
    for m in re.finditer(r'"description"\s*:\s*"([^"]{50,})"', html):
        desc = m.group(1)[:500]
        break
    if not desc:
        # Ищем в видимом тексте после h1
        h1_end = html.find("</h1>")
        if h1_end > 0:
            chunk = html[h1_end:h1_end+20000]
            text_blocks = re.findall(r'>([А-Яа-яA-Za-z]{20,})<', chunk)
            if text_blocks:
                desc = " ".join(text_blocks[:5])[:500]
    extracted["description_sample"] = desc
    log.info(f"  Description: {(desc or 'NOT FOUND')[:100]}")

    # --- SKU / артикул ---
    m = re.search(r'/product/[a-z0-9\-]*(\d{6,})/', html)
    if m:
        extracted["sku"] = m.group(1)
        log.info(f"  SKU: {m.group(1)}")

    # --- data-widget inventory: какие виджеты есть ---
    widgets = set(re.findall(r'data-widget="([^"]+)"', html))
    extracted["widgets"] = sorted(widgets)
    log.info(f"  Widgets ({len(widgets)}): {', '.join(sorted(widgets)[:15])}")

    # --- og: meta (может быть в <head>) ---
    og_data = {}
    for m in re.finditer(r'<meta[^>]*property=["\']og:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']', html, re.I):
        og_data[m.group(1)] = m.group(2)[:200]
    extracted["og_meta"] = og_data
    log.info(f"  OG meta: {og_data}")

    # --- data-replace-layout-path: структура ---
    layout_paths = re.findall(r'data-replace-layout-path="([^"]+)"', html)
    extracted["layout_paths_count"] = len(layout_paths)
    log.info(f"  Layout paths: {len(layout_paths)}")

    # --- Сохраняем результат ---
    out = BASE / "diag_v36_ozon_parsed.json"
    out.write_text(json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")
    record("OZON_offline_parse", {
        "verdict": "PARSED" if extracted.get("title") else "PARTIAL",
        "note": f"title={bool(extracted.get('title'))} prices={len(prices)} imgs={len(img_urls)} widgets={len(widgets)}",
        "details": extracted,
    })

else:
    record("OZON_offline_parse", {"verdict": "NO_FILE", "note": str(ozon_html_path)})

# =====================================================================
# 2. WB — card.wb.ru с правильными заголовками
# =====================================================================
log.info("=" * 60)
log.info("WB: card.wb.ru API with proper headers")
log.info("=" * 60)

WB_NM = "199298871"
WB_API_TESTS = [
    ("WB_v2_origin", {
        "url": f"https://card.wb.ru/cards/v2/detail?nm={WB_NM}",
        "headers": {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.wildberries.ru",
            "Referer": "https://www.wildberries.ru/",
            "X-Requested-With": "XMLHttpRequest",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
    }),
    ("WB_v2_slim", {
        "url": f"https://card.wb.ru/cards/v2/detail?nm={WB_NM}&appType=1&curr=rub&dest=-1257786",
        "headers": {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.wildberries.ru",
            "Referer": f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx",
            "X-Requested-With": "XMLHttpRequest",
        }
    }),
    ("WB_nm_api", {
        "url": f"https://wbx-api.wildberries.ru/nm/v1/{WB_NM}",
        "headers": {
            "User-Agent": UA,
            "Accept": "application/json",
            "Origin": "https://www.wildberries.ru",
            "Referer": "https://www.wildberries.ru/",
        }
    }),
    ("WB_web_api", {
        "url": f"https://www.wildberries.ru/web/api/v1/cards/detail?nm={WB_NM}",
        "headers": {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.wildberries.ru",
            "Referer": f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx",
            "X-Requested-With": "XMLHttpRequest",
        }
    }),
    ("WB_search_api", {
        "url": f"https://search.wb.ru/exactmatch/ru/common/v9/search?query=&nm={WB_NM}&appType=1&curr=rub",
        "headers": {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.wildberries.ru",
            "Referer": "https://www.wildberries.ru/",
        }
    }),
]

for key, cfg in WB_API_TESTS:
    try:
        t0 = time.time()
        r = requests.get(cfg["url"], headers=cfg["headers"], timeout=15)
        elapsed = time.time() - t0
        body = r.text
        nbytes = len(body.encode("utf-8"))

        data = None
        try:
            data = json.loads(body)
        except Exception:
            pass

        title = ""
        verdict = "EMPTY"
        if data is not None:
            products = []
            if isinstance(data, dict):
                products = data.get("data", {}).get("products", [])
                if not products:
                    products = data.get("products", [])
                if not products and "data" in data:
                    d = data["data"]
                    if isinstance(d, list):
                        products = d
                    elif isinstance(d, dict):
                        products = [d]
            elif isinstance(data, list):
                products = data

            if products:
                p = products[0]
                title = p.get("name", "no_name")
                verdict = "API_OK"
            else:
                title = str(data)[:100]
                verdict = "API_EMPTY"
        else:
            title = body[:100].replace("\n", " ")
            if "wbaas" in body.lower() or "challenge" in body.lower():
                verdict = "CHALLENGE"
            elif nbytes > 100:
                verdict = "HTML_RESPONSE"
            else:
                verdict = "EMPTY"

        saved = f"v36_{key}.json" if data is not None else f"v36_{key}.html"
        (BASE / saved).write_text(body[:500000], encoding="utf-8")

        record(key, {
            "verdict": verdict,
            "note": f"{r.status_code} {nbytes}b {elapsed:.1f}s",
            "title": title[:100],
            "bytes": nbytes,
            "status": r.status_code,
            "saved": saved,
        })
    except Exception as e:
        record(key, {"verdict": "ERROR", "note": str(e)[:100]})
    time.sleep(2)

# =====================================================================
# 3. AVITO — mobile + API
# =====================================================================
log.info("=" * 60)
log.info("AVITO: mobile + API attempts")
log.info("=" * 60)

AVITO_TESTS = [
    ("AVITO_m_api", {
        "url": "https://m.avito.ru/api/v1/ads/1096783029",
        "headers": {
            "User-Agent": "Avito/6.0 (Linux; Android 14)",
            "Accept": "application/json",
        }
    }),
    ("AVITO_m_page", {
        "url": "https://m.avito.ru/moskva/ad/1096783029",
        "headers": {
            "User-Agent": UA,
            "Accept": "text/html",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
    }),
    ("AVITO_m_home", {
        "url": "https://m.avito.ru/moskva/",
        "headers": {
            "User-Agent": UA,
            "Accept": "text/html",
        }
    }),
    ("AVITO_api_search", {
        "url": "https://www.avito.ru/api/search?query=iphone&city=moskva",
        "headers": {
            "User-Agent": UA,
            "Accept": "application/json",
            "Referer": "https://www.avito.ru/",
        }
    }),
]

for key, cfg in AVITO_TESTS:
    try:
        t0 = time.time()
        r = requests.get(cfg["url"], headers=cfg["headers"], timeout=15)
        elapsed = time.time() - t0
        body = r.text
        nbytes = len(body.encode("utf-8"))

        data = None
        try:
            data = json.loads(body)
        except Exception:
            pass

        title = ""
        verdict = "EMPTY"
        if data is not None:
            if isinstance(data, dict):
                title = data.get("title", data.get("name", str(data)[:80]))
                verdict = "API_OK"
            else:
                title = str(data)[:80]
                verdict = "API_LIST"
        else:
            title = body[:80].replace("\n", " ")
            if "ограничен" in body.lower() or "captcha" in body.lower():
                verdict = "IP_BLOCK"
            elif "404" in body[:200]:
                verdict = "NOT_FOUND"
            elif nbytes > 5000:
                verdict = "HTML"
            else:
                verdict = "SHORT"

        saved = f"v36_{key}.json" if data is not None else f"v36_{key}.html"
        (BASE / saved).write_text(body[:300000], encoding="utf-8")

        record(key, {
            "verdict": verdict,
            "note": f"{r.status_code} {nbytes}b {elapsed:.1f}s",
            "title": title[:80],
            "bytes": nbytes,
            "status": r.status_code,
            "saved": saved,
        })
    except Exception as e:
        record(key, {"verdict": "ERROR", "note": str(e)[:100]})
    time.sleep(2)

# =====================================================================
# Итог
# =====================================================================
out = BASE / "diag_v36_results.json"
out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
log.info(f"\nResults → {out}")

print("\n" + "=" * 75)
print(f"{'KEY':<30s} {'VERDICT':<15s} NOTE")
print("-" * 75)
for r in RESULTS:
    print(f"{r['key']:<30s} {r.get('verdict','?'):<15s} {r.get('note','')[:50]}")
print("=" * 75)

"""Avito v1: диагностика — найти реальное объявление и проверить парсинг.
Стратегия:
  A) Запросить карточку по короткому URL: https://www.avito.ru/ad/{id}
  B) Проверить JSON-LD, state, HTML
  C) Попробовать мобильную версию m.avito.ru
"""
import json, sys, time, logging, re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_avito_v1")

from curl_cffi import requests as cr

H_HTML = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.avito.ru/",
}
H_JSON = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.avito.ru/",
}

# Тестовые ID (крупные объявления, вероятно активные)
# Берём несколько — если одно не сработает, попробуем другое
TEST_IDS = [
    "4456789012",  # пример (заменит на реальный при поиске)
    "4501234567",
    "4499876543",
]

# Но сначала — найдём реальное объявление через поиск
log.info("=== 0) FIND REAL AVITO AD ===")

# Поиск на Avito (публичный)
search_urls = [
    ("search_html", "https://www.avito.ru/lis-petr/transport?adType=all"),
    ("search_api", "https://www.avito.ru/api/search?category=transport&city=lis-petr&page=1"),
    ("m_search", "https://m.avito.ru/lis-petr/transport"),
]

found_ids = []
for label, url in search_urls:
    try:
        r = cr.get(url, headers=H_HTML, impersonate="chrome", timeout=12)
        log.info("%-20s %d  %dB", label, r.status_code, len(r.content))
        if r.status_code == 200:
            # Ищем ID объявлений в HTML
            ids = re.findall(r'/ad/(\d{6,})', r.text)
            if not ids:
                ids = re.findall(r'ad_id[=:](\d{6,})', r.text)
            if not ids:
                ids = re.findall(r'"id"\s*:\s*(\d{6,})', r.text)
            if ids:
                found_ids = list(set(ids))[:10]
                log.info("  *** found IDs: %s", found_ids[:5])
                break
    except Exception as e:
        log.info("%-20s ERR %s", label, str(e)[:80])
    time.sleep(2)

if not found_ids:
    # Fallback: известные крупные ID (можно подставить вручную)
    log.info("  No IDs from search, using test IDs")
    found_ids = TEST_IDS

log.info("=== 1) FETCH CARD ===")
results = {}
target_id = found_ids[0] if found_ids else TEST_IDS[0]
log.info("Target: %s", target_id)

card_urls = [
    ("desktop", f"https://www.avito.ru/ad/{target_id}"),
    ("mobile", f"https://m.avito.ru/ad/{target_id}"),
    ("desktop_full", f"https://www.avito.ru/{target_id}"),
]

for label, url in card_urls:
    try:
        r = cr.get(url, headers=H_HTML, impersonate="chrome", timeout=15)
        dt = round(time.monotonic(), 1)
        log.info("%-15s %d  %dB  %s", label, r.status_code, len(r.content), url[:60])
        
        if r.status_code == 200 and len(r.content) > 1000:
            html = r.text
            results[f"{label}_size"] = len(html)
            
            # Check for CAPTCHA
            captcha_markers = ["smartcaptcha", "captcha", "recaptcha", "cf-challenge", "turnstile", "data-sitekey"]
            lower = html[:5000].lower()
            if any(m in lower for m in captcha_markers):
                log.info("  *** CAPTCHA detected!")
                results[f"{label}_captcha"] = True
                continue
            
            # JSON-LD
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.DOTALL):
                try:
                    ld = json.loads(m.group(1))
                    results[f"{label}_jsonld"] = ld
                    log.info("  *** JSON-LD: %s", json.dumps(ld, ensure_ascii=False)[:300])
                except Exception:
                    pass
            
            # State
            state_patterns = [
                r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>',
                r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>',
                r'<script[^>]*id=["\']preloaded-state["\'][^>]*>(\{.*?\})</script>',
            ]
            for i, pat in enumerate(state_patterns):
                m = re.search(pat, html, re.DOTALL)
                if m:
                    try:
                        state = json.loads(m.group(1))
                        results[f"{label}_state_pat{i}"] = "found"
                        # Extract key fields
                        ad = state.get("ad") or state.get("data", {}).get("ad") or {}
                        results[f"{label}_state_keys"] = list(state.keys())[:20]
                        if ad:
                            results[f"{label}_ad_keys"] = list(ad.keys())[:30]
                            results[f"{label}_ad_name"] = ad.get("name") or ad.get("title", "")
                            results[f"{label}_ad_price"] = ad.get("price") or ad.get("priceValue")
                            results[f"{label}_ad_desc"] = (ad.get("description") or ad.get("text", ""))[:200]
                        log.info("  *** State found (pattern %d): %d keys", i, len(state))
                    except Exception as e:
                        log.info("  *** State pattern %d parse: %s", i, str(e)[:60])
                    break
            
            # og: meta
            og_title = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
            og_desc = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)', html, re.I)
            og_img = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
            if og_title:
                results[f"{label}_og_title"] = og_title.group(1)
            if og_desc:
                results[f"{label}_og_desc"] = og_desc.group(1)[:200]
            if og_img:
                results[f"{label}_og_img"] = og_img.group(1)
            
            # Prices in HTML
            prices = re.findall(r'(\d{3,7}[.,]?\d{0,2})\s*(?:₽|руб)', html)
            if prices:
                results[f"{label}_prices_html"] = prices[:10]
                log.info("  *** prices in HTML: %s", prices[:5])
            
            # Media
            imgs = re.findall(r'https?://[^"\'\s]*avatars\.mmedia\.avito\.st[^"\'\s]*', html)
            if imgs:
                results[f"{label}_images"] = imgs[:10]
                log.info("  *** images: %d found", len(imgs))
            
            # Title from <title>
            title_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            if title_m:
                results[f"{label}_title_tag"] = title_m.group(1)
            
            break  # первый успешный — достаточно
        
        elif r.status_code in (403, 429):
            log.info("  *** BLOCKED %d", r.status_code)
            results[f"{label}_blocked"] = r.status_code
        elif r.status_code == 302:
            loc = r.headers.get("Location", "")
            log.info("  *** REDIRECT -> %s", loc[:100])
            results[f"{label}_redirect"] = loc
            # Follow redirect
            if loc and "avito.ru" in loc:
                r2 = cr.get(loc, headers=H_HTML, impersonate="chrome", timeout=15)
                log.info("  follow: %d %dB", r2.status_code, len(r2.content))
                if r2.status_code == 200:
                    results[f"{label}_follow"] = r2.text[:2000]
        time.sleep(2)
    except Exception as e:
        log.info("%-15s ERR %s", label, str(e)[:100])
        time.sleep(2)

# ============================================================
# SAVE
# ============================================================
out = Path(__file__).parent / "diag_avito_v1_out.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== DONE. %d results. Saved: %s ===", len(results), out)

"""
AVITO v2: вызывает collect_by_search напрямую + разбирает одну карточку.
Цель: понять, почему prices=0 в search-режиме и как извлечь цену из ad-страницы.
"""
import sys, os, re, json, time, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("R52_HOME", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test_home"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.avito2")

from r52.collectors.avito import AvitoCollector
from r52.collectors.discovery import SearchError

col = AvitoCollector(data_dir=Path(os.environ["R52_HOME"]))
QUERY = "тест"
DEBUG_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

# === PHASE 1: collect_by_search ===
log.info("=== PHASE 1: collect_by_search ===")
t0 = time.monotonic()
try:
    rows = col.collect_by_search(QUERY, page=1, page_size=3)
    log.info("collect_by_search OK: %d rows in %.1fs", len(rows), time.monotonic() - t0)
except SearchError as e:
    log.warning("collect_by_search SearchError: %s", e)
    rows = []
except Exception as e:
    log.error("collect_by_search error: %s %s", type(e).__name__, e)
    rows = []

# Dump observations
for i, r in enumerate(rows):
    obs = r.get("observation", {})
    pc = obs.get("price_candidates", [])
    title = obs.get("title", obs.get("fields", {}).get("title", {}).get("value", "?"))
    aid = obs.get("id", obs.get("listing_id", "?"))
    avail = obs.get("availability", "?")
    log.info("  Row %d: id=%s title=%r prices=%d avail=%s", i+1, aid, title[:50], len(pc), avail)
    for p in pc:
        log.info("    price: %s", json.dumps(p, ensure_ascii=False))

# === PHASE 2: Fetch one ad page for deep price analysis ===
log.info("=== PHASE 2: Fetch ad page for price structure ===")
ad_id = None
for r in rows:
    obs = r.get("observation", {})
    aid = obs.get("id") or obs.get("listing_id")
    if aid:
        ad_id = aid
        break

if ad_id:
    log.info("Fetching ad %s ...", ad_id)
    ad_url = f"https://m.avito.ru/ad/{ad_id}"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": f"https://m.avito.ru/rossiya?q={QUERY}",
    }
    time.sleep(3)  # throttle
    if col._cffi:
        resp = col._cffi.get(ad_url, impersonate="chrome", timeout=(15, 30), headers=headers)
    else:
        resp = col.session.get(ad_url, headers=headers, timeout=(10, 30))
    status = resp.status_code
    html = resp.text if hasattr(resp, 'text') else resp.content.decode('utf-8', 'replace')
    log.info("Ad page: HTTP %d, %d bytes", status, len(html))

    # Save
    ad_html_path = DEBUG_DIR / f"avito_ad_{ad_id}.html"
    ad_html_path.write_text(html, encoding="utf-8")
    log.info("Saved: %s", ad_html_path)

    # Price patterns on ad page
    PRICE_PATTERNS = [
        ("jsonld_price", re.compile(r'"price"\s*:\s*"?([\d.]+)"?')),
        ("jsonld_offer", re.compile(r'"offers"\s*:\s*\{[^}]*"price"\s*:\s*"?([\d.]+)"?')),
        ("og_price", re.compile(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', re.I)),
        ("meta_price", re.compile(r'itemprop=["\']price["\'][^>]*content=["\']([\d.]+)', re.I)),
        ("data-price", re.compile(r'data-price=["\']?([\d.]+)')),
        ("data-ad-price", re.compile(r'data-ad-price=["\']?([\d.]+)')),
        ("class-price", re.compile(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>\s*(\d[\d\s\u00a0,.]*)', re.I)),
        ("span-rub", re.compile(r'<span[^>]*>(\d[\d\s\u00a0]*)\s*(?:₽|руб)', re.I)),
        ("b-rub", re.compile(r'<b[^>]*>(\d[\d\s\u00a0]*)\s*(?:₽|руб)', re.I)),
        ("any-rub", re.compile(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)\s*(?:₽|руб)', re.I)),
        ("state-price", re.compile(r'"price"\s*:\s*(\d{3,9})\b')),
        ("state-priceInfo", re.compile(r'"priceInfo"\s*:\s*\{[^}]*"price"\s*:\s*(\d+)', re.I)),
    ]
    log.info("--- Price patterns on ad page ---")
    for name, pat in PRICE_PATTERNS:
        ms = list(pat.finditer(html))
        if ms:
            samples = [m.group(1).strip()[:40] for m in ms[:5]]
            log.info("  %s: %d hits → %s", name, len(ms), samples)
        else:
            log.info("  %s: 0", name)

    # Script JSON scan
    log.info("--- Script JSON with price ---")
    script_pat = re.compile(r'<script[^>]*>([\s\S]{50,}?)</script>', re.I)
    for si, sm in enumerate(script_pat.finditer(html)):
        sc = sm.group(1)
        if 'price' in sc.lower() and len(sc) > 100:
            idx = sc.lower().find('price')
            snippet = sc[max(0, idx-80):idx+120].replace('\n', ' ')
            log.info("  Script #%d (%dch): ...%s...", si, len(sc), snippet[:200])

    # Try parse_page (the collector's own method)
    log.info("--- parse_page test ---")
    try:
        from r52.collectors.base import _Resp
        _resp = _Resp(html, status)
        obs = col.parse_page(_resp, ad_id, ad_url, "test")
        if obs:
            pc2 = obs.get("price_candidates", [])
            log.info("parse_page: %d price_candidates", len(pc2))
            for p in pc2:
                log.info("  %s", json.dumps(p, ensure_ascii=False))
        else:
            log.info("parse_page: returned None")
    except Exception as e:
        log.warning("parse_page error: %s", e)
else:
    log.warning("No ad_id found, skipping phase 2")

# === Report ===
report = {
    "ts": datetime.now(timezone(timedelta(hours=10))).isoformat(),
    "phase1": {
        "rows": len(rows),
        "observations": [
            {
                "id": r.get("observation", {}).get("id", "?"),
                "title": r.get("observation", {}).get("title", "")[:80],
                "prices": r.get("observation", {}).get("price_candidates", []),
                "availability": r.get("observation", {}).get("availability", "?"),
            }
            for r in rows
        ],
    },
    "phase2": {
        "ad_id": ad_id,
        "http_status": status if ad_id else None,
        "html_bytes": len(html) if ad_id else 0,
    },
    "elapsed_sec": round(time.monotonic() - t0, 1),
}
rpath = DEBUG_DIR / "diag_avito_v2_report.json"
rpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Report: %s", rpath)
log.info("=== DONE ===")

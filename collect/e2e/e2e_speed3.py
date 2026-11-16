"""E2E v3: 4 площадки ПАРАЛЛЕЛЬНО, оптимизированные стратегии.
WB: card.json + media (skip price API, цена из selling)
OZON: curl_cffi → mobile API → Playwright (3 стратегии)
AVITO: real ID, single shot
FARPOST: as-is (уже работает)
Total: ~55s (max thread)
"""
import json, logging, sys, time, threading, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

LOG_FILE = ROOT / "e2e_speed3.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("e2e3")

IDS = {
    "WB": "100000001",
    "OZON": "278346543",
    "AVITO": "1102345678",
    "FARPOST": "139122082",
}

results = {}
lock = threading.Lock()

def _wrap(c, obs, source, lid, url):
    """Обёртка observation в intake формат."""
    if obs is None:
        return None
    if "observation" in obs:
        return obs
    c._ensure_evidence(obs)
    c._ensure_price_amounts(obs)
    c._ensure_availability_in_fields(obs)
    now = datetime.now(TZ).isoformat()
    return {"format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": source,
            "observation": {**obs, "source": source, "id": str(lid), "url": url,
                            "observed_at": now, "status": "PARTIAL_SUCCESS",
                            "product_content": True,
                            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                            "http_status": 200}}

def _summarize(c, obs, source, lid, t0):
    out = {"source": source, "id": lid, "elapsed": round(time.monotonic() - t0, 1),
           "stats": c.stats if c else {}}
    if obs is None:
        out["status"] = "NO_DATA"
        out["verdict"] = "DATA_WEAK"
        return out
    o = obs.get("observation", obs)
    fields = o.get("fields", {})
    prices = o.get("price_candidates", [])
    media = o.get("media", {}).get("items", [])
    out["status"] = o.get("status", "PARTIAL_SUCCESS")
    out["title"] = fields.get("title", {}).get("value", "")
    out["price_count"] = len(prices)
    out["price_first"] = prices[0] if prices else None
    out["media_count"] = len(media)
    out["desc_len"] = len(fields.get("description", {}).get("value", ""))
    out["category"] = fields.get("category", {}).get("value", "")
    has_title = bool(out["title"]) and not out["title"].startswith(f"{source}-")
    has_price = len(prices) > 0
    has_media = len(media) > 0
    out["verdict"] = "DATA_OK" if (has_title and (has_price or has_media)) else \
                     "DATA_PARTIAL" if has_title else "DATA_WEAK"
    return out

# ==================== WB: card.json + media, SKIP price API ====================
def run_wb(lid):
    t0 = time.monotonic()
    from r52.collectors.wb import WBCollector, _basket_host_cache
    c = WBCollector(use_proxies=False)
    c._last_request = time.monotonic() - 100
    url = c.build_url(lid)
    nm_id = int(lid)

    # Step 1: card.json (fast, ~5s)
    card_data = c._fetch_card_json(nm_id)

    # Step 2: media (fast, ~3s)
    basket_host = _basket_host_cache.get(nm_id // 100000)
    html_resp = None  # skip HTML (498) to save time
    media_items = c._build_media(nm_id, card_data, basket_host, html_resp)

    # Step 3: Build observation from card.json directly
    fields = {}
    price_candidates = []
    availability = "UNKNOWN"
    category = None

    if card_data:
        title = card_data.get("imt_name")
        if title:
            fields["title"] = {"status": "KNOWN", "value": title, "method": "CARD_JSON"}
        # Brand
        selling = card_data.get("selling", {})
        brand = selling.get("brand_name") if isinstance(selling, dict) else None
        if not brand:
            for opt in card_data.get("options", []):
                if isinstance(opt, dict) and (opt.get("name") or "").lower() in ("бренд", "производитель", "brand"):
                    brand = opt.get("value")
                    break
        if brand:
            fields["brand"] = {"status": "KNOWN", "value": str(brand), "method": "CARD_JSON"}
        # Description
        desc = card_data.get("description")
        if desc:
            fields["description"] = {"status": "KNOWN", "value": desc[:3000], "method": "CARD_JSON"}
        # Category
        subj = card_data.get("subj_name", "")
        subj_root = card_data.get("subj_root_name", "")
        if subj and subj_root:
            category = f"{subj_root} > {subj}"
            fields["category"] = {"status": "KNOWN", "value": category, "method": "CARD_JSON"}
        # PRICES from card.json selling
        if isinstance(selling, dict):
            for pk in ("price", "priceU", "priceD", "oldPrice"):
                pv = selling.get(pk)
                if isinstance(pv, (int, float)) and pv > 0:
                    price_candidates.append({
                        "role": "regular" if pk in ("price", "priceU") else "old",
                        "amount_minor": int(pv), "currency": "RUB",
                        "label": f"card_json.selling.{pk}"})
        # Top-level price
        for pk in ("price", "priceU", "priceD"):
            pv = card_data.get(pk)
            if isinstance(pv, (int, float)) and pv > 0 and not price_candidates:
                price_candidates.append({"role": "regular", "amount_minor": int(pv),
                                         "currency": "RUB", "label": f"card_json.{pk}"})

    if not fields.get("title"):
        fields["title"] = {"status": "KNOWN", "value": f"WB-{nm_id}", "method": "FALLBACK"}

    price_candidates = c._dedupe_prices(price_candidates)

    obs = {"fields": fields, "price_candidates": price_candidates,
           "media": {"items": media_items}, "availability": availability,
           "category_path": category}
    obs = _wrap(c, obs, "WB", lid, url)
    return _summarize(c, obs, "WB", lid, t0)

# ==================== OZON: 3 strategies ====================
def run_ozon(lid):
    t0 = time.monotonic()
    from r52.collectors.ozon import OzonCollector
    c = OzonCollector(use_proxies=False)
    url = c.build_url(lid)

    # Strategy 1: curl_cffi with rich headers (fast, ~5s)
    try:
        from curl_cffi import requests as cffi_req
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.ozon.ru/",
        }
        resp = cffi_req.get(url, impersonate="chrome", timeout=12, headers=headers)
        log.info("OZON S1 cffi: HTTP %d (%dB)", resp.status_code, len(resp.text))
        if resp.status_code == 200 and len(resp.text) > 1000:
            class _R:
                def __init__(s, t, sc): s.text = t; s.status_code = sc
            obs = c.parse_page(_R(resp.text, 200), lid, url, "")
            if obs:
                obs = _wrap(c, obs, "OZON", lid, url)
                return _summarize(c, obs, "OZON", lid, t0)
    except Exception as e:
        log.info("OZON S1 error: %s", e)

    # Strategy 2: Mobile API (api.ozon.ru, less protected)
    try:
        from curl_cffi import requests as cffi_req
        api_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/"
        resp = cffi_req.get(api_url, impersonate="chrome", timeout=12,
                            headers={"Accept": "application/json",
                                     "Accept-Language": "ru-RU,ru;q=0.9"})
        log.info("OZON S2 api: HTTP %d (%dB)", resp.status_code, len(resp.text))
        if resp.status_code == 200:
            data = resp.json()
            # Extract from composer API
            state = data.get("widgetStates") or data
            class _R:
                def __init__(s, t, sc): s.text = t; s.status_code = sc
            obs = c.parse_page(_R(json.dumps(data), 200), lid, url, "")
            if obs:
                obs = _wrap(c, obs, "OZON", lid, t0 and url)
                return _summarize(c, obs, "OZON", lid, t0)
    except Exception as e:
        log.info("OZON S2 error: %s", e)

    # Strategy 3: Playwright (last resort, ~15s)
    try:
        pw = c._get_pw()
        result = pw.get(url, extra_wait_ms=2000)
        if result and result.status == 200 and len(result.html) > 1000:
            class _R:
                def __init__(s, t, sc): s.text = t; s.status_code = sc
            obs = c.parse_page(_R(result.html, result.status), lid, url, "")
            if obs:
                obs = _wrap(c, obs, "OZON", lid, url)
                return _summarize(c, obs, "OZON", lid, t0)
        log.info("OZON S3 PW: status=%d len=%d", result.status if result else 0,
                 len(result.html) if result else 0)
    except Exception as e:
        log.info("OZON S3 error: %s", e)

    if hasattr(c, "close"):
        c.close()
    return _summarize(c, None, "OZON", lid, t0)

# ==================== AVITO: single shot ====================
def run_avito(lid):
    t0 = time.monotonic()
    from r52.collectors.avito import AvitoCollector
    c = AvitoCollector(use_proxies=False)
    c._last_request = time.monotonic() - 100
    url = c.build_url(lid)
    log.info("AVITO URL: %s", url)

    # Direct fetch with no retry
    resp = c.fetch(url)
    if resp is None:
        log.info("AVITO: fetch=None (404/blocked)")
        return _summarize(c, None, "AVITO", lid, t0)

    log.info("AVITO: HTTP %d, %dB", resp.status_code, len(resp.text))
    if resp.status_code != 200:
        return _summarize(c, None, "AVITO", lid, t0)

    obs = c.parse_page(resp, lid, url, "")
    if obs:
        obs = _wrap(c, obs, "AVITO", lid, url)
    return _summarize(c, obs, "AVITO", lid, t0)

# ==================== FARPOST: as-is ====================
def run_farpost(lid):
    t0 = time.monotonic()
    from r52.collectors.farpost import FarpostCollector
    c = FarpostCollector(use_proxies=False)
    c._last_request = time.monotonic() - 100
    url = c.build_url(lid)
    obs = c.collect_product(lid, url)
    return _summarize(c, obs, "FARPOST", lid, t0)

# ==================== MAIN: parallel ====================
def main():
    log.info("=== E2E v3 PARALLEL START %s ===", datetime.now(TZ).isoformat())
    log.info("IDs: %s", IDS)
    t_start = time.monotonic()

    runners = {
        "WB": run_wb,
        "OZON": run_ozon,
        "AVITO": run_avito,
        "FARPOST": run_farpost,
    }

    threads = {}
    for source, runner in runners.items():
        lid = IDS[source]
        def _w(s=source, l=lid, r=runner):
            try:
                results[s] = r(l)
            except Exception as e:
                results[s] = {"source": s, "id": l, "status": "ERROR",
                              "error": f"{type(e).__name__}: {str(e)[:300]}",
                              "verdict": "DATA_WEAK", "elapsed": 0}
        th = threading.Thread(target=_w, daemon=True)
        threads[source] = th
        th.start()

    # Wait up to 55s total
    deadline = time.monotonic() + 55
    for source, th in threads.items():
        remaining = max(1, deadline - time.monotonic())
        th.join(timeout=remaining)
        if th.is_alive():
            log.warning("%s: still alive after join, forcing result", source)
            if source not in results:
                results[source] = {"source": source, "id": IDS[source],
                                   "status": "TIMEOUT", "verdict": "UNKNOWN"}

    total = time.monotonic() - t_start
    log.info("=== E2E v3 DONE in %.1fs ===", total)

    summary = {s: {"verdict": r.get("verdict", r.get("status")),
                   "title": (r.get("title") or "")[:60],
                   "prices": r.get("price_count", 0),
                   "media": r.get("media_count", 0),
                   "elapsed": r.get("elapsed", 0)}
               for s, r in results.items()}
    log.info("SUMMARY:\n%s", json.dumps(summary, ensure_ascii=False, indent=2))

    out_json = ROOT / "e2e_speed3_result.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    ok = sum(1 for r in results.values() if r.get("verdict") in ("DATA_OK", "DATA_PARTIAL"))
    log.info("=== VERDICT: %d/4 ===", ok)
    print(f"\nE2E_V3_DONE ok={ok}/4 total={total:.0f}s")

if __name__ == "__main__":
    main()

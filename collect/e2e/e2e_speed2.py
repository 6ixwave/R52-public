"""E2E v2: 4 площадки, оптимизированные таймауты, реальные ID.
WB=40s, OZON=30s, AVITO=15s, FARPOST=10s. Total ~95s.
"""
import json, logging, sys, time, threading, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

LOG_FILE = ROOT / "e2e_speed2.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("e2e2")

# --- REAL IDS ---
IDS = {
    "WB": "100000001",        # confirmed: card.json works
    "OZON": "278346543",      # iPhone 15
    "AVITO": "1102345678",    # 10-digit, common range
    "FARPOST": "139122082",   # confirmed: DATA_OK
}

BUDGETS = {"WB": 40, "OZON": 30, "AVITO": 15, "FARPOST": 10}
results = {}

def test_wb(lid):
    from r52.collectors.wb import WBCollector
    c = WBCollector(use_proxies=False)
    c._last_request = time.monotonic() - 100  # skip first throttle
    url = c.build_url(lid)
    obs = c.collect_product(lid, url)
    return c, obs

def test_ozon(lid):
    """Ozon: try curl_cffi first (fast), then HTTP with 1 retry max."""
    from r52.collectors.ozon import OzonCollector
    c = OzonCollector(use_proxies=False)
    url = c.build_url(lid)

    # Strategy 1: curl_cffi direct (skip Playwright — slow + 403)
    try:
        from curl_cffi import requests as cffi_req
        resp = cffi_req.get(url, impersonate="chrome", timeout=15,
                            headers={"Accept": "text/html,application/xhtml+xml",
                                     "Accept-Language": "ru-RU,ru;q=0.9"})
        if resp.status_code == 200 and len(resp.text) > 1000:
            class _R:
                def __init__(s, t, sc): s.text=t; s.status_code=sc
            obs = c.parse_page(_R(resp.text, 200), lid, url, "")
            if obs:
                c._ensure_evidence(obs)
                c._ensure_price_amounts(obs)
                c._ensure_availability_in_fields(obs)
                now = datetime.now(TZ).isoformat()
                return c, {"format":"JARVIS_BR02_EYE_USER_CAPTURE",
                    "observation":{**obs,"source":"OZON","id":lid,"url":url,
                    "observed_at":now,"status":"PARTIAL_SUCCESS",
                    "product_content":True,"evidence_level":"COLLECTOR_CURL_CFFI","http_status":200}}
        log.info("OZON cffi: HTTP %d (%dB)", resp.status_code, len(resp.text))
    except Exception as e:
        log.info("OZON cffi error: %s", e)

    # Strategy 2: HTTP with reduced retries (patch MAX_RETRIES)
    import r52.collectors.base as base_mod
    orig_retries = base_mod.MAX_RETRIES
    base_mod.MAX_RETRIES = 1  # only 1 retry
    try:
        obs = c.collect_product(lid, url)
    finally:
        base_mod.MAX_RETRIES = orig_retries
    if hasattr(c, "close"):
        c.close()
    return c, obs

def test_avito(lid):
    from r52.collectors.avito import AvitoCollector
    c = AvitoCollector(use_proxies=False)
    c._last_request = time.monotonic() - 100
    url = c.build_url(lid)
    # Single shot: fetch + parse, no retries
    resp = c.fetch(url)
    if resp is None:
        return c, None
    obs = c.parse_page(resp, lid, url, "")
    if obs:
        c._ensure_evidence(obs)
        c._ensure_price_amounts(obs)
        c._ensure_availability_in_fields(obs)
        now = datetime.now(TZ).isoformat()
        obs = {"format":"JARVIS_BR02_EYE_USER_CAPTURE",
            "observation":{**obs,"source":"AVITO","id":lid,"url":url,
            "observed_at":now,"status":"PARTIAL_SUCCESS",
            "product_content":True,"evidence_level":"COLLECTOR_AUTONOMOUS_HTTP","http_status":200}}
    return c, obs

def test_farpost(lid):
    from r52.collectors.farpost import FarpostCollector
    c = FarpostCollector(use_proxies=False)
    c._last_request = time.monotonic() - 100
    url = c.build_url(lid)
    obs = c.collect_product(lid, url)
    return c, obs

def run_platform(source, lid):
    t0 = time.monotonic()
    out = {"source": source, "id": lid, "status": "UNKNOWN", "elapsed": 0}
    try:
        if source == "WB":
            c, obs = test_wb(lid)
        elif source == "OZON":
            c, obs = test_ozon(lid)
        elif source == "AVITO":
            c, obs = test_avito(lid)
        elif source == "FARPOST":
            c, obs = test_farpost(lid)
        else:
            out["error"] = f"Unknown {source}"; return out

        out["elapsed"] = round(time.monotonic() - t0, 1)
        out["stats"] = c.stats

        if obs is None:
            out["status"] = "NO_DATA"
            out["verdict"] = "DATA_WEAK"
            return out

        o = obs.get("observation", obs)
        fields = o.get("fields", {})
        prices = o.get("price_candidates", [])
        media = o.get("media", {}).get("items", [])

        out["status"] = o.get("status", "UNKNOWN")
        out["title"] = fields.get("title", {}).get("value", "")
        out["price_count"] = len(prices)
        out["price_first"] = prices[0] if prices else None
        out["media_count"] = len(media)
        out["desc_len"] = len(fields.get("description", {}).get("value", ""))
        out["category"] = fields.get("category", {}).get("value", "")

        has_title = bool(out["title"]) and not out["title"].startswith(f"{source}-")
        has_price = len(prices) > 0
        has_media = len(media) > 0
        if has_title and (has_price or has_media):
            out["verdict"] = "DATA_OK"
        elif has_title:
            out["verdict"] = "DATA_PARTIAL"
        else:
            out["verdict"] = "DATA_WEAK"
    except Exception as e:
        out["status"] = "ERROR"
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        out["elapsed"] = round(time.monotonic() - t0, 1)
    return out

def main():
    log.info("=== E2E v2 START %s ===", datetime.now(TZ).isoformat())
    t_start = time.monotonic()

    for source in ["WB", "OZON", "AVITO", "FARPOST"]:
        budget = BUDGETS[source]
        lid = IDS[source]
        log.info("--- %s id=%s budget=%ds ---", source, lid, budget)

        holder = [None]
        def _w(s=source, l=lid):
            holder[0] = run_platform(s, l)
        th = threading.Thread(target=_w, daemon=True)
        th.start()
        th.join(timeout=budget)

        if th.is_alive():
            log.warning("%s: TIMEOUT %ds", source, budget)
            results[source] = {"source": source, "id": lid, "status": "TIMEOUT", "verdict": "UNKNOWN"}
        else:
            r = holder[0]
            results[source] = r
            log.info("[%s] %s: status=%s title='%s' prices=%d media=%d elapsed=%.1fs verdict=%s",
                     source, lid, r.get("status"), (r.get("title") or "")[:50],
                     r.get("price_count", 0), r.get("media_count", 0),
                     r.get("elapsed", 0), r.get("verdict", "?"))

    total = time.monotonic() - t_start
    log.info("=== E2E v2 DONE in %.1fs ===", total)

    summary = {s: {"verdict": r.get("verdict", r.get("status")),
                   "title": (r.get("title") or "")[:60],
                   "prices": r.get("price_count", 0),
                   "media": r.get("media_count", 0),
                   "elapsed": r.get("elapsed", 0)}
               for s, r in results.items()}
    log.info("SUMMARY:\n%s", json.dumps(summary, ensure_ascii=False, indent=2))

    out_json = ROOT / "e2e_speed2_result.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    ok = sum(1 for r in results.values() if r.get("verdict") in ("DATA_OK", "DATA_PARTIAL"))
    log.info("=== VERDICT: %d/4 ===", ok)
    print(f"\nE2E_V2_DONE ok={ok}/4 total={total:.0f}s")

if __name__ == "__main__":
    main()

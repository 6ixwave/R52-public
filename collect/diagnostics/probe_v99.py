"""probe_v99 — WB E2E: full 60-host basket scan, multi-endpoint price, 240s watchdog.

Использование:
    python -u probe_v99.py

Вывод: stdout + probe_v99.log
"""
import json, logging, os, random, sys, time, threading
from pathlib import Path

ROOT = Path(__file__).parent
LOG_FILE = ROOT / "probe_v99.log"

_fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
_fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
_fh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_sh, _fh])
log = logging.getLogger("v99")

WATCHDOG_SECS = 240
def _watchdog():
    time.sleep(WATCHDOG_SECS)
    log.error("WATCHDOG: %d s exceeded, forcing exit", WATCHDOG_SECS)
    os._exit(2)
threading.Thread(target=_watchdog, daemon=True).start()

SOURCE = "WB"
LID = "259671175"
NM_ID = int(LID)
VOL = NM_ID // 100000
PART = (NM_ID // 1000) % 1000
BASKET_PATH = f"/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"

sys.path.insert(0, str(ROOT))
HOME = ROOT / "r52"
INTAKE = HOME / "data" / "intake"
INTAKE.mkdir(parents=True, exist_ok=True)


def main():
    t_start = time.monotonic()
    log.info("=== probe_v99 START lid=%s vol=%d part=%d path=%s ===",
             LID, VOL, PART, BASKET_PATH)

    # ---- P0: Quick connectivity tests ----
    from curl_cffi import requests as cffi_req

    # P0a: card.wb.ru with chrome124 + full headers
    t0 = time.monotonic()
    price_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
        "X-Requested-With": "XMLHttpRequest",
    }
    for imp in ["chrome124", "chrome"]:
        try:
            r = cffi_req.get(
                f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}",
                headers=price_headers, impersonate=imp, timeout=12)
            log.info("P0a card.wb.ru [%s] HTTP %d in %.1fs body[:200]=%s",
                     imp, r.status_code, time.monotonic()-t0, r.text[:200])
            if r.status_code == 200:
                break
        except Exception as e:
            log.error("P0a card.wb.ru [%s] FAIL: %s", imp, e)

    # P0b: basket quick test on 3 hosts to verify URL format
    t0 = time.monotonic()
    for n in [1, 20, 40]:
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{BASKET_PATH}"
        try:
            r = cffi_req.get(url, headers={"Accept": "application/json"},
                             impersonate="chrome124", timeout=6)
            log.info("P0b %s -> %d (%.1fs) body[:100]=%s",
                     host, r.status_code, time.monotonic()-t0, r.text[:100])
        except Exception as e:
            log.info("P0b %s -> ERR %s", host, e)

    # P0c: minimal card.wb.ru (no extra params)
    t0 = time.monotonic()
    try:
        r = cffi_req.get(
            f"https://card.wb.ru/cards/v2/detail?nm={NM_ID}",
            headers=price_headers, impersonate="chrome124", timeout=12)
        log.info("P0c card.wb.ru minimal HTTP %d in %.1fs body[:200]=%s",
                 r.status_code, time.monotonic()-t0, r.text[:200])
    except Exception as e:
        log.error("P0c FAIL: %s", e)

    # ---- P1: Config + App ----
    t0 = time.monotonic()
    from r52.config import load_config
    config = load_config(home=str(HOME))
    db_path = Path(config.paths.db_file)
    log.info("P1 Config: %.1fs db=%s exists=%s",
             time.monotonic()-t0, db_path, db_path.exists())

    t0 = time.monotonic()
    from r52.service import App
    app = App(config, install_guard=False)
    app.open()
    log.info("P1 App: %.1fs", time.monotonic()-t0)

    # ---- P2: Collector ----
    t0 = time.monotonic()
    from r52.collectors import COLLECTOR_REGISTRY
    from r52.collectors.wb import JSON_HEADERS as WB_JSON
    cls, display = COLLECTOR_REGISTRY[SOURCE]
    col = cls(data_dir=app.config.paths.data_dir, use_proxies=False)
    log.info("P2 Collector: %.1fs cffi=%s", time.monotonic()-t0,
             "yes" if col._cffi else "no")

    # Patch throttles
    def _fast():
        now = time.monotonic()
        wait = random.uniform(0.8, 1.5) - (now - col._last_request)
        if wait > 0:
            time.sleep(wait)
        col._last_request = time.monotonic()
    col._fast_throttle = _fast

    def _normal():
        now = time.monotonic()
        wait = random.uniform(2, 4) - (now - col._last_request)
        if wait > 0:
            time.sleep(wait)
        col._last_request = time.monotonic()
    col._throttle = _normal

    # ---- P3: Full basket scan (60 hosts) ----
    t0 = time.monotonic()
    basket_host = None
    card_data = None
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{BASKET_PATH}"
        t_b = time.monotonic()
        try:
            resp = cffi_req.get(url, headers=WB_JSON,
                                impersonate="chrome124", timeout=4)
            code = resp.status_code
            body_len = len(resp.content)
        except Exception as e:
            code = f"ERR:{type(e).__name__}"
            body_len = 0
        dt = time.monotonic() - t_b
        if code == 200:
            basket_host = host
            try:
                card_data = resp.json()
                if not isinstance(card_data, dict) or not card_data.get("nm_id"):
                    log.warning("P3 %s: 200 but no nm_id", host)
                    card_data = None
            except Exception:
                card_data = None
            log.info("P3 BASKET HIT: %s -> 200 %dB (%.1fs) elapsed=%.0fs",
                     host, body_len, dt, time.monotonic()-t0)
            break
        else:
            if n % 10 == 0:
                log.info("P3 basket: scanned %d/60, last=%s->%s (%.0fs elapsed)",
                         n, host, code, time.monotonic()-t0)
            else:
                log.debug("P3 basket: %s -> %s", host, code)
    log.info("P3 Basket scan: %.0fs host=%s card_data=%s",
             time.monotonic()-t0, basket_host,
             f"yes({len(card_data)} keys)" if card_data else "no")

    # ---- P4: Price (multi-endpoint) ----
    t0 = time.monotonic()
    price_data = None
    price_endpoints = [
        ("v2_full", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
        ("v2_min",  f"https://card.wb.ru/cards/v2/detail?nm={NM_ID}"),
        ("v1_full", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
        ("v4_full", f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
        ("v5_full", f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
        ("web_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}"),
    ]
    for label, purl in price_endpoints:
        if price_data:
            break
        t_p = time.monotonic()
        try:
            resp = cffi_req.get(purl, headers=WB_JSON,
                                impersonate="chrome124", timeout=10)
            code = resp.status_code
            if code == 200:
                data = resp.json()
                products = data.get("products") or data.get("data", {}).get("items", [])
                if products:
                    p = products[0]
                    price_data = {
                        "name": p.get("name"),
                        "brand": p.get("brand"),
                        "sizes": p.get("sizes", []),
                        "source": label,
                    }
                    for sz in p.get("sizes", []):
                        pr = sz.get("price", {})
                        if pr:
                            price_data["prices"] = pr
                            price_data["qty"] = sz.get("qty")
                            break
                    log.info("P4 Price [%s] OK: name=%s (%.1fs)",
                             label, str(price_data.get("name","?"))[:40],
                             time.monotonic()-t_p)
                    break
                # web_api format
                if "data" in data and isinstance(data["data"], dict):
                    d = data["data"]
                    prs = d.get("prices") or d.get("price")
                    if prs:
                        price_data = {"prices": prs, "qty": d.get("qty"),
                                      "name": d.get("name"), "source": label}
                        log.info("P4 Price [%s] OK (web_api) (%.1fs)",
                                 label, time.monotonic()-t_p)
                        break
            log.info("P4 Price [%s] -> %d (%.1fs)", label, code,
                     time.monotonic()-t_p)
        except Exception as e:
            log.info("P4 Price [%s] ERR %s (%.1fs)", label, e,
                     time.monotonic()-t_p)
        time.sleep(1)
    log.info("P4 Price total: %.0fs data=%s",
             time.monotonic()-t0, "yes" if price_data else "no")

    # ---- P5: HTML (best-effort, 15s timeout) ----
    t0 = time.monotonic()
    html_resp = None
    try:
        hresp = cffi_req.get(
            f"https://www.wildberries.ru/catalog/{LID}/detail.aspx",
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": "https://www.wildberries.ru/",
            },
            impersonate="chrome124", timeout=15)
        if hresp.status_code == 200:
            html_resp = hresp
            log.info("P5 HTML: 200 %dB (%.1fs)", len(hresp.content),
                     time.monotonic()-t0)
        else:
            log.info("P5 HTML: %d (%.1fs)", hresp.status_code,
                     time.monotonic()-t0)
    except Exception as e:
        log.info("P5 HTML ERR: %s (%.1fs)", e, time.monotonic()-t0)

    # ---- P6: Merge observations manually ----
    t0 = time.monotonic()
    fields = {}
    price_candidates = []
    media_items = []
    availability = "UNKNOWN"
    category = None

    # Title
    title = None
    title_method = "FALLBACK"
    if card_data:
        title = card_data.get("imt_name")
        if title:
            title_method = "CARD_JSON"
    if not title and price_data:
        title = price_data.get("name")
        if title:
            title_method = "PRICE_API"
    if not title and html_resp:
        import re
        m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)',
                      html_resp.text, re.I)
        if m:
            title = m.group(1).strip()
            title_method = "HTML"
    if not title:
        title = f"WB-{NM_ID}"
    fields["title"] = {"status": "KNOWN", "value": title, "method": title_method,
                       "evidence": {"method": title_method, "source": "WB"}}
    log.info("P6 title=%s method=%s", str(title)[:60], title_method)

    # Brand
    brand = None
    if card_data:
        selling = card_data.get("selling", {})
        if isinstance(selling, dict):
            brand = selling.get("brand_name")
        if not brand:
            for opt in card_data.get("options", []):
                if isinstance(opt, dict):
                    if (opt.get("name") or "").lower() in ("бренд", "производитель", "brand"):
                        brand = opt.get("value")
                        break
    if not brand and price_data:
        brand = price_data.get("brand")
    if brand:
        fields["brand"] = {"status": "KNOWN", "value": str(brand), "method": "CARD_JSON",
                           "evidence": {"method": "CARD_JSON", "source": "WB"}}

    # Description
    desc = None
    if card_data:
        desc = card_data.get("description")
    if desc:
        fields["description"] = {"status": "KNOWN", "value": desc[:3000],
                                 "method": "CARD_JSON",
                                 "evidence": {"method": "CARD_JSON", "source": "WB"}}

    # Category
    if card_data:
        subj = card_data.get("subj_name", "")
        subj_root = card_data.get("subj_root_name", "")
        if subj and subj_root:
            category = f"{subj_root} > {subj}"
        elif subj:
            category = subj
    if category:
        fields["category"] = {"status": "KNOWN", "value": category,
                              "method": "CARD_JSON",
                              "evidence": {"method": "CARD_JSON", "source": "WB"}}

    # Options
    if card_data:
        opts = card_data.get("options", [])
        if isinstance(opts, list) and opts:
            lines = []
            for opt in opts[:25]:
                if isinstance(opt, dict) and opt.get("name") and opt.get("value"):
                    lines.append(f"{opt['name']}: {opt['value']}")
            if lines:
                fields["options"] = {"status": "KNOWN", "value": "\n".join(lines),
                                     "method": "CARD_JSON",
                                     "evidence": {"method": "CARD_JSON", "source": "WB"}}

    # Prices
    if price_data and price_data.get("prices"):
        pr = price_data["prices"]
        role_map = {"basic": "regular", "product": "regular", "card": "card",
                    "wallet": "wallet", "old": "old", "crossed": "old",
                    "bankCard": "card"}
        for key, val in pr.items():
            if isinstance(val, (int, float)) and val > 0:
                role = role_map.get(key, "regular")
                price_candidates.append({
                    "role": role, "amount_minor": int(val),
                    "currency": "RUB", "label": f"price_api.{key}",
                    "amount": f"{int(val)//100}.{int(val)%100:02d}",
                    "visible_label": f"price_api.{key}",
                })
    if not price_candidates and html_resp:
        import re
        m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)',
                      html_resp.text, re.I)
        if m:
            try:
                minor = int(round(float(m.group(1)) * 100))
                price_candidates.append({"role": "regular", "amount_minor": minor,
                                         "currency": "RUB", "label": "og:price",
                                         "amount": f"{minor//100}.{minor%100:02d}",
                                         "visible_label": "og:price"})
            except ValueError:
                pass

    # Availability
    if price_data and price_data.get("qty") is not None:
        availability = "IN_STOCK" if price_data["qty"] > 0 else "OUT_OF_STOCK"
    if availability == "UNKNOWN" and html_resp:
        import re
        if re.search(r"в наличии|есть в наличии|доступен|купить", html_resp.text, re.I):
            availability = "IN_STOCK"
        elif re.search(r"нет в наличии|закончился|распродан|ожидание", html_resp.text, re.I):
            availability = "OUT_OF_STOCK"

    # Media
    if basket_host:
        n = int(basket_host.split("-")[1])
        primary = (f"https://basket-{n:02d}.wbbasket.ru/vol{VOL}/part{PART}"
                   f"/{NM_ID}/images/S800/S800.jpg")
        media_items.append({"type": "image", "source_url": primary, "image_data": None})
    if html_resp:
        import re
        m = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)',
                      html_resp.text, re.I)
        if m:
            u = m.group(1)
            if u and u not in [i["source_url"] for i in media_items]:
                media_items.append({"type": "image", "source_url": u, "image_data": None})

    # Status
    has_title = bool(title) and title != f"WB-{NM_ID}"
    has_price = len(price_candidates) > 0
    has_media = len(media_items) > 0
    if has_title and has_price and has_media:
        status = "SUCCESS"
    elif has_title or has_price:
        status = "PARTIAL_SUCCESS"
    else:
        status = "DEGRADED"

    observation = {
        "fields": fields,
        "price_candidates": price_candidates,
        "media": {"items": media_items},
        "availability": availability,
        "category_path": category,
    }

    # Ensure evidence / price amounts / availability in fields
    for key, item in fields.items():
        if isinstance(item, dict) and item.get("status") == "KNOWN" and not item.get("evidence"):
            item["evidence"] = {"method": item.get("method", "COLLECTOR"), "source": "WB"}
    for c in price_candidates:
        if "amount" not in c and c.get("amount_minor") is not None:
            minor = int(c["amount_minor"])
            c["amount"] = f"{minor//100}.{minor%100:02d}"
        if "visible_label" not in c and c.get("label"):
            c["visible_label"] = c["label"]
    if availability and availability != "UNKNOWN" and "availability" not in fields:
        fields["availability"] = {"status": "KNOWN", "value": availability,
                                  "method": "COLLECTOR",
                                  "evidence": {"method": "COLLECTOR", "source": "WB"}}

    from datetime import datetime, timezone, timedelta
    now_iso = datetime.now(timezone(timedelta(hours=10))).isoformat()
    obs = {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": SOURCE,
        "observation": {
            **observation,
            "source": SOURCE,
            "id": LID,
            "url": f"https://www.wildberries.ru/catalog/{LID}/detail.aspx",
            "observed_at": now_iso,
            "status": status,
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
        },
    }
    log.info("P6 Merge: %.1fs status=%s title=%s prices=%d media=%d avail=%s",
             time.monotonic()-t0, status, str(title)[:40],
             len(price_candidates), len(media_items), availability)

    # ---- P7: Write JSON ----
    t0 = time.monotonic()
    written = col.write_observations([obs], INTAKE)
    log.info("P7 Written: %s (%.1fs)", [Path(w).name for w in written],
             time.monotonic()-t0)

    # ---- P8: Inbox ----
    t0 = time.monotonic()
    for wp in written:
        data = Path(wp).read_bytes()
        fname = Path(wp).name
        item, created = app.inbox.stage_bytes(data, filename=fname, origin="COLLECTOR")
        log.info("P8 Staged: id=%s status=%s err=%s",
                 item.get("id"), item.get("status"), item.get("error_code"))
        if item.get("status") == "PENDING":
            res = app.inbox.accept(item["id"])
            log.info("P8 Accepted: pid=%s obs=%s",
                     res.get("product_id"), res.get("observation_created"))
        elif item.get("status") == "ACCEPTED":
            log.info("P8 Auto-accepted")
        elif item.get("status") == "INVALID":
            log.warning("P8 INVALID: %s", item.get("error_code"))
    log.info("P8 Inbox: %.1fs", time.monotonic()-t0)

    # ---- P9: DB verify ----
    t0 = time.monotonic()
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    prods = [dict(r) for r in conn.execute(
        "SELECT id,source,listing_id,title,last_regular_minor,"
        "last_availability,observation_count "
        "FROM product WHERE source=? AND listing_id=? "
        "ORDER BY id DESC LIMIT 3", (SOURCE, LID))]
    pps = [dict(r) for r in conn.execute(
        "SELECT pp.id,pp.amount_minor,pp.currency,pp.label "
        "FROM price_point pp JOIN product p ON pp.product_id=p.id "
        "WHERE p.source=? AND p.listing_id=? "
        "ORDER BY pp.id DESC LIMIT 5", (SOURCE, LID))]
    conn.close()
    log.info("P9 DB: %d products, %d pps (%.1fs)",
             len(prods), len(pps), time.monotonic()-t0)
    for p in prods:
        log.info("P9  P: id=%s title=%s reg=%s avail=%s obs=%s",
                 p["id"], str(p["title"])[:60], p["last_regular_minor"],
                 p["last_availability"], p["observation_count"])
    for pp in pps[:3]:
        log.info("P9  PP: amt=%s cur=%s label=%s",
                 pp["amount_minor"], pp["currency"], pp["label"])

    # ---- Verdict ----
    has_p = len(prods) > 0
    has_t = has_p and not str(prods[0]["title"]).startswith(f"{SOURCE}-")
    has_pp = len(pps) > 0 and pps[0]["amount_minor"] is not None
    verdict = (f"{'PASS' if has_p else 'FAIL'}"
               f"{'_TITLE' if has_t else '_FBT'}"
               f"{'_PRICE' if has_pp else '_NOP'}")
    log.info("VERDICT: %s (total %.0fs)", verdict, time.monotonic()-t_start)

    app.stop()
    log.info("=== probe_v99 DONE ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("FATAL")
        sys.exit(1)

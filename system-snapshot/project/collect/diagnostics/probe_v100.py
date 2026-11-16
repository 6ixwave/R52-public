"""probe_v100 — WB E2E: FIXED PART formula, v4 endpoint, two test items.

Fix: PART = nm_id // 1000 (was (nm_id//1000)%1000)
Price: v4 first (current), v2/v1 fallback.
Items: 1097975275 (new, confirmed in web search) + 259671175 (old test).

Usage:
    python -u probe_v100.py

Output: stdout + probe_v100.log
"""
import json, logging, os, random, re, sys, time, threading
from pathlib import Path

ROOT = Path(__file__).parent
LOG_FILE = ROOT / "probe_v100.log"

_fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
_fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
_fh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_sh, _fh])
log = logging.getLogger("v100")

WATCHDOG_SECS = 240
def _watchdog():
    time.sleep(WATCHDOG_SECS)
    log.error("WATCHDOG: %d s exceeded, forcing exit", WATCHDOG_SECS)
    os._exit(2)
threading.Thread(target=_watchdog, daemon=True).start()

SOURCE = "WB"
# Two test items: new (confirmed in search) + old (our original)
TEST_ITEMS = [
    ("1097975275", "NEW"),   # basket-42 confirmed in web search
    ("259671175",  "OLD"),   # original test item
]

sys.path.insert(0, str(ROOT))
HOME = ROOT / "r52"
INTAKE = HOME / "data" / "intake"
INTAKE.mkdir(parents=True, exist_ok=True)


def basket_path(nm_id: int) -> str:
    """CORRECTED: vol=nm//100000, part=nm//1000 (NO mod 1000)."""
    vol = nm_id // 100000
    part = nm_id // 1000  # FIX: was (nm_id // 1000) % 1000
    return f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"


def scan_basket(nm_id: int, cffi_req, max_hosts: int = 60) -> tuple:
    """Scan basket hosts. Returns (host, card_data) or (None, None)."""
    path = basket_path(nm_id)
    log.info("  basket path: %s", path)
    for n in range(1, max_hosts + 1):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        t_b = time.monotonic()
        try:
            resp = cffi_req.get(url, headers={"Accept": "application/json"},
                                impersonate="chrome124", timeout=4)
            code = resp.status_code
            if code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("nm_id"):
                    log.info("  basket HIT: %s (%.1fs, %dB)", host,
                             time.monotonic()-t_b, len(resp.content))
                    return host, data
                log.info("  basket %s: 200 but no nm_id", host)
            elif n % 10 == 0:
                log.info("  basket: %d/%d scanned, last=%s->%d",
                         n, max_hosts, host, code)
        except Exception as e:
            if n % 10 == 0:
                log.info("  basket: %d/%d scanned, last=%s->ERR", n, max_hosts, host)
    log.info("  basket: no host in 1-%d", max_hosts)
    return None, None


def fetch_price(nm_id: int, cffi_req) -> dict | None:
    """Try v4 (current), then v2, v1, web_api. Returns price_data or None."""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    }
    endpoints = [
        ("v4", f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("web", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
    ]
    for label, url in endpoints:
        t_p = time.monotonic()
        try:
            resp = cffi_req.get(url, headers=headers,
                                impersonate="chrome124", timeout=10)
            code = resp.status_code
            if code == 200:
                data = resp.json()
                products = data.get("products") or data.get("data", {}).get("items", [])
                if products:
                    p = products[0]
                    result = {
                        "name": p.get("name"),
                        "brand": p.get("brand"),
                        "sizes": p.get("sizes", []),
                        "source": label,
                    }
                    for sz in p.get("sizes", []):
                        pr = sz.get("price", {})
                        if pr:
                            result["prices"] = pr
                            result["qty"] = sz.get("qty")
                            break
                    log.info("  price [%s] OK: name=%s qty=%s (%.1fs)",
                             label, str(result.get("name","?"))[:40],
                             result.get("qty"), time.monotonic()-t_p)
                    return result
                if "data" in data and isinstance(data["data"], dict):
                    d = data["data"]
                    prs = d.get("prices") or d.get("price")
                    if prs:
                        result = {"prices": prs, "qty": d.get("qty"),
                                  "name": d.get("name"), "source": label}
                        log.info("  price [%s] OK (web) (%.1fs)", label,
                                 time.monotonic()-t_p)
                        return result
            log.info("  price [%s] -> %d (%.1fs)", label, code,
                     time.monotonic()-t_p)
        except Exception as e:
            log.info("  price [%s] ERR %s (%.1fs)", label, e, time.monotonic()-t_p)
        time.sleep(0.5)
    return None


def merge_obs(nm_id: int, card_data, price_data, html_resp, basket_host) -> dict:
    """Merge all sources into observation dict."""
    fields = {}
    price_candidates = []
    media_items = []
    availability = "UNKNOWN"
    category = None

    # Title
    title, title_method = None, "FALLBACK"
    if card_data:
        title = card_data.get("imt_name")
        if title:
            title_method = "CARD_JSON"
    if not title and price_data:
        title = price_data.get("name")
        if title:
            title_method = "PRICE_API"
    if not title and html_resp:
        m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)',
                      html_resp.text, re.I)
        if m:
            title = m.group(1).strip()
            title_method = "HTML"
    if not title:
        title = f"WB-{nm_id}"
    fields["title"] = {"status": "KNOWN", "value": title, "method": title_method,
                       "evidence": {"method": title_method, "source": "WB"}}

    # Brand
    brand = None
    if card_data:
        selling = card_data.get("selling", {})
        if isinstance(selling, dict):
            brand = selling.get("brand_name")
        if not brand:
            for opt in card_data.get("options", []):
                if isinstance(opt, dict) and (opt.get("name") or "").lower() in ("бренд", "производитель", "brand"):
                    brand = opt.get("value")
                    break
    if not brand and price_data:
        brand = price_data.get("brand")
    if brand:
        fields["brand"] = {"status": "KNOWN", "value": str(brand), "method": "CARD_JSON",
                           "evidence": {"method": "CARD_JSON", "source": "WB"}}

    # Description
    desc = card_data.get("description") if card_data else None
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
            lines = [f"{o['name']}: {o['value']}" for o in opts[:25]
                     if isinstance(o, dict) and o.get("name") and o.get("value")]
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
        if re.search(r"в наличии|есть в наличии|доступен|купить", html_resp.text, re.I):
            availability = "IN_STOCK"
        elif re.search(r"нет в наличии|закончился|распродан|ожидание", html_resp.text, re.I):
            availability = "OUT_OF_STOCK"

    # Media
    if basket_host:
        n = int(basket_host.split("-")[1])
        vol = nm_id // 100000
        part = nm_id // 1000
        primary = (f"https://basket-{n:02d}.wbbasket.ru/vol{vol}/part{part}"
                   f"/{nm_id}/images/S800/S800.jpg")
        media_items.append({"type": "image", "source_url": primary, "image_data": None})
    if html_resp:
        m = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)',
                      html_resp.text, re.I)
        if m:
            u = m.group(1)
            if u and u not in [i["source_url"] for i in media_items]:
                media_items.append({"type": "image", "source_url": u, "image_data": None})

    # Status
    has_title = bool(title) and title != f"WB-{nm_id}"
    has_price = len(price_candidates) > 0
    has_media = len(media_items) > 0
    if has_title and has_price and has_media:
        status = "SUCCESS"
    elif has_title or has_price:
        status = "PARTIAL_SUCCESS"
    else:
        status = "DEGRADED"

    return {
        "fields": fields,
        "price_candidates": price_candidates,
        "media": {"items": media_items},
        "availability": availability,
        "category_path": category,
        "status": status,
    }


def main():
    t_start = time.monotonic()
    log.info("=== probe_v100 START ===")

    from curl_cffi import requests as cffi_req

    # Config + App (once)
    from r52.config import load_config
    config = load_config(home=str(HOME))
    db_path = Path(config.paths.db_file)
    log.info("DB: %s exists=%s", db_path, db_path.exists())

    from r52.service import App
    app = App(config, install_guard=False)
    app.open()
    log.info("App opened")

    from r52.collectors import COLLECTOR_REGISTRY
    cls, _ = COLLECTOR_REGISTRY[SOURCE]
    col = cls(data_dir=app.config.paths.data_dir, use_proxies=False)

    results = {}
    for lid, tag in TEST_ITEMS:
        nm_id = int(lid)
        log.info("--- ITEM %s (%s) vol=%d part=%d ---",
                 lid, tag, nm_id//100000, nm_id//1000)
        t_item = time.monotonic()

        # 1. Basket
        t0 = time.monotonic()
        basket_host, card_data = scan_basket(nm_id, cffi_req, max_hosts=60)
        log.info("  basket: %.0fs host=%s card=%s",
                 time.monotonic()-t0, basket_host,
                 f"yes({len(card_data)} keys)" if card_data else "no")

        # 2. Price
        t0 = time.monotonic()
        price_data = fetch_price(nm_id, cffi_req)
        log.info("  price: %.0fs data=%s", time.monotonic()-t0,
                 "yes" if price_data else "no")

        # 3. HTML (best-effort, 12s)
        t0 = time.monotonic()
        html_resp = None
        try:
            hresp = cffi_req.get(
                f"https://www.wildberries.ru/catalog/{lid}/detail.aspx",
                headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                         "Accept-Language": "ru-RU,ru;q=0.9"},
                impersonate="chrome124", timeout=12)
            if hresp.status_code == 200:
                html_resp = hresp
                log.info("  html: 200 %dB (%.1fs)", len(hresp.content),
                         time.monotonic()-t0)
            else:
                log.info("  html: %d (%.1fs)", hresp.status_code,
                         time.monotonic()-t0)
        except Exception as e:
            log.info("  html ERR: %s (%.1fs)", e, time.monotonic()-t0)

        # 4. Merge
        obs_dict = merge_obs(nm_id, card_data, price_data, html_resp, basket_host)
        log.info("  merge: status=%s title=%s prices=%d media=%d avail=%s",
                 obs_dict["status"],
                 str(obs_dict["fields"]["title"]["value"])[:50],
                 len(obs_dict["price_candidates"]),
                 len(obs_dict["media"]["items"]),
                 obs_dict["availability"])

        # 5. Wrap in intake format
        from datetime import datetime, timezone, timedelta
        now_iso = datetime.now(timezone(timedelta(hours=10))).isoformat()
        obs = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": SOURCE,
            "observation": {
                **obs_dict,
                "source": SOURCE,
                "id": lid,
                "url": f"https://www.wildberries.ru/catalog/{lid}/detail.aspx",
                "observed_at": now_iso,
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
            },
        }

        # 6. Write + Inbox
        written = col.write_observations([obs], INTAKE)
        log.info("  written: %s", [Path(w).name for w in written])
        for wp in written:
            data = Path(wp).read_bytes()
            item, _ = app.inbox.stage_bytes(data, filename=Path(wp).name, origin="COLLECTOR")
            st = item.get("status")
            log.info("  inbox: id=%s status=%s", item.get("id"), st)
            if st == "PENDING":
                res = app.inbox.accept(item["id"])
                log.info("  accepted: pid=%s obs=%s",
                         res.get("product_id"), res.get("observation_created"))
            elif st == "ACCEPTED":
                log.info("  auto-accepted")
            elif st == "INVALID":
                log.warning("  INVALID: %s", item.get("error_code"))

        # 7. DB check
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        prods = [dict(r) for r in conn.execute(
            "SELECT id,source,listing_id,title,last_regular_minor,"
            "last_availability,observation_count "
            "FROM product WHERE source=? AND listing_id=? ORDER BY id DESC LIMIT 2",
            (SOURCE, lid))]
        pps = [dict(r) for r in conn.execute(
            "SELECT pp.id,pp.amount_minor,pp.currency,pp.label "
            "FROM price_point pp JOIN product p ON pp.product_id=p.id "
            "WHERE p.source=? AND p.listing_id=? ORDER BY pp.id DESC LIMIT 5",
            (SOURCE, lid))]
        conn.close()
        log.info("  db: %d products, %d pps", len(prods), len(pps))
        for p in prods:
            log.info("    P: id=%s title=%s reg=%s avail=%s obs=%s",
                     p["id"], str(p["title"])[:50], p["last_regular_minor"],
                     p["last_availability"], p["observation_count"])
        for pp in pps[:3]:
            log.info("    PP: amt=%s cur=%s label=%s",
                     pp["amount_minor"], pp["currency"], pp["label"])

        has_p = len(prods) > 0
        has_t = has_p and not str(prods[0]["title"]).startswith(f"{SOURCE}-")
        has_pp = len(pps) > 0 and pps[0]["amount_minor"] is not None
        verdict = (f"{'PASS' if has_p else 'FAIL'}"
                   f"{'_TITLE' if has_t else '_FBT'}"
                   f"{'_PRICE' if has_pp else '_NOP'}")
        log.info("  VERDICT %s: %s (%.0fs)", tag, verdict, time.monotonic()-t_item)
        results[tag] = verdict

    # Final summary
    log.info("=== SUMMARY: %s ===", results)
    app.stop()
    log.info("=== probe_v100 DONE (%.0fs) ===", time.monotonic()-t_start)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("FATAL")
        sys.exit(1)

"""probe_v101 — WB: fixed host parse, card.json dump, multi-strategy price.

Fixes from v100:
- basket_host.split("-")[1] -> regex to extract number
- Dump card.json keys + data + media fields
- Price: v4 minimal, multiple impersonate, 3s delay before
- Both test items

Usage: python -u probe_v101.py
"""
import json, logging, os, random, re, sys, time, threading
from pathlib import Path

ROOT = Path(__file__).parent
LOG_FILE = ROOT / "probe_v101.log"

_fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
_fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
_fh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_sh, _fh])
log = logging.getLogger("v101")

WATCHDOG_SECS = 300
def _watchdog():
    time.sleep(WATCHDOG_SECS)
    log.error("WATCHDOG: %d s exceeded, forcing exit", WATCHDOG_SECS)
    os._exit(2)
threading.Thread(target=_watchdog, daemon=True).start()

SOURCE = "WB"
TEST_ITEMS = [
    ("1097975275", "NEW"),
    ("259671175",  "OLD"),
]

sys.path.insert(0, str(ROOT))
HOME = ROOT / "r52"
INTAKE = HOME / "data" / "intake"
INTAKE.mkdir(parents=True, exist_ok=True)


def basket_path(nm_id: int) -> str:
    vol = nm_id // 100000
    part = nm_id // 1000
    return f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"


def parse_basket_num(host: str) -> int:
    """Extract number from 'basket-42.wbbasket.ru' -> 42."""
    m = re.search(r"basket-(\d+)\.", host)
    return int(m.group(1)) if m else 0


def scan_basket(nm_id: int, cffi_req, max_hosts: int = 60) -> tuple:
    path = basket_path(nm_id)
    log.info("  basket path: %s", path)
    for n in range(1, max_hosts + 1):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        t_b = time.monotonic()
        try:
            resp = cffi_req.get(url, headers={"Accept": "application/json"},
                                impersonate="chrome124", timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("nm_id"):
                    log.info("  basket HIT: %s (%.1fs, %dB)", host,
                             time.monotonic()-t_b, len(resp.content))
                    return host, data
            elif n % 10 == 0:
                log.info("  basket: %d/%d, last=%s->%d", n, max_hosts, host, resp.status_code)
        except Exception:
            if n % 10 == 0:
                log.info("  basket: %d/%d ERR", n, max_hosts)
    return None, None


def try_price(nm_id: int, cffi_req) -> dict | None:
    """Multi-strategy price fetch."""
    strategies = [
        # (label, url, impersonate, headers_extra)
        ("v4_min_chrome124",
         f"https://card.wb.ru/cards/v4/detail?nm={nm_id}",
         "chrome124", {}),
        ("v4_full_chrome124",
         f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
         "chrome124", {
             "Accept": "application/json, text/plain, */*",
             "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
             "Referer": "https://www.wildberries.ru/",
             "Origin": "https://www.wildberries.ru",
         }),
        ("v4_min_chrome",
         f"https://card.wb.ru/cards/v4/detail?nm={nm_id}",
         "chrome", {}),
        ("v4_full_chrome",
         f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
         "chrome", {
             "Accept": "application/json, text/plain, */*",
             "Referer": "https://www.wildberries.ru/",
         }),
        ("v4_safari",
         f"https://card.wb.ru/cards/v4/detail?nm={nm_id}",
         "safari", {}),
    ]
    for label, url, imp, extra_h in strategies:
        t_p = time.monotonic()
        headers = {"Accept": "application/json"}
        headers.update(extra_h)
        try:
            resp = cffi_req.get(url, headers=headers,
                                impersonate=imp, timeout=10)
            code = resp.status_code
            body_snip = resp.text[:150] if resp.text else ""
            if code == 200:
                data = resp.json()
                products = data.get("products") or []
                if products:
                    p = products[0]
                    result = {"name": p.get("name"), "brand": p.get("brand"),
                              "sizes": p.get("sizes", []), "source": label}
                    for sz in p.get("sizes", []):
                        pr = sz.get("price")
                        if pr and isinstance(pr, dict) and any(
                            isinstance(v, (int, float)) and v > 0 for v in pr.values()):
                            result["prices"] = pr
                            result["qty"] = sz.get("qty")
                            break
                    log.info("  price [%s] OK: name=%s qty=%s (%.1fs)",
                             label, str(result.get("name","?"))[:40],
                             result.get("qty"), time.monotonic()-t_p)
                    return result
                log.info("  price [%s] 200 but no products (%.1fs)", label, time.monotonic()-t_p)
            else:
                log.info("  price [%s] -> %d body=%s (%.1fs)",
                         label, code, body_snip[:80], time.monotonic()-t_p)
        except Exception as e:
            log.info("  price [%s] ERR %s (%.1fs)", label, e, time.monotonic()-t_p)
        time.sleep(1)
    return None


def merge_obs(nm_id: int, card_data, price_data, basket_host) -> dict:
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
        fields["description"] = {"status": "KNOWN", "value": str(desc)[:3000],
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

    # Prices from price_data
    if price_data and price_data.get("prices"):
        pr = price_data["prices"]
        role_map = {"basic": "regular", "product": "regular", "card": "card",
                    "wallet": "wallet", "old": "old", "crossed": "old", "bankCard": "card"}
        for key, val in pr.items():
            if isinstance(val, (int, float)) and val > 0:
                role = role_map.get(key, "regular")
                price_candidates.append({
                    "role": role, "amount_minor": int(val),
                    "currency": "RUB", "label": f"price_api.{key}",
                    "amount": f"{int(val)//100}.{int(val)%100:02d}",
                    "visible_label": f"price_api.{key}",
                })

    # Availability
    if price_data and price_data.get("qty") is not None:
        availability = "IN_STOCK" if price_data["qty"] > 0 else "OUT_OF_STOCK"

    # Media from card.json
    if card_data:
        media_field = card_data.get("media")
        if isinstance(media_field, list):
            for m in media_field[:10]:
                if isinstance(m, dict):
                    mtype = m.get("type", "image")
                    murl = m.get("url") or m.get("src") or ""
                    if murl and murl.startswith("http"):
                        media_items.append({"type": mtype, "source_url": murl, "image_data": None})
        # Fallback: construct S800 URL
        if not media_items and basket_host:
            n = parse_basket_num(basket_host)
            vol = nm_id // 100000
            part = nm_id // 1000
            primary = (f"https://basket-{n:02d}.wbbasket.ru/vol{vol}/part{part}"
                       f"/{nm_id}/images/S800/S800.jpg")
            media_items.append({"type": "image", "source_url": primary, "image_data": None})

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
    log.info("=== probe_v101 START ===")

    from curl_cffi import requests as cffi_req

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

        # 1b. Dump card.json structure
        if card_data:
            keys = sorted(card_data.keys())
            log.info("  card keys: %s", keys)
            log.info("  card imt_name: %s", str(card_data.get("imt_name",""))[:80])
            log.info("  card subj: %s / %s",
                     card_data.get("subj_root_name",""), card_data.get("subj_name",""))
            data_f = card_data.get("data")
            if data_f:
                if isinstance(data_f, dict):
                    log.info("  card data keys: %s", list(data_f.keys())[:20])
                    # Look for price in data
                    for dk in ("price", "prices", "priceData", "price_data"):
                        if dk in data_f:
                            log.info("  card data.%s: %s", dk, json.dumps(data_f[dk], ensure_ascii=False)[:200])
                elif isinstance(data_f, list):
                    log.info("  card data: list len=%d first=%s",
                             len(data_f), json.dumps(data_f[0], ensure_ascii=False)[:150] if data_f else "empty")
                else:
                    log.info("  card data: %s", str(data_f)[:150])
            media_f = card_data.get("media")
            if media_f:
                log.info("  card media: type=%s len=%s sample=%s",
                         type(media_f).__name__,
                         len(media_f) if isinstance(media_f, (list, dict)) else "?",
                         json.dumps(media_f, ensure_ascii=False)[:200] if isinstance(media_f, (list, dict)) else str(media_f)[:100])
            # Dump full card to file for inspection
            dump_path = ROOT / f"card_{lid}.json"
            dump_path.write_text(json.dumps(card_data, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("  card dumped: %s (%dB)", dump_path.name, dump_path.stat().st_size)

        # 2. Price (with 3s delay after basket)
        time.sleep(3)
        t0 = time.monotonic()
        price_data = try_price(nm_id, cffi_req)
        log.info("  price: %.0fs data=%s", time.monotonic()-t0,
                 "yes" if price_data else "no")

        # 3. Merge
        obs_dict = merge_obs(nm_id, card_data, price_data, basket_host)
        log.info("  merge: status=%s title=%s prices=%d media=%d avail=%s",
                 obs_dict["status"],
                 str(obs_dict["fields"]["title"]["value"])[:50],
                 len(obs_dict["price_candidates"]),
                 len(obs_dict["media"]["items"]),
                 obs_dict["availability"])

        # 4. Wrap in intake format
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

        # 5. Write + Inbox
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

        # 6. DB check
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

    log.info("=== SUMMARY: %s ===", results)
    app.stop()
    log.info("=== probe_v101 DONE (%.0fs) ===", time.monotonic()-t_start)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("FATAL")
        sys.exit(1)

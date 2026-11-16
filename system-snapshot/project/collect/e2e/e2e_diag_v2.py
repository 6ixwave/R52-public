r"""e2e_diag_v2.py — Focused diagnostic: one listing per marketplace, step timing, JSON dump.
Run: C:\Python315\python.exe e2e_diag_v2.py
"""
import sys, os, json, time, logging, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

LOG_FILE = ROOT / "e2e_diag_v2_out.txt"
fmt = logging.Formatter("%(asctime)s %(name)-18s %(levelname)-5s %(message)s")
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
fh.setFormatter(fmt)
log = logging.getLogger("DIAG2")
log.setLevel(logging.DEBUG)
log.addHandler(sh)
log.addHandler(fh)

# Also capture collector logs
for name in ("r52.collectors", "r52.collectors.wb", "r52.collectors.ozon",
             "r52.collectors.farpost", "r52.collectors.avito", "r52.collectors.base"):
    lg = logging.getLogger(name)
    lg.setLevel(logging.DEBUG)
    lg.addHandler(sh)
    lg.addHandler(fh)

RESULTS = {}


def test_wb():
    log.info("=== WB: 1097975275 (iPhone 17 Pro Max) ===")
    t0 = time.time()
    from r52.collectors.wb import WBCollector
    col = WBCollector(data_dir=None, use_proxies=False)
    nm_id = 1097975275

    # Step 1: card.json
    t1 = time.time()
    card = col._fetch_card_json(nm_id)
    log.info("  card.json: %.1fs  keys=%s", time.time()-t1,
             sorted(card.keys())[:15] if card else "None")
    if card:
        selling = card.get("selling", {})
        log.info("  selling keys: %s", list(selling.keys()) if isinstance(selling, dict) else type(selling))
        for pk in ("price", "priceU", "priceD", "oldPrice"):
            log.info("    selling.%s = %s", pk, selling.get(pk) if isinstance(selling, dict) else "?")
        for pk in ("price", "priceU", "priceD"):
            log.info("    top.%s = %s", pk, card.get(pk))
        sizes = card.get("sizes", [])
        log.info("    sizes: %d items", len(sizes) if isinstance(sizes, list) else 0)
        if isinstance(sizes, list) and sizes:
            s0 = sizes[0]
            log.info("    sizes[0] keys: %s", list(s0.keys())[:10])
            log.info("    sizes[0].price: %s", s0.get("price"))

    # Step 2: price API
    t2 = time.time()
    price = col._fetch_price(nm_id)
    log.info("  _fetch_price: %.1fs  result=%s", time.time()-t2,
             json.dumps(price, ensure_ascii=False, default=str)[:200] if price else "None")

    # Step 3: HTML
    t3 = time.time()
    url = col.build_url(str(nm_id))
    html_resp = col._fetch_html(url)
    log.info("  _fetch_html: %.1fs  status=%s len=%d", time.time()-t3,
             html_resp.status_code if html_resp else 0,
             len(html_resp.content) if html_resp else 0)

    # Step 4: full collect
    t4 = time.time()
    result = col.collect_product(str(nm_id), url, "iPhone 17 Pro Max")
    dt = time.time() - t4
    log.info("  collect_product: %.1fs  result=%s", dt, "OK" if result else "None")

    if result:
        obs = result.get("observation", {})
        prices = obs.get("price_candidates", [])
        log.info("  prices=%d  status=%s", len(prices), obs.get("status"))
        for p in prices[:5]:
            log.info("    %s %d %s '%s'", p.get("role"), p.get("amount_minor", 0),
                     p.get("currency"), p.get("label", ""))
    RESULTS["WB"] = {"dt": round(dt, 1), "result": result,
                     "card_keys": sorted(card.keys()) if card else None,
                     "price_data": price}
    if hasattr(col, "close"):
        col.close()


def test_ozon():
    log.info("=== OZON: 2067947177 (Palit RTX 5060 Ti) ===")
    t0 = time.time()
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)
    lid = "2067947177"
    url = col.build_url(lid)
    log.info("  URL: %s", url)

    # Step 1: Composer API only
    t1 = time.time()
    composer = col._try_composer_api(lid, url, "Palit RTX 5060 Ti")
    log.info("  composer_api: %.1fs  result=%s", time.time()-t1, "OK" if composer else "None")
    if composer:
        obs = composer.get("observation", {})
        log.info("    prices=%d title=%s", len(obs.get("price_candidates", [])),
                 str(obs.get("fields", {}).get("title", {}).get("value", ""))[:50])

    # Step 2: full collect (includes PW + HTTP)
    t2 = time.time()
    result = col.collect_product(lid, url, "Palit RTX 5060 Ti")
    dt = time.time() - t2
    log.info("  collect_product: %.1fs  result=%s", dt, "OK" if result else "None")

    if result:
        obs = result.get("observation", {})
        prices = obs.get("price_candidates", [])
        log.info("  prices=%d  status=%s  evidence=%s", len(prices), obs.get("status"),
                 obs.get("evidence_level"))
    RESULTS["OZON"] = {"dt": round(dt, 1), "result": result,
                       "composer": composer}
    if hasattr(col, "close"):
        col.close()


def test_farpost():
    log.info("=== FARPOST: 16879823067 (Датчик кислорода LYNXauto) ===")
    t0 = time.time()
    from r52.collectors.farpost import FarpostCollector
    col = FarpostCollector(data_dir=None, use_proxies=False)
    lid = "16879823067"
    url = col.build_url(lid)
    log.info("  URL: %s", url)

    t1 = time.time()
    result = col.collect_product(lid, url, "Датчик кислорода LYNXauto")
    dt = time.time() - t1
    log.info("  collect_product: %.1fs  result=%s", dt, "OK" if result else "None")

    if result:
        obs = result.get("observation", {})
        prices = obs.get("price_candidates", [])
        log.info("  prices=%d  status=%s", len(prices), obs.get("status"))
        for p in prices[:5]:
            log.info("    %s %d %s '%s'", p.get("role"), p.get("amount_minor", 0),
                     p.get("currency"), p.get("label", ""))
        title = obs.get("fields", {}).get("title", {}).get("value", "")
        log.info("  title: %s", title[:60])
    RESULTS["FARPOST"] = {"dt": round(dt, 1), "result": result}
    if hasattr(col, "close"):
        col.close()


def test_avito():
    log.info("=== AVITO: 1154321987 ===")
    t0 = time.time()
    from r52.collectors.avito import AvitoCollector
    col = AvitoCollector(data_dir=None, use_proxies=False)
    lid = "1154321987"
    url = col.build_url(lid)
    log.info("  URL: %s", url)

    t1 = time.time()
    result = col.collect_product(lid, url, "Avito test")
    dt = time.time() - t1
    log.info("  collect_product: %.1fs  result=%s", dt, "OK" if result else "None")

    if result:
        obs = result.get("observation", {})
        prices = obs.get("price_candidates", [])
        log.info("  prices=%d  status=%s", len(prices), obs.get("status"))
        title = obs.get("fields", {}).get("title", {}).get("value", "")
        log.info("  title: %s", title[:60])
    RESULTS["AVITO"] = {"dt": round(dt, 1), "result": result}
    if hasattr(col, "close"):
        col.close()


def main():
    log.info("E2E DIAG V2  %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    t_start = time.time()

    for name, fn in [("WB", test_wb), ("OZON", test_ozon),
                     ("FARPOST", test_farpost), ("AVITO", test_avito)]:
        try:
            fn()
        except Exception:
            log.error("%s EXCEPTION:\n%s", name, traceback.format_exc())
            RESULTS[name] = {"error": traceback.format_exc()}

    total = time.time() - t_start
    log.info("=" * 50)
    log.info("SUMMARY (%.1fs total):", total)
    for name in ("WB", "OZON", "FARPOST", "AVITO"):
        r = RESULTS.get(name, {})
        res = "OK" if r.get("result") else ("ERR" if r.get("error") else "None")
        log.info("  %-10s %-6s  %.1fs", name, res, r.get("dt", 0))

    # Dump
    dump = {}
    for name, r in RESULTS.items():
        dump[name] = {
            "dt": r.get("dt"),
            "error": r.get("error"),
            "result": r.get("result"),
            "extra": {k: v for k, v in r.items() if k not in ("dt", "error", "result")},
        }
    out = ROOT / "e2e_diag_v2_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2, default=str)
    log.info("Saved %s", out.name)
    log.info("DONE")


if __name__ == "__main__":
    main()

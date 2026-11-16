r"""e2e_diag2.py — Целенаправленная диагностика v2.
Фокус: Ozon (SSL+403), WB (price), Avito (m.avito), Farpost (подтверждение).
Run: C:\Python315\python.exe e2e_diag2.py
"""
import sys, os, time, json, logging, traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(name)-20s %(levelname)-5s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("DIAG2")

TIMEOUT_PER = 50  # сек на один коллектор

# Живые объявления (12.11.2026)
TESTS = [
    ("OZON",    "5711140788", "PB Play Box RTX 5060 Ti"),
    ("WB",      "1097975275", "iPhone 17 Pro Max"),
    ("FARPOST", "138859452",  "Dvigatel Nissan CGA3"),
    ("AVITO",   "1045231001", "Avito test"),
]

COLLECTOR_MAP = {
    "OZON":    ("r52.collectors.ozon",    "OzonCollector"),
    "WB":      ("r52.collectors.wb",      "WBCollector"),
    ("FARPOST": ("r52.collectors.farpost",  "FarpostCollector"),
    ("AVITO":   ("r52.collectors.avito",    "AvitoCollector"),
}


def run_ozon(lid, hint):
    """Ozon: curl_cffi verify=False → requests → Playwright."""
    import importlib
    mod = importlib.import_module("r52.collectors.ozon")
    col = mod.OzonCollector(data_dir=None, use_proxies=False)
    url = col.build_url(lid)
    report = {"name": "OZON", "lid": lid, "url": url}

    # Strategy 1: curl_cffi with verify=False
    try:
        from curl_cffi import requests as cffi_req
        t0 = time.time()
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.ozon.ru/",
        }
        ep = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/"
        log.info("OZON cffi composer: %s", ep)
        resp = cffi_req.get(ep, impersonate="chrome", timeout=15, headers=headers, verify=False)
        dt = time.time() - t0
        log.info("OZON cffi composer: HTTP %d (%.1fs, %dB)", resp.status_code, dt, len(resp.content))
        if resp.status_code == 200:
            data = resp.json()
            # Quick check: is there useful data?
            has_title = False
            has_price = False
            for path in [("page","title"),("product","title"),("product","name")]:
                obj = data
                for k in path:
                    if isinstance(obj, dict):
                        obj = obj.get(k)
                    else:
                        obj = None
                        break
                if obj:
                    has_title = True
                    report["title"] = str(obj)[:60]
                    break
            for path in [("product","price"),("page","price"),("product","priceInfo")]:
                obj = data
                for k in path:
                    if isinstance(obj, dict):
                        obj = obj.get(k)
                    else:
                        obj = None
                        break
                if obj:
                    has_price = True
                    report["price_obj"] = str(obj)[:200]
                    break
            report["ok"] = True
            report["method"] = "cffi_composer"
            report["dt"] = round(dt, 1)
            report["n_prices"] = 1 if has_price else 0
            col.close()
            return report
        elif resp.status_code == 403:
            report["cffi_status"] = 403
        else:
            report["cffi_status"] = resp.status_code
    except Exception as e:
        log.info("OZON cffi error: %s", e)
        report["cffi_error"] = str(e)[:100]

    # Strategy 2: plain requests (no SSL issue)
    try:
        import requests as req
        t0 = time.time()
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }
        resp = req.get(url, headers=headers, timeout=(5, 15), verify=True)
        dt = time.time() - t0
        log.info("OZON requests HTML: HTTP %d (%.1fs, %dB)", resp.status_code, dt, len(resp.text))
        if resp.status_code == 200 and len(resp.text) > 1000:
            # Parse inline
            import re
            title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', resp.text, re.I)
            price_m = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб)', resp.text)
            report["ok"] = True
            report["method"] = "requests_html"
            report["dt"] = round(dt, 1)
            report["title"] = title_m.group(1)[:60] if title_m else "?"
            report["n_prices"] = len(price_m[:5])
            report["prices_raw"] = price_m[:5]
            col.close()
            return report
        elif resp.status_code == 403:
            report["req_status"] = 403
    except Exception as e:
        log.info("OZON requests error: %s", e)
        report["req_error"] = str(e)[:100]

    # Strategy 3: Playwright
    try:
        t0 = time.time()
        pw = col._get_pw()
        result = pw.get(url, extra_wait_ms=3000)
        dt = time.time() - t0
        if result and result.status == 200 and len(result.html) > 1000:
            import re
            title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', result.html, re.I)
            price_m = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб)', result.html)
            report["ok"] = True
            report["method"] = "playwright"
            report["dt"] = round(dt, 1)
            report["title"] = title_m.group(1)[:60] if title_m else "?"
            report["n_prices"] = len(price_m[:5])
            report["prices_raw"] = price_m[:5]
        else:
            report["pw_status"] = result.status if result else 0
            report["pw_len"] = len(result.html) if result else 0
            report["dt"] = round(dt, 1)
    except Exception as e:
        log.info("OZON PW error: %s", e)
        report["pw_error"] = str(e)[:100]
        report.setdefault("dt", round(time.time() - t0, 1))

    if "ok" not in report:
        report["ok"] = False
        report["reason"] = "all strategies failed"
    col.close()
    return report


def run_wb(lid, hint):
    """WB: card.json + price API (быстро)."""
    import importlib
    mod = importlib.import_module("r52.collectors.wb")
    col = mod.WBCollector(data_dir=None, use_proxies=False)
    nm_id = int(lid)
    report = {"name": "WB", "lid": lid, "url": col.build_url(lid)}

    # 1. Card JSON
    t0 = time.time()
    card = col._fetch_card_json(nm_id)
    dt_card = time.time() - t0
    if card:
        report["card_ok"] = True
        report["title"] = card.get("imt_name", "?")[:60]
        report["card_dt"] = round(dt_card, 1)
        log.info("WB card.json OK: %s (%.1fs)", report["title"], dt_card)
    else:
        report["card_ok"] = False
        report["card_dt"] = round(dt_card, 1)
        log.info("WB card.json FAILED (%.1fs)", dt_card)

    # 2. Price
    t0 = time.time()
    price = col._fetch_price(nm_id)
    dt_price = time.time() - t0
    if price:
        report["price_ok"] = True
        report["price_source"] = price.get("source", "?")
        prices = price.get("prices", {})
        if isinstance(prices, dict):
            report["price_keys"] = list(prices.keys())[:10]
            report["price_sample"] = {k: prices[k] for k in list(prices.keys())[:5]}
        report["price_dt"] = round(dt_price, 1)
        log.info("WB price OK via %s (%.1fs)", price.get("source"), dt_price)
    else:
        report["price_ok"] = False
        report["price_dt"] = round(dt_price, 1)
        log.info("WB price FAILED (%.1fs)", dt_price)

    report["ok"] = report.get("card_ok", False) or report.get("price_ok", False)
    report["dt"] = round(dt_card + dt_price, 1)
    col.close()
    return report


def run_farpost(lid, hint):
    """Farpost: полный collect_product."""
    import importlib
    mod = importlib.import_module("r52.collectors.farpost")
    col = mod.FarpostCollector(data_dir=None, use_proxies=False)
    url = col.build_url(lid)
    t0 = time.time()
    try:
        result = col.collect_product(lid, url, hint)
        dt = time.time() - t0
        if result:
            obs = result.get("observation", {})
            fields = obs.get("fields", {})
            prices = obs.get("price_candidates", [])
            report = {
                "name": "FARPOST", "lid": lid, "url": url,
                "ok": True, "dt": round(dt, 1),
                "status": obs.get("status", "?"),
                "title": fields.get("title", {}).get("value", "?")[:60],
                "n_prices": len(prices),
                "prices": [{"role": p.get("role"), "minor": p.get("amount_minor"),
                            "label": p.get("label", "")[:30]} for p in prices[:5]],
                "n_media": len(obs.get("media", {}).get("items", [])),
            }
        else:
            report = {"name": "FARPOST", "lid": lid, "url": url,
                      "ok": False, "dt": round(dt, 1), "reason": "None"}
    except Exception as e:
        report = {"name": "FARPOST", "lid": lid, "ok": False,
                  "dt": round(time.time()-t0, 1), "reason": str(e)[:100]}
    col.close()
    return report


def run_avito(lid, hint):
    """Avito: m.avito.ru → www.avito.ru, короткий таймаут."""
    import importlib
    mod = importlib.import_module("r52.collectors.avito")
    col = mod.AVITOCOLLECTOR = mod.AvitoCollector(data_dir=None, use_proxies=False)
    report = {"name": "AVITO", "lid": lid}

    # Try m.avito.ru first (less captcha)
    for domain in ["m.avito.ru", "www.avito.ru"]:
        url = f"https://{domain}/ad/{lid}"
        t0 = time.time()
        try:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            }
            resp = col.session.get(url, headers=headers, timeout=(5, 12))
            dt = time.time() - t0
            log.info("AVITO %s: HTTP %d (%.1fs, %dB)", domain, resp.status_code, dt, len(resp.text))
            if resp.status_code == 200 and len(resp.text) > 500:
                # Quick captcha check
                if "captcha" in resp.text[:3000].lower():
                    log.info("AVITO %s: CAPTCHA detected", domain)
                    report[f"{domain}_captcha"] = True
                    continue
                import re
                title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', resp.text, re.I)
                price_m = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб)', resp.text)
                report["ok"] = True
                report["method"] = domain
                report["dt"] = round(dt, 1)
                report["title"] = title_m.group(1)[:60] if title_m else "?"
                report["n_prices"] = len(price_m[:5])
                report["prices_raw"] = price_m[:5]
                break
            elif resp.status_code in (403, 429):
                report[f"{domain}_status"] = resp.status_code
            else:
                report[f"{domain}_status"] = resp.status_code
        except Exception as e:
            log.info("AVITO %s error: %s", domain, e)
            report[f"{domain}_error"] = str(e)[:80]

    if "ok" not in report:
        report["ok"] = False
        report["reason"] = "all URLs failed"
    col.close()
    return report


def main():
    log.info("=" * 60)
    log.info("DIAG2  %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    runners = {
        "OZON": run_ozon,
        "WB": run_wb,
        "FARPOST": run_farpost,
        "AVITO": run_avito,
    }

    results = []
    for name, lid, hint in TESTS:
        log.info("--- %s (%s) ---", name, lid)
        runner = runners[name]
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(runner, lid, hint)
            try:
                r = fut.result(timeout=TIMEOUT_PER)
            except FuturesTimeout:
                r = {"name": name, "lid": lid, "dt": TIMEOUT_PER, "ok": False, "reason": "TIMEOUT"}
            except Exception as e:
                r = {"name": name, "lid": lid, "ok": False, "reason": str(e)[:100]}
        results.append(r)

        log.info("  dt=%.1fs  ok=%s  method=%s  title='%s'",
                 r.get("dt", 0), r.get("ok"), r.get("method", "?"), r.get("title", "?")[:40])
        if r.get("ok"):
            log.info("  prices=%d  media=%d", r.get("n_prices", 0), r.get("n_media", 0))
            for p in r.get("prices", [])[:3]:
                log.info("    %-8s %10d  '%s'", p.get("role","?"), p.get("minor",0), p.get("label",""))
            if r.get("prices_raw"):
                log.info("    raw: %s", r["prices_raw"][:3])
        else:
            log.info("  reason: %s", r.get("reason", r.get("cffi_status", r.get("req_status", "?"))))
        log.info("  extra: %s", {k:v for k,v in r.items() if k not in ("name","lid","dt","ok","title","method","n_prices","n_media","reason","url")})

    log.info("=" * 60)
    log.info("SUMMARY:")
    for r in results:
        mark = "PASS" if r.get("ok") and r.get("n_prices", 0) > 0 else "PART" if r.get("ok") else "FAIL"
        log.info("  %-6s %-10s dt=%5.1fs  prices=%d  %s",
                 mark, r["name"], r.get("dt", 0), r.get("n_prices", 0), r.get("method", r.get("reason", "?")[:20]))

    with open(ROOT / "e2e_diag2_result.json", "w", encoding="utf-8") as f:
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results},
                  f, ensure_ascii=False, indent=2, default=str)
    log.info("Saved e2e_diag2_result.json")


if __name__ == "__main__":
    main()

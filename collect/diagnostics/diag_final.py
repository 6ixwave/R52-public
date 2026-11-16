"""diag_final.py — E2E: все 4 маркетплейса, быстрые вероятные методы + fallback."""
import json, logging, re, time, sys, traceback
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_final")

BASE = Path(r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")
sys.path.insert(0, str(BASE))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
results = {}


def _pack(o, method, dt):
    prices = o.get("price_candidates", [])
    media = o.get("media", {}).get("items", [])
    title = o.get("fields", {}).get("title", {}).get("value", "?")
    return {
        "ok": bool(prices),
        "method": method,
        "title": title[:80],
        "prices": [{"role": p["role"], "amount": p.get("amount")} for p in prices[:4]],
        "media_count": len(media),
        "availability": o.get("availability", "UNKNOWN"),
        "time": round(dt, 1),
    }


# ================================================================
# 1. OZON
# ================================================================
def test_ozon():
    log.info("=== OZON ===")
    t0 = time.monotonic()
    pid = "1600562267"
    url = f"https://www.ozon.ru/product/{pid}/"

    # S1: requests SSR (current collector)
    try:
        from r52.collectors.ozon import OzonCollector
        c = OzonCollector(data_dir=BASE, use_proxies=False)
        obs = c.collect_product(pid, url, "Ozon test")
        if obs:
            o = obs["observation"]
            if o.get("price_candidates"):
                results["ozon"] = _pack(o, "requests_ssr", time.monotonic() - t0)
                return
            log.info("OZON S1: no prices, trying PW")
    except Exception as e:
        log.warning("OZON S1: %s", e)

    # S2: Playwright
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True)
            pg = br.new_page(user_agent=UA, locale="ru-RU",
                             viewport={"width": 1920, "height": 1080})
            pg.goto(url, timeout=30000, wait_until="domcontentloaded")
            pg.wait_for_timeout(4000)
            html = pg.content()
            br.close()

        class FR:
            text = html
        from r52.collectors.ozon import OzonCollector
        c = OzonCollector(data_dir=BASE, use_proxies=False)
        od = c.parse_page(FR(), pid, url, "Ozon PW")
        if od:
            o = {"fields": od.get("fields", {}),
                 "price_candidates": od.get("price_candidates", []),
                 "media": od.get("media", {"items": []}),
                 "availability": od.get("availability", "UNKNOWN")}
            results["ozon"] = _pack(o, f"playwright {len(html)}B", time.monotonic() - t0)
            return
    except Exception as e:
        log.warning("OZON S2: %s", e)

    results["ozon"] = {"ok": False, "error": "all failed", "time": round(time.monotonic() - t0, 1)}


# ================================================================
# 2. WB
# ================================================================
def test_wb():
    log.info("=== WB ===")
    t0 = time.monotonic()
    nm = 138278567
    url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"

    # S1: WBCollector (basket CDN + price API)
    try:
        from r52.collectors.wb import WBCollector
        c = WBCollector(data_dir=BASE, use_proxies=False)
        obs = c.collect_product(str(nm), url, "WB test")
        if obs:
            o = obs["observation"]
            prices = o.get("price_candidates", [])
            title = o.get("fields", {}).get("title", {}).get("value", "")
            if prices or (title and title != f"WB-{nm}"):
                results["wb"] = _pack(o, "basket_cdn", time.monotonic() - t0)
                return
            log.info("WB S1: partial (title=%s, prices=%d)", title[:40], len(prices))
    except Exception as e:
        log.warning("WB S1: %s", e)

    # S2: Playwright + WBAAS → rendered DOM + XHR
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True)
            ctx = br.new_context(user_agent=UA, locale="ru-RU",
                                 viewport={"width": 1920, "height": 1080})
            pg = ctx.new_page()
            xhr_bodies = []
            def _on_resp(r):
                try:
                    if "card" in r.url.lower() or "price" in r.url.lower():
                        if r.status == 200:
                            j = r.json()
                            xhr_bodies.append({"url": r.url[:120], "keys": list(j.keys())[:10] if isinstance(j, dict) else type(j).__name__})
                except Exception:
                    pass
            pg.on("response", _on_resp)
            pg.goto(url, timeout=30000, wait_until="domcontentloaded")
            pg.wait_for_timeout(10000)
            html = pg.content()

            # Extract from DOM
            tm = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
            prices_dom = []
            for pm in re.finditer(r'(\d[\d\s\u00a0]{2,10})\s*₽', html):
                raw = pm.group(1).replace(" ", "").replace("\u00a0", "")
                try:
                    v = int(raw)
                    if 100 < v < 10_000_000:
                        prices_dom.append({"role": "regular", "amount_minor": v,
                                           "currency": "RUB", "label": "PW-DOM",
                                           "amount": f"{v//100}.{v%100:02d}"})
                except ValueError:
                    pass
            prices_dom = prices_dom[:6]

            # XHR data
            for xb in xhr_bodies[:5]:
                log.info("WB XHR: %s keys=%s", xb["url"], xb["keys"])

            br.close()

        o = {
            "fields": {"title": {"status": "KNOWN",
                                 "value": tm.group(1).strip() if tm else f"WB-{nm}"}},
            "price_candidates": prices_dom,
            "media": {"items": []},
            "availability": "UNKNOWN",
        }
        results["wb"] = _pack(o, f"playwright {len(html)}B xhr={len(xhr_bodies)}",
                              time.monotonic() - t0)
        return
    except Exception as e:
        log.warning("WB S2: %s", e)

    results["wb"] = {"ok": False, "error": "all failed", "time": round(time.monotonic() - t0, 1)}


# ================================================================
# 3. AVITO
# ================================================================
def test_avito():
    log.info("=== AVITO ===")
    t0 = time.monotonic()
    live_id = None
    ad_html_pw = ""

    # S1: Playwright search → live ID
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True)
            ctx = br.new_context(user_agent=UA, locale="ru-RU",
                                 viewport={"width": 1920, "height": 1080})
            pg = ctx.new_page()
            pg.goto("https://www.avito.ru/moskva/noutbuki",
                    timeout=30000, wait_until="domcontentloaded")
            pg.wait_for_timeout(6000)
            shtml = pg.content()

            ids = list(dict.fromkeys(
                re.findall(r'avito\.ru/[^"\'/?]+/(\d{6,})/', shtml)
                + re.findall(r'"adId"\s*:\s*(\d+)', shtml)
                + re.findall(r'data-ad-id=["\'](\d+)["\']', shtml)
            ))
            log.info("AVITO search: %d IDs: %s", len(ids), ids[:8])

            for aid in ids[:6]:
                try:
                    pg.goto(f"https://www.avito.ru/ad/{aid}",
                            timeout=20000, wait_until="domcontentloaded")
                    pg.wait_for_timeout(3000)
                    ahtml = pg.content()
                    dt = pg.title()
                    if len(ahtml) > 5000 and "404" not in dt.lower() \
                       and "не найдено" not in dt.lower():
                        live_id = aid
                        ad_html_pw = ahtml
                        log.info("AVITO live: %s (%s, %dB)", aid, dt[:50], len(ahtml))
                        break
                except Exception:
                    continue
            br.close()
    except Exception as e:
        log.warning("AVITO PW: %s", e)

    if not live_id:
        results["avito"] = {"ok": False, "error": "no live ID",
                            "time": round(time.monotonic() - t0, 1)}
        return

    # S2: Parse with AvitoCollector (requests)
    try:
        from r52.collectors.avito import AvitoCollector
        c = AvitoCollector(data_dir=BASE, use_proxies=False)
        obs = c.collect_product(live_id, f"https://www.avito.ru/ad/{live_id}", "Avito")
        if obs:
            o = obs["observation"]
            results["avito"] = _pack(o, f"requests id={live_id}",
                                     time.monotonic() - t0)
            return
    except Exception as e:
        log.warning("AVITO parse: %s", e)

    # S3: Parse PW HTML directly
    if ad_html_pw:
        try:
            class FR:
                text = ad_html_pw
            from r52.collectors.avito import AvitoCollector
            c = AvitoCollector(data_dir=BASE, use_proxies=False)
            od = c.parse_page(FR(), live_id, f"https://www.avito.ru/ad/{live_id}", "Avito PW")
            if od:
                o = {"fields": od.get("fields", {}),
                     "price_candidates": od.get("price_candidates", []),
                     "media": od.get("media", {"items": []}),
                     "availability": od.get("availability", "UNKNOWN")}
                results["avito"] = _pack(o, f"pw_html id={live_id}",
                                         time.monotonic() - t0)
                return
        except Exception as e:
            log.warning("AVITO PW parse: %s", e)

    results["avito"] = {"ok": False, "live_id": live_id, "error": "parse failed",
                        "time": round(time.monotonic() - t0, 1)}


# ================================================================
# 4. FARPOST
# ================================================================
def test_farpost():
    log.info("=== FARPOST ===")
    t0 = time.monotonic()

    fp_file = BASE / "r52" / "collectors" / "farpost.py"
    if not fp_file.exists():
        results["farpost"] = {"ok": False, "error": "farpost.py missing", "time": 0}
        return

    # S1: Collector
    try:
        from r52.collectors.farpost import FarpostCollector
        c = FarpostCollector(data_dir=BASE, use_proxies=False)
        obs = c.collect_product("123456", "https://www.farpost.ru/ads/", "FP test")
        if obs:
            o = obs["observation"]
            results["farpost"] = _pack(o, "collector", time.monotonic() - t0)
            return
    except Exception as e:
        log.warning("FARPOST S1: %s", e)

    # S2: Playwright on a known ad (try search first)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True)
            pg = br.new_page(user_agent=UA, locale="ru-RU",
                             viewport={"width": 1920, "height": 1080})
            pg.goto("https://www.farpost.ru/ads/", timeout=25000,
                    wait_until="domcontentloaded")
            pg.wait_for_timeout(5000)
            html = pg.content()
            br.close()

        tm = re.search(r'<title[^>]*>([^<]+)</title>', html)
        prices_fp = []
        for pm in re.finditer(r'(\d[\d\s\u00a0]{2,10})\s*₽', html):
            raw = pm.group(1).replace(" ", "").replace("\u00a0", "")
            try:
                v = int(raw)
                if 100 < v < 10_000_000:
                    prices_fp.append({"role": "regular", "amount_minor": v,
                                      "currency": "RUB", "label": "PW",
                                      "amount": f"{v//100}.{v%100:02d}"})
            except ValueError:
                pass

        o = {"fields": {"title": {"status": "KNOWN",
                                  "value": tm.group(1).strip()[:80] if tm else "FP"}},
             "price_candidates": prices_fp[:5],
             "media": {"items": []}, "availability": "UNKNOWN"}
        results["farpost"] = _pack(o, f"playwright {len(html)}B",
                                   time.monotonic() - t0)
        return
    except Exception as e:
        log.warning("FARPOST S2: %s", e)

    results["farpost"] = {"ok": False, "error": "all failed",
                          "time": round(time.monotonic() - t0, 1)}


# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    t_start = time.monotonic()
    for name, func in [("ozon", test_ozon), ("wb", test_wb),
                       ("avito", test_avito), ("farpost", test_farpost)]:
        try:
            func()
        except Exception as e:
            results[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            log.exception("TEST %s crashed", name)

    total = round(time.monotonic() - t_start, 1)
    results["summary"] = {n: results.get(n, {}).get("ok", False)
                          for n in ["ozon", "wb", "avito", "farpost"]}
    results["total_time"] = total

    out = BASE / "diag_final_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    print("  SUMMARY")
    print("=" * 64)
    for n in ["ozon", "wb", "avito", "farpost"]:
        r = results.get(n, {})
        s = "OK  " if r.get("ok") else "FAIL"
        t = r.get("title", r.get("error", "?"))[:50]
        ps = ", ".join(f"{p['role']}={p.get('amount','?')}"
                       for p in r.get("prices", [])[:3]) or "no prices"
        print(f"  {n:8s} [{s}] {t}")
        print(f"           {ps}  |  {r.get('method','?')}  |  {r.get('time','?')}s")
    print(f"\n  Total: {total}s")
    print(f"  JSON:  {out}")
    print("=" * 64)

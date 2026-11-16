"""Round 2: FARPOST price + AVITO HTML dump + WB endpoints."""
import sys, time, re, json, logging
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.r2")

HOME = ROOT / "data" / "test_home"
results = {}

# === 1. FARPOST: confirm price fix ===
log.info("=== FARPOST price test ===")
t0 = time.monotonic()
try:
    from r52.collectors.runner import _get_collector_class
    fp = _get_collector_class("FARPOST")(data_dir=HOME, use_proxies=False)
    rows = fp.collect_by_search("тест", page=1, page_size=5)
    prices = []
    for r in rows:
        obs = r.get("observation", {})
        pc = obs.get("price_candidates", [])
        if pc and pc[0].get("amount_minor", 0) > 0:
            prices.append({"id": obs.get("listing_id"), "price": pc[0].get("amount"),
                           "minor": pc[0].get("amount_minor"), "cur": pc[0].get("currency")})
    results["FARPOST"] = {
        "status": "PASS" if prices else "FAIL",
        "rows": len(rows), "prices": len(prices),
        "details": prices[:5],
        "elapsed": round(time.monotonic() - t0, 1),
    }
    log.info("FARPOST: %d rows, %d prices", len(rows), len(prices))
    for p in prices[:3]:
        log.info("  %s: %s %s", p["id"], p["price"], p["cur"])
    fp.close()
except Exception as e:
    results["FARPOST"] = {"status": "FAIL", "error": str(e)[:300], "elapsed": round(time.monotonic() - t0, 1)}
    log.error("FARPOST: %s", e)

# === 2. AVITO: dump desktop HTML for price structure ===
log.info("=== AVITO HTML dump ===")
t0 = time.monotonic()
try:
    av = _get_collector_class("AVITO")(data_dir=HOME, use_proxies=False)
    rows = av.collect_by_search("тест", page=1, page_size=3)

    # Re-fetch desktop HTML to inspect price structure
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    m_url = "https://www.avito.ru/search?q=" + quote("тест")
    if av._cffi:
        m_resp = av._cffi.get(m_url, impersonate="chrome", timeout=(10, 20), headers=headers)
    else:
        m_resp = av.session.get(m_url, headers=headers, timeout=(10, 20))

    html = m_resp.text if hasattr(m_resp, 'text') else m_resp.content.decode('utf-8', 'replace')
    log.info("AVITO desktop: HTTP %d, %d bytes", m_resp.status_code, len(html))

    dump_path = ROOT / "debug" / "avito_desktop_raw.html"
    dump_path.write_text(html, encoding="utf-8")
    log.info("Saved: %s", dump_path)

    # Find price patterns
    patterns = {
        "data-price": re.findall(r'data-price=["\x27]?(\d+)', html)[:10],
        "class_price": re.findall(r'class="[^"]*price[^"]*"[^>]*>([^<]{2,30})<', html, re.I)[:10],
        "text_rub": re.findall(r'(\d[\d\s\xa0]{2,15})\s*(?:\u20bd|\u0440\u0443\u0431)', html)[:10],
        "jsonld_price": re.findall(r'"price"\s*:\s*"?(\d+\.?\d*)"?', html)[:10],
        "itemprop": re.findall(r'itemprop="price"[^>]*content=["\x27]([^"\x27]+)["\x27]', html)[:10],
    }
    for name, vals in patterns.items():
        log.info("  %-18s: %s", name, vals[:5] if vals else "NONE")

    # Dump first 3 ad card blocks
    card_re = re.compile(
        r"""<a[^>]*href=["'][^"']*?(\d{6,})[^"']*["'][^>]*>""",
        re.I
    )
    matches = list(card_re.finditer(html))
    log.info("  ad links: %d", len(matches))
    if matches:
        cards_dump = []
        for ci, cm in enumerate(matches[:3]):
            start = max(0, cm.start() - 500)
            end = min(len(html), cm.end() + 3000)
            block = html[start:end]
            cards_dump.append("--- CARD %d (id=%s) ---\n%s" % (ci + 1, cm.group(1), block[:3000]))
        cards_path = ROOT / "debug" / "avito_desktop_cards.txt"
        cards_path.write_text("\n\n".join(cards_dump), encoding="utf-8")
        log.info("  Cards dump: %s", cards_path)

    prices_found = sum(1 for r in rows if r.get("observation", {}).get("price_candidates"))
    results["AVITO"] = {
        "status": "PASS" if rows else "FAIL",
        "rows": len(rows), "prices_in_obs": prices_found,
        "html_bytes": len(html),
        "elapsed": round(time.monotonic() - t0, 1),
    }
    av.close()
except Exception as e:
    results["AVITO"] = {"status": "FAIL", "error": str(e)[:300], "elapsed": round(time.monotonic() - t0, 1)}
    log.error("AVITO: %s", e)

# === 3. WB: try v5 + sec-ch-ua headers ===
log.info("=== WB advanced test ===")
t0 = time.monotonic()
try:
    wb = _get_collector_class("WB")(data_dir=HOME, use_proxies=False)

    wb_headers = {
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    }

    test_urls = [
        "https://search.wb.ru/exactmatch/ru/common/v5/search?query=%D1%82%D0%B5%D1%81%D1%82&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1",
        "https://catalog.wb.ru/exactmatch/ru/common/v5/search?query=%D1%82%D0%B5%D1%81%D1%82&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1",
        "https://card.wb.ru/cards/v2/query?%D1%82%D0%B5%D1%81%D1%82&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1",
    ]

    wb_results = []
    for u in test_urls:
        host = u.split('/')[2]
        if wb._cffi:
            r = wb._cffi.get(u, impersonate="chrome", timeout=(8, 15), headers=wb_headers)
        else:
            r = wb.session.get(u, headers=wb_headers, timeout=(8, 15))
        status = getattr(r, 'status_code', 0)
        body_len = len(r.content) if hasattr(r, 'content') else 0
        log.info("  WB %s: HTTP %d (%d bytes)", host, status, body_len)
        wb_results.append({"host": host, "status": status, "bytes": body_len})
        if status == 200 and body_len > 100:
            try:
                data = r.json()
                prods = data.get("products", data.get("data", {}).get("products", []))
                log.info("  WB %s: %d products!", host, len(prods))
                wb_results[-1]["products"] = len(prods)
                break
            except Exception:
                pass
        time.sleep(2)

    # Cookie flow
    log.info("  Trying cookie flow...")
    if wb._cffi:
        home_r = wb._cffi.get("https://www.wildberries.ru/", impersonate="chrome", timeout=(8, 15))
        log.info("  Homepage: HTTP %d", home_r.status_code)
        cookies = home_r.cookies
        log.info("  Cookies: %s", dict(cookies) if cookies else "none")
        s_url = "https://search.wb.ru/exactmatch/ru/common/v5/search?query=%D1%82%D0%B5%D1%81%D1%82&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1"
        r2 = wb._cffi.get(s_url, impersonate="chrome", timeout=(8, 15), headers=wb_headers)
        log.info("  Search with cookies: HTTP %d", r2.status_code)
        wb_results.append({"host": "search+cookies", "status": r2.status_code, "bytes": len(r2.content)})

    results["WB"] = {
        "status": "PASS" if any(x.get("status") == 200 for x in wb_results) else "FAIL",
        "endpoints": wb_results,
        "elapsed": round(time.monotonic() - t0, 1),
    }
    wb.close()
except Exception as e:
    results["WB"] = {"status": "FAIL", "error": str(e)[:300], "elapsed": round(time.monotonic() - t0, 1)}
    log.error("WB: %s", e)

# === Summary ===
log.info("=== SUMMARY ===")
for src, res in results.items():
    st = res.get("status")
    if st == "PASS":
        extra = " rows=%s prices=%s" % (res.get('rows', '?'), res.get('prices', res.get('prices_in_obs', '?')))
    else:
        extra = " err=%s" % str(res.get('error', '?'))[:60]
    log.info("  %-12s %s%s (%.1fs)", src, st, extra, res.get("elapsed", 0))

report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}
out = ROOT / "debug" / "diag_round2_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Report: %s", out)

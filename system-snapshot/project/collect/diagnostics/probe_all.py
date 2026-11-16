r"""probe_all.py — Быстрая проверка 4 площадок, несколько стратегий на каждую.
Run: C:\Python315\python.exe probe_all.py
"""
import sys, os, json, time, re, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-12s %(levelname)-5s %(message)s")
log = logging.getLogger("PROBE")

results = {}

# ============================================================
# OZON
# ============================================================
def probe_ozon():
    log.info("=== OZON ===")
    oid = "3481320800"
    url = f"https://www.ozon.ru/product/{oid}/"

    # S1: Composer API
    try:
        from curl_cffi import requests as cffi
        r = cffi.get(
            f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/",
            impersonate="chrome", timeout=10,
            headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/"}
        )
        log.info("OZON Composer: HTTP %d len=%d", r.status_code, len(r.text))
        if r.status_code == 200:
            data = r.json()
            s = json.dumps(data)
            has_price = "284" in s or '"price"' in s.lower()
            log.info("OZON Composer: has_price=%s keys=%s", has_price, list(data.keys())[:8])
            results["ozon_composer"] = {"status": 200, "has_price": has_price, "keys": list(data.keys())[:8]}
        else:
            results["ozon_composer"] = {"status": r.status_code}
    except Exception as e:
        log.warning("OZON Composer: %s", e)
        results["ozon_composer"] = {"error": str(e)}

    # S2: SSR HTML -> JSON-LD + data-widget
    try:
        r2 = cffi.get(url, impersonate="chrome", timeout=15)
        log.info("OZON SSR: HTTP %d len=%d", r2.status_code, len(r2.text))
        if r2.status_code == 200:
            html = r2.text
            ld_blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL)
            log.info("OZON SSR: %d JSON-LD blocks", len(ld_blocks))
            for i, block in enumerate(ld_blocks):
                try:
                    ld = json.loads(block)
                    if isinstance(ld, dict) and ld.get("@type") == "Product":
                        offers = ld.get("offers", {})
                        log.info("OZON LD[%d]: price=%s cur=%s name=%s",
                                 i, offers.get("price"), offers.get("priceCurrency"),
                                 ld.get("name", "")[:50])
                        results["ozon_jsonld"] = {"price": offers.get("price"),
                                                  "cur": offers.get("priceCurrency"),
                                                  "name": ld.get("name", "")[:80]}
                except Exception:
                    pass
            dw = re.findall(r'data-widget=["\']([^"\']+)["\']', html)
            log.info("OZON SSR: %d data-widget", len(dw))
            results["ozon_ssr"] = {"status": 200, "len": len(html),
                                   "jsonld": len(ld_blocks), "widgets": len(dw)}
        else:
            results["ozon_ssr"] = {"status": r2.status_code}
    except Exception as e:
        log.warning("OZON SSR: %s", e)
        results["ozon_ssr"] = {"error": str(e)}

# ============================================================
# WB
# ============================================================
def probe_wb():
    log.info("=== WB ===")
    nm_id = 1097975275
    vol = nm_id // 100000
    part = nm_id // 1000
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"

    # S1: card.json (known: basket-42)
    try:
        from curl_cffi import requests as cffi
        r = cffi.get(f"https://basket-42.wbbasket.ru{path}", impersonate="chrome",
                     timeout=10, headers={"Accept": "application/json",
                                          "Referer": "https://www.wildberries.ru/"})
        log.info("WB card.json: HTTP %d len=%d", r.status_code, len(r.text))
        if r.status_code == 200:
            d = r.json()
            log.info("WB card: nm_id=%s name=%s", d.get("nm_id"), d.get("imt_name", "")[:40])
            results["wb_card"] = {"status": 200, "nm_id": d.get("nm_id")}
        else:
            results["wb_card"] = {"status": r.status_code}
    except Exception as e:
        log.warning("WB card.json: %s", e)
        results["wb_card"] = {"error": str(e)}

    # S2: HTML detail.aspx -> price
    try:
        r2 = cffi.get(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                      impersonate="chrome", timeout=15)
        log.info("WB HTML: HTTP %d len=%d", r2.status_code, len(r2.text))
        if r2.status_code == 200:
            html = r2.text
            found = []
            for pat, label in [
                (r'"price"\s*:\s*(\d+)', "json.price"),
                (r'"salePriceU"\s*:\s*(\d+)', "salePriceU"),
                (r'"priceU"\s*:\s*(\d+)', "priceU"),
                (r'"oldPriceU"\s*:\s*(\d+)', "oldPriceU"),
                (r'data-price=["\'](\d+)["\']', "data-price"),
            ]:
                m = re.search(pat, html)
                if m:
                    found.append((label, m.group(1)))
            ld_blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL)
            for block in ld_blocks:
                try:
                    ld = json.loads(block)
                    if isinstance(ld, dict) and ld.get("@type") == "Product":
                        off = ld.get("offers", {})
                        found.append(("jsonld", str(off.get("price"))))
                except Exception:
                    pass
            log.info("WB HTML prices: %s", found)
            results["wb_html"] = {"status": 200, "len": len(html),
                                  "prices": found, "jsonld": len(ld_blocks)}
        else:
            results["wb_html"] = {"status": r2.status_code}
    except Exception as e:
        log.warning("WB HTML: %s", e)
        results["wb_html"] = {"error": str(e)}

    # S3: Price API v2
    try:
        r3 = cffi.get(
            f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
            impersonate="chrome", timeout=10,
            headers={"Accept": "application/json",
                     "Referer": "https://www.wildberries.ru/",
                     "Origin": "https://www.wildberries.ru"})
        log.info("WB price v2: HTTP %d len=%d", r3.status_code, len(r3.text))
        if r3.status_code == 200:
            d = r3.json()
            prods = d.get("data", {}).get("products", [])
            if prods:
                p = prods[0]
                log.info("WB v2: priceU=%s sale=%s old=%s",
                         p.get("priceU"), p.get("salePriceU"), p.get("oldPriceU"))
                results["wb_price_v2"] = {"status": 200, "priceU": p.get("priceU"),
                                          "sale": p.get("salePriceU")}
            else:
                results["wb_price_v2"] = {"status": 200, "empty": True}
        else:
            results["wb_price_v2"] = {"status": r3.status_code}
    except Exception as e:
        log.warning("WB price v2: %s", e)
        results["wb_price_v2"] = {"error": str(e)}

# ============================================================
# FARPOST
# ============================================================
def probe_farpost():
    log.info("=== FARPOST ===")
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        s.get("https://www.farpost.ru/", timeout=10)
        time.sleep(1)
        for lid in ["138859452", "138952176"]:
            url = f"https://www.farpost.ru/vladivostok/{lid}.html"
            r = s.get(url, timeout=15)
            log.info("FP %s: HTTP %d len=%d url=%s", lid, r.status_code, len(r.text), r.url[:60])
            if r.status_code == 200 and len(r.text) > 500:
                html = r.text
                m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
                pa = m.group(1) if m else None
                ld_blocks = re.findall(
                    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    html, re.DOTALL)
                lp = None
                for b in ld_blocks:
                    try:
                        ld = json.loads(b)
                        if isinstance(ld, dict) and ld.get("@type") == "Product":
                            lp = ld.get("offers", {}).get("price")
                    except Exception:
                        pass
                log.info("FP %s: attr=%s jsonld=%s", lid, pa, lp)
                results[f"fp_{lid}"] = {"status": 200, "attr": pa, "jsonld": lp}
            elif "/verify" in r.url:
                results[f"fp_{lid}"] = {"status": "verify"}
            else:
                results[f"fp_{lid}"] = {"status": r.status_code}
            time.sleep(2)
    except Exception as e:
        log.warning("FARPOST: %s", e)
        results["farpost_err"] = {"error": str(e)}

# ============================================================
# AVITO
# ============================================================
def probe_avito():
    log.info("=== AVITO ===")
    aid = "1045231001"
    try:
        from curl_cffi import requests as cffi
        for host in ["www.avito.ru", "m.avito.ru"]:
            url = f"https://{host}/ad/{aid}"
            r = cffi.get(url, impersonate="chrome", timeout=12)
            log.info("AV %s: HTTP %d len=%d", host, r.status_code, len(r.text))
            if r.status_code == 200:
                html = r.text
                cap = "captcha" in html.lower() or "hcaptcha" in html.lower()
                ld_blocks = re.findall(
                    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    html, re.DOTALL)
                lp = None
                for b in ld_blocks:
                    try:
                        ld = json.loads(b)
                        if isinstance(ld, dict) and ld.get("@type") in ("Product", "Offer"):
                            lp = ld.get("offers", {}).get("price") or ld.get("price")
                    except Exception:
                        pass
                log.info("AV %s: cap=%s ld=%d price=%s", host, cap, len(ld_blocks), lp)
                results[f"av_{host}"] = {"status": 200, "cap": cap,
                                         "ld": len(ld_blocks), "price": lp}
            else:
                results[f"av_{host}"] = {"status": r.status_code}
            time.sleep(2)
    except Exception as e:
        log.warning("AVITO: %s", e)
        results["avito_err"] = {"error": str(e)}

# ============================================================
if __name__ == "__main__":
    t0 = time.time()
    probe_ozon()
    probe_wb()
    probe_farpost()
    probe_avito()
    dt = time.time() - t0
    log.info("=== DONE %.1fs ===", dt)
    out = ROOT / "probe_all_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    log.info("Saved: %s", out)

"""Диагностика v50: WB (новые API + curl_cffi) и Avito (sitemap + curl_cffi).
Запуск: C:/Python315/python.exe diag_v50_wb_avito.py
"""
import json
import re
import sys
import time
from datetime import datetime

OUT = []

def log(msg):
    OUT.append(msg)
    print(msg)

def main():
    log("=" * 60)
    log(f"DIAG v50: WB + AVITO  |  {datetime.now().isoformat()}")
    log("=" * 60)

    # ============================================================
    # WILDBERRIES
    # ============================================================
    log("\n" + "=" * 60)
    log("WILDBERRIES")
    log("=" * 60)

    wb_ids = ["199298871", "110835207", "188497640", "12736071"]
    wb_api_ok = False
    wb_cffi_ok = False

    # --- Strategy 1: catalog.wb.ru API (v1) ---
    log("\n--- WB Strategy 1: catalog.wb.ru/cards/v1/detail ---")
    for nm in wb_ids:
        url = f"https://catalog.wb.ru/cards/v1/detail?nm={nm}"
        try:
            import requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.wildberries.ru/",
            }
            resp = requests.get(url, headers=headers, timeout=15)
            log(f"  nm={nm}: HTTP {resp.status_code}, len={len(resp.content)}")
            if resp.status_code == 200 and len(resp.content) > 100:
                data = resp.json()
                log(f"    JSON keys: {list(data.keys())[:10]}")
                products = data.get("data", {}).get("products", [])
                if products:
                    p = products[0]
                    log(f"    Product: name={p.get('name','?')[:60]}")
                    sizes = p.get("sizes", [])
                    if sizes:
                        log(f"    Price: {sizes[0].get('price',{}).get('basic','?')}")
                    log(f"    Brand: {p.get('brand','?')}")
                    log(f"    Imt (images): {len(p.get('imts', []))} items")
                    wb_api_ok = True
                    with open(f"diag_v50_wb_api_{nm}.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    log(f"    Saved: diag_v50_wb_api_{nm}.json")
                    break
            elif resp.status_code == 200:
                log(f"    Body[:200]: {resp.text[:200]}")
        except Exception as e:
            log(f"  nm={nm}: ERROR {e}")
        time.sleep(2)

    # --- Strategy 2: curl_cffi with Chrome impersonation ---
    log("\n--- WB Strategy 2: curl_cffi Chrome impersonation ---")
    for nm in wb_ids[:2]:
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        try:
            from curl_cffi import requests as cffi_req
            resp = cffi_req.get(url, impersonate="chrome", timeout=20)
            log(f"  nm={nm}: HTTP {resp.status_code}, len={len(resp.content)}")
            html = resp.text
            has_wbaas = "wbaas" in html.lower() or "browser-check" in html.lower()
            has_jsonld = "application/ld+json" in html
            has_state = "__STATE__" in html or "data-state" in html
            log(f"    WBAAS={has_wbaas}, JSON-LD={has_jsonld}, State={has_state}")
            if not has_wbaas and len(html) > 5000:
                jsonld_blocks = re.findall(
                    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    html, re.DOTALL)
                log(f"    JSON-LD blocks: {len(jsonld_blocks)}")
                for i, block in enumerate(jsonld_blocks[:3]):
                    try:
                        d = json.loads(block.strip())
                        if isinstance(d, dict) and d.get("@type") == "Product":
                            log(f"    LD[{i}]: name={d.get('name','?')[:60]}, price={d.get('offers',{}).get('price','?')}")
                            wb_cffi_ok = True
                    except Exception:
                        pass
                with open(f"diag_v50_wb_cffi_{nm}.html", "w", encoding="utf-8") as f:
                    f.write(html)
                log(f"    Saved: diag_v50_wb_cffi_{nm}.html ({len(html)} B)")
                if wb_cffi_ok:
                    break
            elif has_wbaas:
                log(f"    WBAAS challenge detected, len={len(html)}")
        except Exception as e:
            log(f"  curl_cffi ERROR: {e}")
        time.sleep(3)

    # --- Strategy 3: WB web/api ---
    log("\n--- WB Strategy 3: web/api catalog ---")
    try:
        import requests
        for nm in wb_ids[:2]:
            url = f"https://www.wildberries.ru/web/api/catalog/product/{nm}"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            log(f"  web/api nm={nm}: HTTP {resp.status_code}, len={len(resp.content)}")
            if resp.status_code == 200 and len(resp.content) > 50:
                log(f"    Body[:200]: {resp.text[:200]}")
                wb_api_ok = True
            time.sleep(2)
    except Exception as e:
        log(f"  web/api ERROR: {e}")

    # --- Strategy 4: Alternate API endpoints ---
    log("\n--- WB Strategy 4: alternate API endpoints ---")
    wb_alt_endpoints = [
        f"https://catalog.wb.ru/cards/detail?nm={wb_ids[0]}",
        f"https://www.wildberries.ru/web/api/catalog/product/{wb_ids[0]}/info",
        f"https://card.wb.ru/cards/detail?nm={wb_ids[0]}",
    ]
    for url in wb_alt_endpoints:
        try:
            import requests
            resp = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0",
                "Accept": "application/json",
            }, timeout=10)
            log(f"  {url[:70]}: HTTP {resp.status_code}, len={len(resp.content)}")
            if resp.status_code == 200 and len(resp.content) > 50:
                log(f"    Body[:150]: {resp.text[:150]}")
                wb_api_ok = True
        except Exception as e:
            log(f"  {url[:50]}: ERR {e}")
        time.sleep(1)

    log(f"\nWB RESULT: api_ok={wb_api_ok}, cffi_ok={wb_cffi_ok}")

    # ============================================================
    # AVITO
    # ============================================================
    log("\n" + "=" * 60)
    log("AVITO")
    log("=" * 60)

    avito_ids = []
    avito_cffi_ok = False
    avito_api_ok = False

    # --- Strategy 1: Sitemap ---
    log("\n--- AVITO Strategy 1: Sitemap ---")
    try:
        import requests
        sitemap_urls = [
            "https://www.avito.ru/sitemap.xml",
            "https://www.avito.ru/moscow/sitemap.xml",
        ]
        for sm_url in sitemap_urls:
            try:
                resp = requests.get(sm_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                log(f"  Sitemap {sm_url}: HTTP {resp.status_code}, len={len(resp.content)}")
                if resp.status_code == 200:
                    urls = re.findall(r'<loc>(https://www\.avito\.ru/[^<]+)</loc>', resp.text)
                    log(f"    URLs found: {len(urls)}")
                    for u in urls[:10]:
                        log(f"      {u}")
                        m = re.search(r'/(\d{8,})/?$', u)
                        if m:
                            avito_ids.append(m.group(1))
            except Exception as e:
                log(f"  Sitemap ERR: {e}")
    except Exception as e:
        log(f"  Sitemap section ERR: {e}")

    # --- Strategy 2: curl_cffi on ad pages ---
    log("\n--- AVITO Strategy 2: curl_cffi on ad pages ---")
    test_ids = avito_ids[:3] if avito_ids else ["1154321987", "1045236789", "987654321"]
    for aid in test_ids:
        for url_pattern in [
            f"https://www.avito.ru/ad/{aid}",
            f"https://www.avito.ru/all/{aid}",
        ]:
            try:
                from curl_cffi import requests as cffi_req
                resp = cffi_req.get(url_pattern, impersonate="chrome", timeout=20)
                html = resp.text
                log(f"  id={aid} url={url_pattern[:50]}: HTTP {resp.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:80] if title_m else '?'}")
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}")
                if not has_captcha and len(html) > 5000 and has_jsonld:
                    avito_cffi_ok = True
                    with open(f"diag_v50_avito_cffi_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    log(f"    Saved: diag_v50_avito_cffi_{aid}.html")
                    break
            except Exception as e:
                log(f"  id={aid}: ERR {e}")
            time.sleep(2)
        if avito_cffi_ok:
            break

    # --- Strategy 3: Avito API endpoints ---
    log("\n--- AVITO Strategy 3: API endpoints ---")
    try:
        import requests
        api_urls = [
            f"https://www.avito.ru/api/ads/{test_ids[0]}",
            f"https://api.avito.ru/ads/{test_ids[0]}",
            f"https://www.avito.ru/mobile/api/ads/{test_ids[0]}",
        ]
        for url in api_urls:
            try:
                resp = requests.get(url, headers={
                    "User-Agent": "AvitoMobile/1.0",
                    "Accept": "application/json",
                }, timeout=10)
                log(f"  {url[:60]}: HTTP {resp.status_code}, len={len(resp.content)}")
                if resp.status_code == 200 and len(resp.content) > 50:
                    log(f"    Body[:200]: {resp.text[:200]}")
                    avito_api_ok = True
            except Exception as e:
                log(f"  {url[:50]}: ERR {e}")
            time.sleep(1)
    except Exception as e:
        log(f"  API section ERR: {e}")

    # --- Strategy 4: Category page via curl_cffi ---
    log("\n--- AVITO Strategy 4: Category page via curl_cffi ---")
    try:
        from curl_cffi import requests as cffi_req
        cat_urls = [
            "https://www.avito.ru/moscow/noutbuki",
            "https://www.avito.ru/spb/telefony-i-smm",
            "https://www.avito.ru/moscow/avto-zapchasti",
        ]
        for cat_url in cat_urls:
            try:
                resp = cffi_req.get(cat_url, impersonate="chrome", timeout=20)
                html = resp.text
                log(f"  {cat_url[:50]}: HTTP {resp.status_code}, len={len(html)}")
                ad_ids = re.findall(r'href="https://www\.avito\.ru/[^"]*?/(\d{8,})/?"', html)
                ad_ids2 = re.findall(r'data-ad-id=["\'](\d+)["\']', html)
                ad_ids3 = re.findall(r'/ad/(\d{8,})', html)
                all_ids = list(set(ad_ids + ad_ids2 + ad_ids3))
                log(f"    Ad IDs found: {len(all_ids)}")
                for aid in all_ids[:5]:
                    log(f"      {aid}")
                if all_ids:
                    avito_ids.extend(all_ids[:5])
                    fname = f"diag_v50_avito_cat_{cat_url.split('/')[-1]}.html"
                    with open(fname, "w", encoding="utf-8") as f:
                        f.write(html)
                    log(f"    Saved: {fname}")
                    break
            except Exception as e:
                log(f"  {cat_url[:40]}: ERR {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  Cat section ERR: {e}")

    log(f"\nAVITO RESULT: cffi_ok={avito_cffi_ok}, api_ok={avito_api_ok}, ids_found={len(avito_ids)}")

    # ============================================================
    # SUMMARY
    # ============================================================
    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)
    log(f"  WB:   api={wb_api_ok}, cffi={wb_cffi_ok}")
    log(f"  AVITO: cffi={avito_cffi_ok}, api={avito_api_ok}, ids={avito_ids[:5]}")
    log(f"  DONE: {datetime.now().isoformat()}")

    # Запись в файл: только если НЕ через shell-редирект (иначе PermissionError на Win)
    import os
    if not os.environ.get("AHUB_REDIRECT"):
        with open("diag_v50_out.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        log(f"\nFull log saved: diag_v50_out.txt ({len(OUT)} lines)")
    else:
        log(f"\nFull log: {len(OUT)} lines (via shell redirect)")

if __name__ == "__main__":
    main()

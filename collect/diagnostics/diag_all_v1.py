"""Диагностика: WB (curl CLI) + Ozon + Avito.

WB: пробуем curl (libcurl нативный, другой TLS fingerprint) + content API.
Ozon: API api.ozon.ru + HTML www.ozon.ru.
Avito: HTML www.avito.ru + API.
"""
import json, sys, time, re, subprocess, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
OUT = Path(__file__).parent

WB_IDS = ["199298871", "314337853"]
OZON_IDS = ["3349505344", "2718589061"]
AVITO_IDS = ["7345810001", "7201234567"]

results = {"WB": [], "OZON": [], "AVITO": []}

# ============================================================
# SECTION 1: WB via curl CLI
# ============================================================
print("=" * 70)
print("SECTION 1: WB via curl CLI (native libcurl, different TLS)")
print("=" * 70)

def curl_get(url, label, extra_args=None, timeout=15):
    cmd = ["curl", "-s", "-o", "WOUT", "-w", "%{http_code}|%{size_download}|%{time_total}",
           "--max-time", str(timeout),
           "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
           "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "-H", "Accept-Language: ru-RU,ru;q=0.9",
           url]
    if extra_args:
        cmd.extend(extra_args)
    print(f"\n  [{label}] {url}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, cwd=str(OUT))
        meta = r.stdout.strip()
        parts = meta.split("|")
        code, size, elapsed = parts[0], parts[1] if len(parts) > 1 else "?", parts[2] if len(parts) > 2 else "?"
        print(f"    {code} | {size}B | {elapsed}s")
        if code == "200":
            body = (OUT / "WOUT").read_text(encoding="utf-8", errors="replace") if size > 0 else ""
            return int(code), body
        elif code == "403" or code == "498":
            body = (OUT / "WOUT").read_text(encoding="utf-8", errors="replace") if size > 0 else ""
            print(f"    Body[:200]: {body[:200]}")
            return int(code), body
        else:
            return int(code), ""
    except Exception as e:
        print(f"    ERROR: {e}")
        return -1, ""

# 1a. WB main page via curl
code, body = curl_get("https://www.wildberries.ru/", "WB main curl")
results["WB"].append({"target": "main", "method": "curl", "status": code})

# 1b. WB card API via curl
for wid in WB_IDS:
    url = f"https://card.wb.ru/cards/v2/detail?nm={wid}"
    code, body = curl_get(url, f"WB card curl {wid}")
    if code == 200 and body:
        try:
            data = json.loads(body)
            prods = data.get("data", {}).get("products", [])
            print(f"    Products: {len(prods)}")
            if prods:
                p = prods[0]
                print(f"    name={p.get('name','?')[:50]} brand={p.get('brand','?')}")
                results["WB"].append({"id": wid, "method": "card_curl", "status": "OK", "name": p.get("name","")[:50]})
                continue
        except:
            pass
    results["WB"].append({"id": wid, "method": "card_curl", "status": code})
    time.sleep(1)

# 1c. WB content API (newer pattern)
for wid in WB_IDS:
    for domain in ("wbx-content-v2.wbstatic.net", "wbx-content-v1.wbstatic.net", "wbx-content.wbstatic.net"):
        url = f"https://{domain}/ru/{wid}/"
        code, body = curl_get(url, f"WB content {domain} {wid}", timeout=8)
        if code == 200:
            print(f"    FOUND via {domain}!")
            results["WB"].append({"id": wid, "method": f"content_{domain}", "status": "OK"})
            break
        elif code == -1:
            print(f"    DNS fail for {domain}")
            results["WB"].append({"id": wid, "method": f"content_{domain}", "status": "DNS_FAIL"})
        else:
            results["WB"].append({"id": wid, "method": f"content_{domain}", "status": code})
        time.sleep(0.5)

# 1d. WB with mobile UA
for wid in WB_IDS:
    url = f"https://www.wildberries.ru/catalog/{wid}/detail.aspx"
    code, body = curl_get(url, f"WB mobile-UA {wid}",
                          extra_args=["-H", "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"])
    if code == 200:
        results["WB"].append({"id": wid, "method": "mobile_ua", "status": "OK"})
    else:
        results["WB"].append({"id": wid, "method": "mobile_ua", "status": code})
    time.sleep(1)

# 1e. WB supersale / other subdomains
for sub in ("supersale.wildberries.ru", "promo.wildberries.ru", "api.wildberries.ru"):
    url = f"https://{sub}/"
    code, body = curl_get(url, f"WB {sub}", timeout=8)
    results["WB"].append({"target": sub, "method": "curl", "status": code})
    time.sleep(0.5)

# ============================================================
# SECTION 2: OZON
# ============================================================
print("\n" + "=" * 70)
print("SECTION 2: OZON")
print("=" * 70)

from curl_cffi import requests as cr

ozon_session = cr.Session(impersonate="chrome")

# 2a. Ozon main page (warm-up)
print("\n  [Ozon main]")
try:
    r = ozon_session.get("https://www.ozon.ru/", timeout=15, impersonate="chrome")
    print(f"    {r.status_code} | {len(r.content)}B")
    results["OZON"].append({"target": "main", "status": r.status_code})
    if r.status_code == 200:
        cookies = dict(ozon_session.cookies)
        print(f"    Cookies: {list(cookies.keys())[:10]}")
except Exception as e:
    print(f"    ERROR: {e}")
    results["OZON"].append({"target": "main", "status": str(e)[:50]})

time.sleep(2)

# 2b. Ozon product page
for oid in OZON_IDS:
    url = f"https://www.ozon.ru/product-{oid}/"
    print(f"\n  [Ozon product {oid}]")
    try:
        r = ozon_session.get(url, timeout=15, impersonate="chrome")
        print(f"    {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            text = r.text
            # JSON-LD
            ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.DOTALL)
            if ld:
                print(f"    JSON-LD: {len(ld)} blocks")
                for i, block in enumerate(ld[:3]):
                    try:
                        obj = json.loads(block.strip())
                        if isinstance(obj, dict):
                            print(f"      [{i}] @type={obj.get('@type','?')} name={str(obj.get('name',''))[:60]}")
                            offers = obj.get("offers")
                            if offers:
                                print(f"      offers: {json.dumps(offers, ensure_ascii=False)[:200]}")
                    except:
                        pass
            # OG
            og = re.findall(r'<meta[^>]*property=["\']og:(?:title|price:amount|price:currency|image)["\'][^>]*content=["\']([^"\']*)["\']', text)
            if og:
                print(f"    OG: {og[:5]}")
            # window.__STATE__ or initialState
            for var in ("__STATE__", "initialState", "__NEXT_DATA__", "window.__PRERENDERED_STATE__"):
                m = re.search(rf'{var}\s*=\s*(\{{.*?\}})\s*[;)]', text, re.DOTALL)
                if m:
                    print(f"    {var}: {len(m.group(1))}B")
            results["OZON"].append({"id": oid, "method": "html", "status": "OK", "len": len(text)})
        else:
            results["OZON"].append({"id": oid, "method": "html", "status": r.status_code})
    except Exception as e:
        print(f"    ERROR: {e}")
        results["OZON"].append({"id": oid, "method": "html", "status": str(e)[:50]})
    time.sleep(2)

# 2c. Ozon API (api.ozon.ru)
for oid in OZON_IDS:
    url = f"https://api.ozon.ru/composer-api.bx/page/json/v2"
    print(f"\n  [Ozon API {oid}]")
    try:
        r = ozon_session.get(url, timeout=15, impersonate="chrome",
                             params={"url": f"/product-{oid}/"})
        print(f"    {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            data = r.json()
            keys = list(data.keys())[:10]
            print(f"    Keys: {keys}")
            results["OZON"].append({"id": oid, "method": "composer_api", "status": "OK", "keys": keys})
        else:
            results["OZON"].append({"id": oid, "method": "composer_api", "status": r.status_code})
    except Exception as e:
        print(f"    ERROR: {e}")
        results["OZON"].append({"id": oid, "method": "composer_api", "status": str(e)[:50]})
    time.sleep(1)

# 2d. Ozon mobile
for oid in OZON_IDS[:1]:
    url = f"https://m.ozon.ru/product-{oid}/"
    print(f"\n  [Ozon mobile {oid}]")
    try:
        r = ozon_session.get(url, timeout=15, impersonate="chrome")
        print(f"    {r.status_code} | {len(r.content)}B")
        results["OZON"].append({"id": oid, "method": "mobile", "status": r.status_code})
    except Exception as e:
        print(f"    ERROR: {e}")
        results["OZON"].append({"id": oid, "method": "mobile", "status": str(e)[:50]})
    time.sleep(1)

# ============================================================
# SECTION 3: AVITO
# ============================================================
print("\n" + "=" * 70)
print("SECTION 3: AVITO")
print("=" * 70)

avito_session = cr.Session(impersonate="chrome")

# 3a. Avito main
print("\n  [Avito main]")
try:
    r = avito_session.get("https://www.avito.ru/", timeout=15, impersonate="chrome")
    print(f"    {r.status_code} | {len(r.content)}B")
    results["AVITO"].append({"target": "main", "status": r.status_code})
    if r.status_code == 200:
        cookies = dict(avito_session.cookies)
        print(f"    Cookies: {list(cookies.keys())[:10]}")
except Exception as e:
    print(f"    ERROR: {e}")
    results["AVITO"].append({"target": "main", "status": str(e)[:50]})

time.sleep(2)

# 3b. Avito product page
for aid in AVITO_IDS:
    url = f"https://www.avito.ru/vladivostok/{aid}"
    print(f"\n  [Avito {aid}]")
    try:
        r = avito_session.get(url, timeout=15, impersonate="chrome")
        print(f"    {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            text = r.text
            ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.DOTALL)
            if ld:
                print(f"    JSON-LD: {len(ld)} blocks")
                for i, block in enumerate(ld[:3]):
                    try:
                        obj = json.loads(block.strip())
                        if isinstance(obj, dict):
                            print(f"      [{i}] @type={obj.get('@type','?')} name={str(obj.get('name',''))[:60]}")
                    except:
                        pass
            og = re.findall(r'<meta[^>]*property=["\']og:(?:title|price:amount|price:currency|image)["\'][^>]*content=["\']([^"\']*)["\']', text)
            if og:
                print(f"    OG: {og[:5]}")
            results["AVITO"].append({"id": aid, "method": "html", "status": "OK", "len": len(text)})
        else:
            body_preview = r.text[:200] if r.text else ""
            print(f"    Body: {body_preview}")
            results["AVITO"].append({"id": aid, "method": "html", "status": r.status_code})
    except Exception as e:
        print(f"    ERROR: {e}")
        results["AVITO"].append({"id": aid, "method": "html", "status": str(e)[:50]})
    time.sleep(2)

# 3c. Avito API
for aid in AVITO_IDS[:1]:
    url = f"https://www.avito.ru/api/v1/items/{aid}"
    print(f"\n  [Avito API {aid}]")
    try:
        r = avito_session.get(url, timeout=15, impersonate="chrome",
                              headers={"Accept": "application/json"})
        print(f"    {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            data = r.json()
            print(f"    Keys: {list(data.keys())[:10]}")
            results["AVITO"].append({"id": aid, "method": "api", "status": "OK"})
        else:
            results["AVITO"].append({"id": aid, "method": "api", "status": r.status_code})
    except Exception as e:
        print(f"    ERROR: {e}")
        results["AVITO"].append({"id": aid, "method": "api", "status": str(e)[:50]})
    time.sleep(1)

# 3d. Avito mobile
for aid in AVITO_IDS[:1]:
    url = f"https://m.avito.ru/vladivostok/{aid}"
    print(f"\n  [Avito mobile {aid}]")
    try:
        r = avito_session.get(url, timeout=15, impersonate="chrome")
        print(f"    {r.status_code} | {len(r.content)}B")
        results["AVITO"].append({"id": aid, "method": "mobile", "status": r.status_code})
    except Exception as e:
        print(f"    ERROR: {e}")
        results["AVITO"].append({"id": aid, "method": "mobile", "status": str(e)[:50]})
    time.sleep(1)

# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'='*70}")
print("SUMMARY")
print(f"{'='*70}")
for platform, items in results.items():
    print(f"\n{platform}:")
    for item in items:
        print(f"  {item}")

out = OUT / "diag_all_v1_results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")

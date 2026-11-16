"""Диагностика AliExpress MTOP API: двухфазный запрос (token → sign → data)."""
import sys
import json
import hashlib
import time
import logging
import re
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

PRODUCT_ID = "1005011813323763"
APP_KEY = "12574478"
JSV = "2.5.1"
API = "mtop.aliexpress.pdp.pc.query"
VERSION = "1.0"
BASE = "https://acs.aliexpress.com/h5/"

# Data payload
DATA_JSON = json.dumps({
    "productId": PRODUCT_ID,
    "extParams": "{}",
    "_currency": "EUR",
    "_lang": "it_IT",
    "site": "ita",
}, separators=(",", ":"))

def build_mtop_url(token: str, t: int, data: str) -> str:
    """Строит URL MTOP запроса с sign."""
    sign_str = f"{token}&{t}&{APP_KEY}&{data}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    params = (
        f"jsv={JSV}&appKey={APP_KEY}&t={t}"
        f"&sign={sign}&api={API}&v={VERSION}"
        f"&type=originaljson&dataType=json"
        f"&data={quote(data)}"
    )
    return f"{BASE}{API}/{VERSION}/?{params}"

def main():
    from curl_cffi import requests as cffi_req

    s = cffi_req.Session(impersonate="chrome")
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        "Origin": "https://www.aliexpress.com",
        "Referer": f"https://www.aliexpress.com/item/{PRODUCT_ID}.html",
    })

    # ─── Phase 1: Получить _m_h5_tk cookie ───
    print("=" * 60)
    print("PHASE 1: Get token (first MTOP call, expect TOKEN_EMPTY)")
    print("=" * 60)

    t1 = int(time.time() * 1000)
    url1 = f"{BASE}{API}/{VERSION}/?jsv={JSV}&appKey={APP_KEY}&t={t1}&api={API}&v={VERSION}&type=originaljson&dataType=json&data={quote(DATA_JSON)}"
    print(f"URL: {url1[:150]}...")

    resp1 = s.get(url1, timeout=(10, 20))
    print(f"Status: {resp1.status_code}")
    print(f"Headers (set-cookie): {[h for h in resp1.headers if 'cookie' in h.lower()]}")
    print(f"Body[:500]: {resp1.text[:500]}")

    # Извлекаем token из cookie
    cookies = s.cookies
    print(f"\nCookies after phase 1: {dict(cookies)}")

    token_raw = cookies.get("_m_h5_tk", "")
    token = token_raw.split("_")[0] if token_raw else ""
    print(f"Token: {token[:30]}{'...' if len(token) > 30 else ''}")

    if not token:
        print("NO TOKEN OBTAINED - trying alternative: visit main page first")
        # Visit aliexpress.com to get cookie
        resp0 = s.get("https://www.aliexpress.com/", timeout=(10, 20))
        print(f"Main page: {resp0.status_code}, cookies: {dict(s.cookies)}")
        token_raw = s.cookies.get("_m_h5_tk", "")
        token = token_raw.split("_")[0] if token_raw else ""
        print(f"Token after main page: {token[:30]}")

        if not token:
            print("STILL NO TOKEN - trying acs.aliexpress.com directly")
            resp0 = s.get("https://acs.aliexpress.com/h5/", timeout=(10, 20))
            print(f"ACS root: {resp0.status_code}, cookies: {dict(s.cookies)}")
            token_raw = s.cookies.get("_m_h5_tk", "")
            token = token_raw.split("_")[0] if token_raw else ""

    if not token:
        print("FATAL: Cannot get token. Exiting.")
        sys.exit(1)

    # ─── Phase 2: Запрос с token + sign ───
    print("\n" + "=" * 60)
    print("PHASE 2: MTOP call with token + sign")
    print("=" * 60)

    t2 = int(time.time() * 1000)
    url2 = build_mtop_url(token, t2, DATA_JSON)
    print(f"URL: {url2[:150]}...")
    print(f"Sign input: {token[:20]}...&{t2}&{APP_KEY}&{DATA_JSON[:50]}...")

    time.sleep(1)  # small delay
    resp2 = s.get(url2, timeout=(10, 20))
    print(f"Status: {resp2.status_code}")
    print(f"Body[:800]: {resp2.text[:800]}")

    # Parse response
    try:
        # MTOP sometimes wraps in jsonp
        text = resp2.text
        if "mtopjsonp" in text:
            m = re.search(r'mtopjsonp\d*\((.*)\)', text, re.DOTALL)
            if m:
                text = m.group(1)
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"JSON parse error: {e}")
        print(f"Raw: {resp2.text[:500]}")
        data = None

    if data:
        ret = data.get("ret", [])
        print(f"\nret: {ret}")

        if any("SUCCESS" in r for r in ret):
            print("\n*** SUCCESS! Product data received ***")
            pdata = data.get("data", {})
            # Explore structure
            print(f"data keys: {list(pdata.keys())[:20]}")

            # Title
            title = None
            for k in ("title", "subject", "name"):
                if k in pdata:
                    title = pdata[k]
                    break
            if not title:
                # Deep search
                s = json.dumps(pdata, ensure_ascii=False)
                m = re.search(r'"title"\s*:\s*"([^"]{10,200})"', s)
                if m:
                    title = m.group(1)
            print(f"Title: {title[:100] if title else 'NOT FOUND'}")

            # Price
            price_str = json.dumps(pdata, ensure_ascii=False)
            price_matches = re.findall(r'"(?:minPrice|salePrice|price|amount|formattedPrice)"\s*:\s*"?([\d.]+)"?', price_str)
            print(f"Price candidates: {price_matches[:10]}")

            # Currency
            curr_matches = re.findall(r'"(?:currency|priceCurrency)"\s*:\s*"([^"]+)"', price_str)
            print(f"Currency: {curr_matches[:5]}")

            # Images
            img_matches = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(//[^"]*alicdn[^"]*)"', price_str)
            print(f"Images (first 5): {img_matches[:5]}")

            # Availability
            avail_matches = re.findall(r'"(?:stock|inStock|stockStatus|available)"\s*:\s*("?[^,}\]]+)"?', price_str)
            print(f"Availability hints: {avail_matches[:5]}")

            # Save full response for analysis
            out_file = "diag_ali_mtop_response.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"\nFull response saved to: {out_file} ({len(json.dumps(data))} bytes)")

        elif any("TOKEN" in r for r in ret):
            print("\nToken issue - need to retry with fresh token")
        elif any("VALIDATE" in r or "RGV587" in r for r in ret):
            print("\n*** CAPTCHA / USER_VALIDATE ***")
            # Extract captcha URL
            captcha_url = None
            s = json.dumps(data, ensure_ascii=False)
            m = re.search(r'"url"\s*:\s*"(https://[^"]*punish[^"]*)"', s)
            if m:
                captcha_url = m.group(1)
            print(f"Captcha URL: {captcha_url[:150] if captcha_url else 'N/A'}")
            print("Need Playwright to solve captcha or different approach")
        else:
            print(f"\nUnknown response: {ret}")
    else:
        print("No data parsed")

    # ─── Phase 3: Если captcha, пробуем msite API ───
    if data and any("VALIDATE" in r or "RGV587" in r for r in data.get("ret", [])):
        print("\n" + "=" * 60)
        print("PHASE 3: Try msite API (less protected?)")
        print("=" * 60)

        MSITE_API = "mtop.aliexpress.pdp.msite.query"
        t3 = int(time.time() * 1000)
        msite_data = json.dumps({
            "productId": PRODUCT_ID,
            "extParams": "{}",
            "_currency": "EUR",
            "_lang": "it_IT",
        }, separators=(",", ":"))
        sign_str = f"{token}&{t3}&{APP_KEY}&{msite_data}"
        sign3 = hashlib.md5(sign_str.encode()).hexdigest()
        url3 = (
            f"https://acs.aliexpress.com/h5/{MSITE_API}/1.0/"
            f"?jsv={JSV}&appKey={APP_KEY}&t={t3}&sign={sign3}"
            f"&api={MSITE_API}&v=1.0&type=originaljson&dataType=json"
            f"&data={quote(msite_data)}"
        )
        print(f"MSITE URL: {url3[:150]}...")
        resp3 = s.get(url3, timeout=(10, 20))
        print(f"Status: {resp3.status_code}")
        print(f"Body[:600]: {resp3.text[:600]}")

    # ─── Phase 4: Прямой open API (если есть) ───
    print("\n" + "=" * 60)
    print("PHASE 4: Try open/semi-public endpoints")
    print("=" * 60)

    # 4a: item page with mobile UA
    mobile_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    s2 = cffi_req.Session(impersonate="chrome")
    s2.headers["User-Agent"] = mobile_ua
    s2.headers["Accept-Language"] = "it-IT,it;q=0.9,en;q=0.8"

    # 4b: Try the "open" detail API
    open_urls = [
        f"https://www.aliexpress.com/fetch/item/detail.htm?productId={PRODUCT_ID}",
        f"https://open.aliexpress.com/api/item/detail?productId={PRODUCT_ID}",
        f"https://www.aliexpress.com/glosearch/api/product/detail?productId={PRODUCT_ID}",
    ]
    for u in open_urls:
        try:
            r = s2.get(u, timeout=(10, 15))
            print(f"\n  [{r.status_code}] {u[:100]}")
            print(f"  CT: {r.headers.get('content-type','')[:50]}")
            print(f"  Body[:300]: {r.text[:300]}")
        except Exception as e:
            print(f"  [ERR] {u[:100]}: {e}")

    print("\nDone.")

if __name__ == "__main__":
    main()

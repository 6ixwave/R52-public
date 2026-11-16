"""Диагностика локали AliExpress.ru: что реально приходит от сервера."""
import re, json, time, sys

print("=" * 60)
print("DIAG: AliExpress RU locale check")
print("=" * 60)

# ─── 1. Wholesale HTML ───
print("\n[1] Wholesale HTML (curl_cffi, chrome impersonate)")
from curl_cffi import requests as cffi_req
from urllib.parse import quote

query = "наушники bluetooth"
url = f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}&page=1&_lang=ru_RU&_currency=RUB"

cookies_v1 = {
    "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"
}
# v2: добавляем site cookie и intl_common_forever
cookies_v2 = {
    "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
    "site": "ru",
    "intl_common_forever": "ru",
}
# v3: _lang=ru (без подчёркивания)
url_v3 = f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}&page=1&_lang=ru&_currency=RUB"

headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://ru.aliexpress.com/",
    "Upgrade-Insecure-Requests": "1",
}

for label, u, ck in [
    ("v1 basic", url, cookies_v1),
    ("v2 +site+intl", url, cookies_v2),
    ("v3 _lang=ru", url_v3, cookies_v1),
]:
    print(f"\n  --- {label} ---")
    try:
        t0 = time.monotonic()
        resp = cffi_req.get(u, impersonate="chrome", timeout=(15, 30), headers=headers, cookies=ck)
        lat = time.monotonic() - t0
        print(f"  HTTP {resp.status_code}, {len(resp.text)} bytes, {lat:.1f}s")
        
        # lang attr
        lang_m = re.search(r'<html[^>]*lang="([^"]*)"', resp.text)
        print(f"  <html lang=\"{lang_m.group(1) if lang_m else 'NOT FOUND'}\">")
        
        # title
        title_m = re.search(r'<title>(.*?)</title>', resp.text, re.DOTALL)
        print(f"  <title>: {title_m.group(1).strip()[:80] if title_m else 'NOT FOUND'}")
        
        # First 3 h3 titles
        h3s = re.findall(r'<h3[^>]*>(.*?)</h3>', resp.text[:200000], re.DOTALL)
        h3s_clean = [re.sub(r'<[^>]+>', '', h).strip()[:60] for h in h3s[:5]]
        for i, t in enumerate(h3s_clean):
            print(f"  h3[{i}]: {t}")
        
        # Check for Russian chars in first 100KB
        sample = resp.text[:100000]
        ru_chars = sum(1 for c in sample if '\u0410' <= c <= '\u044f')
        it_markers = sum(1 for w in ['cuffie','bluetooth','wireless','nuovo','prezzo'] if w.lower() in sample.lower())
        print(f"  RU chars in first 100KB: {ru_chars}, IT markers: {it_markers}")
        
        # card-list
        cl = resp.text.find('id="card-list"')
        print(f"  card-list at: {cl}")
        
    except Exception as e:
        print(f"  ERROR: {e}")

# ─── 2. Search API ───
print("\n[2] Search API (aer-webapi/v1/search)")
try:
    api_url = (f"https://ru.aliexpress.com/aer-webapi/v1/search"
               f"?SearchText={quote(query)}&page=1&pageSize=10"
               f"&sortType=default&_lang=ru_RU&_currency=RUB")
    t0 = time.monotonic()
    resp = cffi_req.get(api_url, impersonate="chrome", timeout=(15, 30),
                        headers={
                            "Accept": "application/json, text/plain, */*",
                            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                            "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}",
                            "Origin": "https://ru.aliexpress.com",
                        },
                        cookies=cookies_v2)
    lat = time.monotonic() - t0
    print(f"  HTTP {resp.status_code}, {len(resp.text)} bytes, {lat:.1f}s")
    
    if resp.status_code == 200:
        try:
            data = resp.json()
            # Navigate to products
            items = []
            if isinstance(data, dict):
                # Try common structures
                for key in ["data", "result", "items", "products"]:
                    if key in data:
                        inner = data[key]
                        if isinstance(inner, dict):
                            for k2 in ["items", "products", "list", "result"]:
                                if k2 in inner and isinstance(inner[k2], list):
                                    items = inner[k2]
                                    break
                        elif isinstance(inner, list):
                            items = inner
                        if items:
                            break
            elif isinstance(data, list):
                items = data
            
            print(f"  Items found: {len(items)}")
            for i, item in enumerate(items[:5]):
                if isinstance(item, dict):
                    title = item.get("title") or item.get("subject") or item.get("name") or ""
                    price = item.get("price") or item.get("minPrice") or ""
                    if isinstance(price, dict):
                        price = price.get("value") or price.get("formatted") or str(price)
                    item_id = item.get("itemId") or item.get("id") or item.get("productId") or ""
                    print(f"  [{i}] id={item_id} title={str(title)[:60]} price={price}")
                else:
                    print(f"  [{i}] type={type(item).__name__} val={str(item)[:60]}")
            
            # Check RU in titles
            if items:
                titles = [str(it.get("title","") or it.get("subject","")) for it in items[:10] if isinstance(it, dict)]
                ru_titles = sum(1 for t in titles if any('\u0410' <= c <= '\u044f' for c in t))
                print(f"  RU titles: {ru_titles}/{len(titles)}")
        except json.JSONDecodeError:
            print(f"  Not JSON. First 300: {resp.text[:300]}")
    else:
        print(f"  Body: {resp.text[:300]}")
except Exception as e:
    print(f"  ERROR: {e}")

# ─── 3. Try www.aliexpress.com with _lang=ru_RU ───
print("\n[3] www.aliexpress.com (com domain) with RU params")
try:
    url_com = f"https://www.aliexpress.com/wholesale?SearchText={quote(query)}&page=1&_lang=ru_RU&_currency=RUB"
    t0 = time.monotonic()
    resp = cffi_req.get(url_com, impersonate="chrome", timeout=(15, 30), headers=headers, cookies=cookies_v2)
    lat = time.monotonic() - t0
    print(f"  HTTP {resp.status_code}, {len(resp.text)} bytes, {lat:.1f}s")
    lang_m = re.search(r'<html[^>]*lang="([^"]*)"', resp.text)
    print(f"  <html lang=\"{lang_m.group(1) if lang_m else 'NOT FOUND'}\">")
    h3s = re.findall(r'<h3[^>]*>(.*?)</h3>', resp.text[:200000], re.DOTALL)
    h3s_clean = [re.sub(r'<[^>]+>', '', h).strip()[:60] for h in h3s[:3]]
    for i, t in enumerate(h3s_clean):
        print(f"  h3[{i}]: {t}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n" + "=" * 60)
print("DIAG complete")
print("=" * 60)

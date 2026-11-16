"""OZON v3: 403 body analysis + mobile + api.ozon.ru + entrypoint + different headers."""
import json, sys, time, re

def main():
    from curl_cffi import requests as cffi_req
    print("[OK] curl_cffi")
    print(f"\n{'='*60}\nOZON V3\n{'='*60}")

    pid = "1600562267"
    headers_base = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    # ============================================================
    # A. Read the 403 body (8.5KB) — what's in it?
    # ============================================================
    print("\n[A] 403 BODY ANALYSIS")
    r = cffi_req.get(f"https://www.ozon.ru/product/{pid}/",
                     impersonate="chrome", headers=headers_base, timeout=15)
    print(f"    status={r.status_code} size={len(r.content)}")
    html = r.text
    # Is it HTML or JSON?
    if html.strip().startswith('<'):
        print(f"    Type: HTML")
        # Extract title
        tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
        print(f"    title: {tm.group(1)[:80] if tm else 'N/A'}")
        # Scripts
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        print(f"    scripts: {len(scripts)}")
        for i, s in enumerate(scripts[:5]):
            s = s.strip()
            if s:
                print(f"    [{i}] {s[:200]}")
        # Meta tags
        metas = re.findall(r'<meta[^>]+>', html)
        for m in metas[:10]:
            print(f"    {m[:100]}")
        # Any embedded JSON?
        json_blocks = re.findall(r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        print(f"    JSON scripts: {len(json_blocks)}")
        for jb in json_blocks[:3]:
            print(f"      {jb[:200]}")
        # data-widget
        widgets = re.findall(r'data-widget=["\'](\w+)["\']', html)
        if widgets:
            print(f"    widgets: {list(set(widgets))[:10]}")
    elif html.strip().startswith('{'):
        print(f"    Type: JSON")
        print(f"    {html[:500]}")
    else:
        print(f"    Type: Other")
        print(f"    {html[:300]}")

    # ============================================================
    # B. Mobile UA
    # ============================================================
    print("\n[B] MOBILE UA")
    mobile_headers = {
        **headers_base,
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    }
    for u in [
        f"https://www.ozon.ru/product/{pid}/",
        f"https://m.ozon.ru/product/{pid}/",
        f"https://www.ozon.ru/m/product/{pid}/",
    ]:
        try:
            r = cffi_req.get(u, impersonate="chrome", headers=mobile_headers, timeout=15)
            print(f"    {u.split('ozon.ru')[1][:35]}: {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                print(f"      *** SUCCESS *** {r.text[:200]}")
                break
        except Exception as e:
            print(f"    ERR: {str(e)[:50]}")
        time.sleep(1)

    # ============================================================
    # C. api.ozon.ru (different subdomain)
    # ============================================================
    print("\n[C] API.OZON.RU")
    api_urls = [
        f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{pid}/",
        f"https://api.ozon.ru/entrypoint-api.bx/page/json/v2?url=/product/{pid}/",
        f"https://api.ozon.ru/v1/product/{pid}",
        f"https://api.ozon.ru/product/{pid}",
    ]
    for u in api_urls:
        try:
            r = cffi_req.get(u, impersonate="chrome", timeout=12,
                             headers={"Accept": "application/json", **headers_base})
            tag = u.split("ozon.ru")[1][:45]
            print(f"    {tag}: {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                print(f"      {r.text[:300]}")
        except Exception as e:
            print(f"    {u.split('ozon.ru')[1][:35]}: ERR {str(e)[:50]}")
        time.sleep(0.5)

    # ============================================================
    # D. Composer API with specific headers (X-Request-With)
    # ============================================================
    print("\n[D] COMPOSER WITH HEADERS")
    composer_headers = {
        **headers_base,
        "Accept": "application/json, text/plain, */*",
        "X-Request-With": "XMLHttpRequest",
        "Origin": "https://www.ozon.ru",
        "Referer": f"https://www.ozon.ru/product/{pid}/",
    }
    composer_urls = [
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{pid}/",
        f"https://www.ozon.ru/api/composer-api.bx/page/v2?url=/product/{pid}/",
        f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url=/product/{pid}/",
    ]
    for u in composer_urls:
        try:
            r = cffi_req.get(u, impersonate="chrome", headers=composer_headers, timeout=12)
            tag = u.split("ozon.ru")[1][:45]
            print(f"    {tag}: {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                d = r.json()
                s = json.dumps(d, ensure_ascii=False)
                prices = re.findall(r'"(?:price|v)"\s*:\s*(\d{4,})', s)
                print(f"      prices: {prices[:5]}")
                print(f"      keys: {list(d.keys())[:10]}")
        except Exception as e:
            print(f"    ERR: {str(e)[:50]}")
        time.sleep(0.5)

    # ============================================================
    # E. Safari impersonate (different TLS)
    # ============================================================
    print("\n[E] SAFARI IMPERSONATE")
    r = cffi_req.get(f"https://www.ozon.ru/product/{pid}/",
                     impersonate="safari", headers=headers_base, timeout=15)
    print(f"    safari: {r.status_code} ({len(r.content)}B)")
    if r.status_code == 200:
        print(f"      *** SUCCESS ***")
        print(f"      {r.text[:300]}")

    # ============================================================
    # F. Try without www (ozon.ru directly)
    # ============================================================
    print("\n[F] NO-WWW")
    r = cffi_req.get(f"https://ozon.ru/product/{pid}/",
                     impersonate="chrome", headers=headers_base, timeout=15)
    print(f"    ozon.ru (no www): {r.status_code} ({len(r.content)}B)")
    if r.status_code == 200:
        print(f"      *** SUCCESS *** {r.text[:200]}")

    # ============================================================
    # G. Try /product/ with slug pattern
    # ============================================================
    print("\n[G] SLUG PATTERNS")
    slug_urls = [
        f"https://www.ozon.ru/product/tovar-{pid}/",
        f"https://www.ozon.ru/product/item-{pid}/",
        f"https://www.ozon.ru/product/-{pid}/",
        f"https://www.ozon.ru/product/{pid}",
    ]
    for u in slug_urls:
        try:
            r = cffi_req.get(u, impersonate="chrome", headers=headers_base, timeout=12)
            tag = u.split("ozon.ru")[1][:35]
            print(f"    {tag}: {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                print(f"      *** {r.text[:200]}")
                break
        except Exception as e:
            print(f"    ERR: {str(e)[:40]}")
        time.sleep(0.5)

    print(f"\n{'='*60}\nDONE\n{'='*60}")

if __name__ == "__main__":
    main()

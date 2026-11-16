"""WB Price Diagnostic v3 — deep recursive search + alt endpoints."""
import json, sys, time, re

def main():
    try:
        from curl_cffi import requests as cffi_req
        print("[OK] curl_cffi доступен")
    except ImportError:
        print("[ERR] curl_cffi не найден"); sys.exit(1)

    print("=" * 70)
    print("WB PRICE DIAGNOSTIC v3 — deep search")
    print("=" * 70)

    nm_ids = [138278567, 145567890]
    JH = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    }

    for nm_id in nm_ids:
        vol = nm_id // 100000
        part = nm_id // 1000
        path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        print(f"\n{'='*60}\nNM_ID = {nm_id}\n{'='*60}")

        # 1. Fetch card.json
        host = None; card = None
        for n in range(1, 61):
            h = f"basket-{n:02d}.wbbasket.ru"
            try:
                r = cffi_req.get(f"https://{h}{path}", impersonate="chrome",
                                 headers=JH, timeout=10)
                if r.status_code == 200:
                    host = h; card = r.json(); break
            except Exception:
                pass
            time.sleep(0.3)
        if not host:
            print("  [FAIL] basket host not found"); continue
        print(f"  Host: {host}  size={len(r.content)}B")

        # 2. Recursive price search
        print("\n  --- Recursive price search ---")
        price_keys = []
        def deep(obj, prefix=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    fk = f"{prefix}.{k}" if prefix else k
                    kl = k.lower()
                    if any(p in kl for p in ("price","cost","amount","basic","wallet",
                                             "card","old","crossed","bank","value")):
                        if isinstance(v, (int,float)) and v > 0:
                            price_keys.append((fk, v))
                        elif isinstance(v, str) and re.match(r'^[\d.,]+$', v.strip()):
                            price_keys.append((fk, v))
                    deep(v, fk)
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:40]):
                    deep(item, f"{prefix}[{i}]")
        deep(card)
        if price_keys:
            print(f"  Found {len(price_keys)} price-like fields:")
            for k, v in price_keys[:30]:
                print(f"    {k} = {v}")
        else:
            print("  No price-like fields in card.json")

        # 3. Options dump
        print("\n  --- Options ---")
        opts = card.get("options", [])
        if isinstance(opts, list):
            for i, o in enumerate(opts[:30]):
                if isinstance(o, dict):
                    print(f"    [{i}] {o.get('name','?')} = {str(o.get('value','?'))[:50]} "
                          f"(type={o.get('type','?')}, unifying={o.get('is_unifying',False)})")
        else:
            print(f"    type={type(opts)}")

        # 4. Grouped options
        print("\n  --- Grouped options ---")
        go = card.get("grouped_options", [])
        if isinstance(go, list):
            for i, g in enumerate(go[:10]):
                if isinstance(g, dict):
                    items = g.get("items", [])
                    print(f"    [{i}] {g.get('name','?')} ({len(items)} items)")
                    for it in items[:5]:
                        if isinstance(it, dict):
                            print(f"         {it.get('name','?')} = {str(it.get('value','?'))[:40]}")
        elif isinstance(go, dict):
            for k, v in list(go.items())[:10]:
                print(f"    {k}: {str(v)[:60]}")

        # 5. Selling full
        print("\n  --- Selling ---")
        selling = card.get("selling", {})
        if isinstance(selling, dict):
            print(f"    {json.dumps(selling, ensure_ascii=False)[:400]}")

        # 6. Media
        print("\n  --- Media ---")
        media = card.get("media", {})
        if isinstance(media, dict):
            for k, v in media.items():
                if k != "items":
                    print(f"    {k} = {v}")
            items = media.get("items", [])
            print(f"    items: {len(items)}")

        # 7. Alt basket CDN paths
        print("\n  --- Basket CDN alt paths ---")
        for ap in [f"/vol{vol}/part{part}/{nm_id}/info/ru/price.json",
                   f"/vol{vol}/part{part}/{nm_id}/price.json",
                   f"/vol{vol}/part{part}/{nm_id}/info/price.json"]:
            try:
                r2 = cffi_req.get(f"https://{host}{ap}", impersonate="chrome",
                                  headers=JH, timeout=8)
                print(f"    {ap} -> {r2.status_code} ({len(r2.content)}B)")
                if r2.status_code == 200:
                    print(f"      {r2.text[:200]}")
            except Exception as e:
                print(f"    {ap} -> ERR {e}")
            time.sleep(0.5)

        # 8. Price API enhanced
        print("\n  --- Price API (enhanced) ---")
        PH = {**JH,
              "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36",
              "X-Requested-With": "XMLHttpRequest"}
        apis = [
            ("card_v2_d0", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=0&spp=30&nm={nm_id}"),
            ("card_v1_s1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&spp=1&nm={nm_id}"),
            ("cat_v2_s1",  f"https://catalog.wb.ru/cards/v2/detail?curr=rub&spp=1&nm={nm_id}"),
            ("wbx_v1",     f"https://wbx-content-v1.wbstatic.net/vol{vol}/part{part}/{nm_id}/info/ru/card.json"),
        ]
        for label, url in apis:
            try:
                r3 = cffi_req.get(url, impersonate="chrome", headers=PH, timeout=10)
                print(f"    [{label}] {r3.status_code} ({len(r3.content)}B)")
                if r3.status_code == 200:
                    d = r3.json()
                    prods = d.get("products") or d.get("data",{}).get("items",[])
                    if prods:
                        p = prods[0]
                        print(f"      name={str(p.get('name','?'))[:40]}")
                        for s in p.get("sizes",[])[:3]:
                            print(f"      price={s.get('price','?')} qty={s.get('qty','?')}")
                    else:
                        print(f"      keys={list(d.keys())[:10]}")
                        print(f"      {r3.text[:200]}")
            except Exception as e:
                print(f"    [{label}] ERR {e}")
            time.sleep(1)

        # 9. HTML mobile
        print("\n  --- HTML (mobile UA) ---")
        MH = {"Accept":"text/html",
              "Accept-Language":"ru-RU,ru;q=0.9",
              "User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                           "Version/17.0 Mobile/15E148 Safari/604.1"}
        try:
            r4 = cffi_req.get(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                              impersonate="chrome", headers=MH, timeout=15)
            print(f"    HTML: {r4.status_code} ({len(r4.content)}B)")
            if r4.status_code == 200:
                html = r4.text
                pats = [
                    (r'"price"\s*:\s*\{([^}]+)\}', "price obj"),
                    (r'"basic"\s*:\s*(\d+)', "basic"),
                    (r'"priceU"\s*:\s*(\d+)', "priceU"),
                    (r'"priceD"\s*:\s*(\d+)', "priceD"),
                    (r'og:price:amount[^>]*content=["\']([\d.]+)', "og:price"),
                    (r'data-price=["\'](\d+)["\']', "data-price"),
                    (r'[\d\s\u00a0]{2,10}\s*(?:₽|руб)', "visible"),
                ]
                for pat, lbl in pats:
                    ms = re.findall(pat, html, re.I)
                    if ms:
                        print(f"    [{lbl}] {ms[:5]}")
                for sp in [r'window\.__NEXT_DATA__\s*=\s*(\{.*?\})\s*</script>',
                           r'window\.__STATE__\s*=\s*(\{.*?\})\s*;']:
                    m = re.search(sp, html, re.DOTALL)
                    if m:
                        print(f"    [state] {sp[:35]}... ({len(m.group(1))}ch)")
            else:
                print(f"    body[:200]: {r4.text[:200]}")
        except Exception as e:
            print(f"    ERR {e}")

    print(f"\n{'='*70}\nDONE\n{'='*70}")

if __name__ == "__main__":
    main()

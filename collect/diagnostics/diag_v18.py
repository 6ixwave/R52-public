"""Диагностика v18: 90s, фокусный.
Цель: понять, какой HTTP-клиент (requests vs curl_cffi) даёт лучший результат
на каждой площадке, и где именно падает Farpost challenge.
"""
import json, re, time, traceback
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

T0 = time.monotonic()
LIMIT = 90
def el(): return f"{time.monotonic()-T0:.1f}s"
def rem(): return LIMIT - (time.monotonic() - T0)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
H = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
    print("[OK] curl_cffi available")
except ImportError:
    HAS_CFFI = False
    print("[WARN] no curl_cffi")

results = {}

# ============================================================
print(f"\n{'='*70}\n1. WILDBERRIES [{el()}] rem={rem():.0f}s\n{'='*70}")

wb_nm = "199298871"
wb_url = f"https://www.wildberries.ru/catalog/{wb_nm}/detail.aspx"
wb_card = f"https://card.wb.ru/cards/v2/detail?nm={wb_nm}"

# 1a. requests → main page
try:
    s1 = requests.Session()
    s1.headers.update(H)
    r = s1.get(wb_url, timeout=(5, 10))
    print(f"  [req main] {r.status_code} | {len(r.content)}B | CT={r.headers.get('Content-Type','?')[:40]}")
    results["wb_req_main"] = r.status_code
except Exception as e:
    print(f"  [req main] ERR: {str(e)[:60]}")

time.sleep(0.3)

# 1b. cffi → main page
if HAS_CFFI:
    s2 = cffi_req.Session(impersonate="chrome")
    try:
        r = s2.get(wb_url, headers=H, timeout=(5, 10))
        print(f"  [cffi main] {r.status_code} | {len(r.content)}B | CT={r.headers.get('Content-Type','?')[:40]}")
        results["wb_cffi_main"] = r.status_code
        if r.status_code == 200 and len(r.content) > 5000:
            # Check for JSON-LD
            html = r.text
            ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
            print(f"  JSON-LD blocks: {len(ld)}")
            for i, b in enumerate(ld[:2]):
                try:
                    d = json.loads(b.strip())
                    t = d.get("@type", "?") if isinstance(d, dict) else "?"
                    print(f"    [{i}] type={t} keys={list(d.keys())[:8] if isinstance(d,dict) else '?'}")
                except:
                    print(f"    [{i}] parse fail, len={len(b)}")
            # Check for state
            st = re.findall(r'window\.__\w+__\s*=', html)
            print(f"  State vars: {st[:5]}")
    except Exception as e:
        print(f"  [cffi main] ERR: {str(e)[:60]}")

    time.sleep(0.3)

    # 1c. cffi → card.wb.ru API
    try:
        r = s2.get(wb_card, headers={
            "User-Agent": UA, "Accept": "application/json",
            "Origin": "https://www.wildberries.ru",
            "Referer": "https://www.wildberries.ru/",
        }, timeout=(5, 10))
        print(f"  [cffi card] {r.status_code} | {len(r.content)}B")
        results["wb_cffi_card"] = r.status_code
        if r.status_code == 200:
            data = r.json()
            cards = data.get("data", {}).get("cards", [])
            print(f"  Cards: {len(cards)}")
            if cards:
                c = cards[0]
                print(f"    name={c.get('name','?')[:50]}")
                print(f"    imgs={len(c.get('images',[]))} price={c.get('sizes',[{}])[0].get('price',{}).get('basic',0) if c.get('sizes') else '?'}")
                # Extract image URLs
                imgs = c.get("images", [])
                for im in imgs[:3]:
                    print(f"    img: {im[:80]}")
                with open("wb_v18_card.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [cffi card] ERR: {str(e)[:60]}")

    time.sleep(0.3)

    # 1d. cffi → search API
    try:
        r = s2.get("https://search.wb.ru/exactmatch/ru/common/v5/search",
                   params={"query": "телефон", "nm": "1", "resultset": "catalog", "sort": "popular", "spp": "30"},
                   headers={"User-Agent": UA, "Accept": "application/json",
                            "Origin": "https://www.wildberries.ru", "Referer": "https://www.wildberries.ru/"},
                   timeout=(5, 10))
        print(f"  [cffi search] {r.status_code} | {len(r.content)}B")
        results["wb_cffi_search"] = r.status_code
        if r.status_code == 200:
            prods = r.json().get("data", {}).get("products", [])
            print(f"  Products: {len(prods)}")
            for p in prods[:3]:
                print(f"    id={p.get('id')} name={p.get('name','?')[:40]} imgs={len(p.get('images',[]))}")
    except Exception as e:
        print(f"  [cffi search] ERR: {str(e)[:60]}")

# ============================================================
if rem() > 25:
    print(f"\n{'='*70}\n2. FARPOST — challenge deep [{el()}] rem={rem():.0f}s\n{'='*70}")

    fp_base = "https://www.farpost.ru/geo/moskva/l/telefony/"
    if HAS_CFFI:
        s_fp = cffi_req.Session(impersonate="chrome")
        try:
            r = s_fp.get(fp_base, headers=H, timeout=(5, 12))
            print(f"  [FP page] {r.status_code} | {len(r.content)}B")
            html = r.text
            cookies = dict(s_fp.cookies)
            print(f"  Cookies: {list(cookies.keys())}")

            # Extract challenge URL
            ch_m = re.search(r'challengeurl="([^"]+)"', html)
            ch_url = ch_m.group(1).replace("&amp;", "&") if ch_m else ""
            print(f"  challengeurl: {ch_url}")

            # Try multiple URL patterns
            patterns = []
            if ch_url:
                # Pattern 1: page + query
                patterns.append(("page+query", fp_base + ch_url))
                # Pattern 2: root + query
                patterns.append(("root+query", "https://www.farpost.ru" + ch_url))
                # Pattern 3: /altcha/ + query
                patterns.append(("altcha+query", "https://www.farpost.ru/altcha" + ch_url))
                # Pattern 4: /geo/moskva/l/telefony/altcha + query
                patterns.append(("path/altcha", fp_base + "altcha" + ch_url))
                # Pattern 5: /verify/ + query
                patterns.append(("verify+query", "https://www.farpost.ru/verify" + ch_url))

            # Also try without the 'a' param (maybe it's session-bound)
            ch_no_a = re.sub(r'&a=\d+', '', ch_url)
            if ch_no_a != ch_url:
                patterns.append(("no-a", fp_base + ch_no_a))

            for label, test_url in patterns:
                try:
                    r_t = s_fp.get(test_url, headers={
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "Referer": fp_base,
                        "Sec-Fetch-Dest": "empty",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Site": "same-origin",
                        "X-Requested-With": "XMLHttpRequest",
                    }, timeout=(4, 8))
                    ct = r_t.headers.get("Content-Type", "?")[:40]
                    body_preview = ""
                    if r_t.status_code == 200:
                        body_preview = r_t.text[:200]
                    print(f"  [{label}] {r_t.status_code} | {len(r_t.content)}B | {ct}")
                    if r_t.status_code == 200 and "json" in ct.lower():
                        print(f"    ✅ JSON: {body_preview}")
                        results["fp_challenge"] = label
                        break
                    elif r_t.status_code == 200 and len(r_t.content) < 5000:
                        # Maybe JSON in HTML
                        jm = re.search(r'\{[^{}]*"(?:algorithm|challenge|key|obfuscated)[^{}]*\}', r_t.text)
                        if jm:
                            print(f"    ✅ JSON in HTML: {jm.group(0)[:200]}")
                            results["fp_challenge"] = label
                            break
                except Exception as e:
                    print(f"  [{label}] ERR: {str(e)[:50]}")
                time.sleep(0.2)

            # If still no challenge, check if page has embedded challenge
            if "fp_challenge" not in results:
                # Look for inline JSON in the page
                inline = re.findall(r'(?:challenge|altcha)[^;]*?=\s*(\{[^}]+\})', html, re.I)
                print(f"  Inline challenge vars: {len(inline)}")
                for iv in inline[:3]:
                    print(f"    {iv[:150]}")

            # Check: maybe the page itself IS the challenge (no separate fetch)
            # Look for data in the altcha-widget or nearby scripts
            widget_block = re.search(r'<altcha-widget[^>]*>(.*?)</altcha-widget>', html, re.DOTALL)
            if widget_block:
                print(f"  Widget content: {widget_block.group(1)[:200]}")

            # Try: maybe challenge is at /altcha/challenge (no query)
            for ep in ["https://www.farpost.ru/altcha/challenge",
                       "https://www.farpost.ru/api/altcha/challenge",
                       "https://www.farpost.ru/geo/moskva/l/telefony/altcha/challenge"]:
                try:
                    r_e = s_fp.get(ep, headers={"Accept": "application/json", "Referer": fp_base}, timeout=(3, 6))
                    print(f"  [EP {ep[-35:]}] {r_e.status_code} | {len(r_e.content)}B")
                    if r_e.status_code == 200:
                        print(f"    Body: {r_e.text[:200]}")
                except:
                    pass
                time.sleep(0.2)

        except Exception as e:
            print(f"  [FP] ERR: {e}")
            traceback.print_exc()

# ============================================================
if rem() > 20:
    print(f"\n{'='*70}\n3. AVITO — quick PoW [{el()}] rem={rem():.0f}s\n{'='*70}")

    if HAS_CFFI:
        s_av = cffi_req.Session(impersonate="chrome")
        try:
            r = s_av.get("https://www.avito.ru/moscow/telefony", headers=H, timeout=(5, 12))
            print(f"  [Avito] {r.status_code} | {len(r.content)}B")
            cookies = dict(s_av.cookies)
            print(f"  Cookies: {list(cookies.keys())}")

            pow_ck = cookies.get("pow_challenge", "")
            if pow_ck:
                import base64, struct
                s = pow_ck.replace("-", "+").replace("_", "/")
                while len(s) % 4: s += "="
                decoded = base64.b64decode(s)
                print(f"  PoW: {len(pow_ck)} chars → {len(decoded)} bytes")
                print(f"  First 16B: {decoded[:16].hex()}")

                # Try complexity detection
                complexity = 10
                if len(decoded) >= 4:
                    c4 = struct.unpack('<I', decoded[:4])[0]
                    if 4 <= c4 <= 20: complexity = c4
                if decoded[0] >= 4 and decoded[0] <= 20:
                    complexity = decoded[0]
                print(f"  Complexity: {complexity}")

                import hashlib
                t0p = time.monotonic()
                nonce = None
                max_try = 500_000 if complexity <= 12 else 200_000
                for i in range(max_try):
                    h = hashlib.sha256(decoded + str(i).encode()).digest()
                    byte_idx = complexity // 8
                    bit_off = complexity % 8
                    ok = all(h[j] == 0 for j in range(byte_idx))
                    if ok and bit_off > 0:
                        ok = (h[byte_idx] >> (8 - bit_off)) == 0
                    if ok:
                        nonce = i
                        break
                print(f"  Nonce={nonce} ({time.monotonic()-t0p:.1f}s)")

                if nonce is not None:
                    pow_resp = base64.b64encode(str(nonce).encode()).decode()
                    s_av.cookies.set("pow-response", pow_resp, domain="www.avito.ru")
                    time.sleep(1)
                    r2 = s_av.get("https://www.avito.ru/moscow/telefony", headers=H, timeout=(5, 12))
                    print(f"  [Retry] {r2.status_code} | {len(r2.content)}B")
                    if r2.status_code == 200:
                        print("  ✅ AVITO UNLOCKED")
                        results["avito"] = "unlocked"
                    else:
                        print(f"  ⚠ Still {r2.status_code}")
            else:
                print(f"  ⚠ No pow_challenge")
                # Maybe direct access?
                if r.status_code == 200 and len(r.content) > 10000:
                    print("  ✅ Direct access (no PoW needed)")
                    results["avito"] = "direct"
        except Exception as e:
            print(f"  [Avito] ERR: {e}")

# ============================================================
if rem() > 15:
    print(f"\n{'='*70}\n4. OZON — composer + cffi [{el()}] rem={rem():.0f}s\n{'='*70}")

    oz_id = "1600562267"
    if HAS_CFFI:
        s_oz = cffi_req.Session(impersonate="chrome")
        try:
            # First hit main page for cookies
            r_main = s_oz.get("https://www.ozon.ru/", headers=H, timeout=(4, 8))
            print(f"  [Main] {r_main.status_code} | {len(r_main.content)}B | CK: {list(s_oz.cookies.keys())}")
            time.sleep(0.5)

            # Composer API
            r_comp = s_oz.get(
                "https://www.ozon.ru/api/composer-api.bx/page/json/v2",
                params={"url": f"/product/{oz_id}/"},
                headers={"Accept": "application/json", "Referer": f"https://www.ozon.ru/product/{oz_id}/"},
                timeout=(5, 10)
            )
            print(f"  [Composer] {r_comp.status_code} | {len(r_comp.content)}B")
            results["ozon_composer"] = r_comp.status_code
            if r_comp.status_code == 200:
                data = r_comp.json()
                print(f"  Keys: {list(data.keys())[:10]}")
                print("  ✅ Composer works!")
                with open("ozon_v18_composer.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            elif r_comp.status_code == 403:
                try:
                    cd = r_comp.json()
                    print(f"  challengeURL: {cd.get('challengeURL','?')[:80]}")
                except:
                    print(f"  Body[:150]: {r_comp.text[:150]}")

            time.sleep(0.3)

            # Direct product page with cffi
            r_prod = s_oz.get(f"https://www.ozon.ru/product/{oz_id}/", headers=H, timeout=(5, 12))
            print(f"  [Product] {r_prod.status_code} | {len(r_prod.content)}B")
            results["ozon_product"] = r_prod.status_code
            if r_prod.status_code == 200 and len(r_prod.content) > 5000:
                html = r_prod.text
                ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
                print(f"  JSON-LD: {len(ld)}")
                for b in ld[:2]:
                    try:
                        d = json.loads(b.strip())
                        t = d.get("@type", "?") if isinstance(d, dict) else "?"
                        print(f"    type={t}")
                    except:
                        pass
        except Exception as e:
            print(f"  [Ozon] ERR: {e}")

# ============================================================
print(f"\n{'='*70}\nSUMMARY [{el()}]\n{'='*70}")
for k, v in results.items():
    print(f"  {k}: {v}")
print(f"\nDONE diag_v18 [{el()}]")

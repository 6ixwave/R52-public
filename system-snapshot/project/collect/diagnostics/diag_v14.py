"""Диагностика v14: фокус на рабочих механизмах.
- Avito: 429/35KB HTML analysis, pow_challenge retry, JS challenge
- Farpost: Altcha config из 198KB страницы (не отдельный URL)
- WB: CDN brute (60s), card.wb.ru с cffi, search
- Ozon: challenge page 8KB parse, composer session, api.ozon.ru
- Proxies: quick 20-test
Timeout: 300s total
"""
import json, re, time, hashlib, base64, traceback, os, struct, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

T_START = time.monotonic()
def elapsed():
    return f"{time.monotonic()-T_START:.1f}s"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
UA_M = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
    print("[OK] curl_cffi available")
except ImportError:
    HAS_CFFI = False
    print("[WARN] curl_cffi NOT available")

def hdr(extra=None):
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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
    if extra:
        h.update(extra)
    return h

def b64url_decode(s):
    s = s.replace("-", "+").replace("_", "/")
    while len(s) % 4:
        s += "="
    return base64.b64decode(s)


# ============================================================
print("\n" + "=" * 70)
print(f"1. AVITO — 429 HTML + pow_challenge retry  [{elapsed()}]")
print("=" * 70)

if HAS_CFFI:
    s_av = cffi_requests.Session(impersonate="chrome")
    h_av = hdr({"Accept": "text/html"})

    try:
        # First hit: get cookies + HTML
        r = s_av.get("https://www.avito.ru/", headers=h_av, timeout=(8, 20))
        print(f"  [Avito home] {r.status_code} | {len(r.content)}B [{elapsed()}]")
        cookies = dict(s_av.cookies)
        print(f"  Cookies: {list(cookies.keys())}")

        # Save HTML for analysis
        with open("avito_v14_home.html", "w", encoding="utf-8") as f:
            f.write(r.text)

        # Look for challenge in HTML
        html = r.text
        # Check for pow/challenge/nonce keywords
        for kw in ["pow_challenge", "pow-response", "challenge", "nonce", "sha256", "b64Decode", "parseJwt"]:
            if kw in html:
                # Find context
                idx = html.find(kw)
                ctx = html[max(0,idx-100):idx+200]
                print(f"  KW [{kw}]: ...{ctx[:150]}...")

        # Try to get pow_challenge: sometimes it's in a script or meta
        pow_m = re.search(r'pow_challenge["\s:=]+["\']([^"\']+)["\']', html)
        if pow_m:
            print(f"  pow_challenge in HTML: {pow_m.group(1)[:80]}")

        # Retry with delay to get 439 + pow_challenge
        time.sleep(3)
        r2 = s_av.get("https://www.avito.ru/moscow/telefony", headers=h_av, timeout=(8, 20))
        print(f"  [Avito retry] {r2.status_code} | {len(r2.content)}B [{elapsed()}]")
        cookies2 = dict(s_av.cookies)
        print(f"  Cookies2: {list(cookies2.keys())}")

        pow_ck = cookies2.get("pow_challenge", "")
        if pow_ck:
            print(f"  pow_challenge: {pow_ck[:100]}...")
            # base64url decode
            try:
                decoded_bytes = b64url_decode(pow_ck)
                decoded = decoded_bytes.decode("utf-8", errors="replace")
                print(f"  Decoded ({len(decoded)}): {decoded[:200]}")

                # JWT?
                parts = pow_ck.split(".")
                print(f"  Dots in cookie: {len(parts)-1}")
                if len(parts) >= 2:
                    try:
                        payload_b64 = parts[1]
                        payload_bytes = b64url_decode(payload_b64)
                        payload_str = payload_bytes.decode("utf-8", errors="replace")
                        print(f"  JWT payload: {payload_str[:300]}")
                        payload = json.loads(payload_str)
                        print(f"  Payload: {json.dumps(payload, ensure_ascii=False)[:400]}")
                    except Exception as e3:
                        print(f"  JWT parse: {e3}")
            except Exception as e2:
                print(f"  b64url ERR: {e2}")
                # Try standard base64
                try:
                    decoded = base64.b64decode(pow_ck + "==").decode("utf-8", errors="replace")
                    print(f"  Std b64: {decoded[:200]}")
                except:
                    pass
        else:
            print(f"  ⚠ No pow_challenge in cookies2")
            # Maybe it's in the HTML as a data attribute or script
            data_attrs = re.findall(r'data-([a-z-]+)=["\']([^"\']{10,})["\']', html)
            print(f"  Data attrs: {data_attrs[:8]}")

        # Extract ALL scripts for deeper analysis
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        challenge_scripts = []
        for i, s in enumerate(scripts):
            ss = s.strip()
            if len(ss) > 100 and any(k in ss.lower() for k in ['pow', 'challenge', 'nonce', 'sha256', 'b64', 'jwt']):
                challenge_scripts.append((i, ss))
        print(f"  Challenge scripts: {len(challenge_scripts)}")
        for i, ss in challenge_scripts[:3]:
            print(f"  Script[{i}] ({len(ss)}): {ss[:400]}")
            with open(f"avito_v14_js_{i}.js", "w", encoding="utf-8") as f:
                f.write(ss)

    except Exception as e:
        print(f"  [Avito] ERR: {e}")
        traceback.print_exc()
else:
    print("  [SKIP] No curl_cffi")


# ============================================================
print(f"\n{'='*70}")
print(f"2. FARPOST — Altcha config from 198KB page  [{elapsed()}]")
print("=" * 70)

s_fp = requests.Session()
s_fp.headers.update(hdr())

try:
    r_fp = s_fp.get("https://www.farpost.ru/geo/moskva/l/telefony/", timeout=(8, 25))
    print(f"  [FP page] {r_fp.status_code} | {len(r_fp.content)}B [{elapsed()}]")
    html_fp = r_fp.text

    if "altcha" not in html_fp.lower():
        print("  -> No CAPTCHA!")
        with open("fp_v14_real.html", "w", encoding="utf-8") as f:
            f.write(html_fp)
    else:
        print("  -> Altcha detected. Parsing config from page...")

        # The Altcha config is in a <script> tag. Look for the specific pattern:
        # Usually: new Altcha({...}) or altcha.config = {...} or window.ALTCHA_CONFIG
        # Or in data attributes of <altcha-widget>
        
        # 1. Look for <altcha-widget> element with data attributes
        widget_m = re.search(r'<altcha-widget[^>]*>', html_fp, re.I)
        if widget_m:
            print(f"  <altcha-widget>: {widget_m.group(0)[:300]}")
            # Extract all data-* attributes
            w_attrs = re.findall(r'data-([a-z-]+)=["\']([^"\']+)["\']', widget_m.group(0))
            print(f"  Widget attrs: {w_attrs}")

        # 2. Look for script with Altcha initialization
        # Pattern: new Altcha({ ... }) or Altcha.init({ ... })
        altcha_scripts = []
        for sm in re.finditer(r'<script[^>]*>(.*?)</script>', html_fp, re.DOTALL):
            sb = sm.group(1).strip()
            if 'Altcha' in sb or 'altcha' in sb.lower() or 'grecap' in sb.lower():
                altcha_scripts.append(sb)

        print(f"  Altcha scripts: {len(altcha_scripts)}")
        for i, sb in enumerate(altcha_scripts):
            print(f"  Script[{i}] ({len(sb)} chars):")
            # Look for config object
            config_m = re.search(r'(?:new\s+Altcha|Altcha\.init|altchaConfig|window\.__ALTCHA)\s*[\(=]\s*(\{[^}]+\})', sb, re.I | re.DOTALL)
            if config_m:
                print(f"    Config: {config_m.group(1)[:400]}")
            else:
                # Look for individual fields
                for field in ["algorithm", "challenge", "salt", "max", "min", "url", "dataUrl"]:
                    fm = re.search(rf'["\']?{field}["\']?\s*[:=]\s*["\']?([^"\',}}\s]+)', sb, re.I)
                    if fm:
                        print(f"    {field}: {fm.group(1)[:80]}")
            print(f"    First 300: {sb[:300]}")
            print()

        # 3. Look for JSON-LD or meta with challenge
        for sm in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_fp, re.DOTALL):
            try:
                ld = json.loads(sm.group(1).strip())
                if "challenge" in json.dumps(ld).lower():
                    print(f"  JSON-LD challenge: {json.dumps(ld, ensure_ascii=False)[:300]}")
            except:
                pass

        # 4. Look for the specific Altcha config pattern in ALL scripts
        # Format: {algorithm: "SHA-256", challenge: "abc...", salt: "def...", max: 1000000}
        all_scripts_text = "\n".join(re.findall(r'<script[^>]*>(.*?)</script>', html_fp, re.DOTALL))
        
        # Find challenge (hex string 32+ chars)
        ch_m = re.search(r'["\']?challenge["\']?\s*[:=]\s*["\']([a-fA-F0-9]{32,})["\']', all_scripts_text)
        salt_m = re.search(r'["\']?salt["\']?\s*[:=]\s*["\']([a-fA-F0-9]{8,})["\']', all_scripts_text)
        max_m = re.search(r'["\']?max["\']?\s*[:=]\s*(\d{4,})', all_scripts_text)
        algo_m = re.search(r'["\']?algorithm["\']?\s*[:=]\s*["\']?([A-Za-z0-9\-]+)', all_scripts_text)
        
        print(f"\n  Challenge: {ch_m.group(1)[:60] if ch_m else 'NOT FOUND'}")
        print(f"  Salt: {salt_m.group(1)[:60] if salt_m else 'NOT FOUND'}")
        print(f"  Max: {max_m.group(1) if max_m else 'NOT FOUND'}")
        print(f"  Algo: {algo_m.group(1) if algo_m else 'NOT FOUND'}")

        if ch_m and salt_m:
            challenge = ch_m.group(1)
            salt = salt_m.group(1)
            max_iter = int(max_m.group(1)) if max_m else 1000000
            algo = algo_m.group(1) if algo_m else "SHA-256"

            print(f"\n  Solving PoW: algo={algo} max={max_iter}")
            try:
                salt_bytes = bytes.fromhex(salt)
            except:
                salt_bytes = salt.encode()
            try:
                target_hash = bytes.fromhex(challenge)
            except:
                target_hash = challenge.encode()

            t0 = time.monotonic()
            nonce = None
            for i in range(max_iter + 1):
                h = hashlib.sha256(salt_bytes + str(i).encode()).digest()
                if h == target_hash:
                    nonce = i
                    break
            pw_time = time.monotonic() - t0
            print(f"  Nonce={nonce} ({pw_time:.2f}s) [{elapsed()}]")

            if nonce is not None:
                token_data = {
                    "version": 1,
                    "data": {
                        "algorithm": algo,
                        "challenge": challenge,
                        "salt": salt,
                        "nonce": nonce,
                        "max": max_iter,
                    }
                }
                token = base64.b64encode(json.dumps(token_data).encode()).decode()
                print(f"  Token: {token[:80]}...")

                # POST to form (same page with query params)
                # Get form action
                form_m = re.search(r'<form[^>]*action=["\']([^"\']+)["\']', html_fp, re.I)
                if form_m:
                    action = form_m.group(1).replace("&amp;", "&")
                else:
                    action = "?r=1&u=%2Fgeo%2Fmoskva%2Fl%2Ftelefony%2F&f=3"

                if action.startswith("?"):
                    post_url = "https://www.farpost.ru/geo/moskva/l/telefony/" + action
                elif action.startswith("/"):
                    post_url = "https://www.farpost.ru" + action
                else:
                    post_url = action

                # Get hidden fields
                field_s = re.search(r'name="s"\s+value="([^"]*)"', html_fp)
                field_t = re.search(r'name="t"\s+value="([^"]*)"', html_fp)
                field_csrf = re.search(r'name="csrf_token"\s+value="([^"]*)"', html_fp)
                field_recaptcha = re.search(r'name="[REDACTED_AUTH_SECRET]"\s+value="([^"]*)"', html_fp)

                data = {"[REDACTED_AUTH_SECRET]": token, "code": ""}
                if field_s:
                    data["s"] = field_s.group(1)
                if field_t:
                    data["t"] = field_t.group(1)
                if field_csrf:
                    data["csrf_token"] = field_csrf.group(1)

                print(f"  POST to: {post_url[:100]}")
                r_post = s_fp.post(post_url, data=data, timeout=(8, 20),
                                   headers={"Referer": "https://www.farpost.ru/geo/moskva/l/telefony/",
                                            "Content-Type": "application/x-www-form-urlencoded"})
                print(f"  [POST] {r_post.status_code} | {len(r_post.content)}B")
                print(f"  Cookies: {list(s_fp.cookies.keys())}")

                time.sleep(1)
                r_real = s_fp.get("https://www.farpost.ru/geo/moskva/l/telefony/", timeout=(8, 20))
                print(f"  [Real] {r_real.status_code} | {len(r_real.content)}B [{elapsed()}]")
                if r_real.status_code == 200 and "altcha" not in r_real.text.lower():
                    print("  ✅ FARPOST UNLOCKED!")
                    with open("fp_v14_real.html", "w", encoding="utf-8") as f:
                        f.write(r_real.text)
                    links = re.findall(r'href="([^"]+)"', r_real.text)
                    ad_links = [l for l in links if re.search(r'/\d{5,}/', l)]
                    print(f"  Links: {len(links)}, ad-like: {len(ad_links)}")
                    for l in ad_links[:10]:
                        print(f"    {l[:80]}")
                else:
                    print(f"  ⚠ Still CAPTCHA. Body[:200]: {r_real.text[:200]}")
        else:
            print(f"  ⚠ Challenge/Salt not found in page scripts")
            # Save full page for manual analysis
            with open("fp_v14_page.html", "w", encoding="utf-8") as f:
                f.write(html_fp)
            print(f"  Saved -> fp_v14_page.html")

except Exception as e:
    print(f"  [FARPOST] ERR: {e}")
    traceback.print_exc()


# ============================================================
print(f"\n{'='*70}")
print(f"3. WILDBERRIES — CDN + card API + search  [{elapsed()}]")
print("=" * 70)

test_nms = [135248485, 207573764, 199298871]

# 3a. card.wb.ru with curl_cffi (better TLS fingerprint)
if HAS_CFFI:
    s_wb_c = cffi_requests.Session(impersonate="chrome")
    for nm in test_nms[:2]:
        try:
            r = s_wb_c.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}",
                           headers={"User-Agent": UA, "Accept": "application/json",
                                    "Origin": "https://www.wildberries.ru",
                                    "Referer": "https://www.wildberries.ru/"},
                           timeout=(8, 15))
            print(f"  [card cffi nm={nm}] {r.status_code} | {len(r.content)}B [{elapsed()}]")
            if r.status_code == 200:
                data = r.json()
                cards = data.get("data", {}).get("cards", [])
                if cards:
                    c = cards[0]
                    print(f"  ✅ name={c.get('name','?')[:50]}")
                    with open(f"wb_v14_card_{nm}.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [card cffi nm={nm}] ERR: {str(e)[:80]}")
        time.sleep(1)

# 3b. CDN images — the key to getting photos
print(f"\n  --- CDN images --- [{elapsed()}]")
s_cdn = requests.Session()
s_cdn.headers.update({"User-Agent": UA, "Accept": "application/json"})

cdn_found = 0
for nm in test_nms:
    s = str(nm)
    found = False
    # vol/part combos (most common: vol=first3, part=first6)
    combos = [
        (s[:3], s[:6]),
        (s[:3], s[:7]),
        (s[:4], s[:7]),
        (s[:4], s[:8]),
        (s[:2], s[:5]),
    ]
    for vol, part in combos:
        for bn in [10, 20, 30]:
            # Try images endpoint
            url = f"https://static-basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/all-with-sizing.json"
            try:
                r = s_cdn.get(url, timeout=(4, 8))
                if r.status_code == 200:
                    data = r.json()
                    imgs = data if isinstance(data, list) else data.get("images", [])
                    print(f"  ✅ IMG nm={nm} vol={vol} part={part} bn={bn} | {len(imgs)} imgs [{elapsed()}]")
                    for i in imgs[:3]:
                        if isinstance(i, dict):
                            print(f"    {i.get('240x300v1', i.get('url','?'))[:80]}")
                        else:
                            print(f"    {str(i)[:80]}")
                    found = True
                    cdn_found += 1
                    break
            except:
                continue
        if found:
            break
    if not found:
        # Try info.json
        for vol, part in combos[:2]:
            for bn in [10, 20]:
                url = f"https://basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/{nm}-info.json"
                try:
                    r = s_cdn.get(url, timeout=(4, 8))
                    if r.status_code == 200:
                        data = r.json()
                        print(f"  ✅ INFO nm={nm} vol={vol} part={part} bn={bn} | keys={list(data.keys())[:6]} [{elapsed()}]")
                        found = True
                        cdn_found += 1
                        break
                except:
                    continue
            if found:
                break
    if not found:
        print(f"  ❌ nm={nm}: CDN not found")
    time.sleep(0.3)

print(f"  CDN found: {cdn_found}/{len(test_nms)}")

# 3c. WB search (product discovery)
print(f"\n  --- WB Search --- [{elapsed()}]")
s_wb_api = requests.Session()
s_wb_api.headers.update({
    "User-Agent": UA, "Accept": "application/json",
    "Origin": "https://www.wildberries.ru", "Referer": "https://www.wildberries.ru/",
})
try:
    r = s_wb_api.get("https://search.wb.ru/exactmatch/ru/common/v5/search",
                     params={"query": "телефон", "nm": "1", "resultset": "catalog",
                             "sort": "popular", "spp": "30"},
                     timeout=(8, 15))
    print(f"  [Search v5] {r.status_code} | {len(r.content)}B")
    if r.status_code == 200:
        prods = r.json().get("data", {}).get("products", [])
        print(f"  Products: {len(prods)}")
        for p in prods[:3]:
            print(f"    id={p.get('id')} name={p.get('name','?')[:40]}")
except Exception as e:
    print(f"  [Search v5] ERR: {str(e)[:80]}")


# ============================================================
print(f"\n{'='*70}")
print(f"4. OZON — challenge + composer + api  [{elapsed()}]")
print("=" * 70)

ozon_id = "1600562267"

# 4a. curl_cffi → get challenge page (8KB)
if HAS_CFFI:
    s_oz_c = cffi_requests.Session(impersonate="chrome")
    try:
        r = s_oz_c.get(f"https://www.ozon.ru/product/{ozon_id}/",
                       headers=hdr(), timeout=(8, 25))
        print(f"  [Ozon cffi] {r.status_code} | {len(r.content)}B [{elapsed()}]")

        if r.status_code == 403:
            ch_html = r.text
            with open("ozon_v14_challenge.html", "w", encoding="utf-8") as f:
                f.write(ch_html)
            print(f"  Saved -> ozon_v14_challenge.html")

            # Parse challenge mechanism
            # 1. Scripts
            scripts = re.findall(r'<script[^>]*>(.*?)</script>', ch_html, re.DOTALL)
            for i, s in enumerate(scripts):
                ss = s.strip()
                if len(ss) > 50:
                    print(f"  Script[{i}] ({len(ss)}): {ss[:300]}")
                    print()

            # 2. Data attributes
            data_attrs = re.findall(r'data-([a-z-]+)=["\']([^"\']+)["\']', ch_html)
            print(f"  Data attrs: {data_attrs[:10]}")

            # 3. Form
            form_m = re.search(r'<form[^>]*>(.*?)</form>', ch_html, re.DOTALL | re.I)
            if form_m:
                print(f"  Form: {form_m.group(0)[:400]}")

            # 4. Cookies
            print(f"  Cookies: {list(s_oz_c.cookies.keys())}")
            for ck_name, ck_val in s_oz_c.cookies.items():
                print(f"    {ck_name} = {ck_val[:60]}")

            # 5. Look for JS challenge: often sets a cookie then redirects
            # Pattern: document.cookie = "xxx=yyy"; location.reload()
            cookie_js = re.findall(r'document\.cookie\s*=\s*["\']([^"\']+)["\']', ch_html)
            print(f"  Cookie JS: {cookie_js[:5]}")

            # 6. Look for meta refresh
            meta_m = re.search(r'<meta[^>]*http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']+)["\']', ch_html, re.I)
            if meta_m:
                print(f"  Meta refresh: {meta_m.group(1)}")

        elif r.status_code == 200:
            print("  ✅ Ozon cffi direct!")
            with open("ozon_v14_product.html", "w", encoding="utf-8") as f:
                f.write(r.text)

    except Exception as e:
        print(f"  [Ozon cffi] ERR: {str(e)[:80]}")

# 4b. Composer with session (get cookies from main page first)
print(f"\n  --- Composer session --- [{elapsed()}]")
s_oz_s = requests.Session()
s_oz_s.headers.update(hdr())
try:
    r_main = s_oz_s.get("https://www.ozon.ru/", timeout=(8, 15), allow_redirects=True)
    print(f"  [Main] {r_main.status_code} | {len(r_main.content)}B | CK: {list(s_oz_s.cookies.keys())}")
    time.sleep(2)
    r_comp = s_oz_s.get(
        "https://www.ozon.ru/api/composer-api.bx/page/json/v2",
        params={"url": f"/product/{ozon_id}/"},
        headers={"Accept": "application/json", "Referer": f"https://www.ozon.ru/product/{ozon_id}/"},
        timeout=(8, 20)
    )
    print(f"  [Composer] {r_comp.status_code} | {r_comp.content[:200] if r_comp.status_code!=200 else r_comp.content[:100]}")
    if r_comp.status_code == 200:
        data = r_comp.json()
        print(f"  Keys: {list(data.keys())[:10]}")
        print("  ✅ Composer works!")
    elif r_comp.status_code == 403:
        try:
            cdata = r_comp.json()
            print(f"  challengeURL: {cdata.get('challengeURL','?')[:100]}")
            print(f"  incidentId: {cdata.get('incidentId','?')[:60]}")
        except:
            print(f"  Body: {r_comp.text[:200]}")
except Exception as e:
    print(f"  [Composer] ERR: {str(e)[:80]}")

# 4c. api.ozon.ru (different subdomain)
print(f"\n  --- api.ozon.ru --- [{elapsed()}]")
s_oz_api = requests.Session()
s_oz_api.headers.update({"User-Agent": UA, "Accept": "application/json", "Referer": "https://www.ozon.ru/"})
try:
    r = s_oz_api.get("https://api.ozon.ru/composer-api.bx/page/json/v2",
                     params={"url": f"/product/{ozon_id}/"},
                     timeout=(8, 20), verify=False)
    print(f"  [api.ozon.ru] {r.status_code} | {len(r.content)}B")
    if r.status_code == 200:
        print("  ✅ api.ozon.ru works!")
    elif r.status_code == 403:
        print(f"  Body: {r.text[:200]}")
except Exception as e:
    print(f"  [api.ozon.ru] ERR: {str(e)[:80]}")


# ============================================================
print(f"\n{'='*70}")
print(f"5. PROXIES — quick test  [{elapsed()}]")
print("=" * 70)

proxies = []
# Quick: hproxy + spys
for name, url in [("hproxy", "https://hproxy.com/free-proxy-list/russia"),
                  ("spys", "https://spys.one/free-proxy-list/all-countries/1/1/1/1/1/1/0/1/1/1/0/1.1")]:
    try:
        r = requests.get(url, headers=hdr(), timeout=10)
        if r.status_code == 200:
            pairs = re.findall(r'(\d+\.\d+\.\d+\.\d+):(\d{2,5})', r.text)
            added = 0
            for ip, port in pairs[:30]:
                if 1024 < int(port) < 65535:
                    proxies.append(f"http://{ip}:{port}")
                    added += 1
            print(f"  {name}: +{added}")
    except Exception as e:
        print(f"  {name} ERR: {str(e)[:50]}")

proxies = list(dict.fromkeys(proxies))
print(f"  Total: {len(proxies)}")

# Quick test: 20 proxies, 4s timeout
def quick_test(p):
    t0 = time.monotonic()
    try:
        r = requests.get("https://card.wb.ru/cards/v2/detail?nm=135248485",
                         proxies={"http": p, "https": p},
                         timeout=(2, 4), verify=False,
                         headers={"User-Agent": UA, "Accept": "application/json"})
        return p, f"WB-{r.status_code}", time.monotonic()-t0, len(r.content)
    except requests.exceptions.Timeout:
        return p, "TIMEOUT", time.monotonic()-t0, 0
    except Exception as e:
        return p, f"ERR:{type(e).__name__}", time.monotonic()-t0, 0

n = min(20, len(proxies))
print(f"  Testing {n} proxies...")
results = []
with ThreadPoolExecutor(max_workers=10) as pool:
    futs = {pool.submit(quick_test, p): p for p in proxies[:n]}
    for fut in as_completed(futs, timeout=12):
        url, status, lat, size = fut.result()
        results.append((url, status, lat, size))
        mark = "✅" if "200" in status else "❌"
        print(f"  {mark} {url} | {status} | {lat*1000:.0f}ms | {size}B")

alive = [x for x in results if "200" in x[1]]
print(f"  Alive: {len(alive)}/{n}")
for a in alive[:5]:
    print(f"    {a[0]} | {a[2]*1000:.0f}ms")

if alive:
    with open("proxy_v14_results.json", "w", encoding="utf-8") as f:
        json.dump([{"proxy": u, "status": s, "lat_ms": round(l*1000), "size": sz}
                   for u, s, l, sz in results], f, indent=2)
    print(f"  Saved -> proxy_v14_results.json")


# ============================================================
print(f"\n{'='*70}")
print(f"DONE — diag_v14  [{elapsed()}]")
print("=" * 70)

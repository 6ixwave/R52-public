"""Диагностика v15: целевая, лимит 180s.
1. Farpost: fetch challengeurl → solve PoW → POST token → verify
2. WB: search API + CDN (basket 1-50, parallel) + card.wb.ru
3. Avito: solve pow_challenge → retry card
4. Ozon: challenge page parse + composer
5. Quick proxy test (10 proxies, 3s timeout)
"""
import json, re, time, hashlib, base64, traceback, os, sys, struct
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

T_START = time.monotonic()
def elapsed():
    return f"{time.monotonic()-T_START:.1f}s"

def remaining():
    return 180 - (time.monotonic() - T_START)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

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
    if extra:
        h.update(extra)
    return h

def b64url_decode(s):
    s = s.replace("-", "+").replace("_", "/")
    while len(s) % 4:
        s += "="
    return base64.b64decode(s)


# ============================================================
print(f"\n{'='*70}")
print(f"1. FARPOST — fetch challengeurl + solve + POST  [{elapsed()}]")
print(f"   Remaining: {remaining():.0f}s")
print("=" * 70)

s_fp = requests.Session()
s_fp.headers.update(hdr())

try:
    # Step 1: GET page to get challengeurl
    r_fp = s_fp.get("https://www.farpost.ru/geo/moskva/l/telefony/", timeout=(8, 20))
    print(f"  [FP page] {r_fp.status_code} | {len(r_fp.content)}B [{elapsed()}]")
    html_fp = r_fp.text

    if "altcha" not in html_fp.lower():
        print("  ✅ No CAPTCHA! Direct access.")
        with open("fp_v15_real.html", "w", encoding="utf-8") as f:
            f.write(html_fp)
    else:
        # Extract challengeurl from <altcha-widget>
        ch_url_m = re.search(r'challengeurl="([^"]+)"', html_fp)
        if not ch_url_m:
            print("  ⚠ No challengeurl found")
        else:
            ch_url = ch_url_m.group(1).replace("&amp;", "&")
            print(f"  challengeurl: {ch_url}")

            # Build absolute URL
            if ch_url.startswith("?"):
                ch_abs = "https://www.farpost.ru/geo/moskva/l/telefony/" + ch_url
            elif ch_url.startswith("/"):
                ch_abs = "https://www.farpost.ru" + ch_url
            else:
                ch_abs = ch_url

            # Step 2: GET challengeurl → get challenge params
            r_ch = s_fp.get(ch_abs, timeout=(8, 15),
                            headers={"Accept": "application/json",
                                     "Referer": "https://www.farpost.ru/geo/moskva/l/telefony/"})
            print(f"  [Challenge] {r_ch.status_code} | {len(r_ch.content)}B [{elapsed()}]")
            print(f"  Content-Type: {r_ch.headers.get('Content-Type','?')}")
            print(f"  Body[:500]: {r_ch.text[:500]}")

            # Parse challenge JSON
            challenge_data = None
            try:
                challenge_data = r_ch.json()
                print(f"  JSON keys: {list(challenge_data.keys())}")
                print(f"  Challenge: {json.dumps(challenge_data, ensure_ascii=False)[:400]}")
            except:
                # Maybe it's in a script tag or HTML
                # Look for JSON in the response
                json_m = re.search(r'\{[^{}]*"algorithm"[^{}]*\}', r_ch.text)
                if json_m:
                    challenge_data = json.loads(json_m.group(0))
                    print(f"  JSON from HTML: {json.dumps(challenge_data, ensure_ascii=False)[:300]}")

            if challenge_data:
                algo = challenge_data.get("algorithm", "SHA-256")
                challenge = challenge_data.get("challenge", "")
                salt = challenge_data.get("salt", "")
                max_iter = int(challenge_data.get("max", 1000000))

                # Check if obfuscated (AES-GCM mode)
                if "obfuscated" in challenge_data:
                    print(f"  MODE: AES-GCM obfuscated")
                    key_b64 = challenge_data.get("key", "")
                    obf_b64 = challenge_data.get("obfuscated", "")
                    print(f"  Key: {key_b64[:60]}...")
                    print(f"  Obfuscated: {obf_b64[:60]}...")
                    # AES-GCM is complex; try for now
                    print(f"  ⚠ AES-GCM mode — need cryptography lib")
                else:
                    print(f"  MODE: Standard PoW")
                    print(f"  Algo: {algo}")
                    print(f"  Challenge: {challenge[:60]}...")
                    print(f"  Salt: {salt[:60]}...")
                    print(f"  Max: {max_iter}")

                    # Step 3: Solve PoW
                    # From worker code: hash(salt + str(nonce)) === challenge
                    # challenge is hex of target hash, salt is hex
                    t0 = time.monotonic()
                    nonce = None

                    # Try hex decode first
                    try:
                        salt_bytes = bytes.fromhex(salt)
                    except:
                        salt_bytes = salt.encode()
                    try:
                        target = bytes.fromhex(challenge)
                    except:
                        target = challenge.encode()

                    # PoW: find nonce where sha256(salt + nonce_str) == target
                    # From JS: b(t, c, r) = digest(algo, salt + nonce)
                    # where t=challenge, c=salt, r=algorithm
                    # Actually looking at JS: w(e,t,r,n,l) where e=challenge, t=salt
                    # b(t,c,r) = digest(algo, encode(t+c)) = digest(algo, salt+nonce)
                    for i in range(max_iter + 1):
                        h = hashlib.new(algo.lower().replace("-", ""), salt_bytes + str(i).encode()).digest()
                        if h == target:
                            nonce = i
                            break
                        if i % 100000 == 0 and i > 0:
                            print(f"    PoW: {i}/{max_iter} ({time.monotonic()-t0:.1f}s)")

                    pw_time = time.monotonic() - t0
                    print(f"  Nonce={nonce} ({pw_time:.2f}s) [{elapsed()}]")

                    if nonce is not None:
                        # Step 4: Build token
                        # From Altcha: token = base64(json({version:1, data:{algorithm,challenge,salt,nonce,max}}))
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

                        # Step 5: POST form
                        # Form action from page
                        form_action_m = re.search(r'<form[^>]*action="([^"]+)"', html_fp)
                        if form_action_m:
                            action = form_action_m.group(1).replace("&amp;", "&")
                        else:
                            action = ch_url

                        if action.startswith("?"):
                            post_url = "https://www.farpost.ru/geo/moskva/l/telefony/" + action
                        elif action.startswith("/"):
                            post_url = "https://www.farpost.ru" + action
                        else:
                            post_url = action

                        # Hidden fields
                        field_s = re.search(r'name="s"\s+value="([^"]*)"', html_fp)
                        field_t = re.search(r'name="t"\s+value="([^"]*)"', html_fp)

                        data = {"g-recaptcha-response": token, "code": ""}
                        if field_s:
                            data["s"] = field_s.group(1)
                        if field_t:
                            data["t"] = field_t.group(1)

                        print(f"  POST to: {post_url[:100]}")
                        r_post = s_fp.post(post_url, data=data, timeout=(8, 15),
                                           headers={"Referer": "https://www.farpost.ru/geo/moskva/l/telefony/",
                                                    "Content-Type": "application/x-www-form-urlencoded"})
                        print(f"  [POST] {r_post.status_code} | {len(r_post.content)}B")
                        print(f"  Set-Cookie: {r_post.headers.get('Set-Cookie','none')[:100]}")

                        # Step 6: Verify — GET the real page
                        time.sleep(1)
                        r_real = s_fp.get("https://www.farpost.ru/geo/moskva/l/telefony/",
                                          timeout=(8, 15))
                        print(f"  [Real] {r_real.status_code} | {len(r_real.content)}B [{elapsed()}]")

                        if r_real.status_code == 200 and "altcha" not in r_real.text.lower():
                            print("  ✅✅✅ FARPOST UNLOCKED!")
                            with open("fp_v15_real.html", "w", encoding="utf-8") as f:
                                f.write(r_real.text)
                            # Count ad links
                            links = re.findall(r'href="([^"]+)"', r_real.text)
                            ad_links = [l for l in links if re.search(r'/\d{5,}/', l)]
                            print(f"  Links: {len(links)}, ad-like: {len(ad_links)}")
                            for l in ad_links[:10]:
                                print(f"    {l[:80]}")
                        else:
                            print(f"  ⚠ Still CAPTCHA or error")
                            print(f"  Body[:300]: {r_real.text[:300]}")
                    else:
                        print(f"  ⚠ Nonce not found within max_iter")

except Exception as e:
    print(f"  [FARPOST] ERR: {e}")
    traceback.print_exc()


# ============================================================
if remaining() > 60:
    print(f"\n{'='*70}")
    print(f"2. WILDBERRIES — search + CDN parallel  [{elapsed()}]")
    print(f"   Remaining: {remaining():.0f}s")
    print("=" * 70)

    # 2a. Search API (product discovery + basic data)
    s_wb = requests.Session()
    s_wb.headers.update({
        "User-Agent": UA, "Accept": "application/json",
        "Origin": "https://www.wildberries.ru", "Referer": "https://www.wildberries.ru/",
    })
    try:
        r = s_wb.get("https://search.wb.ru/exactmatch/ru/common/v5/search",
                     params={"query": "телефон", "nm": "1", "resultset": "catalog",
                             "sort": "popular", "spp": "30"},
                     timeout=(6, 12))
        print(f"  [Search v5] {r.status_code} | {len(r.content)}B [{elapsed()}]")
        if r.status_code == 200:
            data = r.json()
            prods = data.get("data", {}).get("products", [])
            print(f"  Products: {len(prods)}")
            for p in prods[:5]:
                print(f"    id={p.get('id')} name={p.get('name','?')[:40]} price={p.get('totalPrice',{}).get('value','?')}")
            # Save first product's images field
            if prods:
                p0 = prods[0]
                imgs = p0.get("images", [])
                print(f"  Product[0] images: {len(imgs)}")
                for img in imgs[:3]:
                    print(f"    {img[:80]}")
                with open("wb_v15_search.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"  Saved -> wb_v15_search.json")
    except Exception as e:
        print(f"  [Search v5] ERR: {str(e)[:80]}")

    # 2b. CDN images — parallel brute force for 3 nms
    print(f"\n  --- CDN images (parallel) --- [{elapsed()}]")
    test_nms = [135248485, 207573764, 199298871]

    def try_cdn(nm):
        s = str(nm)
        # Generate all plausible vol/part combos
        combos = set()
        for vlen in [2, 3, 4]:
            for plen in [5, 6, 7, 8]:
                if vlen < len(s) and plen < len(s):
                    combos.add((s[:vlen], s[:plen]))
        # Also common pattern: vol=first3, part=first6
        combos.add((s[:3], s[:6]))

        for bn in range(1, 51):
            for vol, part in list(combos)[:8]:  # limit combos
                url = f"https://static-basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/all-with-sizing.json"
                try:
                    r = s_wb.get(url, timeout=(2, 5))
                    if r.status_code == 200:
                        data = r.json()
                        imgs = data if isinstance(data, list) else data.get("images", [])
                        return nm, f"IMG vol={vol} part={part} bn={bn}", len(imgs), imgs[:3]
                except:
                    continue
        return nm, "NOT_FOUND", 0, []

    cdn_results = []
    with ThreadPoolExecutor(max_workers=15) as pool:
        futs = {pool.submit(try_cdn, nm): nm for nm in test_nms}
        for fut in as_completed(futs, timeout=40):
            nm, status, count, samples = fut.result()
            cdn_results.append((nm, status, count))
            mark = "✅" if count > 0 else "❌"
            print(f"  {mark} nm={nm}: {status} | {count} imgs [{elapsed()}]")
            for s in samples:
                print(f"    {s[:80]}")

    # 2c. card.wb.ru with cffi (if still time)
    if remaining() > 20 and HAS_CFFI:
        print(f"\n  --- card.wb.ru (cffi) --- [{elapsed()}]")
        s_wb_c = cffi_requests.Session(impersonate="chrome")
        for nm in test_nms[:2]:
            try:
                r = s_wb_c.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}",
                               headers={"User-Agent": UA, "Accept": "application/json",
                                        "Origin": "https://www.wildberries.ru",
                                        "Referer": "https://www.wildberries.ru/"},
                               timeout=(5, 10))
                print(f"  [card nm={nm}] {r.status_code} | {len(r.content)}B")
                if r.status_code == 200:
                    data = r.json()
                    cards = data.get("data", {}).get("cards", [])
                    if cards:
                        c = cards[0]
                        print(f"    name={c.get('name','?')[:50]}")
                        print(f"    images={len(c.get('images',[]))}")
                        with open(f"wb_v15_card_{nm}.json", "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"  [card nm={nm}] ERR: {str(e)[:60]}")
            time.sleep(0.5)

# ============================================================
if remaining() > 40:
    print(f"\n{'='*70}")
    print(f"3. AVITO — solve PoW + retry  [{elapsed()}]")
    print(f"   Remaining: {remaining():.0f}s")
    print("=" * 70)

    if HAS_CFFI:
        s_av = cffi_requests.Session(impersonate="chrome")
        h_av = hdr({"Accept": "text/html"})
        try:
            r = s_av.get("https://www.avito.ru/moscow/telefony", headers=h_av, timeout=(8, 15))
            print(f"  [Avito] {r.status_code} | {len(r.content)}B [{elapsed()}]")
            cookies = dict(s_av.cookies)
            print(f"  Cookies: {list(cookies.keys())}")

            pow_ck = cookies.get("pow_challenge", "")
            if pow_ck:
                print(f"  pow_challenge: {pow_ck[:80]}...")
                # From JS: decode base64url → get {nonce, complexity, ...}
                # But in v14 it was 116 bytes binary with 0 dots (not JWT)
                # Let's try: the cookie IS the challenge, and we need to find nonce
                # where sha256(cookie + nonce) has N leading zero bits
                try:
                    decoded = b64url_decode(pow_ck)
                    print(f"  Decoded ({len(decoded)}B): {decoded[:40].hex()}")
                    # Maybe it's JSON
                    try:
                        j = json.loads(decoded)
                        print(f"  JSON: {json.dumps(j, ensure_ascii=False)[:300]}")
                    except:
                        pass
                    # Maybe it's a struct: first 4 bytes = complexity?
                    if len(decoded) >= 4:
                        complexity = struct.unpack('<I', decoded[:4])[0]
                        print(f"  Complexity (uint32 LE): {complexity}")
                        if 8 <= complexity <= 30:
                            # Solve: sha256(decoded + nonce_str) has `complexity` leading zero bits
                            target_mask = (1 << (32 - complexity)) - 1
                            t0 = time.monotonic()
                            nonce = None
                            for i in range(10_000_000):
                                h = hashlib.sha256(decoded + str(i).encode()).digest()
                                # Check leading zero bits
                                first_bytes = struct.unpack('>I', h[:4])[0]
                                if first_bytes >> complexity == 0:
                                    nonce = i
                                    break
                                if i % 500000 == 0 and i > 0:
                                    print(f"    AvPoW: {i} ({time.monotonic()-t0:.1f}s)")
                            pw_t = time.monotonic() - t0
                            print(f"  Nonce={nonce} ({pw_t:.2f}s)")

                            if nonce is not None:
                                # Set pow-response cookie
                                # From JS: setCookie('pow-response', btoa(nonce.toString()))
                                pow_response = base64.b64encode(str(nonce).encode()).decode()
                                s_av.cookies.set("pow-response", pow_response, domain="www.avito.ru")
                                print(f"  pow-response: {pow_response}")

                                time.sleep(2)
                                r2 = s_av.get("https://www.avito.ru/moscow/telefony",
                                               headers=h_av, timeout=(8, 15))
                                print(f"  [Retry] {r2.status_code} | {len(r2.content)}B [{elapsed()}]")
                                if r2.status_code == 200:
                                    print("  ✅ AVITO UNLOCKED!")
                                    with open("avito_v15_real.html", "w", encoding="utf-8") as f:
                                        f.write(r2.text)
                                elif r2.status_code == 439:
                                    print(f"  ⚠ Still 439. Body[:200]: {r2.text[:200]}")
                except Exception as e2:
                    print(f"  PoW decode ERR: {e2}")
            else:
                print(f"  ⚠ No pow_challenge cookie")
                # Maybe need to hit a specific card page
                r_card = s_av.get("https://www.avito.ru/moscow/telefony/135248485",
                                  headers=h_av, timeout=(8, 15))
                print(f"  [Card direct] {r_card.status_code} | {len(r_card.content)}B")
                cookies2 = dict(s_av.cookies)
                print(f"  Cookies2: {list(cookies2.keys())}")

        except Exception as e:
            print(f"  [Avito] ERR: {e}")
            traceback.print_exc()

# ============================================================
if remaining() > 25:
    print(f"\n{'='*70}")
    print(f"4. OZON — challenge + composer  [{elapsed()}]")
    print(f"   Remaining: {remaining():.0f}s")
    print("=" * 70)

    ozon_id = "1600562267"

    # 4a. cffi → product page
    if HAS_CFFI:
        s_oz = cffi_requests.Session(impersonate="chrome")
        try:
            r = s_oz.get(f"https://www.ozon.ru/product/{ozon_id}/",
                         headers=hdr(), timeout=(8, 20))
            print(f"  [Ozon cffi] {r.status_code} | {len(r.content)}B [{elapsed()}]")

            if r.status_code == 403:
                # Parse challenge
                ch_html = r.text
                with open("ozon_v15_challenge.html", "w", encoding="utf-8") as f:
                    f.write(ch_html)
                print(f"  Saved -> ozon_v15_challenge.html ({len(ch_html)}B)")

                # Look for challenge mechanism
                scripts = re.findall(r'<script[^>]*>(.*?)</script>', ch_html, re.DOTALL)
                for i, s in enumerate(scripts):
                    ss = s.strip()
                    if len(ss) > 50:
                        print(f"  Script[{i}] ({len(ss)}): {ss[:200]}")

                # Cookies
                print(f"  Cookies: {list(s_oz.cookies.keys())}")
                for cn, cv in s_oz.cookies.items():
                    print(f"    {cn} = {cv[:60]}")

                # Look for JS that sets cookie and reloads
                cookie_js = re.findall(r'document\.cookie\s*=\s*["\']([^"\']+)["\']', ch_html)
                print(f"  Cookie JS: {cookie_js[:5]}")

                # Look for fetch/XHR to challenge endpoint
                fetch_m = re.findall(r'(?:fetch|XMLHttpRequest|\.open)\s*\(\s*["\']([^"\']+)["\']', ch_html)
                print(f"  Fetch URLs: {fetch_m[:5]}")

            elif r.status_code == 200:
                print("  ✅ Ozon direct!")
                with open("ozon_v15_product.html", "w", encoding="utf-8") as f:
                    f.write(r.text)

        except Exception as e:
            print(f"  [Ozon cffi] ERR: {str(e)[:80]}")

    # 4b. Composer with session
    print(f"\n  --- Composer --- [{elapsed()}]")
    s_oz_s = requests.Session()
    s_oz_s.headers.update(hdr())
    try:
        r_main = s_oz_s.get("https://www.ozon.ru/", timeout=(6, 12), allow_redirects=True)
        print(f"  [Main] {r_main.status_code} | {len(r_main.content)}B | CK: {list(s_oz_s.cookies.keys())}")
        time.sleep(1)
        r_comp = s_oz_s.get(
            "https://www.ozon.ru/api/composer-api.bx/page/json/v2",
            params={"url": f"/product/{ozon_id}/"},
            headers={"Accept": "application/json",
                     "Referer": f"https://www.ozon.ru/product/{ozon_id}/"},
            timeout=(6, 12)
        )
        print(f"  [Composer] {r_comp.status_code} | {len(r_comp.content)}B")
        if r_comp.status_code == 200:
            data = r_comp.json()
            print(f"  Keys: {list(data.keys())[:10]}")
            print("  ✅ Composer works!")
            with open("ozon_v15_composer.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        elif r_comp.status_code == 403:
            try:
                cdata = r_comp.json()
                print(f"  challengeURL: {cdata.get('challengeURL','?')[:100]}")
                print(f"  incidentId: {cdata.get('incidentId','?')[:60]}")
            except:
                print(f"  Body[:200]: {r_comp.text[:200]}")
    except Exception as e:
        print(f"  [Composer] ERR: {str(e)[:80]}")

# ============================================================
if remaining() > 15:
    print(f"\n{'='*70}")
    print(f"5. PROXIES — quick 10-test  [{elapsed()}]")
    print(f"   Remaining: {remaining():.0f}s")
    print("=" * 70)

    proxies = []
    for name, url in [("hproxy", "https://hproxy.com/free-proxy-list/russia"),
                      ("spys", "https://spys.one/free-proxy-list/all-countries/1/1/1/1/1/1/0/1/1/1/0/1.1")]:
        try:
            r = requests.get(url, headers=hdr(), timeout=8)
            if r.status_code == 200:
                pairs = re.findall(r'(\d+\.\d+\.\d+\.\d+):(\d{2,5})', r.text)
                added = 0
                for ip, port in pairs[:20]:
                    if 1024 < int(port) < 65535:
                        proxies.append(f"http://{ip}:{port}")
                        added += 1
                print(f"  {name}: +{added}")
        except Exception as e:
            print(f"  {name} ERR: {str(e)[:50]}")

    proxies = list(dict.fromkeys(proxies))[:10]
    print(f"  Testing {len(proxies)} proxies...")

    def quick_test(p):
        t0 = time.monotonic()
        try:
            r = requests.get("https://card.wb.ru/cards/v2/detail?nm=135248485",
                             proxies={"http": p, "https": p},
                             timeout=(2, 3), verify=False,
                             headers={"User-Agent": UA, "Accept": "application/json"})
            return p, f"WB-{r.status_code}", time.monotonic()-t0, len(r.content)
        except requests.exceptions.Timeout:
            return p, "TIMEOUT", time.monotonic()-t0, 0
        except Exception as e:
            return p, f"ERR:{type(e).__name__}", time.monotonic()-t0, 0

    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(quick_test, p): p for p in proxies}
        for fut in as_completed(futs, timeout=10):
            url, status, lat, size = fut.result()
            results.append((url, status, lat, size))
            mark = "✅" if "200" in status else "❌"
            print(f"  {mark} {url} | {status} | {lat*1000:.0f}ms | {size}B")

    alive = [x for x in results if "200" in x[1]]
    print(f"  Alive: {len(alive)}/{len(proxies)}")

# ============================================================
print(f"\n{'='*70}")
print(f"DONE — diag_v15  [{elapsed()}]")
print("=" * 70)

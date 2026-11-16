"""Диагностика v16: 240s, фокус на рабочих механизмах.
1. Farpost: challengeurl с cookie, GET/POST, f=1/2/3
2. WB: CDN parallel (50 baskets × 8 combos) + search + card
3. Avito: PoW solve (binary cookie → nonce → pow-response)
4. Ozon: challenge page + composer session
"""
import json, re, time, hashlib, base64, traceback, os, sys, struct
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

T_START = time.monotonic()
LIMIT = 240
def elapsed():
    return f"{time.monotonic()-T_START:.1f}s"
def remaining():
    return LIMIT - (time.monotonic() - T_START)

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
print(f"1. FARPOST — challengeurl deep  [{elapsed()}] rem={remaining():.0f}s")
print("=" * 70)

s_fp = requests.Session()
s_fp.headers.update(hdr())
fp_base = "https://www.farpost.ru/geo/moskva/l/telefony/"

try:
    r_fp = s_fp.get(fp_base, timeout=(8, 18))
    print(f"  [FP page] {r_fp.status_code} | {len(r_fp.content)}B [{elapsed()}]")
    html_fp = r_fp.text
    cookies_fp = dict(s_fp.cookies)
    print(f"  Cookies: {list(cookies_fp.keys())}")

    if "altcha" not in html_fp.lower():
        print("  ✅ No CAPTCHA!")
        with open("fp_v16_real.html", "w", encoding="utf-8") as f:
            f.write(html_fp)
    else:
        ch_url_m = re.search(r'challengeurl="([^"]+)"', html_fp)
        ch_url = ch_url_m.group(1).replace("&amp;", "&") if ch_url_m else ""
        print(f"  challengeurl: {ch_url}")

        # Build absolute
        if ch_url.startswith("?"):
            ch_abs = fp_base + ch_url
        elif ch_url.startswith("/"):
            ch_abs = "https://www.farpost.ru" + ch_url
        else:
            ch_abs = ch_url

        # Try multiple approaches to get challenge
        approaches = [
            ("GET json", {"Accept": "application/json", "Referer": fp_base}),
            ("GET html", {"Accept": "text/html", "Referer": fp_base}),
            ("GET xhr", {"Accept": "*/*", "Referer": fp_base, "X-Requested-With": "XMLHttpRequest"}),
            ("POST empty", {"Accept": "application/json", "Referer": fp_base, "Content-Type": "application/x-www-form-urlencoded"}),
        ]

        challenge_data = None
        for label, extra_h in approaches:
            try:
                if "POST" in label:
                    r_ch = s_fp.post(ch_abs, data={}, headers=extra_h, timeout=(5, 10))
                else:
                    r_ch = s_fp.get(ch_abs, headers=extra_h, timeout=(5, 10))
                ct = r_ch.headers.get("Content-Type", "")
                print(f"  [{label}] {r_ch.status_code} | {len(r_ch.content)}B | CT={ct[:40]}")
                if r_ch.status_code == 200 and len(r_ch.content) > 50:
                    # Try JSON
                    try:
                        jd = r_ch.json()
                        if "challenge" in jd or "algorithm" in jd or "obfuscated" in jd:
                            challenge_data = jd
                            print(f"  ✅ Challenge JSON: {json.dumps(jd, ensure_ascii=False)[:300]}")
                            break
                    except:
                        pass
                    # Maybe HTML with embedded JSON
                    jm = re.search(r'\{[^{}]*"(?:algorithm|challenge|obfuscated)"[^{}]*\}', r_ch.text)
                    if jm:
                        try:
                            challenge_data = json.loads(jm.group(0))
                            print(f"  ✅ Challenge from HTML: {json.dumps(challenge_data, ensure_ascii=False)[:300]}")
                            break
                        except:
                            pass
                    print(f"    Body[:200]: {r_ch.text[:200]}")
            except Exception as e:
                print(f"  [{label}] ERR: {str(e)[:60]}")
            time.sleep(0.5)

        # If still no challenge, try f=1 (fallback)
        if not challenge_data:
            print(f"  Trying f=1 fallback... [{elapsed()}]")
            f1_url = fp_base + "?r=1&u=%2Fgeo%2Fmoskva%2Fl%2Ftelefony%2F&f=1"
            r_f1 = s_fp.get(f1_url, headers={"Accept": "text/html", "Referer": fp_base}, timeout=(5, 10))
            print(f"  [f=1] {r_f1.status_code} | {len(r_f1.content)}B")
            if "altcha" not in r_f1.text.lower():
                print("  ✅ f=1 gives real page!")
                with open("fp_v16_real.html", "w", encoding="utf-8") as f:
                    f.write(r_f1.text)
                challenge_data = "DIRECT"
            else:
                # Extract new challengeurl from f=1 page
                ch2 = re.search(r'challengeurl="([^"]+)"', r_f1.text)
                if ch2:
                    ch2_url = ch2.group(1).replace("&amp;", "&")
                    print(f"  New challengeurl: {ch2_url}")
                    ch2_abs = fp_base + ch2_url if ch2_url.startswith("?") else ch2_url
                    r_ch2 = s_fp.get(ch2_abs, headers={"Accept": "application/json", "Referer": fp_base}, timeout=(5, 10))
                    print(f"  [ch2] {r_ch2.status_code} | {len(r_ch2.content)}B | CT={r_ch2.headers.get('Content-Type','?')[:40]}")
                    try:
                        challenge_data = r_ch2.json()
                        print(f"  ✅ ch2 JSON: {json.dumps(challenge_data, ensure_ascii=False)[:300]}")
                    except:
                        print(f"    Body[:200]: {r_ch2.text[:200]}")

        # Solve if we have challenge
        if challenge_data and challenge_data != "DIRECT":
            if isinstance(challenge_data, dict):
                if "obfuscated" in challenge_data:
                    print(f"  MODE: AES-GCM (need cryptography lib)")
                    key_b64 = challenge_data.get("key", "")
                    obf_b64 = challenge_data.get("obfuscated", "")
                    max_iter = int(challenge_data.get("max", 1000000))
                    print(f"  Key: {key_b64[:60]}")
                    print(f"  Obf: {obf_b64[:60]}")
                    print(f"  Max: {max_iter}")
                    # Try to solve AES-GCM
                    try:
                        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                        key = base64.b64decode(key_b64)
                        obf = base64.b64decode(obf_b64)
                        # From JS: iv = g(i) = 12-byte LE encoding of i
                        # key derived from: sha256(salt) where salt is the key string
                        # Actually from JS: u=h(e) where e=key_b64, then:
                        # i = await crypto.subtle.digest("SHA-256", d.encode(t)) where t=""
                        # c = await crypto.subtle.importKey("raw", i, {name:"AES-GCM"}, false, ["decrypt"])
                        # So: actual_key = sha256(key_b64_string_as_utf8)
                        actual_key = hashlib.sha256(key_b64.encode()).digest()
                        aes = AESGCM(actual_key)
                        t0 = time.monotonic()
                        nonce_found = None
                        for i in range(max_iter + 1):
                            iv = struct.pack('<12s', struct.pack('<Q', i).ljust(8, b'\x00'))
                            # Actually from JS g(e,t=12): 12-byte LE
                            iv = (i).to_bytes(12, 'little')
                            try:
                                pt = aes.decrypt(iv, obf, None)
                                if pt:
                                    nonce_found = i
                                    break
                            except:
                                continue
                            if i % 200000 == 0 and i > 0:
                                print(f"    AES: {i}/{max_iter} ({time.monotonic()-t0:.1f}s)")
                        pw_t = time.monotonic() - t0
                        print(f"  AES Nonce={nonce_found} ({pw_t:.2f}s)")
                    except ImportError:
                        print(f"  ⚠ cryptography lib not available")
                    except Exception as e:
                        print(f"  AES ERR: {e}")
                else:
                    algo = challenge_data.get("algorithm", "SHA-256")
                    challenge = challenge_data.get("challenge", "")
                    salt = challenge_data.get("salt", "")
                    max_iter = int(challenge_data.get("max", 1000000))
                    print(f"  MODE: PoW | algo={algo} max={max_iter}")
                    print(f"  Challenge: {challenge[:60]}")
                    print(f"  Salt: {salt[:60]}")

                    try:
                        salt_bytes = bytes.fromhex(salt)
                    except:
                        salt_bytes = salt.encode()
                    try:
                        target = bytes.fromhex(challenge)
                    except:
                        target = challenge.encode()

                    t0 = time.monotonic()
                    nonce = None
                    for i in range(max_iter + 1):
                        h = hashlib.new(algo.lower().replace("-", ""), salt_bytes + str(i).encode()).digest()
                        if h == target:
                            nonce = i
                            break
                        if i % 100000 == 0 and i > 0:
                            print(f"    PoW: {i}/{max_iter} ({time.monotonic()-t0:.1f}s)")
                    pw_t = time.monotonic() - t0
                    print(f"  Nonce={nonce} ({pw_t:.2f}s) [{elapsed()}]")

                    if nonce is not None:
                        token_data = {"version": 1, "data": {"algorithm": algo, "challenge": challenge,
                                                             "salt": salt, "nonce": nonce, "max": max_iter}}
                        token = base64.b64encode(json.dumps(token_data).encode()).decode()
                        print(f"  Token: {token[:80]}...")

                        # POST form
                        form_action = re.search(r'<form[^>]*action="([^"]+)"', html_fp)
                        action = form_action.group(1).replace("&amp;", "&") if form_action else ch_url
                        post_url = fp_base + action if action.startswith("?") else ("https://www.farpost.ru" + action if action.startswith("/") else action)

                        field_s = re.search(r'name="s"\s+value="([^"]*)"', html_fp)
                        field_t = re.search(r'name="t"\s+value="([^"]*)"', html_fp)
                        data = {"[REDACTED_AUTH_SECRET]": token, "code": ""}
                        if field_s: data["s"] = field_s.group(1)
                        if field_t: data["t"] = field_t.group(1)

                        r_post = s_fp.post(post_url, data=data, timeout=(8, 15),
                                           headers={"Referer": fp_base, "Content-Type": "application/x-www-form-urlencoded"})
                        print(f"  [POST] {r_post.status_code} | {len(r_post.content)}B")

                        time.sleep(1)
                        r_real = s_fp.get(fp_base, timeout=(8, 15))
                        print(f"  [Real] {r_real.status_code} | {len(r_real.content)}B [{elapsed()}]")
                        if r_real.status_code == 200 and "altcha" not in r_real.text.lower():
                            print("  ✅✅✅ FARPOST UNLOCKED!")
                            with open("fp_v16_real.html", "w", encoding="utf-8") as f:
                                f.write(r_real.text)
                            links = re.findall(r'href="([^"]+)"', r_real.text)
                            ad_links = [l for l in links if re.search(r'/\d{5,}/', l)]
                            print(f"  Ad links: {len(ad_links)}")
                            for l in ad_links[:5]:
                                print(f"    {l[:80]}")
                        else:
                            print(f"  ⚠ Still CAPTCHA. Body[:200]: {r_real.text[:200]}")

except Exception as e:
    print(f"  [FARPOST] ERR: {e}")
    traceback.print_exc()


# ============================================================
if remaining() > 70:
    print(f"\n{'='*70}")
    print(f"2. WILDBERRIES — CDN + search + card  [{elapsed()}] rem={remaining():.0f}s")
    print("=" * 70)

    s_wb = requests.Session()
    s_wb.headers.update({"User-Agent": UA, "Accept": "application/json",
                         "Origin": "https://www.wildberries.ru", "Referer": "https://www.wildberries.ru/"})

    # 2a. Search
    try:
        r = s_wb.get("https://search.wb.ru/exactmatch/ru/common/v5/search",
                     params={"query": "телефон", "nm": "1", "resultset": "catalog",
                             "sort": "popular", "spp": "30"},
                     timeout=(5, 10))
        print(f"  [Search v5] {r.status_code} | {len(r.content)}B [{elapsed()}]")
        if r.status_code == 200:
            prods = r.json().get("data", {}).get("products", [])
            print(f"  Products: {len(prods)}")
            for p in prods[:3]:
                print(f"    id={p.get('id')} name={p.get('name','?')[:40]}")
            with open("wb_v16_search.json", "w", encoding="utf-8") as f:
                json.dump(r.json(), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [Search v5] ERR: {str(e)[:80]}")

    # 2b. CDN parallel — the key
    print(f"\n  --- CDN images (parallel, 50 baskets) --- [{elapsed()}]")
    test_nms = [135248485, 207573764, 199298871]

    def try_cdn(nm):
        s = str(nm)
        combos = [(s[:3], s[:6]), (s[:3], s[:7]), (s[:4], s[:7]), (s[:4], s[:8]),
                  (s[:2], s[:5]), (s[:5], s[:8]), (s[:6], s[:9])]
        for bn in range(1, 51):
            for vol, part in combos:
                if len(vol) >= len(s) or len(part) >= len(s):
                    continue
                url = f"https://static-basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/all-with-sizing.json"
                try:
                    r = s_wb.get(url, timeout=(2, 4))
                    if r.status_code == 200:
                        data = r.json()
                        imgs = data if isinstance(data, list) else data.get("images", [])
                        return nm, f"IMG vol={vol} part={part} bn={bn}", len(imgs), imgs[:3]
                except:
                    continue
        # Try info.json
        for bn in range(1, 51):
            for vol, part in combos[:4]:
                if len(vol) >= len(s) or len(part) >= len(s):
                    continue
                url = f"https://basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/{nm}-info.json"
                try:
                    r = s_wb.get(url, timeout=(2, 4))
                    if r.status_code == 200:
                        data = r.json()
                        return nm, f"INFO vol={vol} part={part} bn={bn}", len(data), list(data.keys())[:5]
                except:
                    continue
        return nm, "NOT_FOUND", 0, []

    cdn_results = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futs = {pool.submit(try_cdn, nm): nm for nm in test_nms}
        for fut in as_completed(futs, timeout=50):
            nm, status, count, samples = fut.result()
            cdn_results.append((nm, status, count))
            mark = "✅" if count > 0 else "❌"
            print(f"  {mark} nm={nm}: {status} | {count} [{elapsed()}]")
            for s in samples:
                print(f"    {str(s)[:80]}")

    # 2c. card.wb.ru
    if remaining() > 25 and HAS_CFFI:
        print(f"\n  --- card.wb.ru (cffi) --- [{elapsed()}]")
        s_wb_c = cffi_requests.Session(impersonate="chrome")
        for nm in test_nms[:2]:
            try:
                r = s_wb_c.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}",
                               headers={"User-Agent": UA, "Accept": "application/json",
                                        "Origin": "https://www.wildberries.ru",
                                        "Referer": "https://www.wildberries.ru/"},
                               timeout=(4, 8))
                print(f"  [card nm={nm}] {r.status_code} | {len(r.content)}B")
                if r.status_code == 200:
                    data = r.json()
                    cards = data.get("data", {}).get("cards", [])
                    if cards:
                        c = cards[0]
                        print(f"    name={c.get('name','?')[:50]} imgs={len(c.get('images',[]))}")
                        with open(f"wb_v16_card_{nm}.json", "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"  [card nm={nm}] ERR: {str(e)[:60]}")
            time.sleep(0.5)


# ============================================================
if remaining() > 50:
    print(f"\n{'='*70}")
    print(f"3. AVITO — PoW solver  [{elapsed()}] rem={remaining():.0f}s")
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
                print(f"  pow_challenge ({len(pow_ck)} chars): {pow_ck[:80]}...")
                decoded = b64url_decode(pow_ck)
                print(f"  Decoded ({len(decoded)}B): {decoded[:32].hex()}")

                # Strategy: the JS does:
                # 1. getCookie('pow_challenge') → base64url string
                # 2. b64DecodeUnicode → raw bytes
                # 3. parseJwtPayload (split by '.') — but we saw 0 dots
                # 4. If not JWT, maybe the whole decoded IS the challenge
                # 5. startPow(1) → complexity=1?
                # From avito_v12_js_0.js: startPow(1) means complexity=1
                # With complexity=1, we need sha256 to have 1 leading zero bit
                # That's trivial: half of all nonces work!

                # Actually re-reading the JS more carefully:
                # The cookie IS the challenge (base64url encoded)
                # The PoW: find nonce where sha256(decoded_bytes + nonce_str) has N leading zero bits
                # N = complexity from the cookie or default

                # Let's try: complexity from first 4 bytes as uint32
                complexity = 10  # default
                if len(decoded) >= 4:
                    c4 = struct.unpack('<I', decoded[:4])[0]
                    if 4 <= c4 <= 24:
                        complexity = c4
                        print(f"  Complexity from bytes: {complexity}")
                    else:
                        print(f"  First 4B as uint32: {c4} (not in range, using default 10)")

                # Also try: maybe the decoded is JSON
                try:
                    j = json.loads(decoded)
                    print(f"  JSON: {json.dumps(j, ensure_ascii=False)[:300]}")
                    if "complexity" in j:
                        complexity = j["complexity"]
                except:
                    pass

                print(f"  Solving PoW: complexity={complexity}")
                t0 = time.monotonic()
                nonce = None
                # sha256(decoded + str(i)) → first `complexity` bits must be 0
                for i in range(50_000_000):
                    h = hashlib.sha256(decoded + str(i).encode()).digest()
                    # Check leading zero bits: first byte's top bits
                    # complexity bits from the start
                    byte_idx = complexity // 8
                    bit_offset = complexity % 8
                    if byte_idx < len(h):
                        if bit_offset == 0:
                            ok = all(h[j] == 0 for j in range(byte_idx))
                        else:
                            ok = all(h[j] == 0 for j in range(byte_idx)) and (h[byte_idx] >> (8 - bit_offset)) == 0
                    else:
                        ok = all(h[j] == 0 for j in range(byte_idx))
                    if ok:
                        nonce = i
                        break
                    if i % 1_000_000 == 0 and i > 0:
                        print(f"    AvPoW: {i} ({time.monotonic()-t0:.1f}s)")
                pw_t = time.monotonic() - t0
                print(f"  Nonce={nonce} ({pw_t:.2f}s) [{elapsed()}]")

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
                        with open("avito_v16_real.html", "w", encoding="utf-8") as f:
                            f.write(r2.text)
                    elif r2.status_code in (429, 439):
                        print(f"  ⚠ Still {r2.status_code}. Body[:200]: {r2.text[:200]}")
                    # Try a card
                    r3 = s_av.get("https://www.avito.ru/moscow/telefony/135248485",
                                   headers=h_av, timeout=(8, 15))
                    print(f"  [Card] {r3.status_code} | {len(r3.content)}B")
                    if r3.status_code == 200:
                        with open("avito_v16_card.html", "w", encoding="utf-8") as f:
                            f.write(r3.text)
                        print("  ✅ Card saved!")
                else:
                    print(f"  ⚠ Nonce not found in 50M")
            else:
                print(f"  ⚠ No pow_challenge")
        except Exception as e:
            print(f"  [Avito] ERR: {e}")
            traceback.print_exc()


# ============================================================
if remaining() > 30:
    print(f"\n{'='*70}")
    print(f"4. OZON — challenge + composer  [{elapsed()}] rem={remaining():.0f}s")
    print("=" * 70)

    ozon_id = "1600562267"

    if HAS_CFFI:
        s_oz = cffi_requests.Session(impersonate="chrome")
        try:
            r = s_oz.get(f"https://www.ozon.ru/product/{ozon_id}/",
                         headers=hdr(), timeout=(8, 18))
            print(f"  [Ozon cffi] {r.status_code} | {len(r.content)}B [{elapsed()}]")
            if r.status_code == 403:
                ch_html = r.text
                with open("ozon_v16_challenge.html", "w", encoding="utf-8") as f:
                    f.write(ch_html)
                print(f"  Saved challenge ({len(ch_html)}B)")
                # Parse
                scripts = re.findall(r'<script[^>]*>(.*?)</script>', ch_html, re.DOTALL)
                for i, s in enumerate(scripts):
                    ss = s.strip()
                    if len(ss) > 50:
                        print(f"  Script[{i}] ({len(ss)}): {ss[:200]}")
                print(f"  Cookies: {list(s_oz.cookies.keys())}")
                for cn, cv in s_oz.cookies.items():
                    print(f"    {cn} = {cv[:60]}")
                # Look for fetch/XHR
                fetch_m = re.findall(r'(?:fetch|\.open|XMLHttpRequest)\s*\(\s*["\']([^"\']+)["\']', ch_html)
                print(f"  Fetch: {fetch_m[:5]}")
            elif r.status_code == 200:
                print("  ✅ Ozon direct!")
                with open("ozon_v16_product.html", "w", encoding="utf-8") as f:
                    f.write(r.text)
        except Exception as e:
            print(f"  [Ozon cffi] ERR: {str(e)[:80]}")

    # Composer
    print(f"\n  --- Composer --- [{elapsed()}]")
    s_oz_s = requests.Session()
    s_oz_s.headers.update(hdr())
    try:
        r_main = s_oz_s.get("https://www.ozon.ru/", timeout=(5, 10), allow_redirects=True)
        print(f"  [Main] {r_main.status_code} | {len(r_main.content)}B | CK: {list(s_oz_s.cookies.keys())}")
        time.sleep(1)
        r_comp = s_oz_s.get(
            "https://www.ozon.ru/api/composer-api.bx/page/json/v2",
            params={"url": f"/product/{ozon_id}/"},
            headers={"Accept": "application/json", "Referer": f"https://www.ozon.ru/product/{ozon_id}/"},
            timeout=(5, 10)
        )
        print(f"  [Composer] {r_comp.status_code} | {len(r_comp.content)}B")
        if r_comp.status_code == 200:
            data = r_comp.json()
            print(f"  Keys: {list(data.keys())[:10]}")
            print("  ✅ Composer works!")
            with open("ozon_v16_composer.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        elif r_comp.status_code == 403:
            try:
                cdata = r_comp.json()
                print(f"  challengeURL: {cdata.get('challengeURL','?')[:100]}")
            except:
                print(f"  Body[:200]: {r_comp.text[:200]}")
    except Exception as e:
        print(f"  [Composer] ERR: {str(e)[:80]}")


# ============================================================
print(f"\n{'='*70}")
print(f"DONE — diag_v16  [{elapsed()}]")
print("=" * 70)

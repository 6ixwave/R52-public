"""Диагностика v17: 150s, оптимизированная.
1. Farpost: cffi + immediate challenge fetch (timestamp-sensitive)
2. WB: CDN parallel (all 50 baskets × top 3 combos) + search
3. Avito: PoW (quick, complexity auto-detect)
4. Ozon: composer + cffi product
"""
import json, re, time, hashlib, base64, traceback, os, sys, struct
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

T_START = time.monotonic()
LIMIT = 150
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
print(f"1. FARPOST — cffi + immediate challenge  [{elapsed()}] rem={remaining():.0f}s")
print("=" * 70)

fp_base = "https://www.farpost.ru/geo/moskva/l/telefony/"
fp_unlocked = False

if HAS_CFFI:
    s_fp = cffi_requests.Session(impersonate="chrome")
else:
    s_fp = requests.Session()
    s_fp.headers.update(hdr())

try:
    # Load page
    r_fp = s_fp.get(fp_base, headers=hdr(), timeout=(6, 15))
    print(f"  [FP page] {r_fp.status_code} | {len(r_fp.content)}B [{elapsed()}]")
    html_fp = r_fp.text
    cookies_fp = dict(s_fp.cookies)
    print(f"  Cookies: {list(cookies_fp.keys())}")

    if "altcha" not in html_fp.lower():
        print("  ✅ No CAPTCHA — direct access!")
        fp_unlocked = True
        with open("fp_v17_real.html", "w", encoding="utf-8") as f:
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

        # IMMEDIATE fetch — the `a` timestamp may expire
        t_fetch = time.monotonic()
        r_ch = s_fp.get(ch_abs, headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": fp_base,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }, timeout=(5, 10))
        print(f"  [Challenge fetch] {r_ch.status_code} | {len(r_ch.content)}B | CT={r_ch.headers.get('Content-Type','?')[:50]} [{time.monotonic()-t_fetch:.2f}s after page]")

        challenge_data = None
        if r_ch.status_code == 200:
            try:
                challenge_data = r_ch.json()
                print(f"  ✅ Challenge JSON: {json.dumps(challenge_data, ensure_ascii=False)[:400]}")
            except:
                # Try to find JSON in response
                jm = re.search(r'\{[^{}]*"(?:algorithm|challenge|obfuscated|key)"[^{}]*\}', r_ch.text)
                if jm:
                    try:
                        challenge_data = json.loads(jm.group(0))
                        print(f"  ✅ Challenge from text: {json.dumps(challenge_data, ensure_ascii=False)[:300]}")
                    except:
                        pass
                if not challenge_data:
                    print(f"  Body[:400]: {r_ch.text[:400]}")
        else:
            print(f"  Body[:300]: {r_ch.text[:300]}")
            # Try: maybe the challenge is in a sub-resource
            # Look for /altcha/ or /challenge/ endpoints in the page
            alt_paths = re.findall(r'(?:src|href|url)\s*[=:]\s*["\']([^"\']*(?:altcha|challenge|captcha|verify)[^"\']*)["\']', html_fp, re.I)
            print(f"  Alt paths in page: {alt_paths[:5]}")

            # Try direct /altcha/ endpoint
            for ep in ["https://www.farpost.ru/altcha/challenge",
                       "https://www.farpost.ru/geo/moskva/l/telefony/altcha",
                       "https://www.farpost.ru/verify/challenge"]:
                try:
                    r_e = s_fp.get(ep, headers={"Accept": "application/json", "Referer": fp_base}, timeout=(3, 6))
                    print(f"  [{ep[-30:]}] {r_e.status_code} | {len(r_e.content)}B")
                    if r_e.status_code == 200:
                        try:
                            challenge_data = r_e.json()
                            print(f"  ✅ Endpoint challenge: {json.dumps(challenge_data, ensure_ascii=False)[:300]}")
                            break
                        except:
                            pass
                except:
                    continue

        # If still no challenge, try reloading page and fetching immediately
        if not challenge_data:
            print(f"  Reloading page for fresh challenge... [{elapsed()}]")
            r_fp2 = s_fp.get(fp_base, headers=hdr(), timeout=(6, 12))
            html_fp2 = r_fp2.text
            ch2_m = re.search(r'challengeurl="([^"]+)"', html_fp2)
            if ch2_m:
                ch2_url = ch2_m.group(1).replace("&amp;", "&")
                ch2_abs = fp_base + ch2_url if ch2_url.startswith("?") else ch2_url
                # Fetch within 0.5s
                r_ch2 = s_fp.get(ch2_abs, headers={
                    "Accept": "*/*",
                    "Referer": fp_base,
                    "X-Requested-With": "XMLHttpRequest",
                }, timeout=(4, 8))
                print(f"  [Ch2] {r_ch2.status_code} | {len(r_ch2.content)}B | CT={r_ch2.headers.get('Content-Type','?')[:40]}")
                if r_ch2.status_code == 200:
                    try:
                        challenge_data = r_ch2.json()
                        print(f"  ✅ Ch2 JSON: {json.dumps(challenge_data, ensure_ascii=False)[:300]}")
                    except:
                        print(f"  Ch2 Body[:200]: {r_ch2.text[:200]}")

        # Solve challenge
        if challenge_data and isinstance(challenge_data, dict):
            if "obfuscated" in challenge_data:
                print(f"  MODE: AES-GCM")
                key_b64 = challenge_data.get("key", "")
                obf_b64 = challenge_data.get("obfuscated", "")
                max_iter = int(challenge_data.get("max", 1000000))
                print(f"  Key: {key_b64[:60]}")
                print(f"  Max: {max_iter}")

                try:
                    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                    key = base64.b64decode(key_b64)
                    obf = base64.b64decode(obf_b64)
                    actual_key = hashlib.sha256(key_b64.encode()).digest()
                    aes = AESGCM(actual_key)
                    t0 = time.monotonic()
                    nonce_found = None
                    for i in range(max_iter + 1):
                        iv = i.to_bytes(12, 'little')
                        try:
                            pt = aes.decrypt(iv, obf, None)
                            if pt:
                                nonce_found = i
                                break
                        except:
                            continue
                        if i % 200000 == 0 and i > 0:
                            print(f"    AES: {i}/{max_iter} ({time.monotonic()-t0:.1f}s)")
                    print(f"  AES Nonce={nonce_found} ({time.monotonic()-t0:.1f}s)")
                    if nonce_found is not None:
                        fp_unlocked = True
                except ImportError:
                    print(f"  ⚠ cryptography not installed")
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
                print(f"  Nonce={nonce} ({time.monotonic()-t0:.1f}s)")

                if nonce is not None:
                    token_data = {"version": 1, "data": {"algorithm": algo, "challenge": challenge,
                                                         "salt": salt, "nonce": nonce, "max": max_iter}}
                    token = base64.b64encode(json.dumps(token_data).encode()).decode()

                    # POST form
                    form_action = re.search(r'<form[^>]*action="([^"]+)"', html_fp)
                    action = form_action.group(1).replace("&amp;", "&") if form_action else ch_url
                    post_url = fp_base + action if action.startswith("?") else action

                    field_s = re.search(r'name="s"\s+value="([^"]*)"', html_fp)
                    field_t = re.search(r'name="t"\s+value="([^"]*)"', html_fp)
                    data = {"[REDACTED_AUTH_SECRET]": token, "code": ""}
                    if field_s: data["s"] = field_s.group(1)
                    if field_t: data["t"] = field_t.group(1)

                    r_post = s_fp.post(post_url, data=data, timeout=(6, 12),
                                       headers={"Referer": fp_base, "Content-Type": "application/x-www-form-urlencoded"})
                    print(f"  [POST] {r_post.status_code} | {len(r_post.content)}B")

                    time.sleep(1)
                    r_real = s_fp.get(fp_base, headers=hdr(), timeout=(6, 12))
                    print(f"  [Real] {r_real.status_code} | {len(r_real.content)}B [{elapsed()}]")
                    if r_real.status_code == 200 and "altcha" not in r_real.text.lower():
                        fp_unlocked = True
                        print("  ✅✅✅ FARPOST UNLOCKED!")
                        with open("fp_v17_real.html", "w", encoding="utf-8") as f:
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

print(f"  Farpost unlocked: {fp_unlocked}")


# ============================================================
if remaining() > 50:
    print(f"\n{'='*70}")
    print(f"2. WILDBERRIES — CDN optimized + search  [{elapsed()}] rem={remaining():.0f}s")
    print("=" * 70)

    s_wb = requests.Session()
    s_wb.headers.update({"User-Agent": UA, "Accept": "application/json",
                         "Origin": "https://www.wildberries.ru", "Referer": "https://www.wildberries.ru/"})

    # 2a. Search (quick)
    try:
        r = s_wb.get("https://search.wb.ru/exactmatch/ru/common/v5/search",
                     params={"query": "телефон", "nm": "1", "resultset": "catalog",
                             "sort": "popular", "spp": "30"},
                     timeout=(4, 8))
        print(f"  [Search v5] {r.status_code} | {len(r.content)}B [{elapsed()}]")
        if r.status_code == 200:
            prods = r.json().get("data", {}).get("products", [])
            print(f"  Products: {len(prods)}")
            for p in prods[:3]:
                print(f"    id={p.get('id')} name={p.get('name','?')[:40]} imgs={len(p.get('images',[]))}")
            with open("wb_v17_search.json", "w", encoding="utf-8") as f:
                json.dump(r.json(), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [Search v5] ERR: {str(e)[:80]}")

    # 2b. CDN — optimized: all baskets in parallel for top combos
    print(f"\n  --- CDN (parallel baskets) --- [{elapsed()}]")
    test_nms = [135248485, 207573764, 199298871]

    def try_cdn_one(nm):
        s = str(nm)
        # Top 3 most likely combos
        combos = [(s[:3], s[:6]), (s[:3], s[:7]), (s[:4], s[:7])]
        # Try all 50 baskets for combo[0] first (most likely)
        for bn in range(1, 51):
            vol, part = combos[0]
            url = f"https://static-basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/all-with-sizing.json"
            try:
                r = s_wb.get(url, timeout=(2, 3))
                if r.status_code == 200:
                    data = r.json()
                    imgs = data if isinstance(data, list) else data.get("images", [])
                    return nm, f"IMG bn={bn} vol={vol} part={part}", len(imgs), imgs[:3]
            except:
                continue
        # Then combo[1] and [2] for all baskets
        for vi in [1, 2]:
            vol, part = combos[vi]
            for bn in range(1, 51):
                url = f"https://static-basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/all-with-sizing.json"
                try:
                    r = s_wb.get(url, timeout=(2, 3))
                    if r.status_code == 200:
                        data = r.json()
                        imgs = data if isinstance(data, list) else data.get("images", [])
                        return nm, f"IMG bn={bn} vol={vol} part={part}", len(imgs), imgs[:3]
                except:
                    continue
        # Try info.json as fallback
        for bn in range(1, 21):
            vol, part = combos[0]
            url = f"https://basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/{nm}-info.json"
            try:
                r = s_wb.get(url, timeout=(2, 3))
                if r.status_code == 200:
                    return nm, f"INFO bn={bn}", 1, [list(r.json().keys())[:5]]
            except:
                continue
        return nm, "NOT_FOUND", 0, []

    with ThreadPoolExecutor(max_workers=20) as pool:
        futs = {pool.submit(try_cdn_one, nm): nm for nm in test_nms}
        for fut in as_completed(futs, timeout=40):
            nm, status, count, samples = fut.result()
            mark = "✅" if count > 0 else "❌"
            print(f"  {mark} nm={nm}: {status} | {count} [{elapsed()}]")
            for s in samples:
                print(f"    {str(s)[:80]}")

    # 2c. card.wb.ru quick
    if remaining() > 20 and HAS_CFFI:
        print(f"\n  --- card.wb.ru --- [{elapsed()}]")
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
                        with open(f"wb_v17_card_{nm}.json", "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"  [card nm={nm}] ERR: {str(e)[:60]}")
            time.sleep(0.3)


# ============================================================
if remaining() > 35:
    print(f"\n{'='*70}")
    print(f"3. AVITO — PoW quick  [{elapsed()}] rem={remaining():.0f}s")
    print("=" * 70)

    if HAS_CFFI:
        s_av = cffi_requests.Session(impersonate="chrome")
        h_av = hdr({"Accept": "text/html"})
        try:
            r = s_av.get("https://www.avito.ru/moscow/telefony", headers=h_av, timeout=(6, 12))
            print(f"  [Avito] {r.status_code} | {len(r.content)}B [{elapsed()}]")
            cookies = dict(s_av.cookies)
            print(f"  Cookies: {list(cookies.keys())}")

            pow_ck = cookies.get("pow_challenge", "")
            if pow_ck:
                decoded = b64url_decode(pow_ck)
                print(f"  pow_challenge: {len(pow_ck)} chars → {len(decoded)} bytes")
                print(f"  First 32B hex: {decoded[:32].hex()}")

                # Detect complexity: try first 4 bytes as uint32
                complexity = 10
                if len(decoded) >= 4:
                    c4 = struct.unpack('<I', decoded[:4])[0]
                    if 4 <= c4 <= 20:
                        complexity = c4
                # Also try: maybe it's a 1-byte complexity at position 0
                if decoded[0] >= 4 and decoded[0] <= 20:
                    complexity = decoded[0]
                print(f"  Complexity: {complexity}")

                # Solve: sha256(decoded + str(i)) → first `complexity` bits = 0
                t0 = time.monotonic()
                nonce = None
                # For complexity <= 10, should find within ~1000 iterations
                # For complexity 12, ~4000; for 16, ~65000
                max_try = 20_000_000 if complexity <= 12 else 5_000_000
                for i in range(max_try):
                    h = hashlib.sha256(decoded + str(i).encode()).digest()
                    # Check leading zero bits
                    byte_idx = complexity // 8
                    bit_off = complexity % 8
                    ok = True
                    for j in range(byte_idx):
                        if h[j] != 0:
                            ok = False
                            break
                    if ok and bit_off > 0:
                        if (h[byte_idx] >> (8 - bit_off)) != 0:
                            ok = False
                    if ok:
                        nonce = i
                        break
                    if i % 2_000_000 == 0 and i > 0:
                        print(f"    AvPoW: {i} ({time.monotonic()-t0:.1f}s)")
                print(f"  Nonce={nonce} ({time.monotonic()-t0:.1f}s)")

                if nonce is not None:
                    pow_response = base64.b64encode(str(nonce).encode()).decode()
                    s_av.cookies.set("pow-response", pow_response, domain="www.avito.ru")
                    print(f"  pow-response: {pow_response}")

                    time.sleep(1.5)
                    r2 = s_av.get("https://www.avito.ru/moscow/telefony", headers=h_av, timeout=(6, 12))
                    print(f"  [Retry] {r2.status_code} | {len(r2.content)}B [{elapsed()}]")
                    if r2.status_code == 200:
                        print("  ✅ AVITO UNLOCKED!")
                        with open("avito_v17_real.html", "w", encoding="utf-8") as f:
                            f.write(r2.text)
                    elif r2.status_code in (429, 439):
                        print(f"  ⚠ Still {r2.status_code}")
            else:
                print(f"  ⚠ No pow_challenge cookie")
        except Exception as e:
            print(f"  [Avito] ERR: {e}")
            traceback.print_exc()


# ============================================================
if remaining() > 20:
    print(f"\n{'='*70}")
    print(f"4. OZON — composer + cffi  [{elapsed()}] rem={remaining():.0f}s")
    print("=" * 70)

    ozon_id = "1600562267"

    # Composer (usually faster)
    s_oz_s = requests.Session()
    s_oz_s.headers.update(hdr())
    try:
        r_main = s_oz_s.get("https://www.ozon.ru/", timeout=(4, 8), allow_redirects=True)
        print(f"  [Main] {r_main.status_code} | {len(r_main.content)}B | CK: {list(s_oz_s.cookies.keys())}")
        time.sleep(0.5)
        r_comp = s_oz_s.get(
            "https://www.ozon.ru/api/composer-api.bx/page/json/v2",
            params={"url": f"/product/{ozon_id}/"},
            headers={"Accept": "application/json", "Referer": f"https://www.ozon.ru/product/{ozon_id}/"},
            timeout=(4, 8)
        )
        print(f"  [Composer] {r_comp.status_code} | {len(r_comp.content)}B [{elapsed()}]")
        if r_comp.status_code == 200:
            data = r_comp.json()
            print(f"  Keys: {list(data.keys())[:10]}")
            print("  ✅ Composer works!")
            with open("ozon_v17_composer.json", "w", encoding="utf-8") as f:
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

    # cffi product page
    if HAS_CFFI and remaining() > 8:
        s_oz = cffi_requests.Session(impersonate="chrome")
        try:
            r = s_oz.get(f"https://www.ozon.ru/product/{ozon_id}/",
                         headers=hdr(), timeout=(5, 12))
            print(f"  [Ozon cffi] {r.status_code} | {len(r.content)}B [{elapsed()}]")
            if r.status_code == 200:
                print("  ✅ Ozon direct!")
                with open("ozon_v17_product.html", "w", encoding="utf-8") as f:
                    f.write(r.text)
            elif r.status_code == 403:
                with open("ozon_v17_challenge.html", "w", encoding="utf-8") as f:
                    f.write(r.text)
                print(f"  Saved challenge ({len(r.text)}B)")
        except Exception as e:
            print(f"  [Ozon cffi] ERR: {str(e)[:60]}")


# ============================================================
print(f"\n{'='*70}")
print(f"DONE — diag_v17  [{elapsed()}]")
print("=" * 70)

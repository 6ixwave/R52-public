"""Диагностика v19: 120s, прокси-фокус.
1. Скрап бесплатных RU-прокси (3 источника)
2. Health-check топ-30
3. WB: 498 body + main/card через прокси
4. Farpost: сохранить 198KB, найти challengeurl, через прокси
5. Avito: PoW + 429 через прокси
6. Ozon: challenge через прокси
"""
import json, re, time, traceback, base64, struct, hashlib
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

T0 = time.monotonic()
LIMIT = 120
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
print(f"\n{'='*70}\n1. PROXY SCRAPE [{el()}] rem={rem():.0f}s\n{'='*70}")

proxy_sources = [
    ("free-proxy-list", "https://www.free-proxy-list.net/country-ru/"),
    ("openproxylist", "https://openproxylist.xyz/proxies/country-ru.txt"),
    ("proxyscrape", "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&proxyUsage=webProxy&country=ru&format=text&limit=50"),
]

all_proxies = []
for label, url in proxy_sources:
    if rem() < 30:
        break
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=(5, 10))
        print(f"  [{label}] {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            lines = r.text.strip().splitlines()
            for line in lines:
                line = line.strip()
                # Parse: ip:port or ip:port:country:protocol
                m = re.match(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{2,5})', line)
                if m:
                    ip, port = m.group(1), m.group(2)
                    if 1024 <= int(port) <= 65535:
                        all_proxies.append(f"http://{ip}:{port}")
            print(f"    Parsed: {len(lines)} lines")
    except Exception as e:
        print(f"  [{label}] ERR: {str(e)[:60]}")
    time.sleep(0.5)

print(f"  Total raw proxies: {len(all_proxies)}")

# Deduplicate
seen = set()
proxies = []
for p in all_proxies:
    if p not in seen:
        seen.add(p)
        proxies.append(p)
print(f"  Unique: {len(proxies)}")

# ============================================================
print(f"\n{'='*70}\n2. PROXY HEALTH-CHECK (top 30) [{el()}] rem={rem():.0f}s\n{'='*70}")

working_proxies = []
test_url = "https://www.google.com/generate_204"

for i, p in enumerate(proxies[:30]):
    if rem() < 25:
        break
    try:
        t0 = time.monotonic()
        r = requests.get(test_url, proxies={"http": p, "https": p},
                         timeout=(3, 5), headers={"User-Agent": "Mozilla/5.0"},
                         allow_redirects=False)
        lat = time.monotonic() - t0
        if r.status_code in (200, 204, 301, 302):
            working_proxies.append(p)
            print(f"  ✅ [{i+1}] {p} | {r.status_code} | {lat:.1f}s")
        else:
            print(f"  ❌ [{i+1}] {p} | {r.status_code}")
    except Exception as e:
        print(f"  ❌ [{i+1}] {p} | {str(e)[:40]}")

print(f"  Working: {len(working_proxies)}/{min(30, len(proxies))}")

# Save working proxies
with open("v19_working_proxies.json", "w") as f:
    json.dump(working_proxies, f, indent=2)

def test_through_proxy(label, url, headers=None, use_cffi=True):
    """Быстрый тест URL через лучший прокси."""
    if not working_proxies:
        print(f"  [{label}] NO PROXY — direct")
        if use_cffi and HAS_CFFI:
            s = cffi_req.Session(impersonate="chrome")
            r = s.get(url, headers=headers or H, timeout=(5, 10))
        else:
            s = requests.Session()
            r = s.get(url, headers=headers or H, timeout=(5, 10))
        return r, None
    p = working_proxies[0]
    if use_cffi and HAS_CFFI:
        s = cffi_req.Session(impersonate="chrome")
        r = s.get(url, headers=headers or H, proxies={"http": p, "https": p}, timeout=(5, 10))
    else:
        s = requests.Session()
        r = s.get(url, headers=headers or H, proxies={"http": p, "https": p}, timeout=(5, 10))
    print(f"  [{label}] {r.status_code} | {len(r.content)}B | proxy={p}")
    return r, p

# ============================================================
print(f"\n{'='*70}\n3. WILDBERRIES [{el()}] rem={rem():.0f}s\n{'='*70}")

wb_nm = "199298871"
wb_main = f"https://www.wildberries.ru/catalog/{wb_nm}/detail.aspx"
wb_card = f"https://card.wb.ru/cards/v2/detail?nm={wb_nm}"

# 3a. Direct: check 498 body
try:
    s_wb = requests.Session()
    s_wb.headers.update(H)
    r = s_wb.get(wb_main, timeout=(5, 10))
    print(f"  [direct main] {r.status_code} | {len(r.content)}B")
    # Save 498 body
    with open("wb_v19_498.html", "w", encoding="utf-8") as f:
        f.write(r.text)
    # Check if it's a redirect/challenge page
    body = r.text[:500]
    print(f"  Body[:300]: {body[:300]}")
    # Check for meta refresh or JS redirect
    m = re.search(r'(?:refresh|location\.href|window\.location)[^;]{0,100}', r.text, re.I)
    if m:
        print(f"  Redirect hint: {m.group(0)[:100]}")
except Exception as e:
    print(f"  [direct main] ERR: {str(e)[:60]}")

time.sleep(0.3)

# 3b. Through proxy
r_wb, p_wb = test_through_proxy("proxy main", wb_main)
if r_wb and r_wb.status_code == 200 and len(r_wb.content) > 5000:
    print(f"  ✅ WB via proxy! {len(r_wb.content)}B")
    with open("wb_v19_proxy.html", "w", encoding="utf-8") as f:
        f.write(r_wb.text)
    ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', r_wb.text, re.DOTALL)
    print(f"  JSON-LD: {len(ld)}")
    results["wb_proxy"] = "200"

# 3c. Card API through proxy
if rem() > 20:
    r_card, _ = test_through_proxy("proxy card", wb_card,
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Origin": "https://www.wildberries.ru",
                 "Referer": "https://www.wildberries.ru/"})
    if r_card and r_card.status_code == 200:
        data = r_card.json()
        cards = data.get("data", {}).get("cards", [])
        print(f"  ✅ Card API! {len(cards)} cards")
        if cards:
            c = cards[0]
            print(f"    name={c.get('name','?')[:50]} imgs={len(c.get('images',[]))}")
            with open("wb_v19_card_proxy.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        results["wb_card_proxy"] = "200"

# 3d. Try mobile/alternative WB endpoints
if rem() > 15:
    wb_alt_urls = [
        f"https://www.wildberries.ru/catalog/{wb_nm}/detail.aspx?ab_testing=1",
        f"https://m.wildberries.ru/catalog/{wb_nm}/detail.aspx",
        f"https://card.wb.ru/cards/v2/detail?nm={wb_nm}&spp=30",
    ]
    for au in wb_alt_urls[:2]:
        if rem() < 10: break
        try:
            if HAS_CFFI:
                s_a = cffi_req.Session(impersonate="chrome")
                r_a = s_a.get(au, headers=H, timeout=(4, 8))
            else:
                r_a = requests.get(au, headers=H, timeout=(4, 8))
            print(f"  [alt {au[-40:]}] {r_a.status_code} | {len(r_a.content)}B")
        except Exception as e:
            print(f"  [alt] ERR: {str(e)[:40]}")
        time.sleep(0.3)

# ============================================================
if rem() > 25:
    print(f"\n{'='*70}\n4. FARPOST [{el()}] rem={rem():.0f}s\n{'='*70}")

    fp_base = "https://www.farpost.ru/geo/moskva/l/telefony/"
    fp_item = "https://www.farpost.ru/item/18530297982"

    if HAS_CFFI:
        s_fp = cffi_req.Session(impersonate="chrome")
        # 4a. Load listing page (not category — item page might be different)
        try:
            r = s_fp.get(fp_item, headers=H, timeout=(5, 12))
            print(f"  [FP item] {r.status_code} | {len(r.content)}B")
            html = r.text
            cookies = dict(s_fp.cookies)
            print(f"  Cookies: {list(cookies.keys())}")

            # Save full page
            with open("fp_v19_item.html", "w", encoding="utf-8") as f:
                f.write(html)

            # Check for altcha
            has_altcha = "altcha" in html.lower()
            print(f"  Has altcha: {has_altcha}")

            # Find challengeurl (multiple patterns)
            ch_patterns = [
                r'challengeurl="([^"]+)"',
                r'challengeurl\s*=\s*["\']([^"\']+)["\']',
                r'data-challenge-url="([^"]+)"',
                r'challengeUrl["\s:=]+["\']([^"\']+)["\']',
            ]
            ch_url = ""
            for cp in ch_patterns:
                m = re.search(cp, html)
                if m:
                    ch_url = m.group(1).replace("&amp;", "&")
                    print(f"  challengeurl (pattern {cp[:30]}): {ch_url}")
                    break

            if not ch_url:
                # Maybe the page is fully loaded (no captcha for item pages?)
                # Check for product data
                ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
                print(f"  JSON-LD blocks: {len(ld)}")
                for i, b in enumerate(ld[:3]):
                    try:
                        d = json.loads(b.strip())
                        t = d.get("@type", "?") if isinstance(d, dict) else "?"
                        print(f"    [{i}] type={t} keys={list(d.keys())[:6] if isinstance(d,dict) else '?'}")
                    except:
                        print(f"    [{i}] parse fail len={len(b)}")

                # Check for og: tags
                og_t = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
                if og_t:
                    print(f"  og:title: {og_t.group(1)[:80]}")

                # Check for price
                price_m = re.findall(r'(\d[\d\s\u00a0]*[^\w<]{1,3})', html[:5000])
                if price_m:
                    print(f"  Price hints: {price_m[:5]}")

                # Check for images
                imgs = re.findall(r'(?:src|data-src|data-original)\s*=\s*["\']([^"\']*(?:farpost|upload|img)[^"\']*)["\']', html, re.I)
                print(f"  Images found: {len(imgs)}")
                for im in imgs[:5]:
                    print(f"    {im[:80]}")

                if len(html) > 50000 and not has_altcha:
                    print(f"  ✅✅ FARPOST ITEM — FULL PAGE, NO CAPTCHA!")
                    results["fp_item"] = "full_no_captcha"
                elif has_altcha:
                    print(f"  ⚠ Has altcha but no challengeurl found")
                    # Dump the altcha section
                    alt_section = re.search(r'<altcha[^>]*>.*?</altcha[^>]*>', html, re.DOTALL)
                    if alt_section:
                        print(f"  Altcha section: {alt_section.group(0)[:300]}")
                    # Look for any URL with 'challenge' or 'altcha'
                    ch_urls = re.findall(r'["\']([^"\']*challenge[^"\']*)["\']', html, re.I)
                    print(f"  Challenge URLs in page: {ch_urls[:5]}")
        except Exception as e:
            print(f"  [FP item] ERR: {e}")
            traceback.print_exc()

        # 4b. Category page through proxy
        if rem() > 10:
            r_cat, p_cat = test_through_proxy("FP cat proxy", fp_base)
            if r_cat and r_cat.status_code == 200:
                with open("fp_v19_cat_proxy.html", "w", encoding="utf-8") as f:
                    f.write(r_cat.text)
                has_ch = "challengeurl" in r_cat.text.lower() or "altcha" in r_cat.text.lower()
                print(f"  Cat proxy: {len(r_cat.content)}B | altcha={has_ch}")
                results["fp_cat_proxy"] = "200"

# ============================================================
if rem() > 20:
    print(f"\n{'='*70}\n5. AVITO [{el()}] rem={rem():.0f}s\n{'='*70}")

    if HAS_CFFI:
        # 5a. Direct PoW (we know it works)
        s_av = cffi_req.Session(impersonate="chrome")
        try:
            r = s_av.get("https://www.avito.ru/moscow/telefony", headers=H, timeout=(5, 12))
            print(f"  [Avito direct] {r.status_code} | {len(r.content)}B")
            cookies = dict(s_av.cookies)
            pow_ck = cookies.get("pow_challenge", "")
            if pow_ck:
                s_b64 = pow_ck.replace("-", "+").replace("_", "/")
                while len(s_b64) % 4: s_b64 += "="
                decoded = base64.b64decode(s_b64)
                complexity = 10
                if len(decoded) >= 4:
                    c4 = struct.unpack('<I', decoded[:4])[0]
                    if 4 <= c4 <= 20: complexity = c4
                if decoded[0] >= 4 and decoded[0] <= 20:
                    complexity = decoded[0]
                t0p = time.monotonic()
                nonce = None
                for i in range(500_000):
                    h = hashlib.sha256(decoded + str(i).encode()).digest()
                    bi, bo = complexity // 8, complexity % 8
                    ok = all(h[j] == 0 for j in range(bi))
                    if ok and bo > 0:
                        ok = (h[bi] >> (8 - bo)) == 0
                    if ok:
                        nonce = i
                        break
                print(f"  PoW: complexity={complexity} nonce={nonce} ({time.monotonic()-t0p:.2f}s)")

                if nonce is not None:
                    pow_resp = base64.b64encode(str(nonce).encode()).decode()
                    s_av.cookies.set("pow-response", pow_resp, domain="www.avito.ru")
                    # Wait longer this time
                    time.sleep(2)
                    r2 = s_av.get("https://www.avito.ru/moscow/telefony", headers=H, timeout=(5, 12))
                    print(f"  [Retry after 2s] {r2.status_code} | {len(r2.content)}B")
                    if r2.status_code == 200:
                        print("  ✅ AVITO UNLOCKED (direct)")
                        results["avito_direct"] = "200"
                        with open("avito_v19_real.html", "w", encoding="utf-8") as f:
                            f.write(r2.text)
                    elif r2.status_code == 429:
                        # Try with different Accept
                        r3 = s_av.get("https://www.avito.ru/moscow/telefony",
                                      headers={**H, "Accept": "text/html", "X-Requested-With": "XMLHttpRequest"},
                                      timeout=(5, 12))
                        print(f"  [Retry XHR] {r3.status_code} | {len(r3.content)}B")
                        # Try item page directly
                        r4 = s_av.get("https://www.avito.ru/ad/1096783029", headers=H, timeout=(5, 12))
                        print(f"  [Item direct] {r4.status_code} | {len(r4.content)}B")
                        if r4.status_code == 200:
                            print("  ✅ AVITO ITEM (direct)")
                            results["avito_item"] = "200"
        except Exception as e:
            print(f"  [Avito] ERR: {e}")

        # 5b. Through proxy
        if rem() > 10 and working_proxies:
            s_av2 = cffi_req.Session(impersonate="chrome")
            p = working_proxies[1] if len(working_proxies) > 1 else working_proxies[0]
            try:
                r = s_av2.get("https://www.avito.ru/moscow/telefony", headers=H,
                              proxies={"http": p, "https": p}, timeout=(5, 12))
                print(f"  [Avito proxy {p[-20:]}] {r.status_code} | {len(r.content)}B")
                cookies2 = dict(s_av2.cookies)
                print(f"  Cookies: {list(cookies2.keys())}")
                if r.status_code == 200:
                    print("  ✅ AVITO via proxy!")
                    results["avito_proxy"] = "200"
                    with open("avito_v19_proxy.html", "w", encoding="utf-8") as f:
                        f.write(r.text)
                elif r.status_code == 439 and cookies2.get("pow_challenge"):
                    print("  439 with PoW — solving...")
                    pc = cookies2["pow_challenge"]
                    s_b64 = pc.replace("-", "+").replace("_", "/")
                    while len(s_b64) % 4: s_b64 += "="
                    dec = base64.b64decode(s_b64)
                    cx = dec[0] if 4 <= dec[0] <= 20 else 10
                    for i in range(500_000):
                        h = hashlib.sha256(dec + str(i).encode()).digest()
                        bi, bo = cx // 8, cx % 8
                        ok = all(h[j] == 0 for j in range(bi))
                        if ok and bo > 0: ok = (h[bi] >> (8 - bo)) == 0
                        if ok:
                            pr = base64.b64encode(str(i).encode()).decode()
                            s_av2.cookies.set("pow-response", pr, domain="www.avito.ru")
                            break
                    time.sleep(1.5)
                    r2 = s_av2.get("https://www.avito.ru/moscow/telefony", headers=H,
                                   proxies={"http": p, "https": p}, timeout=(5, 12))
                    print(f"  [Proxy retry] {r2.status_code} | {len(r2.content)}B")
                    if r2.status_code == 200:
                        results["avito_proxy"] = "200_after_pow"
                        print("  ✅ AVITO proxy unlocked!")
            except Exception as e:
                print(f"  [Avito proxy] ERR: {str(e)[:50]}")

# ============================================================
if rem() > 15:
    print(f"\n{'='*70}\n6. OZON [{el()}] rem={rem():.0f}s\n{'='*70}")

    oz_id = "1600562267"
    if HAS_CFFI:
        # 6a. Direct: main → composer
        s_oz = cffi_req.Session(impersonate="chrome")
        try:
            r_main = s_oz.get("https://www.ozon.ru/", headers=H, timeout=(4, 8))
            print(f"  [Main] {r_main.status_code} | {len(r_main.content)}B | CK: {list(s_oz.cookies.keys())}")
            time.sleep(0.5)

            r_comp = s_oz.get(
                "https://www.ozon.ru/api/composer-api.bx/page/json/v2",
                params={"url": f"/product/{oz_id}/"},
                headers={"Accept": "application/json", "Referer": f"https://www.ozon.ru/product/{oz_id}/"},
                timeout=(5, 10))
            print(f"  [Composer] {r_comp.status_code} | {len(r_comp.content)}B")

            if r_comp.status_code == 403:
                try:
                    cd = r_comp.json()
                    ch_url = cd.get("challengeURL", "")
                    print(f"  challengeURL: {ch_url[:100]}")
                    incident = cd.get("incidentId", "")
                    print(f"  incidentId: {incident[:60]}")

                    # Try solving challenge
                    if ch_url:
                        r_ch = s_oz.get(ch_url, headers=H, timeout=(4, 8))
                        print(f"  [Challenge page] {r_ch.status_code} | {len(r_ch.content)}B")
                        # Look for challenge data in the page
                        ch_data = re.findall(r'["\'](?:challenge|token|key|salt|algorithm)["\']\s*:\s*["\']?([^",\s}]+)', r_ch.text)
                        print(f"  Challenge fields: {ch_data[:8]}")
                        # Check for Turnstile / hCaptcha / custom
                        for cap in ["turnstile", "hcaptcha", "recaptcha", "altcha", "data-sitekey"]:
                            if cap in r_ch.text.lower():
                                print(f"  CAPTCHA type: {cap}")
                                break
                except:
                    print(f"  Body[:200]: {r_comp.text[:200]}")

            # 6b. Through proxy
            if rem() > 8 and working_proxies:
                p = working_proxies[2] if len(working_proxies) > 2 else working_proxies[0]
                s_oz2 = cffi_req.Session(impersonate="chrome")
                r_m2 = s_oz2.get("https://www.ozon.ru/", headers=H,
                                 proxies={"http": p, "https": p}, timeout=(4, 8))
                print(f"  [Proxy main] {r_m2.status_code} | {len(r_m2.content)}B | CK: {list(s_oz2.cookies.keys())}")
                time.sleep(0.5)
                r_c2 = s_oz2.get(
                    "https://www.ozon.ru/api/composer-api.bx/page/json/v2",
                    params={"url": f"/product/{oz_id}/"},
                    headers={"Accept": "application/json", "Referer": f"https://www.ozon.ru/product/{oz_id}/"},
                    proxies={"http": p, "https": p}, timeout=(5, 10))
                print(f"  [Proxy composer] {r_c2.status_code} | {len(r_c2.content)}B")
                if r_c2.status_code == 200:
                    print("  ✅ OZON composer via proxy!")
                    results["ozon_proxy"] = "200"
                    with open("ozon_v18_composer_proxy.json", "w", encoding="utf-8") as f:
                        json.dump(r_c2.json(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [Ozon] ERR: {e}")

# ============================================================
print(f"\n{'='*70}\nSUMMARY [{el()}]\n{'='*70}")
for k, v in results.items():
    print(f"  {k}: {v}")
print(f"\nWorking proxies: {len(working_proxies)}")
print(f"DONE diag_v19 [{el()}]")

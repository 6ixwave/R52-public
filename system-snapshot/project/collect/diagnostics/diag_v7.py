"""Диагностика v7: Farpost geo-каталог, Avito deep-parse, WB cookies, Ozon challenge."""
import json
import re
import time
import traceback

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
    print("[OK] curl_cffi available")
except ImportError:
    HAS_CFFI = False
    print("[WARN] curl_cffi NOT available")

def hdr(extra=None):
    h = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}
    if extra:
        h.update(extra)
    return h

# ============================================================
print("\n" + "=" * 70)
print("1. FARPOST — geo/moskva/l/telefony: полный разбор")
print("=" * 70)

h_fp = hdr({"Accept": "text/html", "Accept-Charset": "windows-1251,utf-8;q=0.7"})
r_fp = requests.get("https://www.farpost.ru/geo/moskva/l/telefony/", headers=h_fp, timeout=(8, 20))
print(f"  [FP geo/moskva/l/telefony] {r_fp.status_code} | {len(r_fp.content)}B")

if r_fp.status_code == 200:
    html = r_fp.content.decode("windows-1251", errors="replace")
    with open("fp_geo_telefony.html", "w", encoding="windows-1251", errors="replace") as f:
        f.write(html)
    print(f"  Saved fp_geo_telefony.html ({len(html)} chars)")

    # Все hrefs
    all_hrefs = re.findall(r'href="([^"]+)"', html)
    print(f"\n  Total hrefs: {len(all_hrefs)}")

    # Группируем
    groups = {}
    for h in all_hrefs:
        short = h.split('?')[0]
        if '/' in short and not short.startswith('http'):
            parts = short.split('/')
            key = parts[1] if len(parts) > 1 else '?'
        elif short.startswith('http'):
            key = 'external'
        else:
            key = 'root'
        groups.setdefault(key, []).append(h)
    for k, v in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(v) > 1:
            print(f"  /{k}/: {len(v)} → {v[:4]}")

    # Ищем ad-ссылки: Farpost обычно /geo/CITY/l/CAT/ADID/
    # Паттерн: в конце URL число
    ad_patterns = [
        (r'href="([^"]*?/(\d{5,12})/)"', "end-numeric"),
        (r'href="([^"]*?/(\d{5,12})/[^"]*)"', "mid-numeric"),
        (r'href="([^"]*?/l/[^"]*?/(\d{5,12})[^"]*)"', "l-numeric"),
        (r'href="([^"]*?/geo/[^"]*?/(\d{5,12})[^"]*)"', "geo-numeric"),
        (r'data-ad-id="(\d+)"', "data-ad-id"),
        (r'data-id="(\d+)"', "data-id"),
        (r'"ad_id"\s*:\s*(\d+)', "json-ad_id"),
        (r'"id"\s*:\s*(\d{6,12})', "json-id"),
    ]
    found_ads = []
    for pat, label in ad_patterns:
        found = re.findall(pat, html)
        if found:
            ids = [f[1] if isinstance(f, tuple) else f for f in found]
            print(f"  [{label}] {len(found)} → {ids[:8]}")
            found_ads.extend(ids)

    # Также ищем в JSON/JS
    json_blocks = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for i, block in enumerate(json_blocks[:5]):
        if 'ad' in block.lower() and len(block) > 100:
            ad_in_js = re.findall(r'(\d{7,12})', block)
            if ad_in_js:
                print(f"  [JS block {i}] ad-like IDs: {ad_in_js[:10]}")

    # Пробуем /tech/ (electronics category)
    print("\n  [FP /tech/ category]")
    r_tech = requests.get("https://www.farpost.ru/tech/", headers=h_fp, timeout=(8, 15))
    print(f"  /tech/: {r_tech.status_code} | {len(r_tech.content)}B")
    if r_tech.status_code == 200:
        thtml = r_tech.content.decode("windows-1251", errors="replace")
        # Подкатегории
        subcats = re.findall(r'href="(/tech/[^/"]+/)"', thtml)
        print(f"  Subcategories: {list(set(subcats))[:10]}")

    # Пробуем /tech/audio_video/ (конкретная подкатегория)
    print("\n  [FP /tech/audio_video/]")
    r_av = requests.get("https://www.farpost.ru/tech/audio_video/", headers=h_fp, timeout=(8, 15))
    print(f"  /tech/audio_video/: {r_av.status_code} | {len(r_av.content)}B")
    if r_av.status_code == 200:
        ahtml = r_av.content.decode("windows-1251", errors="replace")
        with open("fp_tech_av.html", "w", encoding="windows-1251", errors="replace") as f:
            f.write(ahtml)
        # Ищем ad links
        ad_links = re.findall(r'href="([^"]*?/(\d{5,12})/)"', ahtml)
        ad_links2 = re.findall(r'href="([^"]*?/(\d{5,12})/[^"]*)"', ahtml)
        all_ad = [a[0] for a in ad_links] + [a[0] for a in ad_links2]
        print(f"  Ad links: {len(all_ad)} → {all_ad[:10]}")

        # Если нашли — грузим карточку
        if all_ad:
            ad_url = all_ad[0]
            if not ad_url.startswith('http'):
                ad_url = "https://www.farpost.ru" + ad_url
            print(f"\n  [FP AD] {ad_url}")
            r_ad = requests.get(ad_url, headers=h_fp, timeout=(8, 15))
            print(f"  Ad: {r_ad.status_code} | {len(r_ad.content)}B | CT: {r_ad.headers.get('Content-Type','?')[:40]}")
            if r_ad.status_code == 200:
                adhtml = r_ad.content.decode("windows-1251", errors="replace")
                with open("fp_ad_card.html", "w", encoding="windows-1251", errors="replace") as f:
                    f.write(adhtml)
                # OG
                og_t = re.findall(r'og:title[^>]*content="([^"]*)"', adhtml)
                og_d = re.findall(r'og:description[^>]*content="([^"]*)"', adhtml)
                og_i = re.findall(r'og:image[^>]*content="([^"]*)"', adhtml)
                print(f"  og:title: {og_t[:2]}")
                print(f"  og:desc: {og_d[:1]}")
                print(f"  og:image: {og_i[:5]}")
                # Farpost images
                fp_imgs = re.findall(r'https?://img\.farpost\.ru/[^"\'\s<>]+', adhtml)
                print(f"  img.farpost.ru: {len(fp_imgs)} → {fp_imgs[:8]}")
                # JSON-LD
                ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', adhtml, re.DOTALL)
                for i, b in enumerate(ld[:3]):
                    try:
                        d = json.loads(b)
                        print(f"  LD[{i}] type={d.get('@type','?')}")
                        if d.get('@type') == 'Product':
                            print(f"    name: {d.get('name','?')[:60]}")
                            print(f"    offers: {json.dumps(d.get('offers',{}), ensure_ascii=False)[:300]}")
                            imgs = d.get('image', [])
                            if isinstance(imgs, list):
                                print(f"    images: {imgs[:5]}")
                            else:
                                print(f"    image: {str(imgs)[:200]}")
                    except:
                        print(f"  LD[{i}]: {b[:200]!r}")
                # Price in HTML
                prices = re.findall(r'class="[^"]*price[^"]*"[^>]*>([^<]+)<', adhtml)
                print(f"  Prices: {prices[:5]}")
                # Video
                videos = re.findall(r'(https?://[^"\'\s]*\.mp4[^"\'\s]*)', adhtml)
                videos2 = re.findall(r'<video[^>]*src="([^"]*)"', adhtml)
                print(f"  Videos: {videos[:3] + videos2[:3]}")

# ============================================================
print("\n" + "=" * 70)
print("2. AVITO — с задержкой, deep parse listing")
print("=" * 70)

if HAS_CFFI:
    time.sleep(5)  # Пауза после Farpost
    s_av = cffi_requests.Session(impersonate="chrome")
    # Сначала главная (cookie)
    r0 = s_av.get("https://www.avito.ru/", timeout=15, headers=hdr())
    print(f"  [AVITO home] {r0.status_code} | {len(r0.content)}B")
    time.sleep(4)

    # Listing
    r_list = s_av.get("https://www.avito.ru/moscow/telefony",
                      headers=hdr({"Accept": "text/html"}), timeout=25)
    print(f"  [AVITO listing] {r_list.status_code} | {len(r_list.content)}B")

    if r_list.status_code == 200:
        html = r_list.text
        with open("avito_listing2.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Saved avito_listing2.html ({len(html)} chars)")

        # Deep search for ad IDs
        print("\n  --- Ad ID patterns ---")
        # Avito new format: /moscow/telefony/ADID/ (no /ad/ prefix anymore?)
        p1 = re.findall(r'href="[^"]*?/moscow/[^"]*?/(\d{9,15})/"', html)
        print(f"  /moscow/.../NNN/: {len(p1)} → {p1[:10]}")

        p2 = re.findall(r'href="[^"]*?/ad/(\d+)"', html)
        print(f"  /ad/NNN: {len(p2)} → {p2[:10]}")

        p3 = re.findall(r'"adId"\s*:\s*(\d+)', html)
        print(f"  adId JSON: {len(p3)} → {p3[:10]}")

        p4 = re.findall(r'"id"\s*:\s*(\d{9,15})', html)
        print(f"  id JSON: {len(p4)} → {p4[:10]}")

        p5 = re.findall(r'/(\d{10,15})/', html)
        print(f"  /NNNNNNNNNNN/: {len(p5)} → {p5[:10]}")

        # Ищем data-атрибуты
        p6 = re.findall(r'data-[a-z-]*id["\s]*=\s*["\']?(\d{9,15})', html)
        print(f"  data-*-id: {len(p6)} → {p6[:10]}")

        # JSON state / initial data
        p7 = re.findall(r'window\.__STATE__\s*=\s*', html)
        p8 = re.findall(r'window\.__INITIAL_STATE__\s*=\s*', html)
        p9 = re.findall(r'window\.__APP_DATA__\s*=\s*', html)
        print(f"  __STATE__: {len(p7)}, __INITIAL_STATE__: {len(p8)}, __APP_DATA__: {len(p9)}")

        # Ищем в <script> JSON
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        for i, s in enumerate(scripts):
            if len(s) > 500 and ('adId' in s or '"id"' in s or 'listing' in s.lower()):
                ids_in = re.findall(r'(\d{10,15})', s)
                if len(ids_in) > 3:
                    print(f"  [Script {i}] {len(s)} chars, {len(ids_in)} IDs: {ids_in[:8]}")
                    break

        # Собираем все кандидаты
        all_ids = list(dict.fromkeys(p1 + p2 + p3 + p4 + p6))
        print(f"\n  Total unique ad candidates: {len(all_ids)}")

        if all_ids:
            ad_id = all_ids[0]
            # Avito URL: может быть /moscow/telefony/ADID/ или /moscow/ad/ADID/
            ad_urls = [
                f"https://www.avito.ru/moscow/telefony/{ad_id}/",
                f"https://www.avito.ru/moscow/ad/{ad_id}",
            ]
            for ad_url in ad_urls:
                time.sleep(3)
                r_ad = s_av.get(ad_url, headers=hdr({"Accept": "text/html"}), timeout=25)
                print(f"\n  [AVITO ad] {ad_url}")
                print(f"    {r_ad.status_code} | {len(r_ad.content)}B | CT: {r_ad.headers.get('Content-Type','?')[:40]}")
                if r_ad.status_code == 200 and len(r_ad.content) > 10000:
                    ahtml = r_ad.text
                    with open("avito_card2.html", "w", encoding="utf-8") as f:
                        f.write(ahtml)
                    print(f"    Saved avito_card2.html ({len(ahtml)} chars)")
                    # OG
                    og_t = re.findall(r'og:title[^>]*content="([^"]*)"', ahtml)
                    og_d = re.findall(r'og:description[^>]*content="([^"]*)"', ahtml)
                    og_i = re.findall(r'og:image[^>]*content="([^"]*)"', ahtml)
                    print(f"    og:title: {og_t[:2]}")
                    print(f"    og:desc: {og_d[:1]}")
                    print(f"    og:image: {og_i[:5]}")
                    # mmedia
                    mmedia = re.findall(r'https?://[^"\'\s]*avatars\.mmedia\.avito\.st[^"\'\s]*', ahtml)
                    print(f"    mmedia: {len(mmedia)} → {mmedia[:5]}")
                    # JSON-LD
                    ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', ahtml, re.DOTALL)
                    for i, b in enumerate(ld[:3]):
                        try:
                            d = json.loads(b)
                            print(f"    LD[{i}] type={d.get('@type','?')}")
                            if d.get('@type') == 'Product':
                                print(f"      name: {d.get('name','?')[:60]}")
                                offers = d.get('offers', {})
                                print(f"      price: {offers.get('price','?')} {offers.get('priceCurrency','?')}")
                                imgs = d.get('image', [])
                                if isinstance(imgs, list):
                                    print(f"      images: {len(imgs)} → {imgs[:3]}")
                        except:
                            print(f"    LD[{i}]: {b[:150]!r}")
                    # Price
                    price_html = re.findall(r'class="[^"]*price[^"]*"[^>]*>([^<]+)<', ahtml)
                    print(f"    Price HTML: {price_html[:5]}")
                    # Video
                    vids = re.findall(r'(https?://[^"\'\s]*\.mp4[^"\'\s]*)', ahtml)
                    vids2 = re.findall(r'<video[^>]*(?:src|data-src)="([^"]*)"', ahtml)
                    print(f"    Videos: {(vids + vids2)[:5]}")
                    break
    elif r_list.status_code == 429:
        print("  429 — rate limited, waiting 10s and retrying...")
        time.sleep(10)
        r_list = s_av.get("https://www.avito.ru/moscow/telefony",
                          headers=hdr({"Accept": "text/html"}), timeout=25)
        print(f"  [AVITO retry] {r_list.status_code} | {len(r_list.content)}B")
        if r_list.status_code == 200:
            html = r_list.text
            with open("avito_listing2.html", "w", encoding="utf-8") as f:
                f.write(html)
            p1 = re.findall(r'href="[^"]*?/moscow/[^"]*?/(\d{9,15})/"', html)
            p2 = re.findall(r'/ad/(\d+)', html)
            p3 = re.findall(r'"adId"\s*:\s*(\d+)', html)
            all_ids = list(dict.fromkeys(p1 + p2 + p3))
            print(f"  Ad IDs: {len(all_ids)} → {all_ids[:10]}")
else:
    print("  [SKIP] No curl_cffi")

# ============================================================
print("\n" + "=" * 70)
print("3. WB — cookies + session + альтернативные пути")
print("=" * 70)

# 3a: WB через curl_cffi с session (cookies)
if HAS_CFFI:
    print("\n  [WB session cffi]")
    s_wb = cffi_requests.Session(impersonate="chrome")
    # Главная
    r0 = s_wb.get("https://www.wildberries.ru/", timeout=15,
                  headers=hdr({"Accept": "text/html"}))
    print(f"  Home: {r0.status_code} | {len(r0.content)}B")
    print(f"  Cookies: {dict(s_wb.cookies)}")
    time.sleep(3)

    # Каталог с cookies
    r1 = s_wb.get("https://www.wildberries.ru/catalog/0/telefony-smartfony.aspx",
                  timeout=20, headers=hdr({"Accept": "text/html",
                                           "Referer": "https://www.wildberries.ru/"}))
    print(f"  Catalog: {r1.status_code} | {len(r1.content)}B")
    if r1.status_code == 200:
        nms = re.findall(r'/catalog/(\d{6,15})/detail\.aspx', r1.text)
        nms2 = re.findall(r'"nm"\s*:\s*(\d{6,15})', r1.text)
        all_nms = list(dict.fromkeys(nms + nms2))
        print(f"  nms: {len(all_nms)} → {all_nms[:10]}")
        with open("wb_cat_cffi.html", "w", encoding="utf-8") as f:
            f.write(r1.text)
    elif r1.status_code == 498:
        print("  Still 498 — checking response body...")
        print(f"  Body: {r1.text[:300]!r}")

    # Search API с session
    time.sleep(2)
    r2 = s_wb.get("https://search.wb.ru/exactmatch/ru/common/v5/search?query=телефон&nm=1&resultset=catalog&sort=popular",
                  headers=hdr({"Accept": "application/json",
                               "Origin": "https://www.wildberries.ru",
                               "Referer": "https://www.wildberries.ru/"}), timeout=15)
    print(f"\n  Search v5: {r2.status_code} | {len(r2.content)}B | CT: {r2.headers.get('Content-Type','?')[:40]}")
    if r2.status_code == 200:
        data = r2.json()
        prods = data.get("data", {}).get("products", [])
        print(f"  Products: {len(prods)}")
        for p in prods[:5]:
            print(f"    nm={p.get('id')} | {p.get('brand','?')} | {p.get('name','?')[:40]} | {p.get('salePriceU',0)//100}₽")
        with open("wb_search_cffi2.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

# 3b: WB — пробуем API через static-basket с реальными nm из search
print("\n  [WB static-basket with known nms]")
# Известные nm из разных источников
test_nms = [199298871, 123456789, 100000000, 200000000]
for nm in test_nms:
    vol = nm // 100000
    part = nm // 100
    for prefix in ["static-basket", "basket"]:
        for n in [10, 20, 50]:
            url = f"https://{prefix}-{n}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/{nm}-info.json"
            try:
                r = requests.get(url, headers=hdr({"Accept": "application/json"}), timeout=(4, 8))
                if r.status_code == 200:
                    print(f"  {prefix}-{n} nm={nm}: 200! {len(r.content)}B")
                    data = r.json()
                    print(f"    Keys: {list(data.keys())[:12]}")
                    with open(f"wb_sb_{nm}.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    break
            except:
                pass
            time.sleep(0.2)
    else:
        continue
    break

# 3c: WB — пробуем content API (wbx-content)
print("\n  [WB content API]")
# Сначала DNS check
import socket
for host in ["wbx-content-v2.wbcontent.ru", "wbx-content-v1.wbcontent.ru", "static-basket-10.wbbasket.ru"]:
    try:
        ip = socket.getaddrinfo(host, 443, socket.AF_INET)[0][4][0]
        print(f"  {host} → {ip}")
    except Exception as e:
        print(f"  {host} → DNS FAIL")

# Если DNS есть — пробуем
if HAS_CFFI:
    for host in ["wbx-content-v2.wbcontent.ru", "wbx-content-v1.wbcontent.ru"]:
        try:
            socket.getaddrinfo(host, 443, socket.AF_INET)
            nm = 199298871
            url = f"https://{host}/api/v1/card/{nm}"
            r = s_wb.get(url, headers=hdr({"Accept": "application/json"}), timeout=10)
            print(f"  {host}/card/{nm}: {r.status_code} | {len(r.content)}B")
            if r.status_code == 200:
                print(f"    Preview: {r.text[:200]}")
        except Exception as e:
            print(f"  {host}: ERR {str(e)[:80]}")

# ============================================================
print("\n" + "=" * 70)
print("4. OZON — challenge URL + alternative")
print("=" * 70)

if HAS_CFFI:
    # 4a: Разбираем challenge JSON
    print("\n  [OZON challenge analysis]")
    s_oz = cffi_requests.Session(impersonate="chrome")
    r0 = s_oz.get("https://www.ozon.ru/", timeout=15)
    print(f"  Home: {r0.status_code} | {len(r0.content)}B")
    time.sleep(3)

    r1 = s_oz.get("https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/1600562267/",
                  headers=hdr({"Accept": "application/json",
                               "Referer": "https://www.ozon.ru/product/1600562267/"}), timeout=20)
    print(f"  Composer: {r1.status_code} | {len(r1.content)}B")
    if r1.status_code in (200, 403):
        try:
            data = r1.json()
            print(f"  Keys: {list(data.keys())}")
            if "challengeURL" in data:
                ch_url = data["challengeURL"]
                print(f"  challengeURL: {ch_url}")
                # Пробуем открыть challenge
                r_ch = s_oz.get(ch_url, timeout=15)
                print(f"  Challenge: {r_ch.status_code} | {len(r_ch.content)}B | CT: {r_ch.headers.get('Content-Type','?')[:40]}")
                if r_ch.status_code == 200:
                    # Ищем token/cookie
                    ch_html = r_ch.text
                    tokens = re.findall(r'(?:token|key|solution|captcha)["\s]*[:=]\s*["\']([^"\']+)["\']', ch_html, re.I)
                    print(f"  Tokens found: {tokens[:5]}")
                    # Scripts
                    scripts = re.findall(r'<script[^>]*src="([^"]+)"', ch_html)
                    print(f"  Scripts: {scripts[:5]}")
                    # Inline scripts
                    inline = re.findall(r'<script[^>]*>(.*?)</script>', ch_html, re.DOTALL)
                    for i, s in enumerate(inline[:3]):
                        if len(s) > 50:
                            print(f"  Script[{i}]: {s[:200]!r}")
        except Exception as e:
            print(f"  Parse: {e}")

    # 4b: Ozon — пробуем без session, чистый GET на product page
    print("\n  [OZON direct product page]")
    s_oz2 = cffi_requests.Session(impersonate="chrome")
    r2 = s_oz2.get("https://www.ozon.ru/product/1600562267/",
                   headers=hdr({"Accept": "text/html"}), timeout=20)
    print(f"  Product: {r2.status_code} | {len(r2.content)}B | CT: {r2.headers.get('Content-Type','?')[:40]}")
    if r2.status_code == 200:
        with open("ozon_product.html", "w", encoding="utf-8") as f:
            f.write(r2.text)
        # JSON-LD
        ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', r2.text, re.DOTALL)
        print(f"  JSON-LD: {len(ld)}")
        for i, b in enumerate(ld[:3]):
            print(f"  LD[{i}]: {b[:200]!r}")
        # State
        state = re.findall(r'window\.__STATE__\s*=\s*(\{.*?\});?\s*</script>', r2.text, re.DOTALL)
        print(f"  State: {len(state)}")
        # Ozon CDN images
        oz_imgs = re.findall(r'https?://[^"\'\s]*ozoncdn\.net[^"\'\s]*', r2.text)
        print(f"  ozoncdn: {len(oz_imgs)} → {oz_imgs[:5]}")

    # 4c: Ozon — mobile с safari_ios
    print("\n  [OZON mobile safari_ios]")
    s_mob = cffi_requests.Session(impersonate="safari_ios")
    r3 = s_mob.get("https://m.ozon.ru/product/1600562267/",
                   headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                            "Accept": "text/html", "Accept-Language": "ru-RU"},
                   timeout=20)
    print(f"  Mobile: {r3.status_code} | {len(r3.content)}B | CT: {r3.headers.get('Content-Type','?')[:40]}")
    if r3.status_code == 200:
        with open("ozon_m.html", "w", encoding="utf-8") as f:
            f.write(r3.text)
        ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', r3.text, re.DOTALL)
        print(f"  JSON-LD: {len(ld)}")
        for i, b in enumerate(ld[:3]):
            print(f"  LD[{i}]: {b[:200]!r}")

# ============================================================
print("\n" + "=" * 70)
print("5. BONUS — WB через search.wb.ru v4 (старый, может работать)")
print("=" * 70)

# Старый search API
wb_search_urls = [
    "https://search.wb.ru/suggest/v1/search?query=телефон",
    "https://search.wb.ru/exactmatch/ru/common/v4/search?query=телефон&resultset=catalog",
    "https://search.wb.ru/exactmatch/ru/common/v5/search?query=телефон&resultset=catalog&sort=popular&nm=1",
    "https://catalog.wb.ru/catalog/0/telefony-smartfony.aspx",
]
for u in wb_search_urls:
    try:
        r = requests.get(u, headers=hdr({"Accept": "application/json, text/html"}), timeout=(6, 12))
        print(f"  {u.split('wb.ru')[1][:50]}: {r.status_code} | {len(r.content)}B | CT: {r.headers.get('Content-Type','?')[:30]}")
        if r.status_code == 200 and len(r.content) > 100:
            preview = r.text[:200]
            print(f"    {preview!r}")
    except Exception as e:
        print(f"  {u.split('wb.ru')[1][:50]}: ERR {str(e)[:60]}")
    time.sleep(1)

# ============================================================
print("\n" + "=" * 70)
print("DONE — diag_v7")
print("=" * 70)

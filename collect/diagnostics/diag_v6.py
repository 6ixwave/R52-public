"""Диагностика v6: разбор HTML, static-basket WB, Ozon JSON, Farpost структура."""
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
print("1. AVITO — глубокий разбор listing HTML")
print("=" * 70)

if HAS_CFFI:
    s_av = cffi_requests.Session(impersonate="chrome")
    r_list = s_av.get("https://www.avito.ru/moscow/telefony",
                      headers=hdr({"Accept": "text/html"}), timeout=25)
    print(f"  [AVITO listing] {r_list.status_code} | {len(r_list.content)}B")

    if r_list.status_code == 200:
        html = r_list.text
        # Сохраняем для анализа
        with open("avito_listing.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Saved avito_listing.html ({len(html)} chars)")

        # Широкий поиск ID
        print("\n  --- Поиск ad ID (широкие паттерны) ---")
        # 1: /ad/NNN
        p1 = re.findall(r'/ad/(\d+)', html)
        print(f"  /ad/NNN: {len(p1)} → {p1[:10]}")
        # 2: data-ad-id
        p2 = re.findall(r'data-ad-id="(\d+)"', html)
        print(f"  data-ad-id: {len(p2)} → {p2[:10]}")
        # 3: "adId": NNN
        p3 = re.findall(r'"adId"\s*:\s*(\d+)', html)
        print(f"  'adId': NNN: {len(p3)} → {p3[:10]}")
        # 4: "id": NNN (в JSON)
        p4 = re.findall(r'"id"\s*:\s*(\d{9,15})', html)
        print(f"  'id': 9-15 digits: {len(p4)} → {p4[:10]}")
        # 5: href с /moscow/ и цифрами
        p5 = re.findall(r'href="([^"]*?/moscow/[^"]*?/(\d{9,15})[^"]*)"', html)
        print(f"  /moscow/.../NNN: {len(p5)} → {[x[0][:60] for x in p5[:5]]}")
        # 6: data-attributes
        p6 = re.findall(r'data-[^=]*id[^=]*="(\d{9,15})"', html)
        print(f"  data-*-id: {len(p6)} → {p6[:10]}")
        # 7: JSON state
        state_match = re.findall(r'window\.__STATE__\s*=\s*(\{.*?\});\s*</script>', html, re.DOTALL)
        print(f"  __STATE__: {len(state_match)} blocks")
        if state_match:
            try:
                state = json.loads(state_match[0])
                print(f"  State keys: {list(state.keys())[:10]}")
            except:
                print(f"  State parse failed, first 200: {state_match[0][:200]}")
        # 8: __INITIAL_STATE__
        init_match = re.findall(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});', html, re.DOTALL)
        print(f"  __INITIAL_STATE__: {len(init_match)} blocks")
        # 9: avito JSON-LD
        ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
        print(f"  JSON-LD: {len(ld)} blocks")
        for i, b in enumerate(ld[:2]):
            print(f"  LD[{i}]: {b[:200]!r}")
        # 10: Ищем любые 10-15 цифровые ID
        p10 = re.findall(r'\b(\d{10,15})\b', html)
        unique_p10 = list(dict.fromkeys(p10))
        print(f"  Bare 10-15 digit IDs: {len(unique_p10)} → {unique_p10[:15]}")

        # 11: Ищем section/article/li с ссылками
        sections = re.findall(r'<(?:section|article|li|div)[^>]*class="[^"]*card[^"]*"[^>]*>(.*?)</(?:section|article|li|div)>', html, re.DOTALL)
        print(f"  card sections: {len(sections)}")
        if sections:
            print(f"  First card: {sections[0][:300]!r}")

        # 12: Ищем "title" + "id" пары
        title_id = re.findall(r'"title"\s*:\s*"([^"]{10,80})"[^}]*?"id"\s*:\s*(\d+)', html)
        print(f"  title+id pairs: {len(title_id)} → {title_id[:5]}")

        # Если нашли ID — грузим карточку
        ad_ids = list(dict.fromkeys(p1 + p2 + p3 + p4))
        if not ad_ids and unique_p10:
            ad_ids = unique_p10[:5]
        if ad_ids:
            ad_id = ad_ids[0]
            ad_url = f"https://www.avito.ru/moscow/ad/{ad_id}"
            print(f"\n  [AVITO ad] {ad_url}")
            time.sleep(2)
            r_ad = s_av.get(ad_url, headers=hdr({"Accept": "text/html"}), timeout=25)
            print(f"    Status: {r_ad.status_code} | Size: {len(r_ad.content)}B")
            if r_ad.status_code == 200:
                ahtml = r_ad.text
                with open("avito_card.html", "w", encoding="utf-8") as f:
                    f.write(ahtml)
                print(f"    Saved avito_card.html ({len(ahtml)} chars)")
                # OG
                og_t = re.findall(r'og:title[^>]*content="([^"]*)"', ahtml)
                og_d = re.findall(r'og:description[^>]*content="([^"]*)"', ahtml)
                og_i = re.findall(r'og:image[^>]*content="([^"]*)"', ahtml)
                print(f"    og:title: {og_t[:2]}")
                print(f"    og:desc: {og_d[:1]}")
                print(f"    og:image: {og_i[:5]}")
                # mmedia photos
                mmedia = re.findall(r'https?://[^"\'\s]*avatars\.mmedia\.avito\.st[^"\'\s]*', ahtml)
                print(f"    mmedia photos: {len(mmedia)} → {mmedia[:5]}")
                # Price
                price = re.findall(r'"price"\s*:\s*\{[^}]*"value"\s*:\s*(\d+)', ahtml)
                price2 = re.findall(r'class="[^"]*price[^"]*"[^>]*>([^<]+)<', ahtml)
                print(f"    Price JSON: {price[:3]}")
                print(f"    Price HTML: {price2[:3]}")
                # JSON-LD
                ld_ad = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', ahtml, re.DOTALL)
                for i, b in enumerate(ld_ad[:3]):
                    try:
                        d = json.loads(b)
                        print(f"    LD[{i}] type={d.get('@type','?')}")
                        if d.get('@type') == 'Product':
                            print(f"      name: {d.get('name','?')[:50]}")
                            print(f"      offers: {json.dumps(d.get('offers',{}), ensure_ascii=False)[:200]}")
                            print(f"      images: {d.get('image','?')[:200] if isinstance(d.get('image'),str) else d.get('image',['?'])[:3]}")
                    except:
                        print(f"    LD[{i}]: {b[:150]!r}")
            time.sleep(2)
else:
    print("  [SKIP] No curl_cffi")

# ============================================================
print("\n" + "=" * 70)
print("2. FARPOST — поиск структуры URL")
print("=" * 70)

h_fp = hdr({"Accept": "text/html", "Accept-Charset": "windows-1251,utf-8;q=0.7"})
r_fp = requests.get("https://www.farpost.ru/", headers=h_fp, timeout=(8, 15))
print(f"  [FP home] {r_fp.status_code} | {len(r_fp.content)}B")

if r_fp.status_code == 200:
    html = r_fp.content.decode("windows-1251", errors="replace")
    with open("fp_home3.html", "w", encoding="windows-1251", errors="replace") as f:
        f.write(html)

    # Все ссылки
    all_hrefs = re.findall(r'href="([^"]+)"', html)
    print(f"\n  Total hrefs: {len(all_hrefs)}")
    # Группируем по паттерну
    groups = {}
    for h in all_hrefs:
        # Упрощаем
        short = h.split('?')[0]
        if '/' in short:
            parts = short.split('/')
            key = parts[1] if len(parts) > 1 else '?'
        else:
            key = 'root'
        groups.setdefault(key, []).append(h)
    for k, v in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"  /{k}/: {len(v)} links → {v[:3]}")

    # Ищем nav / menu
    navs = re.findall(r'<nav[^>]*>(.*?)</nav>', html, re.DOTALL)
    print(f"\n  Nav blocks: {len(navs)}")
    for i, n in enumerate(navs[:3]):
        links = re.findall(r'href="([^"]+)"', n)
        print(f"  Nav[{i}] ({len(links)} links): {links[:10]}")

    # Пробуем известные категории
    cat_urls = [
        "https://www.farpost.ru/l/telefony/",
        "https://www.farpost.ru/l/elektronika/",
        "https://www.farpost.ru/l/1/telefony/",
        "https://www.farpost.ru/geo/moskva/l/telefony/",
        "https://www.farpost.ru/l/1/",
        "https://www.farpost.ru/l/2/",
        "https://www.farpost.ru/l/3/",
    ]
    for cu in cat_urls:
        try:
            r_cat = requests.get(cu, headers=h_fp, timeout=(6, 12))
            status = r_cat.status_code
            size = len(r_cat.content)
            print(f"\n  [FP] {cu} → {status} | {size}B")
            if status == 200 and size > 5000:
                chtml = r_cat.content.decode("windows-1251", errors="replace")
                # Ищем ad links
                ad_links = re.findall(r'href="([^"]*?/(\d{5,12})/[^"]*)"', chtml)
                ad_links2 = re.findall(r'href="([^"]*?/l/(\d+)/[^"]*)"', chtml)
                print(f"    Ad links (num): {len(ad_links)} → {[x[0][:50] for x in ad_links[:5]]}")
                print(f"    Ad links (/l/): {len(ad_links2)} → {[x[0][:50] for x in ad_links2[:5]]}")
                if ad_links:
                    # Берём первый
                    ad_path = ad_links[0][0]
                    if not ad_path.startswith('http'):
                        ad_path = "https://www.farpost.ru" + ad_path
                    print(f"    → Ad: {ad_path}")
                    r_ad = requests.get(ad_path, headers=h_fp, timeout=(8, 15))
                    print(f"    Ad page: {r_ad.status_code} | {len(r_ad.content)}B")
                    if r_ad.status_code == 200:
                        ahtml = r_ad.content.decode("windows-1251", errors="replace")
                        with open("fp_card.html", "w", encoding="windows-1251", errors="replace") as f:
                            f.write(ahtml)
                        # OG
                        og_t = re.findall(r'og:title[^>]*content="([^"]*)"', ahtml)
                        og_i = re.findall(r'og:image[^>]*content="([^"]*)"', ahtml)
                        fp_imgs = re.findall(r'https?://img\.farpost\.ru/[^"\'\s<>]+', ahtml)
                        print(f"    og:title: {og_t[:2]}")
                        print(f"    og:image: {og_i[:5]}")
                        print(f"    img.farpost.ru: {len(fp_imgs)} → {fp_imgs[:5]}")
                        # JSON-LD
                        ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', ahtml, re.DOTALL)
                        for i, b in enumerate(ld[:3]):
                            print(f"    LD[{i}]: {b[:200]!r}")
                    break
        except Exception as e:
            print(f"  [FP] {cu} → ERR: {e}")
        time.sleep(0.5)

# ============================================================
print("\n" + "=" * 70)
print("3. WB — static-basket + curl_cffi + новые API")
print("=" * 70)

# 3a: static-basket (альтернативный CDN)
print("\n  [WB static-basket]")
test_nms = [123456789, 199298871]
for nm in test_nms:
    vol = nm // 100000
    part = nm // 100
    for prefix in ["static-basket", "basket"]:
        for n in [10, 20, 50]:
            url = f"https://{prefix}-{n}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/{nm}-info.json"
            try:
                r = requests.get(url, headers=hdr({"Accept": "application/json"}), timeout=(4, 8))
                if r.status_code != 404:
                    print(f"    {prefix}-{n} nm={nm}: {r.status_code} | {len(r.content)}B")
                    if r.status_code == 200:
                        data = r.json()
                        print(f"      Keys: {list(data.keys())[:10]}")
                        with open(f"wb_{prefix}_{nm}.json", "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                pass
            time.sleep(0.2)

# 3b: WB через curl_cffi (catalog + card)
if HAS_CFFI:
    print("\n  [WB curl_cffi catalog]")
    s_wb = cffi_requests.Session(impersonate="chrome")
    # Главная
    r0 = s_wb.get("https://www.wildberries.ru/", timeout=15,
                  headers=hdr({"Accept": "text/html"}))
    print(f"    Home: {r0.status_code} | {len(r0.content)}B")
    # Ищем nm на главной
    nms_home = re.findall(r'/catalog/(\d{6,15})/detail\.aspx', r0.text)
    nms_home2 = re.findall(r'"nm"\s*:\s*(\d{6,15})', r0.text)
    print(f"    nms on home: {list(set(nms_home + nms_home2))[:10]}")
    time.sleep(2)

    # Каталог
    r1 = s_wb.get("https://www.wildberries.ru/catalog/0/elektronika.aspx",
                  timeout=20, headers=hdr({"Accept": "text/html"}))
    print(f"    Catalog: {r1.status_code} | {len(r1.content)}B")
    if r1.status_code == 200:
        nms_cat = re.findall(r'/catalog/(\d{6,15})/detail\.aspx', r1.text)
        nms_cat2 = re.findall(r'"nm"\s*:\s*(\d{6,15})', r1.text)
        nms_cat3 = re.findall(r'data-nm="(\d+)"', r1.text)
        all_nms = list(dict.fromkeys(nms_cat + nms_cat2 + nms_cat3))
        print(f"    nms in catalog: {len(all_nms)} → {all_nms[:10]}")
        with open("wb_catalog.html", "w", encoding="utf-8") as f:
            f.write(r1.text)
        # Берём первый nm и пробуем card
        if all_nms:
            nm = all_nms[0]
            time.sleep(2)
            r2 = s_wb.get(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                          timeout=20, headers=hdr({"Accept": "text/html"}))
            print(f"    Card {nm}: {r2.status_code} | {len(r2.content)}B")
            if r2.status_code == 200:
                with open(f"wb_card_{nm}.html", "w", encoding="utf-8") as f:
                    f.write(r2.text)
                # OG
                og_t = re.findall(r'og:title[^>]*content="([^"]*)"', r2.text)
                og_i = re.findall(r'og:image[^>]*content="([^"]*)"', r2.text)
                print(f"    og:title: {og_t[:2]}")
                print(f"    og:image: {og_i[:5]}")
                # JSON-LD
                ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', r2.text, re.DOTALL)
                for i, b in enumerate(ld[:3]):
                    print(f"    LD[{i}]: {b[:200]!r}")
                # wbbasket photos
                wb_imgs = re.findall(r'https?://[^"\'\s]*wbbasket\.ru[^"\'\s]*', r2.text)
                print(f"    wbbasket imgs: {len(wb_imgs)} → {wb_imgs[:5]}")

# 3c: WB search.wb.ru с curl_cffi
if HAS_CFFI:
    print("\n  [WB search via cffi]")
    try:
        s = cffi_requests.Session(impersonate="chrome")
        r = s.get("https://search.wb.ru/exactmatch/ru/common/v5/search?query=телефон&nm=1&resultset=catalog&sort=popular",
                  headers=hdr({"Accept": "application/json", "Origin": "https://www.wildberries.ru",
                               "Referer": "https://www.wildberries.ru/"}), timeout=15)
        print(f"    v5 search: {r.status_code} | {len(r.content)}B | CT: {r.headers.get('Content-Type','?')[:40]}")
        if r.status_code == 200:
            data = r.json()
            prods = data.get("data", {}).get("products", [])
            print(f"    Products: {len(prods)}")
            for p in prods[:5]:
                print(f"      nm={p.get('id')} | {p.get('brand','?')} | {p.get('name','?')[:40]}")
            with open("wb_search_cffi.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        time.sleep(1)
        # v4
        r2 = s.get("https://search.wb.ru/suggest/v1/search?query=телефон",
                   headers=hdr({"Accept": "application/json"}), timeout=15)
        print(f"    v4 suggest: {r2.status_code} | {len(r2.content)}B")
    except Exception as e:
        print(f"    ERR: {e}")

# ============================================================
print("\n" + "=" * 70)
print("4. OZON — разбор JSON 403 + альтернативные API")
print("=" * 70)

if HAS_CFFI:
    print("\n  [OZON composer JSON 403]")
    s = cffi_requests.Session(impersonate="chrome")
    s.get("https://www.ozon.ru/", timeout=15)
    time.sleep(3)
    r = s.get("https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/1600562267/",
              headers=hdr({"Accept": "application/json", "Referer": "https://www.ozon.ru/product/1600562267/"}),
              timeout=20)
    print(f"    Composer: {r.status_code} | {len(r.content)}B | CT: {r.headers.get('Content-Type','?')[:40]}")
    if r.status_code in (200, 403) and len(r.content) > 100:
        try:
            data = r.json()
            print(f"    JSON keys: {list(data.keys())[:15]}")
            if "widgetStates" in data:
                ws = data["widgetStates"]
                print(f"    Widgets: {list(ws.keys())[:15]}")
            if "seo" in data:
                print(f"    SEO: {json.dumps(data['seo'], ensure_ascii=False)[:300]}")
            with open("ozon_composer_403.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"    Saved ozon_composer_403.json")
        except Exception as e:
            print(f"    Not JSON: {e}")
            print(f"    Raw: {r.text[:300]}")

    # Ozon: пробуем api.ozon.ru
    print("\n  [OZON api.ozon.ru]")
    ozon_api_urls = [
        "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/1600562267/",
        "https://api.ozon.ru/entrypoint-api.bx/page/json/v2?url=/product/1600562267/",
        "https://api.ozon.ru/product/1600562267/",
    ]
    for u in ozon_api_urls:
        try:
            r = s.get(u, headers=hdr({"Accept": "application/json"}), timeout=15)
            print(f"    {u.split('ozon.ru')[1][:50]}: {r.status_code} | {len(r.content)}B")
            if r.status_code == 200 and len(r.content) > 100:
                print(f"      Preview: {r.text[:200]}")
        except Exception as e:
            print(f"    {u.split('ozon.ru')[1][:50]}: ERR {str(e)[:80]}")
        time.sleep(1)

    # Ozon: mobile API
    print("\n  [OZON mobile API]")
    mob_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    s_mob = cffi_requests.Session(impersonate="safari_ios")
    r = s_mob.get("https://m.ozon.ru/product/1600562267/",
                  headers={"User-Agent": mob_ua, "Accept": "text/html", "Accept-Language": "ru-RU"},
                  timeout=20)
    print(f"    m.ozon product: {r.status_code} | {len(r.content)}B")
    if r.status_code == 200:
        with open("ozon_m_card.html", "w", encoding="utf-8") as f:
            f.write(r.text)
        ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', r.text, re.DOTALL)
        print(f"    JSON-LD: {len(ld)}")
        for i, b in enumerate(ld[:3]):
            print(f"    LD[{i}]: {b[:200]!r}")
        # Ozon CDN images
        oz_imgs = re.findall(r'https?://[^"\'\s]*ozoncdn\.net[^"\'\s]*', r.text)
        print(f"    ozoncdn imgs: {len(oz_imgs)} → {oz_imgs[:5]}")

# ============================================================
print("\n" + "=" * 70)
print("DONE — diag_v6")
print("=" * 70)

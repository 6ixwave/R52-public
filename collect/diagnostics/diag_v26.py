"""diag_v26 — Farpost item-страница + новый прокси на WB/Ozon/Avito.

Фокус:
1. Farpost: открыть 3 реальные item-страницы, извлечь ВСЁ (JSON-LD, OG, фото, видео, цена)
2. Farpost: проверить CDN изображений
3. Новый прокси 192.0.2.10:8081: WB, Ozon, Avito, Farpost
4. Farpost: search → item (полный цикл)

Бюджет: ~200 сек.
"""
import json
import re
import sys
import time

import requests

T0 = time.monotonic()
OUT = {}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

HDRS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

PROXY_NEW = "http://192.0.2.10:8081"
PROXY_OLD = "http://198.51.100.20:80"
PX_NEW = {"http": PROXY_NEW, "https": PROXY_NEW}
PX_OLD = {"http": PROXY_OLD, "https": PROXY_OLD}


def log(msg):
    print(f"  {msg}", flush=True)


def make_session(headers=None, proxies=None):
    s = requests.Session()
    h = dict(HDRS)
    if headers:
        h.update(headers)
    s.headers.update(h)
    if proxies:
        s.proxies.update(proxies)
    return s


def get(sess, url, timeout=(8, 15), **kw):
    t0 = time.monotonic()
    try:
        r = sess.get(url, timeout=timeout, **kw)
        lat = time.monotonic() - t0
        return {"status": r.status_code, "size": len(r.content),
                "text": r.text[:120000], "headers": dict(r.headers),
                "latency": round(lat, 2), "url": r.url}
    except Exception as e:
        return {"status": -1, "size": 0, "text": "", "headers": {},
                "latency": round(time.monotonic() - t0, 2),
                "error": str(e), "url": url}


def extract_item_data(html, base_url="https://www.farpost.ru"):
    """Извлекает все данные из item-страницы Farpost."""
    data = {}

    # JSON-LD
    jsonld_blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.I)
    jsonld_parsed = []
    for b in jsonld_blocks:
        try:
            obj = json.loads(b.strip())
            jsonld_parsed.append(obj)
        except json.JSONDecodeError:
            pass
    data["jsonld_count"] = len(jsonld_blocks)
    data["jsonld_parsed"] = jsonld_parsed

    # OG tags
    og_tags = re.findall(
        r'<meta[^>]+property=["\']og:([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
        html, re.I)
    data["og"] = {k: v for k, v in og_tags}

    # Title
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.I)
    data["title"] = m.group(1).strip() if m else ""

    # Price patterns
    price_patterns = [
        r'class=["\'][^"\']*price[^"\']*["\'][^>]*>([^<]{1,50})<',
        r'data-price=["\'](\d+)["\']',
        r'[\d\s]{3,}\s*руб',
        r'itemprop=["\']price["\'][^>]*content=["\']([^"\']+)["\']',
    ]
    prices = []
    for p in price_patterns:
        found = re.findall(p, html, re.I)
        if found:
            prices.extend([f.strip() for f in found][:5])
    data["prices"] = prices[:10]

    # Photos: img tags with farpost/baza CDN
    img_patterns = [
        r'<img[^>]+src=["\']([^"\']*(?:farpost|baza|upload|img)[^"\']*)["\']',
        r'<img[^>]+data-src=["\']([^"\']*(?:farpost|baza|upload|img)[^"\']*)["\']',
        r'<img[^>]+data-original=["\']([^"\']*(?:farpost|baza|upload|img)[^"\']*)["\']',
        r'src=["\']([^"\']*(?:avatars|thumbs|photo|gallery)[^"\']*)["\']',
        r'"(?:src|url|thumb|photo|image)"\s*:\s*"([^"]+)"',
    ]
    imgs = set()
    for p in img_patterns:
        found = re.findall(p, html, re.I)
        for f in found:
            f = f.strip()
            if len(f) > 5 and not f.endswith(('.css', '.js', '.svg', '.gif', '.ico')):
                imgs.add(f)
    data["photos_count"] = len(imgs)
    data["photos_sample"] = list(imgs)[:15]

    # Video
    video_patterns = [
        r'<video[^>]+src=["\']([^"\']+)["\']',
        r'<source[^>]+src=["\']([^"\']*(?:mp4|webm|video)[^"\']*)["\']',
        r'"video(?:Url|_url|Src)"\s*:\s*"([^"]+)"',
        r'data-video=["\']([^"\']+)["\']',
        r'youtube\.com/embed/([\w-]+)',
        r'vk\.com/video(?:_ext)?(?:/\d+)?\?id=(\d+)',
        r'youtu\.be/([\w-]+)',
    ]
    videos = []
    for p in video_patterns:
        found = re.findall(p, html, re.I)
        videos.extend(found)
    data["videos"] = list(set(videos))[:10]

    # Description
    desc_patterns = [
        r'<div[^>]+class=["\'][^"\']*desc[^"\']*["\'][^>]*>(.*?)</div>',
        r'<div[^>]+id=["\']description["\'][^>]*>(.*?)</div>',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']',
    ]
    desc = ""
    for p in desc_patterns:
        m = re.search(p, html, re.DOTALL | re.I)
        if m and len(m.group(1).strip()) > 50:
            desc = re.sub(r'<[^>]+>', ' ', m.group(1)).strip()[:500]
            break
    data["description"] = desc

    # Specs / characteristics
    spec_patterns = [
        r'<(?:table|dl|div)[^>]+class=["\'][^"\']*(?:spec|param|char|attr|feature)[^"\']*["\'][^>]*>(.*?)</(?:table|dl|div)>',
        r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>',
    ]
    specs = []
    for p in spec_patterns:
        found = re.findall(p, html, re.DOTALL | re.I)
        specs.extend(found[:20])
    data["specs_count"] = len(specs)
    data["specs_sample"] = [str(s)[:200] for s in specs[:5]]

    # Gallery / carousel
    gallery = re.findall(
        r'class=["\'][^"\']*(?:gallery|carousel|slider|photo|image)[^"\']*["\'][^>]*>',
        html, re.I)
    data["gallery_blocks"] = len(gallery)

    # data-attributes with image URLs
    data_imgs = re.findall(
        r'data-(?:src|thumb|img|photo|image|url)=["\']([^"\']+)["\']', html, re.I)
    data["data_imgs"] = list(set(data_imgs))[:10]

    return data


# ============================================================
print("=" * 70, flush=True)
print("1. FARPOST — реальные item-страницы (прямое соединение)", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
fp_items = {}

sess = make_session()

# Реальные item-URLs из v25
item_urls = [
    "/ni/krasnoyarsk/auto/wheel/tire/shiny-139122082.html",
    "/khabarovsk/auto/wheel/tire/shiny-michelin-x-ice-snow-suv-275-50r21-137723592.html",
    "/ni/khabarovsk/auto/wheel/tire/gruzovaja-shina-long-march-lm777-425-85r21-24pr-173c-137440009.html",
]

# Сначала открываем категорию для cookie
r_cat = get(sess, "https://www.farpost.ru/auto/wheel/")
log(f"[cat] {r_cat['status']} | {r_cat['size']}B | {r_cat['latency']}s")

for i, path in enumerate(item_urls):
    if time.monotonic() - t_sec > 50:
        break
    url = f"https://www.farpost.ru{path}"
    r = get(sess, url)
    short = path.split("/")[-1][:40]
    log(f"[item {short}] {r['status']} | {r['size']}B | {r['latency']}s")

    if r["status"] == 200:
        item_data = extract_item_data(r["text"])
        fp_items[short] = item_data

        # Сохраняем HTML первой item
        if i == 0:
            with open("fp_v26_item1.html", "w", encoding="utf-8") as f:
                f.write(r["text"])
            log("  saved fp_v26_item1.html")

        # Короткая сводка
        log(f"  title={item_data.get('title','')[:60]}")
        log(f"  jsonld={item_data['jsonld_count']} og={len(item_data['og'])}")
        log(f"  price={item_data['prices'][:3]}")
        log(f"  photos={item_data['photos_count']} sample={item_data['photos_sample'][:3]}")
        log(f"  videos={item_data['videos'][:3]}")
        log(f"  desc={item_data['description'][:80]}")
        log(f"  specs={item_data['specs_count']} gallery={item_data['gallery_blocks']}")
    else:
        fp_items[short] = {"status": r["status"], "error": r.get("error", "")}

# 1b. Проверка CDN изображений Farpost
if fp_items:
    first_item = list(fp_items.values())[0]
    if isinstance(first_item, dict) and first_item.get("photos_sample"):
        test_img = first_item["photos_sample"][0]
        if test_img.startswith("/"):
            test_img = f"https://www.farpost.ru{test_img}"
        log(f"\n[CDN test] {test_img[:80]}")
        try:
            r_img = sess.get(test_img, timeout=(5, 10), stream=True)
            ct = r_img.headers.get("Content-Type", "")
            sz = len(r_img.content)
            log(f"  {r_img.status_code} | {ct} | {sz}B")
            fp_items["cdn_test"] = {"url": test_img[:100], "status": r_img.status_code,
                                     "content_type": ct, "size": sz}
        except Exception as e:
            log(f"  ERR: {e}")
            fp_items["cdn_test"] = {"error": str(e)}

# 1c. Farpost search → item (полный цикл)
r_search = get(sess, "https://www.farpost.ru/dir?query=iphone+15")
log(f"\n[search iphone15] {r_search['status']} | {r_search['size']}B")
search_items = re.findall(
    r'class=["\']bulletinLink[^"\']*["\'][^>]*href=["\']([^"\']+\.html)["\']',
    r_search["text"], re.I)
fp_items["search_cycle"] = {
    "status": r_search["status"],
    "items_found": len(set(search_items)),
    "sample": list(set(search_items))[:5],
}
log(f"  items from search: {len(set(search_items))}")

OUT["FARPOST_ITEMS"] = fp_items
log(f"FARPOST: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("2. НОВЫЙ ПРОКСИ 192.0.2.10:8081 — WB / Ozon / Avito / Farpost", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
px_results = {}

# 2a. WB через новый прокси
wb_sess = make_session(proxies=PX_NEW)
r_wb_home = get(wb_sess, "https://www.wildberries.ru/", timeout=(10, 15))
log(f"[WB home] {r_wb_home['status']} | {r_wb_home['size']}B | {r_wb_home['latency']}s")
r_wb_card = get(wb_sess, "https://www.wildberries.ru/catalog/100000001/detail.aspx",
                timeout=(10, 15))
log(f"[WB card] {r_wb_card['status']} | {r_wb_card['size']}B | {r_wb_card['latency']}s")
px_results["WB"] = {
    "home": r_wb_home["status"], "card": r_wb_card["status"],
    "card_size": r_wb_card["size"],
    "antibot": "antibot" in r_wb_card["text"].lower(),
}

# 2b. Ozon через новый прокси
oz_sess = make_session(proxies=PX_NEW)
r_oz_home = get(oz_sess, "https://www.ozon.ru/", timeout=(10, 15))
log(f"[Ozon home] {r_oz_home['status']} | {r_oz_home['size']}B | {r_oz_home['latency']}s")
r_oz_card = get(oz_sess, "https://www.ozon.ru/product/100000001/", timeout=(10, 15))
log(f"[Ozon card] {r_oz_card['status']} | {r_oz_card['size']}B | {r_oz_card['latency']}s")
px_results["OZON"] = {
    "home": r_oz_home["status"], "card": r_oz_card["status"],
    "card_size": r_oz_card["size"],
    "jsonld": "application/ld+json" in r_oz_card["text"],
}

# 2c. Avito через новый прокси
av_sess = make_session(proxies=PX_NEW)
r_av_home = get(av_sess, "https://www.avito.ru/", timeout=(10, 15))
log(f"[Avito home] {r_av_home['status']} | {r_av_home['size']}B | {r_av_home['latency']}s")
r_av_search = get(av_sess, "https://www.avito.ru/moskva/transport/avto", timeout=(10, 15))
log(f"[Avito search] {r_av_search['status']} | {r_av_search['size']}B | {r_av_search['latency']}s")
px_results["AVITO"] = {
    "home": r_av_home["status"], "search": r_av_search["status"],
    "search_size": r_av_search["size"],
    "pow": "pow_challenge" in r_av_search["text"].lower(),
}

# 2d. Farpost через новый прокси (item)
fp_sess = make_session(proxies=PX_NEW)
r_fp_home = get(fp_sess, "https://www.farpost.ru/", timeout=(10, 15))
log(f"[FP home] {r_fp_home['status']} | {r_fp_home['size']}B | {r_fp_home['latency']}s")
r_fp_item = get(fp_sess, "https://www.farpost.ru/ni/krasnoyarsk/auto/wheel/tire/shiny-139122082.html",
                timeout=(10, 15))
log(f"[FP item] {r_fp_item['status']} | {r_fp_item['size']}B | {r_fp_item['latency']}s")
px_results["FARPOST"] = {
    "home": r_fp_home["status"], "item": r_fp_item["status"],
    "item_size": r_fp_item["size"],
}

# 2e. Старый прокси 198.51.100.20:80 — контрольная проверка
old_sess = make_session(proxies=PX_OLD)
r_old_fp = get(old_sess, "https://www.farpost.ru/", timeout=(8, 12))
log(f"[FP old-proxy] {r_old_fp['status']} | {r_old_fp['size']}B | {r_old_fp['latency']}s")
px_results["OLD_PROXY"] = {"fp_home": r_old_fp["status"], "fp_size": r_old_fp["size"]}

OUT["PROXY_NEW"] = px_results
log(f"PROXY: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("3. FARPOST — глубже: структура item, фото, видео", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
fp_deep = {}

# Берём сохранённый HTML первой item
try:
    with open("fp_v26_item1.html", "r", encoding="utf-8") as f:
        item_html = f.read()
    log(f"Loaded fp_v26_item1.html: {len(item_html)} chars")

    # Все <img> теги
    all_imgs = re.findall(r'<img[^>]+>', item_html, re.I)
    fp_deep["all_img_tags"] = len(all_imgs)
    # Фильтруем по src
    img_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', item_html, re.I)
    img_data_srcs = re.findall(r'<img[^>]+data-src=["\']([^"\']+)["\']', item_html, re.I)
    fp_deep["img_src"] = img_srcs[:20]
    fp_deep["img_data_src"] = img_data_srcs[:20]

    # data-атрибуты с URL
    data_urls = re.findall(r'data-(?:src|thumb|img|photo|url|original)=["\']([^"\']+)["\']',
                           item_html, re.I)
    fp_deep["data_urls"] = list(set(data_urls))[:20]

    # Скрипты с JSON (baza config, item data)
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', item_html, re.DOTALL)
    big_scripts = [(i, len(s)) for i, s in enumerate(scripts) if len(s) > 200]
    fp_deep["big_scripts"] = big_scripts[:10]

    # Ищем "photos", "images", "gallery" в скриптах
    for i, s in enumerate(scripts):
        if len(s) > 200:
            for kw in ["photos", "images", "gallery", "video", "thumbs"]:
                if kw in s.lower():
                    # Извлекаем контекст
                    idx = s.lower().find(kw)
                    ctx = s[max(0, idx-50):idx+200]
                    fp_deep.setdefault("script_contexts", []).append(
                        {"script_idx": i, "keyword": kw, "context": ctx[:250]})
    fp_deep["script_contexts"] = fp_deep.get("script_contexts", [])[:10]

    # iframe (видео)
    iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', item_html, re.I)
    fp_deep["iframes"] = iframes[:5]

    # data-reqsig, data-bulletin
    reqsig = re.findall(r'data-reqsig=["\']([^"\']+)["\']', item_html, re.I)
    bull_id = re.findall(r'data-bulletin-id=["\']([^"\']+)["\']', item_html, re.I)
    fp_deep["reqsig"] = reqsig[:3]
    fp_deep["bulletin_id"] = bull_id[:3]

    # CSS-классы с фото/видео
    photo_classes = re.findall(
        r'class=["\'][^"\']*(?:photo|image|gallery|video|carousel|slider|thumb)[^"\']*["\']',
        item_html, re.I)
    fp_deep["photo_classes"] = list(set(photo_classes))[:10]

    # Meta tags
    meta_tags = re.findall(r'<meta[^>]+>', item_html, re.I)
    fp_deep["meta_count"] = len(meta_tags)

    # body class, data attrs
    body_match = re.search(r'<body[^>]+>', item_html, re.I)
    fp_deep["body_tag"] = body_match.group(0)[:300] if body_match else ""

except FileNotFoundError:
    log("fp_v26_item1.html not found, skipping deep analysis")
    fp_deep["error"] = "file not found"

OUT["FARPOST_DEEP"] = fp_deep
log(f"FARPOST_DEEP: {time.monotonic()-T0:.1f}s")

# ============================================================
total = time.monotonic() - T0
print("=" * 70, flush=True)
print(f"SUMMARY [{total:.1f}s]", flush=True)
print("=" * 70, flush=True)
for k, v in OUT.items():
    if isinstance(v, dict):
        log(f"  {k}: {len(v)} keys")

out_path = "diag_v26_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)
log(f"Saved {out_path}")
log(f"DONE diag_v26 [{total:.1f}s]")

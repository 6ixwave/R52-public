"""diag_v25 — глубокое исследование Farpost item-URL + сессионные подходы.

Фокус:
1. Farpost: сохранить HTML категории, найти item-ссылки, проверить карточку
2. WB: session (home → card), cookie-based, catalog.wb.ru
3. Ozon: session с cookie, api.ozon.ru
4. Avito: mobile session, pow_challenge
5. Proxy: дополнительные источники (openproxylist, free-proxy-list)

Бюджет: ~250 сек.
"""
import json
import re
import socket
import sys
import time

import requests
from requests.adapters import HTTPAdapter

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

OUT = {}
T0 = time.monotonic()

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

PROXY = "[REDACTED_AUTH_SECRET]"
PROXIES = {"http": PROXY, "https": PROXY}

def log(msg):
    print(f"  {msg}", flush=True)

def make_session(headers=None):
    s = requests.Session()
    h = dict(HDRS)
    if headers:
        h.update(headers)
    s.headers.update(h)
    return s

def sess_get(sess, url, timeout=(8, 15), **kw):
    t0 = time.monotonic()
    try:
        r = sess.get(url, timeout=timeout, **kw)
        lat = time.monotonic() - t0
        return {"status": r.status_code, "size": len(r.content),
                "text": r.text[:80000], "headers": dict(r.headers),
                "cookies": list(r.cookies.keys()), "latency": round(lat, 2),
                "all_cookies": dict(sess.cookies)}
    except Exception as e:
        return {"status": -1, "size": 0, "text": "", "headers": {},
                "cookies": [], "latency": round(time.monotonic()-t0, 2),
                "error": str(e), "all_cookies": {}}

# ============================================================
print("=" * 70, flush=True)
print("1. FARPOST — глубокий разбор item-URL", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
fp = {}

sess = make_session()

# 1a. Главная → категория /auto/wheel/
r = sess_get(sess, "https://www.farpost.ru/auto/wheel/")
log(f"[cat /auto/wheel/] {r['status']} | {r['size']}B")
fp["cat_wheel"] = {"status": r["status"], "size": r["size"]}

# Сохраняем HTML для анализа
if r["status"] == 200:
    with open("farpost_cat_wheel.html", "w", encoding="utf-8") as f:
        f.write(r["text"])
    log("  saved farpost_cat_wheel.html")

# 1b. Ищем item-ссылки разными паттернами
html = r["text"]
item_patterns = {
    "bulletin": re.findall(r"href=[\"']([^\"']*bulletin[^\"']*)[\"']", html, re.I),
    "numeric_path": re.findall(r'href=["\'](/[\w\-]+(?:/[\w\-]+)*/(\d{7,}))["\']', html, re.I),
    "data-ad-id": re.findall(r'data-ad-id=["\'](\d+)["\']', html, re.I),
    "data-bulletin": re.findall(r'data-bulletin-id=["\'](\d+)["\']', html, re.I),
    "item-class": re.findall(r'class=["\'][^"\']*item[^"\']*["\'][^>]*href=["\']([^"\']+)["\']', html, re.I),
    "href-numeric": re.findall(r'href=["\'](/[\w\-]+/(\d{7,}))/?["\']', html, re.I),
    "all-hrefs-with-num": re.findall(r"href=[\"']([^\"']*?/\d{7,}[^\"']*?)[\"']", html, re.I),
}

for name, matches in item_patterns.items():
    unique = list(set(matches))[:10]
    fp[f"pattern_{name}"] = {"count": len(matches), "sample": unique[:5]}
    log(f"  [{name}] {len(matches)} matches: {unique[:3]}")

# 1c. Ищем по class="bulletin" или data-атрибутам
bulletin_blocks = re.findall(r'<(?:a|div|li)[^>]*class=["\'][^"\']*bulletin[^"\']*["\'][^>]*>', html, re.I)
fp["bulletin_blocks"] = {"count": len(bulletin_blocks), "sample": bulletin_blocks[:3]}
log(f"  [bulletin_blocks] {len(bulletin_blocks)}")

# 1d. data-reqsig, data-ad, data-id
data_attrs = re.findall(r'data-(?:ad|item|bulletin|id|num)["\s]*=["\']?(\d+)', html, re.I)
fp["data_attrs"] = {"count": len(data_attrs), "sample": list(set(data_attrs))[:10]}
log(f"  [data_attrs] {len(data_attrs)}: {list(set(data_attrs))[:5]}")

# 1e. Ищем JSON в странице (baza config, etc)
json_blocks = re.findall(r'<script[^>]*>(\{[^<]{200,})</script>', html, re.DOTALL)
fp["inline_json"] = {"count": len(json_blocks), "sizes": [len(b) for b in json_blocks[:5]]}
log(f"  [inline_json] {len(json_blocks)} blocks")

# 1f. Если нашли item — пробуем открыть
test_items = []
for name in ["bulletin", "numeric_path", "href-numeric", "all-hrefs-with-num"]:
    for m in fp.get(f"pattern_{name}", {}).get("sample", []):
        if isinstance(m, str) and m.startswith("/") and len(m) > 5:
            test_items.append(m)
        elif isinstance(m, tuple) and len(m) >= 2:
            test_items.append(f"/{m[0]}/{m[1]}/")
    if test_items:
        break

# Fallback: известные Farpost URL
if not test_items:
    test_items = [
        "/auto/wheel/tire/",  # подкатегория
        "/bulletin/",
        "/ad/",
    ]

for item_url in test_items[:4]:
    if time.monotonic() - t_sec > 40:
        break
    url = f"https://www.farpost.ru{item_url}"
    r2 = sess_get(sess, url)
    has_jsonld = "application/ld+json" in r2["text"]
    has_og_title = "og:title" in r2["text"]
    has_price = bool(re.search(r'[\d\s]+руб', r2["text"], re.I))
    has_img = bool(re.search(r'<img[^>]+src=["\'][^"\']*(?:upload|img|photo)', r2["text"], re.I))
    fp[f"item_{item_url}"] = {"status": r2["status"], "size": r2["size"],
                               "jsonld": has_jsonld, "og": has_og_title,
                               "price": has_price, "img": has_img}
    log(f"  [item {item_url}] {r2['status']} | {r2['size']}B | jsonld={has_jsonld} price={has_price} img={has_img}")

# 1g. Farpost search с реальным query
r_search = sess_get(sess, "https://www.farpost.ru/dir?query=iphone+15")
search_items = re.findall(r'href=["\'](/[\w\-]+(?:/[\w\-]+)*/(\d{7,}))/?["\']', r_search["text"], re.I)
fp["search"] = {"status": r_search["status"], "size": r_search["size"],
                "items": len(set(search_items)), "sample": list(set(search_items))[:5]}
log(f"  [search iphone15] {r_search['status']} | {r_search['size']}B | items={len(set(search_items))}")

# 1h. Сохраняем search HTML
if r_search["status"] == 200:
    with open("farpost_search_sample.html", "w", encoding="utf-8") as f:
        f.write(r_search["text"])
    log("  saved farpost_search_sample.html")

OUT["FARPOST"] = fp
log(f"FARPOST: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("2. WILDBERRIES — session + cookie + alt endpoints", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
wb = {}

wb_sess = make_session()

# 2a. Home page (получаем cookie)
r_home = sess_get(wb_sess, "https://www.wildberries.ru/", timeout=(8, 12))
wb["home"] = {"status": r_home["status"], "size": r_home["size"],
              "cookies": r_home["cookies"]}
log(f"[home] {r_home['status']} | {r_home['size']}B | ck={r_home['cookies']}")

# 2b. Card через session (с cookie от home)
wb_id = "100000001"
url_card = f"https://www.wildberries.ru/catalog/{wb_id}/detail.aspx"
r_card = sess_get(wb_sess, url_card, timeout=(8, 12))
wb["card_session"] = {"status": r_card["status"], "size": r_card["size"],
                       "has_jsonld": "application/ld+json" in r_card["text"],
                       "antibot": "antibot" in r_card["text"].lower()}
log(f"[card_session] {r_card['status']} | {r_card['size']}B | antibot={wb['card_session']['antibot']}")

# 2c. catalog.wb.ru (старый домен)
r_cat = sess_get(wb_sess, f"https://catalog.wb.ru/cards/v2/detail?nm={wb_id}",
                 headers={"Accept": "application/json"}, timeout=(8, 12))
wb["catalog_wb"] = {"status": r_cat["status"], "size": r_cat["size"],
                    "json_preview": r_cat["text"][:500] if r_cat["status"] == 200 else ""}
log(f"[catalog.wb] {r_cat['status']} | {r_cat['size']}B")

# 2d. static-basket (CDN для изображений)
r_cdn = sess_get(wb_sess, "https://static-basket-01.wbbasket.ru/", timeout=(5, 8))
wb["cdn_static"] = {"status": r_cdn["status"], "size": r_cdn["size"]}
log(f"[cdn_static] {r_cdn['status']} | {r_cdn['size']}B")

# 2e. WB API: batch v2 (другой формат)
r_batch = sess_get(wb_sess, f"https://card.wb.ru/cards/v2/detail?nm={wb_id}&sup=1",
                   headers={"Accept": "application/json"}, timeout=(8, 12))
wb["batch_v2"] = {"status": r_batch["status"], "size": r_batch["size"]}
log(f"[batch_v2] {r_batch['status']} | {r_batch['size']}B")

# 2f. WB через curl_cffi с session
if HAS_CFFI:
    try:
        t0 = time.monotonic()
        # cffi: home → card
        r_h = cffi_requests.get("https://www.wildberries.ru/", headers=HDRS,
                                timeout=(8, 12), impersonate="chrome")
        r_c = cffi_requests.get(url_card, headers=HDRS,
                                timeout=(8, 12), impersonate="chrome")
        wb["cffi_session"] = {
            "home_status": r_h.status_code,
            "card_status": r_c.status_code,
            "card_size": len(r_c.content),
            "card_jsonld": "application/ld+json" in r_c.text,
            "card_antibot": "antibot" in r_c.text.lower(),
        }
        log(f"[cffi_session] home={r_h.status_code} card={r_c.status_code} | {len(r_c.content)}B")
    except Exception as e:
        wb["cffi_session"] = {"error": str(e)}
        log(f"[cffi_session] ERR: {e}")

# 2g. WB: попробовать с Referer
r_ref = sess_get(wb_sess, url_card,
                 headers={"Referer": "https://www.wildberries.ru/",
                          "Sec-Fetch-Site": "same-origin"},
                 timeout=(8, 12))
wb["card_referrer"] = {"status": r_ref["status"], "size": r_ref["size"]}
log(f"[card_referrer] {r_ref['status']} | {r_ref['size']}B")

# 2h. WB через прокси (session)
wb_sess_p = make_session()
r_hp = sess_get(wb_sess_p, "https://www.wildberries.ru/", proxies=PROXIES, timeout=(8, 12))
r_cp = sess_get(wb_sess_p, url_card, proxies=PROXIES, timeout=(8, 12))
wb["proxy_session"] = {"home": r_hp["status"], "card": r_cp["status"],
                        "card_size": r_cp["size"]}
log(f"[proxy_session] home={r_hp['status']} card={r_cp['status']} | {r_cp['size']}B")

OUT["WB"] = wb
log(f"WB: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("3. OZON — session + cookie + API", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
oz = {}

oz_sess = make_session()

# 3a. Home (cookie)
r_home = sess_get(oz_sess, "https://www.ozon.ru/", timeout=(8, 12))
oz["home"] = {"status": r_home["status"], "size": r_home["size"],
              "cookies": r_home["cookies"]}
log(f"[home] {r_home['status']} | {r_home['size']}B | ck={r_home['cookies']}")

# 3b. Card через session
oz_id = "100000001"
r_card = sess_get(oz_sess, f"https://www.ozon.ru/product/{oz_id}/", timeout=(8, 12))
oz["card_session"] = {"status": r_card["status"], "size": r_card["size"],
                       "has_jsonld": "application/ld+json" in r_card["text"]}
log(f"[card_session] {r_card['status']} | {r_card['size']}B")

# 3c. Ozon API composer
r_api = sess_get(oz_sess,
                 f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oz_id}/",
                 headers={"Accept": "application/json",
                          "X-Request-Src": "desktop-web"},
                 timeout=(8, 12))
oz["composer_api"] = {"status": r_api["status"], "size": r_api["size"],
                       "preview": r_api["text"][:300]}
log(f"[composer_api] {r_api['status']} | {r_api['size']}B")

# 3d. Ozon через прокси session
oz_sess_p = make_session()
r_hp = sess_get(oz_sess_p, "https://www.ozon.ru/", proxies=PROXIES, timeout=(8, 12))
r_cp = sess_get(oz_sess_p, f"https://www.ozon.ru/product/{oz_id}/", proxies=PROXIES, timeout=(8, 12))
oz["proxy_session"] = {"home": r_hp["status"], "card": r_cp["status"], "size": r_cp["size"]}
log(f"[proxy_session] home={r_hp['status']} card={r_cp['status']}")

# 3e. Ozon: mobile API
r_mob = sess_get(oz_sess, f"https://m.ozon.ru/product/{oz_id}/",
                 headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"},
                 timeout=(8, 12))
oz["mobile"] = {"status": r_mob["status"], "size": r_mob["size"]}
log(f"[mobile] {r_mob['status']} | {r_mob['size']}B")

# 3f. Ozon: api.ozon.ru
r_api2 = sess_get(oz_sess, f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oz_id}/",
                  headers={"Accept": "application/json"}, timeout=(8, 12))
oz["api_ozon"] = {"status": r_api2["status"], "size": r_api2["size"]}
log(f"[api.ozon.ru] {r_api2['status']} | {r_api2['size']}B")

OUT["OZON"] = oz
log(f"OZON: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("4. AVITO — mobile session + pow + real ID", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
av = {}

av_sess = make_session(headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E147 Safari/604.1"})

# 4a. Home mobile
r_home = sess_get(av_sess, "https://m.avito.ru/", timeout=(8, 12))
av["home_mob"] = {"status": r_home["status"], "size": r_home["size"],
                  "cookies": r_home["cookies"]}
log(f"[home_mob] {r_home['status']} | {r_home['size']}B | ck={r_home['cookies']}")

# 4b. Search mobile (найти реальные ID)
r_search = sess_get(av_sess, "https://m.avito.ru/moskva/transport/avto", timeout=(8, 12))
av["search_mob"] = {"status": r_search["status"], "size": r_search["size"]}
# Извлекаем ID
ids = re.findall(r'avito\.ru/[\w\-]+/(\d{6,})', r_search["text"])
av["search_mob"]["ids"] = list(set(ids))[:10]
log(f"[search_mob] {r_search['status']} | {r_search['size']}B | ids={len(set(ids))}")

# 4c. Card mobile (с session)
if av["search_mob"]["ids"]:
    real_id = av["search_mob"]["ids"][0]
    r_card = sess_get(av_sess, f"https://m.avito.ru/moskva/avto-{real_id}", timeout=(8, 12))
    av["card_mob"] = {"id": real_id, "status": r_card["status"], "size": r_card["size"],
                      "jsonld": "application/ld+json" in r_card["text"]}
    log(f"[card_mob {real_id}] {r_card['status']} | {r_card['size']}B")
else:
    # Fallback: короткий URL
    r_card = sess_get(av_sess, "https://m.avito.ru/ad/100000001", timeout=(8, 12))
    av["card_mob"] = {"id": "100000001", "status": r_card["status"], "size": r_card["size"]}
    log(f"[card_mob fallback] {r_card['status']} | {r_card['size']}B")

# 4d. Desktop session
av_sess_d = make_session()
r_hd = sess_get(av_sess_d, "https://www.avito.ru/", timeout=(8, 12))
r_cd = sess_get(av_sess_d, "https://www.avito.ru/moskva/transport/avto", timeout=(8, 12))
av["desktop_search"] = {"status": r_cd["status"], "size": r_cd["size"],
                         "cookies": r_cd["cookies"]}
ids_d = re.findall(r'avito\.ru/[\w\-]+/(\d{6,})', r_cd["text"])
av["desktop_search"]["ids"] = list(set(ids_d))[:10]
log(f"[desktop_search] {r_cd['status']} | {r_cd['size']}B | ids={len(set(ids_d))}")

# 4e. Avito через прокси
av_sess_p = make_session()
r_hp = sess_get(av_sess_p, "https://www.avito.ru/", proxies=PROXIES, timeout=(8, 12))
r_cp = sess_get(av_sess_p, "https://www.avito.ru/moskva/transport/avto", proxies=PROXIES, timeout=(8, 12))
av["proxy"] = {"home": r_hp["status"], "search": r_cp["status"], "size": r_cp["size"]}
log(f"[proxy] home={r_hp['status']} search={r_cp['status']}")

OUT["AVITO"] = av
log(f"AVITO: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("5. PROXY — дополнительные источники", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
px = {}

# 5a. OpenProxyList
try:
    r = requests.get("https://openproxylist.xyz/https.txt", timeout=(5, 10))
    lines = [l.strip() for l in r.text.splitlines() if l.strip() and ":" in l]
    px["openproxylist"] = {"status": r.status_code, "count": len(lines),
                            "sample": lines[:5]}
    log(f"[openproxylist] {r.status_code} | {len(lines)} proxies")
except Exception as e:
    px["openproxylist"] = {"error": str(e)}
    log(f"[openproxylist] ERR: {e}")

# 5b. FreeProxyList
try:
    r = requests.get("https://www.free-proxy-list.net/http.html", timeout=(5, 10),
                     headers={"User-Agent": UA})
    ips = re.findall(r'(\d+\.\d+\.\d+\.\d+):(\d+)', r.text)
    px["freeproxylist"] = {"status": r.status_code, "count": len(ips),
                            "sample": [f"{a}:{b}" for a, b in ips[:5]]}
    log(f"[freeproxylist] {r.status_code} | {len(ips)} proxies")
except Exception as e:
    px["freeproxylist"] = {"error": str(e)}
    log(f"[freeproxylist] ERR: {e}")

# 5c. Test top-5 новых прокси на Farpost
all_new_proxies = []
for src in ["openproxylist", "freeproxylist"]:
    data = px.get(src, {})
    if src == "openproxylist" and "sample" in data:
        all_new_proxies.extend(data["sample"])
    elif src == "freeproxylist" and "sample" in data:
        all_new_proxies.extend(data["sample"])

tested = []
for p in all_new_proxies[:5]:
    if time.monotonic() - t_sec > 25:
        break
    px_url = f"http://{p}"
    try:
        t0 = time.monotonic()
        r = requests.get("https://www.farpost.ru/",
                         proxies={"http": px_url, "https": px_url},
                         timeout=(5, 10), headers=HDRS)
        lat = time.monotonic() - t0
        tested.append({"proxy": p, "status": r.status_code,
                        "size": len(r.content), "latency": round(lat, 2)})
        log(f"  [{p}] {r.status_code} | {len(r.content)}B | {lat:.1f}s")
    except Exception as e:
        tested.append({"proxy": p, "status": -1, "error": str(e)[:80]})
        log(f"  [{p}] ERR: {str(e)[:60]}")

px["tested"] = tested
px["working"] = [t["proxy"] for t in tested if t.get("status") == 200]
log(f"  New working: {px['working']}")

OUT["PROXY"] = px
log(f"PROXY: {time.monotonic()-T0:.1f}s")

# ============================================================
total = time.monotonic() - T0
print("=" * 70, flush=True)
print(f"SUMMARY [{total:.1f}s]", flush=True)
print("=" * 70, flush=True)
for k, v in OUT.items():
    if isinstance(v, dict):
        ok = sum(1 for x in v.values() if isinstance(x, dict) and x.get("status") == 200)
        log(f"  {k}: {ok} x 200")

out_path = "diag_v25_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)
log(f"Saved {out_path}")
log(f"DONE diag_v25 [{total:.1f}s]")

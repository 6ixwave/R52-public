"""diag_v24 — точечная проверка карточек и прокси.

Фокус:
1. Farpost: категория → item URL → парсинг
2. WB: card API v1/v2, HTML, curl_cffi, CDN
3. Ozon: HTML, curl_cffi, composer-api
4. Avito: мобильная, полный URL, 439-диагностика
5. Proxy 198.51.100.20:80: 3 запроса, латентность, гео

Бюджет: ~200 сек.
"""
import json
import re
import socket
import ssl
import sys
import time
import traceback

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
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

PROXY = "http://198.51.100.20:80"
PROXIES = {"http": PROXY, "https": PROXY}

def log(msg):
    print(f"  {msg}", flush=True)

def http_get(url, headers=None, proxies=None, timeout=(8, 15), session=None, verify=True):
    """GET с деталями."""
    h = dict(HDRS)
    if headers:
        h.update(headers)
    s = session or requests
    t0 = time.monotonic()
    try:
        if hasattr(s, "get"):
            if session:
                r = s.get(url, headers=h, proxies=proxies, timeout=timeout, verify=verify)
            else:
                r = s.get(url, headers=h, proxies=proxies, timeout=timeout, verify=verify)
        else:
            r = requests.get(url, headers=h, proxies=proxies, timeout=timeout, verify=verify)
        lat = time.monotonic() - t0
        return {
            "status": r.status_code,
            "size": len(r.content),
            "text": r.text[:50000],
            "headers": dict(r.headers),
            "cookies": list(r.cookies.keys()),
            "latency": round(lat, 2),
        }
    except Exception as e:
        return {"status": -1, "size": 0, "text": "", "headers": {}, "cookies": [],
                "latency": round(time.monotonic() - t0, 2), "error": str(e)}

def cffi_get(url, proxies=None):
    if not HAS_CFFI:
        return {"status": -1, "size": 0, "text": "", "error": "no cffi"}
    try:
        t0 = time.monotonic()
        r = cffi_requests.get(url, headers=HDRS, proxies=proxies,
                              timeout=(8, 15), impersonate="chrome")
        lat = time.monotonic() - t0
        return {"status": r.status_code, "size": len(r.content),
                "text": r.text[:50000], "headers": dict(r.headers),
                "cookies": list(r.cookies.keys()), "latency": round(lat, 2)}
    except Exception as e:
        return {"status": -1, "size": 0, "text": "", "error": str(e),
                "latency": round(time.monotonic() - t0, 2)}

def extract_item_links(html, base="https://www.farpost.ru"):
    """Находит ссылки на объявления в HTML Farpost."""
    links = set()
    # Паттерны: /bulletin/{id}, /{cat}/{id}, /item/{id}
    patterns = [
        re.compile(r'href=["\'](/(?:bulletin|item|ad)/(\d+))["\']', re.I),
        re.compile(r'href=["\'](/[\w\-]+/(\d{6,}))["\']', re.I),
        re.compile(r'href=["\'](/[\w\-]+/[\w\-]+/(\d{6,}))["\']', re.I),
        re.compile(r'data-id=["\'](\d{6,})["\']'),
        re.compile(r'href=["\'](/\d{6,})["\']'),
    ]
    for p in patterns:
        for m in p.finditer(html):
            if p.groups:
                path, num = m.group(1), m.group(2)
            else:
                path, num = f"/{m.group(1)}", m.group(1)
            if num and len(num) >= 6:
                links.add((path, num))
    return links

# ============================================================
print("=" * 70, flush=True)
print(f"0. PROXY DEEP TEST [budget=30s]", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
proxy_results = []
test_urls = [
    "https://www.farpost.ru/",
    "https://www.wildberries.ru/",
    "https://www.ozon.ru/",
]
for i, u in enumerate(test_urls):
    if time.monotonic() - t_sec > 28:
        break
    r = http_get(u, proxies=PROXIES, timeout=(6, 12))
    proxy_results.append({"url": u, "status": r["status"], "size": r["size"],
                          "latency": r["latency"], "error": r.get("error", "")})
    log(f"[{i}] {u} -> {r['status']} | {r['size']}B | {r['latency']}s | {r.get('error','')}")

# Geo check через headers
geo = http_get("https://api.ipify.org?format=json", proxies=PROXIES, timeout=(5, 8))
proxy_ip = geo.get("text", "")
OUT["PROXY"] = {"results": proxy_results, "exit_ip": proxy_ip,
                "ok_count": sum(1 for p in proxy_results if p["status"] == 200)}
log(f"Proxy OK: {OUT['PROXY']['ok_count']}/{len(proxy_results)}")
log(f"PROXY: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("1. FARPOST — категория → item", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
fp = {}

# 1a. Категория /auto/
cat_urls = ["/auto/", "/zapchasti/", "/realty/"]
cat_items = {}
for cu in cat_urls:
    if time.monotonic() - t_sec > 35:
        break
    url = f"https://www.farpost.ru{cu}"
    r = http_get(url)
    links = extract_item_links(r["text"])
    cat_items[cu] = {"status": r["status"], "size": r["size"],
                     "items_found": len(links),
                     "sample_items": list(links)[:5]}
    log(f"[cat {cu}] {r['status']} | {r['size']}B | items={len(links)}")
    if links:
        for path, num in list(links)[:3]:
            log(f"    item: {path} (id={num})")

# 1b. Попробуем конкретные item URL паттерны
# Берём первый найденный item или используем известные
test_item_ids = []
for cu, data in cat_items.items():
    for path, num in data.get("sample_items", [])[:2]:
        test_item_ids.append((path, num))
    if len(test_item_ids) >= 3:
        break

# Если не нашли, пробуем известные паттерны
if not test_item_ids:
    test_item_ids = [
        ("/auto/wheel/123456789/", "123456789"),
        ("/bulletin/18530297982/", "18530297982"),
        ("/item/18530297982/", "18530297982"),
    ]

item_results = []
for path, num in test_item_ids[:3]:
    if time.monotonic() - t_sec > 45:
        break
    url = f"https://www.farpost.ru{path}"
    r = http_get(url)
    has_jsonld = "application/ld+json" in r["text"]
    has_og = "og:title" in r["text"]
    has_price = re.search(r'\d[\d\s]*руб', r["text"], re.I) is not None
    has_img = re.search(r'<img[^>]+src=["\'][^"\']*(?:upload|img)', r["text"], re.I) is not None
    item_results.append({
        "url": url, "status": r["status"], "size": r["size"],
        "jsonld": has_jsonld, "og": has_og, "price": has_price, "img": has_img,
        "latency": r["latency"],
    })
    log(f"[item {path}] {r['status']} | {r['size']}B | jsonld={has_jsonld} og={has_og} price={has_price} img={has_img}")

# 1c. Поиск Farpost
search_url = "https://www.farpost.ru/dir?query=телефон"
r_search = http_get(search_url)
search_links = extract_item_links(r_search["text"])
fp["search"] = {"status": r_search["status"], "size": r_search["size"],
                "items_found": len(search_links),
                "sample": list(search_links)[:5]}
log(f"[search] {r_search['status']} | {r_search['size']}B | items={len(search_links)}")

OUT["FARPOST"] = {"categories": cat_items, "items": item_results, "search": fp["search"]}
log(f"FARPOST: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("2. WILDBERRIES — API + HTML + CDN", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
wb = {}

# Тестовые ID (популярные товары)
wb_ids = ["100000001", "200000001", "123456789"]

# 2a. Card API v2
for wid in wb_ids[:2]:
    if time.monotonic() - t_sec > 20:
        break
    url = f"https://card.wb.ru/cards/v2/detail?nm={wid}"
    r = http_get(url)
    wb[f"api_v2_{wid}"] = {"status": r["status"], "size": r["size"],
                           "json": r["text"][:2000] if r["status"] == 200 else ""}
    log(f"[api_v2 {wid}] {r['status']} | {r['size']}B")

# 2b. Card API v1
url = f"https://card.wb.ru/cards/v1/detail?nm={wb_ids[0]}"
r = http_get(url)
wb["api_v1"] = {"status": r["status"], "size": r["size"]}
log(f"[api_v1] {r['status']} | {r['size']}B")

# 2c. HTML карточки
url = f"https://www.wildberries.ru/catalog/{wb_ids[0]}/detail.aspx"
r = http_get(url)
wb["html"] = {"status": r["status"], "size": r["size"],
              "has_state": "__STATE__" in r["text"] or "__WB_STATE__" in r["text"],
              "has_jsonld": "application/ld+json" in r["text"],
              "has_og": "og:title" in r["text"],
              "antibot": "antibot" in r["text"].lower() or "browser-check" in r["text"]}
log(f"[html] {r['status']} | {r['size']}B | state={wb['html']['has_state']} jsonld={wb['html']['has_jsonld']} antibot={wb['html']['antibot']}")

# 2d. curl_cffi
if HAS_CFFI:
    r = cffi_get(url)
    wb["cffi_html"] = {"status": r["status"], "size": r["size"],
                       "has_jsonld": "application/ld+json" in r.get("text", ""),
                       "has_og": "og:title" in r.get("text", "")}
    log(f"[cffi_html] {r['status']} | {r['size']}B")

# 2e. WB через прокси
url = f"https://www.wildberries.ru/catalog/{wb_ids[0]}/detail.aspx"
r = http_get(url, proxies=PROXIES)
wb["proxy_html"] = {"status": r["status"], "size": r["size"],
                    "has_jsonld": "application/ld+json" in r["text"]}
log(f"[proxy_html] {r['status']} | {r['size']}B")

# 2f. CDN basket
cdn_hosts = [
    "basket-01.wbbasket.ru", "basket-199.wbbasket.ru",
    "basket-200.wbbasket.ru", "static-basket-199.wbbasket.ru",
]
cdn_results = []
for host in cdn_hosts[:3]:
    if time.monotonic() - t_sec > 35:
        break
    try:
        t0 = time.monotonic()
        ip = socket.getaddrinfo(host, 443)[0][4][0]
        dns_ok = True
    except Exception:
        ip = None
        dns_ok = False
    # HTTP HEAD на корень
    r = http_get(f"https://{host}/", timeout=(5, 8))
    cdn_results.append({"host": host, "dns": ip, "dns_ok": dns_ok,
                        "status": r["status"], "latency": r["latency"]})
    log(f"[cdn {host}] dns={ip} http={r['status']}")

# 2g. WB API search (публичный)
url = "https://search.wb.ru/exactmatch/ru/common/v5/search?query=телефон&resultset=catalog&sort=popular&page=1"
r = http_get(url)
wb["search_api"] = {"status": r["status"], "size": r["size"],
                    "has_data": '"data"' in r["text"][:500]}
log(f"[search_api] {r['status']} | {r['size']}B")

OUT["WB"] = wb
log(f"WB: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("3. OZON — HTML + API + cffi", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
oz = {}

oz_ids = ["100000001", "250000001"]

# 3a. HTML карточки
for oid in oz_ids[:1]:
    url = f"https://www.ozon.ru/product/{oid}/"
    r = http_get(url)
    oz[f"html_{oid}"] = {"status": r["status"], "size": r["size"],
                         "has_jsonld": "application/ld+json" in r["text"],
                         "has_og": "og:title" in r["text"],
                         "has_state": "__INITIAL_STATE__" in r["text"] or "__APP_STATE__" in r["text"]}
    log(f"[html {oid}] {r['status']} | {r['size']}B | jsonld={oz[f'html_{oid}']['has_jsonld']}")

# 3b. curl_cffi
if HAS_CFFI:
    url = f"https://www.ozon.ru/product/{oz_ids[0]}/"
    r = cffi_get(url)
    oz["cffi"] = {"status": r["status"], "size": r["size"],
                  "has_jsonld": "application/ld+json" in r.get("text", "")}
    log(f"[cffi] {r['status']} | {r['size']}B")

# 3c. Ozon через прокси
url = f"https://www.ozon.ru/product/{oz_ids[0]}/"
r = http_get(url, proxies=PROXIES)
oz["proxy"] = {"status": r["status"], "size": r["size"]}
log(f"[proxy] {r['status']} | {r['size']}B")

# 3d. Ozon search API (публичный)
url = "https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/search/?text=телефон"
r = http_get(url, headers={"Accept": "application/json"})
oz["search_api"] = {"status": r["status"], "size": r["size"]}
log(f"[search_api] {r['status']} | {r['size']}B")

# 3e. Ozon mobile
url = f"https://m.ozon.ru/product/{oz_ids[0]}/"
r = http_get(url, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"})
oz["mobile"] = {"status": r["status"], "size": r["size"]}
log(f"[mobile] {r['status']} | {r['size']}B")

# 3f. Ozon CDN
cdn_hosts = ["ir-220.ozoncdn.net", "av-853.ozoncdn.net"]
for host in cdn_hosts:
    try:
        ip = socket.getaddrinfo(host, 443)[0][4][0]
        log(f"[cdn {host}] dns={ip}")
    except Exception as e:
        log(f"[cdn {host}] DNS fail: {e}")

OUT["OZON"] = oz
log(f"OZON: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("4. AVITO — мобильная + полный URL + 439", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
av = {}

av_ids = ["100000001", "500000001"]

# 4a. Полный URL с городом
for aid in av_ids[:1]:
    url = f"https://www.avito.ru/moskva/transport/avto/avto-{aid}"
    r = http_get(url)
    av[f"full_url_{aid}"] = {"status": r["status"], "size": r["size"],
                             "has_jsonld": "application/ld+json" in r["text"],
                             "has_og": "og:title" in r["text"],
                             "cookies": r["cookies"]}
    log(f"[full {aid}] {r['status']} | {r['size']}B | jsonld={av[f'full_url_{aid}']['has_jsonld']}")

# 4b. Мобильная версия
url = f"https://m.avito.ru/moskva/transport/avto/avto-{av_ids[0]}"
r = http_get(url, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E147 Safari/604.1"})
av["mobile"] = {"status": r["status"], "size": r["size"],
                "has_jsonld": "application/ld+json" in r["text"],
                "cookies": r["cookies"]}
log(f"[mobile] {r['status']} | {r['size']}B | jsonld={av['mobile']['has_jsonld']}")

# 4c. Короткий /ad/{id}
url = f"https://www.avito.ru/ad/{av_ids[0]}"
r = http_get(url)
av["short_ad"] = {"status": r["status"], "size": r["size"],
                  "has_jsonld": "application/ld+json" in r["text"]}
log(f"[short_ad] {r['status']} | {r['size']}B")

# 4d. Avito через прокси
url = f"https://www.avito.ru/ad/{av_ids[0]}"
r = http_get(url, proxies=PROXIES)
av["proxy"] = {"status": r["status"], "size": r["size"]}
log(f"[proxy] {r['status']} | {r['size']}B")

# 4e. Avito search (чтобы найти реальные ID)
url = "https://www.avito.ru/moskva/transport/avto"
r = http_get(url)
av["search_page"] = {"status": r["status"], "size": r["size"],
                     "has_jsonld": "application/ld+json" in r["text"],
                     "cookies": r["cookies"]}
# Извлекаем ID из поиска
av_ids_found = re.findall(r'avito\.ru/[\w\-]+/(\d{6,})', r["text"])
av["search_page"]["ids_found"] = list(set(av_ids_found))[:10]
log(f"[search] {r['status']} | {r['size']}B | ids={len(set(av_ids_found))}")

# 4f. Если нашли реальные ID — проверяем карточку
if av["search_page"]["ids_found"]:
    real_id = av["search_page"]["ids_found"][0]
    url = f"https://www.avito.ru/moskva/avto-{real_id}"
    r = http_get(url)
    av["real_item"] = {"id": real_id, "status": r["status"], "size": r["size"],
                       "has_jsonld": "application/ld+json" in r["text"],
                       "has_og": "og:title" in r["text"]}
    log(f"[real_item {real_id}] {r['status']} | {r['size']}B | jsonld={av['real_item']['has_jsonld']}")

OUT["AVITO"] = av
log(f"AVITO: {time.monotonic()-T0:.1f}s")

# ============================================================
print("=" * 70, flush=True)
print("5. SWEEP — быстрая проверка всех через прокси", flush=True)
print("=" * 70, flush=True)
t_sec = time.monotonic()
sweep = []
sweep_urls = [
    ("WB", "https://www.wildberries.ru/catalog/100000001/detail.aspx"),
    ("OZON", "https://www.ozon.ru/product/100000001/"),
    ("AVITO", "https://www.avito.ru/ad/100000001"),
    ("FARPOST", "https://www.farpost.ru/auto/"),
]
for src, u in sweep_urls:
    if time.monotonic() - t_sec > 25:
        break
    r = http_get(u, proxies=PROXIES, timeout=(6, 12))
    sweep.append({"src": src, "status": r["status"], "size": r["size"],
                  "latency": r["latency"]})
    log(f"[{src}] {r['status']} | {r['size']}B | {r['latency']}s")
OUT["SWEEP_PROXY"] = sweep

# ============================================================
total = time.monotonic() - T0
print("=" * 70, flush=True)
print(f"SUMMARY [{total:.1f}s]", flush=True)
print("=" * 70, flush=True)
for k, v in OUT.items():
    if isinstance(v, dict):
        ok = sum(1 for x in v.values() if isinstance(x, dict) and x.get("status") == 200)
        log(f"  {k}: {ok} x 200")
    elif isinstance(v, list):
        ok = sum(1 for x in v if isinstance(x, dict) and x.get("status") == 200)
        log(f"  {k}: {ok}/{len(v)} x 200")

# Сохраняем
out_path = "diag_v24_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)
log(f"Saved {out_path}")
log(f"DONE diag_v24 [{total:.1f}s]")

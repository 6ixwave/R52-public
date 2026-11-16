"""Прямой (direct, без прокси) прогон через curl_cffi — как в реальных сборщиках.
Цель: понять, где direct проходит, а где 403, чтобы поставить correct fallback."""
import time
from curl_cffi import requests as cr

s = cr.Session(impersonate="chrome")

OZ_H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.ozon.ru/",
}
JSON_H = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

def hit(tag, url, headers, t=12):
    t0 = time.monotonic()
    try:
        r = s.get(url, timeout=t, headers=headers, verify=False)
        body = r.content
        extra = ""
        try:
            j = r.json()
            if isinstance(j, dict):
                extra = " keys=" + ",".join(list(j.keys())[:6])
        except Exception:
            pass
        print(f"{tag} -> {r.status_code} len={len(body)} ({time.monotonic()-t0:.1f}s){extra}")
        return r
    except Exception as e:
        print(f"{tag} -> ERR {type(e).__name__}: {str(e)[:120]} ({time.monotonic()-t0:.1f}s)")
        return None

print("=== OZON composer API (direct) ===")
hit("OZON-composer-v2", "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/2748584093/", OZ_H)
hit("OZON-entrypoint", "https://www.ozon.ru/api/entrypoint/pdp/2748584093", OZ_H)
print("=== OZON home (direct) ===")
hit("OZON-home", "https://www.ozon.ru/", OZ_H)
print("=== OZON search p2 (direct) ===")
hit("OZON-search-p2", "https://www.ozon.ru/search/?text=butbuki&from_global=true&page=2", OZ_H)
print("=== WB warmup (direct) ===")
hit("WB-www", "https://www.wildberries.ru/", {"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
hit("WB-catalog", "https://catalog.wb.ru/", {"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
print("=== WB search v4 (direct) ===")
hit("WB-search-v4", "https://catalog.wb.ru/exactmatch/ru/common/v4/search?query=video&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1", JSON_H, t=8)
hit("WB-search-v4-2", "https://search.wb.ru/exactmatch/ru/common/v4/search?query=video&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1", JSON_H, t=8)

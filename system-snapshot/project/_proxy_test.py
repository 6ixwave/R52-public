"""Быстрая проверка: живы ли прокси и какой статус они сами получают (без браузера)."""
import json
import time
import requests
from pathlib import Path

p = Path(r"C:\Users\aleks\AppData\Local\R52\collectors\proxies.json")
proxies = json.loads(p.read_text("utf-8"))
print("total proxies:", len(proxies))

tests = [
    ("WB-search", "https://catalog.wb.ru/exactmatch/ru/common/v4/search?query=video&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1"),
    ("OZON-home", "https://www.ozon.ru/"),
    ("OZON-prod", "https://www.ozon.ru/product/2748584093/"),
]
hdr = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
N = 3
for name, url in tests:
    print(f"=== {name} ===")
    for i in range(N):
        px = {"http": proxies[i], "https": proxies[i]}
        host = proxies[i].split("@")[-1]
        t0 = time.monotonic()
        try:
            r = requests.get(url, proxies=px, headers=hdr, timeout=12)
            print(f"{name} px{i} {host} -> {r.status_code} len={len(r.content)} ({time.monotonic()-t0:.1f}s)")
        except Exception as e:
            print(f"{name} px{i} {host} -> ERR {type(e).__name__}: {str(e)[:120]} ({time.monotonic()-t0:.1f}s)")
        time.sleep(1)

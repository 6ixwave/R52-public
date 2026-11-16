"""Диагностика: почему FARPOST не извлекает цену из search HTML."""
import re, sys, time, logging
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.farpost")

HOME = Path(__file__).resolve().parents[1] / "data" / "test_home"
from r52.collectors.farpost import FarpostCollector

c = FarpostCollector(data_dir=HOME, use_proxies=False)
c._warmup()
time.sleep(1)

url = "https://www.farpost.ru/dir?query=" + quote("тест") + "&page=1"
headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.farpost.ru/",
}
proxies = c.pool.get_proxy()
if c._cffi:
    resp = c._cffi.get(url, impersonate="chrome", timeout=(15, 30), headers=headers, proxies=proxies)
else:
    resp = c.session.get(url, headers=headers, proxies=proxies, timeout=(10, 30))

print(f"Status: {resp.status_code}")
print(f"Final URL: {resp.url}")
ct = (resp.headers.get("Content-Type") or "").lower()
print(f"Content-Type: {ct}")
if "1251" in ct or "cp1251" in ct:
    html = resp.content.decode("windows-1251", errors="replace")
else:
    html = resp.text
print(f"HTML length: {len(html)}")

# Save HTML for inspection
out = Path(__file__).parent / "farpost_search_raw.html"
out.write_text(html, encoding="utf-8")
print(f"Saved HTML to {out}")

# Find bulletin links
card_pat = re.compile(
    r'<a[^>]*href=["\'](?:https?://www\.farpost\.ru)?/[^"\']*/([a-z0-9\-]*?(\d{5,}))[a-z0-9\-]*\.html["\'][^>]*>',
    re.I
)
matches = list(card_pat.finditer(html))
print(f"\nBulletin links found: {len(matches)}")

for i, m in enumerate(matches[:5]):
    fid = m.group(2)
    bs = m.start()
    be = matches[i + 1].start() if i + 1 < len(matches) else min(bs + 4000, len(html))
    block = html[bs:be]
    print(f"\n{'='*60}")
    print(f"BLOCK {i+1} id={fid} len={len(block)}")
    print(f"{'='*60}")
    # Print first 1200 chars
    print(block[:1200])
    print("...")

    # Try various price patterns
    p1 = re.search(r'data-bulletin-price=["\'](\d+)["\']', block)
    p2 = re.search(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)(?:\s*[.,]\d{1,2})?\s*(?:\u20bd|руб)', block)
    p3 = re.search(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>([^<]+)<', block, re.I)
    p4 = re.search(r'>(\d{3,12}(?:[\s\u00a0]\d{3})*)\s*(?:\u20bd|руб|\.)\s*<', block)
    p5 = re.search(r'content=["\'](\d+)["\']\s*itemprop=["\']price', block, re.I)
    p6 = re.search(r'itemprop=["\']price["\'][^>]*content=["\']?([\d.]+)', block, re.I)
    p7 = re.search(r'"price"\s*:\s*"?(\d+)', block, re.I)
    p8 = re.search(r'(\d{3,12})\s*(?:\u20bd|руб)', block)
    p9 = re.search(r'<span[^>]*>(\d{3,12}(?:[\s\u00a0]\d{3})*)</span>', block)

    print(f"  [p1] data-bulletin-price: {p1.group(1) if p1 else None}")
    print(f"  [p2] text RUB: {p2.group(0) if p2 else None}")
    print(f"  [p3] class=price: {p3.group(1) if p3 else None}")
    print(f"  [p4] >num< RUB: {p4.group(0) if p4 else None}")
    print(f"  [p5] itemprop=price: {p5.group(1) if p5 else None}")
    print(f"  [p6] itemprop price content: {p6.group(1) if p6 else None}")
    print(f"  [p7] JSON price: {p7.group(1) if p7 else None}")
    print(f"  [p8] num+RUB: {p8.group(0) if p8 else None}")
    print(f"  [p9] span num: {p9.group(1) if p9 else None}")

c.close()
print("\nDone.")

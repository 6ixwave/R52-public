"""Диагностика v2: ищем цену в полном HTML Farpost search."""
import re, sys, time, logging
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.farpost2")

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

ct = (resp.headers.get("Content-Type") or "").lower()
if "1251" in ct or "cp1251" in ct:
    html = resp.content.decode("windows-1251", errors="replace")
else:
    html = resp.text
print(f"Status: {resp.status_code}, len={len(html)}")

# 1. Find ALL price-related elements
print("\n" + "="*70)
print("1. ALL elements with 'price' in class/id/attribute")
print("="*70)
price_pat = re.compile(r'<[^>]*(?:class|id|data-)[^>]*price[^>]*>[^<]*<', re.I)
price_matches = list(price_pat.finditer(html))
print(f"Found {len(price_matches)} price elements")
for i, m in enumerate(price_matches[:15]):
    start = max(0, m.start() - 100)
    end = min(len(html), m.end() + 200)
    snippet = html[start:end]
    print(f"\n--- price element {i+1} (pos {m.start()}) ---")
    print(snippet)

# 2. Find ruble sign occurrences with context
print("\n" + "="*70)
print("2. Ruble sign (₽ / &#8381;) with surrounding context")
print("="*70)
rub_pat = re.compile(r'(\d[\d\s\u00a0]{2,15})\s*(?:&#8381;|₽|руб)')
rub_matches = list(rub_pat.finditer(html))
print(f"Found {len(rub_matches)} price+currency pairs")
for i, m in enumerate(rub_matches[:10]):
    start = max(0, m.start() - 200)
    end = min(len(html), m.end() + 100)
    snippet = html[start:end]
    print(f"\n--- ruble {i+1} (pos {m.start()}, val={m.group(0)}) ---")
    print(snippet)

# 3. Look for data-price, data-cost, data-value attributes
print("\n" + "="*70)
print("3. data-* price attributes")
print("="*70)
data_pat = re.compile(r'data-(?:price|cost|value|amount|sum)[^=]*=["\']([^"\']*)["\']', re.I)
data_matches = list(data_pat.finditer(html))
print(f"Found {len(data_matches)} data-price attributes")
for i, m in enumerate(data_matches[:10]):
    print(f"  {m.group(0)} (pos {m.start()})")

# 4. Look at the FULL card structure: find one bulletin card and show 3000 chars
print("\n" + "="*70)
print("4. Full card structure (first bulletin, 3000 chars from card start)")
print("="*70)
card_pat = re.compile(
    r'<a[^>]*href=["\'](?:https?://www\.farpost\.ru)?/[^"\']*/([a-z0-9\-]*?(\d{5,}))[a-z0-9\-]*\.html["\'][^>]*>',
    re.I
)
matches = list(card_pat.finditer(html))
if matches:
    # Go back 500 chars before first <a> to catch the card wrapper
    first_a = matches[0]
    card_start = max(0, first_a.start() - 500)
    card_end = min(len(html), first_a.start() + 3000)
    card_html = html[card_start:card_end]
    print(f"Card start offset: {card_start}, first <a> at: {first_a.start()}")
    print(card_html)

# 5. Look for bull-item structure
print("\n" + "="*70)
print("5. 'bull-item' divs (first 5)")
print("="*70)
bull_pat = re.compile(r'<div[^>]*class=["\'][^"\']*bull-item[^"\']*["\'][^>]*>')
bull_matches = list(bull_pat.finditer(html))
print(f"Found {len(bull_matches)} bull-item divs")
for i, m in enumerate(bull_matches[:5]):
    end = min(len(html), m.end() + 500)
    print(f"\n--- bull-item {i+1} (pos {m.start()}) ---")
    print(html[m.start():end])

# 6. JSON-LD or embedded JSON with prices
print("\n" + "="*70)
print("6. JSON-LD / embedded JSON")
print("="*70)
json_pat = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL | re.I)
json_matches = list(json_pat.finditer(html))
print(f"Found {len(json_matches)} JSON-LD blocks")
for i, m in enumerate(json_matches[:3]):
    print(f"\n--- JSON-LD {i+1} (first 500 chars) ---")
    print(m.group(1)[:500])

# Also look for window.__INITIAL_STATE__ or similar
state_pat = re.compile(r'window\.__\w+__\s*=\s*(\{.*?\});?\s*</script>', re.DOTALL)
state_matches = list(state_pat.finditer(html))
print(f"\nFound {len(state_matches)} window state objects")
for i, m in enumerate(state_matches[:2]):
    print(f"--- state {i+1} (first 500) ---")
    print(m.group(1)[:500])

c.close()
print("\nDone.")

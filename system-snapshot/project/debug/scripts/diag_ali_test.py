"""Быстрая диагностика AliExpress сборщика."""
import sys
import json
import logging
import re
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, ".")

from r52.collectors.aliexpress import AliExpressCollector

c = AliExpressCollector(use_proxies=False)
lid = "1005011813323763"
url = c.build_url(lid)
print(f"URL: {url}")
print(f"Source: {c.source}")

t0 = time.monotonic()
resp = c.fetch(url)
elapsed = time.monotonic() - t0

if resp is None:
    print(f"FETCH FAILED ({elapsed:.1f}s)")
    print(f"Stats: {c.stats}")
    sys.exit(1)

html = resp.text if hasattr(resp, "text") else resp.decode("utf-8", errors="replace")
print(f"HTML: {len(html)} bytes ({elapsed:.1f}s)")

# Title tag
m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
print(f"<title>: {(m.group(1)[:120] if m else 'N/A')}")

# Parse
obs = c.parse_page(resp, lid, url, "")
if obs:
    title_val = obs["fields"].get("title", {}).get("value", "N/A")
    print(f"Title: {title_val[:100]}")
    print(f"Prices: {json.dumps(obs['price_candidates'], ensure_ascii=False)[:300]}")
    print(f"Availability: {obs['availability']}")
    media = obs["media"]["items"]
    print(f"Media: {len(media)} items")
    if media:
        print(f"First img: {media[0]['source_url'][:120]}")
    print(f"Category: {obs.get('category_path')}")
    print(f"Fields: {list(obs['fields'].keys())}")
else:
    print("PARSE FAILED - returned None")
    # Debug: что было в HTML
    print("--- DEBUG ---")
    print("Has JSON-LD:", "application/ld+json" in html)
    print("Has __INIT_DATA_STATE__:", "__INIT_DATA_STATE__" in html)
    print("Has runParams:", "runParams" in html)
    print("Has h1:", "<h1" in html.lower())
    print("Has alicdn:", "alicdn" in html)
    print("Has price class:", re.search(r'class="[^"]*price', html, re.I) is not None)
    print("Has og:title:", 'og:title' in html)
    print("Has og:image:", 'og:image' in html)
    # Первый 500 символов
    print(f"HTML head: {html[:500]}")

print(f"\nStats: {c.stats}")

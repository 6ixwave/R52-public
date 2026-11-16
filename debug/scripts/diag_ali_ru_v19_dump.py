"""V19: Dump first 3 card blocks from saved v17 HTML to see exact structure."""
import re, os, html as html_mod

BASE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE, "data", "collector_output", "diag_v17_page.html")

with open(HTML_PATH, "r", encoding="utf-8") as f:
    html = f.read()

print(f"HTML size: {len(html)} bytes")

# Find card-list
idx = html.find('id="card-list"')
print(f"card-list at: {idx}")

# Find all search-card-item anchors
anchor_pat = re.compile(
    r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>'
)
matches = list(anchor_pat.finditer(html[idx:idx+700000]))
print(f"Found {len(matches)} card anchors")

# Dump first 3 cards (from anchor start to next anchor start, max 3000 chars)
for i, m in enumerate(matches[:3]):
    start = m.start()
    end = matches[i+1].start() if i+1 < len(matches) else start + 3000
    block = html[idx+start:idx+end]
    # Limit to 3000 chars for readability
    block = block[:3000]
    
    print(f"\n{'='*70}")
    print(f"CARD {i+1} (len={end-start})")
    print(f"{'='*70}")
    print(f"HREF: {html_mod.unescape(m.group(1))[:150]}")
    print(f"\n--- HTML (first 2500 chars) ---")
    print(block[:2500])
    print(f"\n--- END CARD {i+1} ---")

# Also: find all us--title occurrences in card area
print(f"\n{'='*70}")
print("ALL us--title matches in card area")
print(f"{'='*70}")
card_area = html[idx:idx+700000]
title_matches = list(re.finditer(r'class="[^"]*us--title[^"]*"[^>]*>(.{0,300})', card_area, re.DOTALL))
print(f"Found {len(title_matches)} title elements")
for i, tm in enumerate(title_matches[:5]):
    print(f"\n  [{i+1}] attrs+content: {tm.group(0)[:250]}")

# Find price-like patterns
print(f"\n{'='*70}")
print("PRICE patterns in card area")
print(f"{'='*70}")
# Look for price spans/divs
price_pat = re.compile(r'(?:class="[^"]*[Pp]rice[^"]*"|data-price)[^>]*>([^<]{1,50})')
price_matches = list(price_pat.finditer(card_area))
print(f"Found {len(price_matches)} price elements")
for i, pm in enumerate(price_matches[:10]):
    print(f"  [{i+1}] {pm.group(0)[:120]}")

# pdp_npi in hrefs
print(f"\n--- pdp_npi in hrefs ---")
pdp_matches = re.findall(r'pdp_npi=([^&"]{5,100})', card_area)
print(f"Found {len(pdp_matches)} pdp_npi params")
for i, p in enumerate(pdp_matches[:5]):
    decoded = html_mod.unescape(p)
    print(f"  [{i+1}] raw: {p[:80]}")
    print(f"       dec: {decoded[:80]}")

# Check for aria-label
print(f"\n--- aria-label in cards ---")
aria_matches = re.findall(r'aria-label="([^"]{10,200})"', card_area)
print(f"Found {len(aria_matches)} aria-labels")
for i, a in enumerate(aria_matches[:5]):
    print(f"  [{i+1}] {html_mod.unescape(a)[:120]}")

# Check for title= attribute
print(f"\n--- title= attributes ---")
title_attr_matches = re.findall(r'title="([^"]{10,200})"', card_area)
print(f"Found {len(title_attr_matches)} title attributes")
for i, t in enumerate(title_attr_matches[:5]):
    print(f"  [{i+1}] {html_mod.unescape(t)[:120]}")

"""
diag_avito_img.py — Find image patterns in AVITO cards 10-20 (the ones with img=N).
Dumps URL-like patterns around each data-item-id to understand structure.
"""
import re, sys, os
from pathlib import Path

DEBUG_DIR = Path(__file__).parent
html = (DEBUG_DIR / "avito_final_page.html").read_text(encoding="utf-8")
print(f"HTML: {len(html)} bytes")

card_pat = re.compile(r'data-item-id=["\'](\d{6,})["\']', re.I)
matches = list(card_pat.finditer(html))
print(f"Cards: {len(matches)}")

# Check cards 10-19 (0-indexed) — the ones with img=N
print("\n=== CARDS 10-19 (img=N group) ===")
for i in range(9, min(20, len(matches))):
    m = matches[i]
    aid = m.group(1)
    # Window: 200 chars before to 3000 chars after
    w_start = max(0, m.start() - 200)
    w_end = min(len(html), m.end() + 3000)
    window = html[w_start:w_end]
    
    # Find all URL-like patterns
    urls = re.findall(r'(?:https?:)?//[^"\'\s<>]+', window)
    avito_urls = [u for u in urls if 'avito' in u.lower()]
    
    # Find img/source/picture tags
    img_tags = re.findall(r'<(?:img|source|picture)[^>]*>', window, re.I)
    
    # Find any src/srcset/data-* attributes
    src_attrs = re.findall(r'(?:src|srcset|data-src|data-lazy-src|data-original|data-url|data-image)\s*=\s*["\']([^"\']+)["\']', window, re.I)
    
    print(f"\n[{i}] ID={aid}")
    print(f"  avito URLs in window: {len(avito_urls)}")
    for u in avito_urls[:3]:
        print(f"    {u[:90]}")
    print(f"  img/source/picture tags: {len(img_tags)}")
    for t in img_tags[:3]:
        print(f"    {t[:100]}")
    print(f"  src-like attrs: {len(src_attrs)}")
    for s in src_attrs[:3]:
        print(f"    {s[:90]}")

# Also check: where exactly are avito.st URLs relative to data-item-id?
print("\n\n=== GLOBAL: avito.st URLs relative to cards ===")
avito_st_pat = re.compile(r'(?:https?:)?//[^"\'\s<>]*avito\.st[^"\'\s<>]*', re.I)
avito_st_matches = list(avito_st_pat.finditer(html))
print(f"Total avito.st URLs: {len(avito_st_matches)}")

# For each card, count avito.st URLs in [card_start, next_card_start]
card_boundaries = [(m.start(), m.end()) for m in matches]
for i in range(min(20, len(matches))):
    cs = card_boundaries[i][0]
    ce = card_boundaries[i+1][0] if i+1 < len(card_boundaries) else cs + 4000
    count = sum(1 for am in avito_st_matches if cs <= am.start() < ce)
    # Also check BEFORE the card (previous card's tail)
    prev_start = card_boundaries[i-1][0] if i > 0 else 0
    before_count = sum(1 for am in avito_st_matches if prev_start <= am.start() < cs)
    print(f"  Card {i:2d} (ID={matches[i].group(1)}): after={count:3d}, before={before_count:3d}")

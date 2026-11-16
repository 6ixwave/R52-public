"""
diag_avito_img2.py — Dump exact HTML context around avito.st URLs in img=N cards.
Shows 120 chars before and after each URL to identify the embedding pattern.
"""
import re, sys
from pathlib import Path

DEBUG_DIR = Path(__file__).parent
html = (DEBUG_DIR / "avito_final_page.html").read_text(encoding="utf-8")
print(f"HTML: {len(html)} bytes")

card_pat = re.compile(r'data-item-id=["\'](\d{6,})["\']', re.I)
matches = list(card_pat.finditer(html))

# Focus on card 10 (ID=8430727570, img=N) and card 9 (ID=8546927331, img=N)
for idx in [9, 10, 14]:
    m = matches[idx]
    aid = m.group(1)
    w_start = max(0, m.start() - 100)
    w_end = min(len(html), m.end() + 3000)
    window = html[w_start:w_end]
    
    # Find all avito.st/image URLs (product images, not static icons)
    img_url_pat = re.compile(r'(?:https?:)?//[\d]+\.img\.avito\.st/image/[^"\'\s)]+', re.I)
    url_matches = list(img_url_pat.finditer(window))
    
    print(f"\n{'='*60}")
    print(f"Card {idx} ID={aid} — {len(url_matches)} product img URLs")
    print(f"{'='*60}")
    
    for j, um in enumerate(url_matches[:4]):
        ctx_start = max(0, um.start() - 120)
        ctx_end = min(len(window), um.end() + 60)
        context = window[ctx_start:ctx_end]
        # Clean for display
        context = context.replace('\n', '\\n').replace('\r', '')
        print(f"\n  URL[{j}]: {um.group(0)[:80]}")
        print(f"  Context: ...{context}...")
        print()

# Also: check if there's a pattern like style="...url(...)" or --var: url(...)
print("\n\n=== CSS PATTERN ANALYSIS ===")
# Check for background-image, --custom-prop, url() patterns
css_patterns = [
    (r'background-image\s*:\s*url\(', 'background-image:url('),
    (r'--[\w-]+\s*:\s*url\(', 'CSS var: url('),
    (r'style="[^"]*avito\.st', 'inline style with avito.st'),
    (r'<img[^>]*avito\.st', '<img tag with avito.st'),
    (r'data-[a-z-]*="[^"]*avito\.st', 'data-* attr with avito.st'),
]
for pat, label in css_patterns:
    count = len(re.findall(pat, html, re.I))
    print(f"  {label}: {count} matches")

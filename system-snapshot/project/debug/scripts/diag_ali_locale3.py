"""Диагностика v3: Playwright language selector + data quality check."""
import re, json, time, os, shutil, sys

print("=" * 60)
print("DIAG v3: Playwright language switch + data quality")
print("=" * 60)

PW_PROFILE = os.path.expanduser("~/.local/share/r52/aliexpress_pw_profile_RU_FRESH")
# Keep existing profile (has cookies from v2)

from playwright.sync_api import sync_playwright
from urllib.parse import quote

query = "наушники bluetooth"
search_url = f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}&page=1&_lang=ru_RU&_currency=RUB"

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    user_data_dir=PW_PROFILE,
    locale="ru-RU",
    timezone_id="Europe/Moscow",
    viewport={"width": 1920, "height": 1080},
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ignore_https_errors=True,
)
ctx.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en']});
    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
    Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
    window.chrome = {runtime: {}};
""")
page = ctx.pages[0] if ctx.pages else ctx.new_page()

# ─── Phase A: Load search page ───
print("\n[A] Loading search page...")
page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
page.wait_for_timeout(5000)

html_a = page.content()
lang_a = re.search(r'<html[^>]*lang="([^"]*)"', html_a)
print(f"  lang: {lang_a.group(1) if lang_a else 'NOT FOUND'}")

h3s_a = re.findall(r'<h3[^>]*>(.*?)</h3>', html_a[:300000], re.DOTALL)
h3s_a_clean = [re.sub(r'<[^>]+>', '', h).strip()[:70] for h in h3s_a[:5]]
for i, t in enumerate(h3s_a_clean):
    print(f"  h3[{i}]: {t}")

# ─── Phase B: Try language selector ───
print("\n[B] Looking for language/country selector...")

# Common selectors for AliExpress language switcher
selectors_to_try = [
    # Header language dropdown
    '[class*="language"]',
    '[class*="locale"]',
    '[data-spm*="language"]',
    '[class*="header"] [class*="lang"]',
    'a[href*="_lang=ru"]',
    'a[href*="locale"]',
    '[class*="site-select"]',
    '[class*="country"]',
    # The "RU" or "EN" text in header
    'header a',
    '[class*="topbar"] a',
]

found_selector = None
for sel in selectors_to_try:
    try:
        els = page.query_selector_all(sel)
        if els:
            print(f"  '{sel}': {len(els)} elements")
            for el in els[:5]:
                txt = el.inner_text().strip()[:50] if el.inner_text() else ""
                cls = el.get_attribute("class") or ""
                href = el.get_attribute("href") or ""
                print(f"    text={txt!r} class={cls[:60]!r} href={href[:60]!r}")
            if not found_selector:
                found_selector = sel
    except Exception as e:
        pass

# Try clicking a visible "Русский" or "RU" link/button
print("\n[B2] Trying to click language switch...")
clicked = False
try:
    # Strategy 1: look for an element with text "Русский" or "RU" in header area
    for sel in ['[class*="header"] a', '[class*="topbar"] a', 'header a', '[class*="nav"] a']:
        els = page.query_selector_all(sel)
        for el in els:
            txt = (el.inner_text() or "").strip()
            if txt.lower() in ("русский", "ru", "russian", "рус"):
                print(f"  Found: '{txt}' in {sel}")
                el.click()
                clicked = True
                break
        if clicked:
            break
except Exception as e:
    print(f"  Click error: {e}")

if not clicked:
    # Strategy 2: look for a dropdown/selector component
    try:
        for sel in ['[class*="select"]', '[class*="dropdown"]', '[role="listbox"]', '[class*="combo"]']:
            els = page.query_selector_all(sel)
            for el in els[:3]:
                txt = (el.inner_text() or "").strip()[:80]
                if any(w in txt.lower() for w in ("lang", "locale", "country", "region", "рус", "ital")):
                    print(f"  Dropdown: '{txt}' in {sel}")
                    el.click()
                    page.wait_for_timeout(1000)
                    # Now look for "Русский" option
                    opts = page.query_selector_all('[class*="option"], [class*="item"], li, [role="option"]')
                    for opt in opts:
                        opt_txt = (opt.inner_text() or "").strip()
                        if "рус" in opt_txt.lower() or "russian" in opt_txt.lower():
                            print(f"  Clicking option: '{opt_txt}'")
                            opt.click()
                            clicked = True
                            break
                    if clicked:
                        break
            if clicked:
                break
    except Exception as e:
        print(f"  Dropdown error: {e}")

if clicked:
    print("  Waiting for page reload...")
    page.wait_for_timeout(6000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except:
        pass
    page.wait_for_timeout(3000)
    
    html_b = page.content()
    lang_b = re.search(r'<html[^>]*lang="([^"]*)"', html_b)
    print(f"\n  After switch: lang={lang_b.group(1) if lang_b else 'NOT FOUND'}")
    h3s_b = re.findall(r'<h3[^>]*>(.*?)</h3>', html_b[:300000], re.DOTALL)
    h3s_b_clean = [re.sub(r'<[^>]+>', '', h).strip()[:70] for h in h3s_b[:5]]
    for i, t in enumerate(h3s_b_clean):
        print(f"  h3[{i}]: {t}")
    
    # Check if we're still on search page
    url_b = page.url
    print(f"  URL: {url_b[:100]}")
    
    if "wholesale" not in url_b:
        print("  Redirected away from search, going back...")
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        html_b = page.content()
        lang_b = re.search(r'<html[^>]*lang="([^"]*)"', html_b)
        print(f"  Re-loaded: lang={lang_b.group(1) if lang_b else 'NOT FOUND'}")
        h3s_b = re.findall(r'<h3[^>]*>(.*?)</h3>', html_b[:300000], re.DOTALL)
        h3s_b_clean = [re.sub(r'<[^>]+>', '', h).strip()[:70] for h in h3s_b[:5]]
        for i, t in enumerate(h3s_b_clean):
            print(f"  h3[{i}]: {t}")
else:
    print("  No language switcher found/clicked")
    html_b = html_a

# ─── Phase C: Data quality from the best HTML ───
print("\n[C] Data quality analysis")
html_final = html_b
card_list_idx = html_final.find('id="card-list"')
print(f"  card-list at: {card_list_idx}")

if card_list_idx >= 0:
    window_end = min(card_list_idx + 700000, len(html_final))
    card_area = html_final[card_list_idx:window_end]
    
    # Count product cards
    anchor_pat = re.compile(r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>')
    matches = list(anchor_pat.finditer(card_area))
    if not matches:
        anchor_pat2 = re.compile(r'<a[^>]*href="([^"]*/item/\d+\.html[^"]*)"[^>]*>')
        matches = list(anchor_pat2.finditer(card_area))
    print(f"  Product cards: {len(matches)}")
    
    # Extract first 5 products with full detail
    for i, m in enumerate(matches[:5]):
        href = m.group(1)
        block_start = m.start()
        block_end = matches[i+1].start() if i+1 < len(matches) else min(block_start + 7000, len(card_area))
        card_html = card_area[block_start:block_end]
        
        id_m = re.search(r'/item/(\d+)\.html', href)
        pid = id_m.group(1) if id_m else "?"
        
        # Title
        t_m = re.search(r'<h3[^>]*>(.*?)</h3>', card_html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', t_m.group(1)).strip() if t_m else "NO TITLE"
        
        # Price
        price_m = re.search(r'class="[^"]*price[^"]*"[^>]*>.*?([\d.,]+)', card_html, re.DOTALL)
        price = price_m.group(1) if price_m else "NO PRICE"
        
        # Image
        img_m = re.search(r'<img[^>]*src="([^"]+)"', card_html)
        img = img_m.group(1)[:60] if img_m else "NO IMG"
        
        # Currency
        cur_m = re.search(r'([A-Z]{3})', card_html[card_html.find('price'):card_html.find('price')+200]) if 'price' in card_html else None
        currency = cur_m.group(1) if cur_m else "?"
        
        # Detect language
        has_ru = any('\u0410' <= c <= '\u044f' for c in title)
        has_it = any(w in title.lower() for w in ('cuffie', 'blu', 'nuovo', 'prezzo', 'offerta'))
        lang_det = "RU" if has_ru else ("IT" if has_it else "EN/OTHER")
        
        print(f"\n  Product[{i}]: id={pid}")
        print(f"    title ({lang_det}): {title[:70]}")
        print(f"    price: {price} {currency}")
        print(f"    img: {img}")
    
    # Overall stats
    all_titles = []
    for m in matches:
        block_start = m.start()
        block_end = matches[matches.index(m)+1].start() if matches.index(m)+1 < len(matches) else min(block_start + 7000, len(card_area))
        card_html = card_area[block_start:block_end]
        t_m = re.search(r'<h3[^>]*>(.*?)</h3>', card_html, re.DOTALL)
        if t_m:
            title = re.sub(r'<[^>]+>', '', t_m.group(1)).strip()
            all_titles.append(title)
    
    ru_t = sum(1 for t in all_titles if any('\u0410' <= c <= '\u044f' for c in t))
    it_t = sum(1 for t in all_titles if any(w in t.lower() for w in ('cuffie','blu','nuovo','prezzo','offerta','con')))
    en_t = len(all_titles) - ru_t - it_t
    print(f"\n  Title language: RU={ru_t}, IT={it_t}, EN/OTHER={en_t} (total={len(all_titles)})")

# ─── Phase D: Check price format (RUB vs EUR) ───
print("\n[D] Price/currency check")
price_patterns = re.findall(r'([\d.,]+)\s*(?:₽|руб|EUR|€|\$|RUB|USD|ITL)', html_final[card_list_idx:card_list_idx+200000])
print(f"  Price tokens found: {len(price_patterns)}")
for p in price_patterns[:10]:
    print(f"    {p}")

# Also look for "RUB" or "₽" in the page
rub_count = html_final.count('₽') + html_final.count('RUB') + html_final.count('руб')
eur_count = html_final.count('€') + html_final.count('EUR') + html_final.count('eur')
print(f"  RUB markers: {rub_count}, EUR markers: {eur_count}")

# ─── Phase E: Check if prices are in the right format ───
print("\n[E] Price structure in cards")
# Look for price-related class names
price_classes = set(re.findall(r'class="([^"]*price[^"]*)"', html_final[card_list_idx:card_list_idx+200000]))
print(f"  Price classes: {list(price_classes)[:10]}")

# Look for data attributes with prices
data_prices = re.findall(r'data-[^=]*price[^=]*="([^"]+)"', html_final[card_list_idx:card_list_idx+200000])
print(f"  data-price attrs: {data_prices[:5]}")

ctx.close()
pw.stop()

print("\n" + "=" * 60)
print("DIAG v3 complete")
print("=" * 60)

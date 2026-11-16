"""
diag_farpost_v2.py — Углублённая диагностика FARPOST search.
1. Пробует несколько URL-форматов поиска
2. Определяет: CAPTCHA (grecap/altcha) vs реальная выдача
3. При выдаче — парсит карточки и цены
4. При CAPTCHA — анализирует форму для auto-solve
"""
import sys, os, re, json, time, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("diag.fp2")

from r52.collectors.farpost import FarpostCollector
from urllib.parse import quote

QUERY = "тест"
collector = FarpostCollector(data_dir=None, use_proxies=False)
collector._warmup()
time.sleep(2)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.farpost.ru/",
}

# URL variants to try
URLS = [
    ("A: /dir?query=тест&page=1", f"https://www.farpost.ru/dir?query={quote(QUERY)}&page=1"),
    ("B: /dir?query=тест (no page)", f"https://www.farpost.ru/dir?query={quote(QUERY)}"),
    ("C: /search?query=тест", f"https://www.farpost.ru/search?query={quote(QUERY)}"),
    ("D: /vladivostok/all/ (category)", "https://www.farpost.ru/vladivostok/all/"),
    ("E: /dir (POST-like GET)", f"https://www.farpost.ru/dir?query={quote(QUERY)}&page=1&f=1"),
]

def fetch(url, label):
    print(f"\n{'='*60}")
    print(f"[{label}] {url}")
    t0 = time.time()
    try:
        if collector._cffi:
            resp = collector._cffi.get(url, impersonate="chrome", timeout=(15, 30), headers=HEADERS)
        else:
            resp = collector.session.get(url, headers=HEADERS, timeout=(10, 30))
        elapsed = time.time() - t0
        ct = (resp.headers.get("Content-Type") or "").lower()
        if "1251" in ct or "cp1251" in ct:
            html = resp.content.decode("windows-1251", errors="replace")
        else:
            html = resp.text
        print(f"    Status: {resp.status_code}, Size: {len(resp.content)}B, Time: {elapsed:.1f}s")
        print(f"    Final URL: {resp.url}")
        print(f"    CT: {ct}")

        # Detect CAPTCHA
        has_grecap = "grecap-form" in html or "altcha-widget" in html
        has_verify = "/verify" in (resp.url or "")
        title_m = re.search(r'<title>([^<]+)</title>', html, re.I)
        title = title_m.group(1).strip() if title_m else "?"
        print(f"    Title: {title[:80]}")
        print(f"    CAPTCHA: grecap={has_grecap}, /verify={has_verify}")

        # Count bulletin links
        card_pat = re.compile(
            r'<a[^>]*href=["\'](?:https?://www\.farpost\.ru)?/[^"\']*/([a-z0-9\-]*?(\d{5,}))[a-z0-9\-]*\.html["\'][^>]*>',
            re.I
        )
        matches = list(card_pat.finditer(html))
        print(f"    Bulletin links: {len(matches)}")

        # Price indicators
        price_hits = len(re.findall(r'(?:data-price|data-bulletin-price|viewbull.*price|₽|руб)', html, re.I))
        print(f"    Price indicators: {price_hits}")

        # Save if useful
        if matches or has_grecap:
            fname = f"farpost_v2_{label.split(':')[0].strip()}.html"
            fpath = os.path.join(os.path.dirname(__file__), fname)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"    Saved: {fname}")

        return {"label": label, "status": resp.status_code, "size": len(resp.content),
                "url": resp.url, "title": title, "grecap": has_grecap,
                "verify": has_verify, "cards": len(matches), "prices": price_hits,
                "html": html, "matches": matches}
    except Exception as e:
        print(f"    ERROR: {type(e).__name__}: {e}")
        return {"label": label, "error": str(e)}

# Try each URL
results = []
for label, url in URLS:
    r = fetch(url, label)
    results.append(r)
    time.sleep(3)  # be polite

# Summary
print(f"\n{'='*60}")
print("SUMMARY:")
for r in results:
    if "error" in r:
        print(f"  {r['label']}: ERROR {r['error']}")
    else:
        status = "CAPTCHA" if (r["grecap"] or r["verify"]) else ("RESULTS" if r["cards"] > 0 else "EMPTY")
        print(f"  {r['label']}: {status} | cards={r['cards']} prices={r['prices']} title={r['title'][:40]}")

# If any URL got real results, parse them
best = max((r for r in results if r.get("cards", 0) > 0), key=lambda x: x["cards"], default=None)
if best:
    print(f"\n{'='*60}")
    print(f"PARSING BEST RESULT: {best['label']} ({best['cards']} cards)")
    html = best["html"]
    matches = best["matches"]

    # Parse first 10 cards
    for i, m in enumerate(matches[:10]):
        fid = m.group(2)
        block_start = max(0, m.start() - 2500)
        block_end = matches[i+1].start() if i+1 < len(matches) else min(m.end()+2000, len(html))
        block = html[block_start:block_end]

        # Title
        title = None
        a_close = html.find('</a>', m.end())
        if a_close > 0:
            a_text = html[m.end():a_close]
            a_clean = re.sub(r'<[^>]+>', '', a_text).strip()
            if len(a_clean) >= 5:
                title = a_clean

        # Price
        price = 0
        for pat in [
            re.compile(r'data-price=["\']([\d][\d\s\u00a0]*)'),
            re.compile(r'data-role=["\']price["\']>([\d][\d\s\u00a0]*)'),
            re.compile(r'data-bulletin-price=["\'](\d+)["\']'),
            re.compile(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)\s*(?:₽|руб|&#8381;)'),
        ]:
            pm = pat.search(block)
            if pm:
                try:
                    price = int(float(pm.group(1).replace(' ','').replace('\u00a0','')) * 100)
                    break
                except:
                    pass

        print(f"  [{i+1}] ID={fid} | price={'₽'+str(price//100) if price else 'NONE'} | {title or 'NO TITLE'}[:50]")

# If all CAPTCHA, analyze the form
all_captcha = all(r.get("grecap") or r.get("verify") for r in results if "error" not in r)
if all_captcha:
    print(f"\n{'='*60}")
    print("ALL URLS RETURN CAPTCHA — analyzing grecap form...")
    sample = next((r for r in results if r.get("grecap")), results[0])
    html = sample["html"]
    # Extract form
    form_m = re.search(r'<form[^>]*id="grecap-form"[^>]*>(.*?)</form>', html, re.DOTALL)
    if form_m:
        print(f"  Form HTML:\n  {form_m.group(0)[:1000]}")
    # AltCHA challenge
    altcha_m = re.search(r'<altcha-widget[^>]*challenge="([^"]+)"', html)
    if altcha_m:
        print(f"  AltCHA challenge: {altcha_m.group(1)}")
    # Check for fallback link
    fallback_m = re.search(r'id="grecap-fallback"[^>]*href="([^"]+)"', html)
    if fallback_m:
        print(f"  Fallback URL: {fallback_m.group(1)}")

print(f"\n=== DONE ===")

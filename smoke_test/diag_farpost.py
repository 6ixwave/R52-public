"""Диагностика FARPOST: сохраняет HTML, пробует parse_page, ищет паттерны."""
import sys, os, re, time, json, traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

OUT = Path(__file__).resolve().parent / "diag_farpost.log"
HTML_OUT = Path(__file__).resolve().parent / "diag_farpost_page.html"

def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
        f.flush()
        os.fsync(f.fileno())

def main():
    OUT.write_text("", encoding="utf-8")
    lid = "18530297982"
    url = f"https://www.farpost.ru/vladivostok/{lid}.html"
    log(f"=== FARPOST DIAG at {time.strftime('%H:%M:%S')} ===")
    log(f"URL: {url}")

    # 1. Fetch
    import requests
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.farpost.ru/",
    }
    t0 = time.monotonic()
    resp = requests.get(url, headers=headers, timeout=(10, 30))
    log(f"Fetch: HTTP {resp.status_code}, {len(resp.content)} bytes ({time.monotonic()-t0:.1f}s)")
    log(f"Content-Type: {resp.headers.get('Content-Type','?')}")

    # Save raw HTML
    HTML_OUT.write_bytes(resp.content)
    log(f"HTML saved: {HTML_OUT.name}")

    # Decode
    ct = (resp.headers.get("Content-Type") or "").lower()
    if "windows-1251" in ct or "cp1251" in ct or "1251" in ct:
        html = resp.content.decode("windows-1251", errors="replace")
        log(f"Decoded as windows-1251")
    else:
        raw_head = resp.content[:500].decode("ascii", errors="ignore")
        if "windows-1251" in raw_head.lower() or "cp1251" in raw_head.lower():
            html = resp.content.decode("windows-1251", errors="replace")
            log(f"Decoded as windows-1251 (from meta)")
        else:
            html = resp.text
            log(f"Decoded as {resp.encoding}")
    log(f"HTML length: {len(html)} chars")

    # 2. Pattern search
    log("\n--- PATTERN SEARCH ---")
    # Title
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    log(f"<title>: {m.group(1)[:100] if m else 'NOT FOUND'}")

    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
    log(f"<h1>: {re.sub(r'<[^>]+>','',m.group(1)).strip()[:100] if m else 'NOT FOUND'}")

    # JSON-LD
    ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL | re.I)
    log(f"JSON-LD blocks: {len(ld_blocks)}")
    for i, lb in enumerate(ld_blocks[:3]):
        try:
            data = json.loads(lb.strip())
            log(f"  LD[{i}] type={data.get('@type','?')} name={str(data.get('name',''))[:80]}")
        except Exception as e:
            log(f"  LD[{i}] parse error: {e}, first 200: {lb.strip()[:200]}")

    # OG
    og_title = re.search(r'property="og:title"[^>]*content="([^"]*)"', html) or \
               re.search(r'content="([^"]*)"[^>]*property="og:title"', html)
    log(f"og:title: {og_title.group(1)[:100] if og_title else 'NOT FOUND'}")

    # Price patterns
    price_patterns = [
        (r'class="[^"]*price[^"]*"[^>]*>([^<]{3,50})<', "class*=price"),
        (r'data-field="price"[^>]*>([^<]{3,50})<', "data-field=price"),
        (r'itemprop="price"[^>]*content="([^"]*)"', "itemprop=price"),
        (r'"price"\s*:\s*"?([\d.]+)', "json price"),
        (r'(\d[\d\s]{2,12})\s*руб', "N руб"),
    ]
    for pat, label in price_patterns:
        matches = re.findall(pat, html, re.I)
        log(f"  [{label}]: {len(matches)} matches, first={matches[:3] if matches else '—'}")

    # 3. Try collector parse_page
    log("\n--- COLLECTOR PARSE_PAGE ---")
    try:
        from r52.collectors.farpost import FarpostCollector
        col = FarpostCollector(use_proxies=False)
        t0 = time.monotonic()
        obs = col.parse_page(resp, lid, url, "")
        elapsed = round(time.monotonic() - t0, 2)
        if obs is None:
            log(f"parse_page → None ({elapsed}s)")
        else:
            fields = obs.get("fields", {})
            prices = obs.get("price_candidates", [])
            log(f"parse_page → OK ({elapsed}s)")
            log(f"  fields: {list(fields.keys())}")
            for k, v in fields.items():
                val = v.get("value", "") if isinstance(v, dict) else str(v)
                log(f"    {k}: {str(val)[:80]}")
            log(f"  prices: {len(prices)}")
            for p in prices[:5]:
                log(f"    {p}")
            log(f"  availability: {obs.get('availability')}")
    except Exception as e:
        log(f"parse_page EXCEPTION: {type(e).__name__}: {e}")
        log(traceback.format_exc())

    log(f"\n=== END at {time.strftime('%H:%M:%S')} ===")
    print("FARPOST diag done. Log:", OUT)

if __name__ == "__main__":
    main()

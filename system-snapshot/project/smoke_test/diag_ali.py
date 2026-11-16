"""Диагностика ALIEXPRESS: сохраняет HTML из Playwright, ищет паттерны title/price."""
import sys, os, re, time, json, traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

OUT = Path(__file__).resolve().parent / "diag_ali.log"
HTML_OUT = Path(__file__).resolve().parent / "diag_ali_page.html"

def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
        f.flush()
        os.fsync(f.fileno())

def main():
    OUT.write_text("", encoding="utf-8")
    lid = "1005006564853571"
    url = f"https://www.aliexpress.com/item/{lid}.html"
    log(f"=== ALIEXPRESS DIAG at {time.strftime('%H:%M:%S')} ===")
    log(f"URL: {url}")

    # 1. HTTP fetch (fast, no browser)
    import requests
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    t0 = time.monotonic()
    resp = requests.get(url, headers=headers, timeout=(10, 30))
    log(f"HTTP: {resp.status_code}, {len(resp.content)} bytes ({time.monotonic()-t0:.1f}s)")
    HTML_OUT.write_bytes(resp.content)
    log(f"HTML saved: {HTML_OUT.name}")

    html = resp.text
    log(f"HTML len: {len(html)}")

    # 2. Pattern search
    log("\n--- PATTERN SEARCH ---")
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    log(f"<title>: {(m.group(1)[:120] if m else 'NOT FOUND')}")

    # h1
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
    log(f"<h1>: {re.sub(r'<[^>]+>','',m.group(1)).strip()[:120] if m else 'NOT FOUND'}")

    # JSON-LD
    ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL | re.I)
    log(f"JSON-LD blocks: {len(ld_blocks)}")
    for i, lb in enumerate(ld_blocks[:5]):
        try:
            data = json.loads(lb.strip())
            t = data.get("@type", "?")
            name = data.get("name", data.get("title", ""))
            price = data.get("offers", {}).get("price", data.get("price", ""))
            log(f"  LD[{i}] type={t} name={str(name)[:80]} price={price}")
        except Exception as e:
            log(f"  LD[{i}] parse err: {e}, first100={lb.strip()[:100]}")

    # OG
    og_t = re.search(r'property="og:title"[^>]*content="([^"]*)"', html) or \
           re.search(r'content="([^"]*)"[^>]*property="og:title"', html)
    log(f"og:title: {og_t.group(1)[:120] if og_t else 'NOT FOUND'}")

    # Price patterns
    price_pats = [
        (r'class="[^"]*price[^"]*"[^>]*>([^<]{3,60})<', "class*=price"),
        (r'data-pl="price"[^>]*>([^<]{3,60})<', "data-pl=price"),
        (r'"price"\s*:\s*"?([\d.]+)', "json price"),
        (r'US\s?\$?\s?([\d,]+\.?\d*)', "US$ pattern"),
        (r'(\d[\d,]{2,12})\s*(?:USD|US\$|\$)', "N USD"),
        (r'"formattedPrice"\s*:\s*"([^"]+)"', "formattedPrice"),
        (r'"priceText"\s*:\s*"([^"]+)"', "priceText"),
    ]
    for pat, label in price_pats:
        matches = re.findall(pat, html, re.I)
        log(f"  [{label}]: {len(matches)} matches, first={matches[:3] if matches else '—'}")

    # TMD / captcha check
    tmd = "baxia-punish" in html or "tmd" in html.lower()[:5000]
    log(f"\nTMD/captcha detected: {tmd}")

    # 3. Try collector parse_page
    log("\n--- COLLECTOR PARSE ---")
    try:
        from r52.collectors.aliexpress import AliExpressCollector
        col = AliExpressCollector(use_proxies=False)
        t0 = time.monotonic()
        # Call internal parse if available
        if hasattr(col, "parse_page"):
            class _R:
                def __init__(s, text, status):
                    s.text = text
                    s.status_code = status
                    s.headers = {"Content-Type": "text/html"}
            obs = col.parse_page(_R(html, 200), lid, url, "")
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
        else:
            log("No parse_page method found, trying collect_product...")
            wrapper = col.collect_product(lid, url)
            elapsed = round(time.monotonic() - t0, 2)
            if wrapper is None:
                log(f"collect_product → None ({elapsed}s)")
            else:
                obs = wrapper.get("observation", {})
                fields = obs.get("fields", {})
                log(f"collect_product → OK ({elapsed}s) fields={list(fields.keys())}")
    except Exception as e:
        log(f"PARSE EXCEPTION: {type(e).__name__}: {e}")
        log(traceback.format_exc())

    log(f"\n=== END at {time.strftime('%H:%M:%S')} ===")
    print("ALIEXPRESS diag done. Log:", OUT)

if __name__ == "__main__":
    main()

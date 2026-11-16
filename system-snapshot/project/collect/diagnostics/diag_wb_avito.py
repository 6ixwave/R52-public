"""Диагностика WB и Avito через curl_cffi (browser impersonation).
Цель: получить реальный HTML карточки, проверить JSON-LD / state / цены / медиа.
"""
import json, os, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
OUT = Path(__file__).parent

from curl_cffi import requests as cffi_req

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def report_common(tag, r, html):
    print(f"  HTTP: {r.status_code}, len: {len(html)}")
    if r.history:
        for h in r.history:
            print(f"  Redirect: {h.status_code} -> {h.url[:100]}")
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.I)
    if m:
        print(f"  <title>: {m.group(1).strip()[:100]}")
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
    if m:
        print(f"  <h1>: {m.group(1).strip()[:100]}")
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        print(f"  og:title: {m.group(1)[:100]}")
    # JSON-LD
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.I)
    print(f"  JSON-LD blocks: {len(blocks)}")
    for i, b in enumerate(blocks):
        try:
            d = json.loads(b.strip())
            t = d.get("@type", "?")
            name = d.get("name", "")[:60]
            price = ""
            off = d.get("offers")
            if isinstance(off, dict):
                price = f" price={off.get('price','?')} {off.get('priceCurrency','')}"
            elif isinstance(off, list) and off:
                price = f" price={off[0].get('price','?')}"
            print(f"    [{i}] type={t} name={name}{price}")
        except Exception:
            print(f"    [{i}] parse-error, len={len(b)}")
    # Prices in visible text
    prices = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
    print(f"  Price mentions: {len(prices)}, sample: {prices[:6]}")
    return r.status_code == 200 and len(html) > 2000

def try_wb(nm_id):
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    print(f"\n{'='*60}\nWB: {url}\n{'='*60}")
    try:
        r = cffi_req.get(url, impersonate="chrome", timeout=25)
        html = r.text
        ok = report_common("WB", r, html)
        # Save
        hf = OUT / f"diag_wb_{nm_id}.html"
        hf.write_text(html, encoding="utf-8")
        print(f"  Saved: {hf.name} ({hf.stat().st_size} B)")
        # State patterns
        for pat_name, pat in [
            ("__STATE__", r'window\.__STATE__\s*=\s*(\{.+?\})\s*;?\s*</script>'),
            ("script#state", r'<script[^>]*id=["\']state["\'][^>]*>(\{.+?\})</script>'),
            ("__WB_STATE__", r'window\.__WB_STATE__\s*=\s*(\{.+?\})\s*;'),
            ("data-state", r'data-state=["\'](\{.+?\})["\']'),
        ]:
            m = re.search(pat, html, re.DOTALL)
            if m:
                print(f"  STATE[{pat_name}]: len={len(m.group(1))}")
                break
        # wbbasket images
        imgs = re.findall(r'(?:src|data-src|data-original)=["\']([^"\']*wbbasket\.ru[^"\']*)["\']', html, re.I)
        print(f"  wbbasket imgs: {len(imgs)}")
        if imgs:
            print(f"    sample: {imgs[0][:80]}")
        # Check if it's a redirect/SPA shell
        if len(html) < 5000:
            print("  WARNING: very short HTML — possible SPA shell or redirect")
            # Look for meta refresh or JS redirect
            m = re.search(r'<meta[^>]*http-equiv=["\']refresh["\'][^>]*>', html, re.I)
            if m:
                print(f"  META REFRESH: {m.group(0)[:100]}")
            m = re.search(r'window\.location\.(?:href|replace)\s*=\s*["\']([^"\']+)', html)
            if m:
                print(f"  JS REDIRECT: {m.group(1)[:100]}")
        return ok
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return False

def try_avito(ad_id):
    urls = [
        f"https://www.avito.ru/ad/{ad_id}",
        f"https://m.avito.ru/ad/{ad_id}",
    ]
    for url in urls:
        print(f"\n{'='*60}\nAVITO: {url}\n{'='*60}")
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=25,
                             headers={"Accept-Language": "ru-RU,ru;q=0.9"})
            html = r.text
            ok = report_common("AVITO", r, html)
            hf = OUT / f"diag_avito_{ad_id}_{r.status_code}.html"
            hf.write_text(html, encoding="utf-8")
            print(f"  Saved: {hf.name} ({hf.stat().st_size} B)")
            # State
            for pat_name, pat in [
                ("__PRELOADED_STATE__", r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\})'),
                ("__INITIAL_STATE__", r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})'),
                ("data-state", r'data-state=["\'](\{.+?\})["\']'),
            ]:
                m = re.search(pat, html, re.DOTALL)
                if m:
                    print(f"  STATE[{pat_name}]: len={len(m.group(1))}")
                    break
            # avito.st images
            imgs = re.findall(r'(?:src|data-src)=["\']([^"\']*avito\.st[^"\']*)["\']', html, re.I)
            print(f"  avito.st imgs: {len(imgs)}")
            # CAPTCHA
            cap = any(x in html[:5000].lower() for x in
                      ["captcha", "recaptcha", "smartcaptcha", "turnstile", "cf-challenge"])
            print(f"  CAPTCHA: {cap}")
            if ok and not cap:
                return True
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
    return False

# ── Main ──
print("DIAG WB + AVITO via curl_cffi")
print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"curl_cffi: {cffi_req.__version__ if hasattr(cffi_req,'__version__') else 'ok'}")

# WB: try several IDs (9-10 digit, realistic ranges)
wb_ids = sys.argv[1:] if len(sys.argv) > 1 else [
    "110835207", "128686583", "145321987", "160000001", "175000002",
]
wb_ok = False
for nm in wb_ids:
    if try_wb(nm):
        print(f"\n>>> WB {nm} SUCCESS")
        wb_ok = True
        break
    time.sleep(1.5)

# Avito
avito_ids = sys.argv[1 + len(wb_ids):] if len(sys.argv) > 1 + len(wb_ids) else [
    "1154321987", "1204567890", "1187654321",
]
avito_ok = False
for aid in avito_ids:
    if try_avito(aid):
        print(f"\n>>> AVITO {aid} SUCCESS")
        avito_ok = True
        break
    time.sleep(2)

print(f"\n{'='*60}")
print(f"RESULT: WB={'OK' if wb_ok else 'FAIL'}, AVITO={'OK' if avito_ok else 'FAIL'}")
print("DONE")

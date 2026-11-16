"""Диагностика v2: WB cookies, OZON HTML-структура, ALIEXPRESS redirect."""
import sys, time, json, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curl_cffi import requests as cr

out = {}

# ============================================================
# 1. WB: warmup → cookies → search
# ============================================================
print("=" * 60)
print("WB: warmup + search with session cookies")
print("=" * 60)

s = cr.Session(impersonate="chrome")

# Warmup: главная WB
t0 = time.monotonic()
try:
    r = s.get("https://www.wildberries.ru/", timeout=15,
              headers={"Accept-Language": "ru-RU,ru;q=0.9"})
    print(f"  WARMUP: HTTP {r.status_code}, {len(r.content)} bytes, {time.monotonic()-t0:.1f}s")
    print(f"  COOKIES: {dict(s.cookies)}")
    wb_cookies = dict(s.cookies)
except Exception as e:
    print(f"  WARMUP ERROR: {e}")
    wb_cookies = {}

time.sleep(1)

# Search via catalog.wb.ru (с теми же cookies из сессии)
for host in ["catalog.wb.ru", "search.wb.ru"]:
    t0 = time.monotonic()
    try:
        r = s.get(
            f"https://{host}/exactmatch/ru/common/v4/search"
            "?query=%D0%BD%D0%B0%D1%83%D1%88%D0%BD%D0%B8%D0%BA%D0%B8"
            "&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&page=1",
            headers={"Accept": "application/json", "Accept-Language": "ru-RU,ru;q=0.9"},
            timeout=15,
        )
        el = time.monotonic() - t0
        print(f"\n  {host}: HTTP {r.status_code}, {len(r.content)} bytes, {el:.1f}s")
        if r.status_code == 200:
            d = r.json()
            prods = d.get("products", [])
            print(f"  PRODUCTS={len(prods)}")
            for p in prods[:3]:
                print(f"    id={p.get('id')} name={str(p.get('name',''))[:50]}")
            out[f"WB_{host}"] = {"status": "OK", "count": len(prods)}
        elif r.status_code == 403:
            print(f"  BODY[:500]={r.text[:500]}")
            out[f"WB_{host}"] = {"status": "403", "body": r.text[:200]}
        else:
            print(f"  BODY[:300]={r.text[:300]}")
            out[f"WB_{host}"] = {"status": str(r.status_code)}
    except Exception as e:
        el = time.monotonic() - t0
        print(f"  {host} ERROR: {type(e).__name__}: {e}")
        out[f"WB_{host}"] = {"status": "ERROR", "error": str(e)[:150]}
    time.sleep(2)

# WB warmup 498: пробуем www.wb.ru вместо wildberries.ru
print("\n  --- WB warmup alt: www.wb.ru ---")
s2 = cr.Session(impersonate="chrome")
t0 = time.monotonic()
try:
    r = s2.get("https://www.wb.ru/", timeout=15,
               headers={"Accept-Language": "ru-RU,ru;q=0.9"})
    print(f"  WARMUP wb.ru: HTTP {r.status_code}, {len(r.content)} bytes, {time.monotonic()-t0:.1f}s")
    print(f"  COOKIES: {dict(s2.cookies)}")
    time.sleep(1)
    r2 = s2.get(
        "https://catalog.wb.ru/exactmatch/ru/common/v4/search"
        "?query=%D0%BD%D0%B0%D1%83%D1%88%D0%BD%D0%B8%D0%BA%D0%B8"
        "&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&page=1",
        headers={"Accept": "application/json", "Accept-Language": "ru-RU,ru;q=0.9"},
        timeout=15,
    )
    print(f"  SEARCH after wb.ru warmup: HTTP {r2.status_code}, {len(r2.content)} bytes")
    if r2.status_code == 200:
        d = r2.json()
        prods = d.get("products", [])
        print(f"  PRODUCTS={len(prods)}")
        out["WB_wb_ru_warmup"] = {"status": "OK", "count": len(prods)}
except Exception as e:
    print(f"  ERROR: {e}")

# ============================================================
# 2. OZON: page 2 HTML structure dump
# ============================================================
print("\n" + "=" * 60)
print("OZON: page 2 HTML structure")
print("=" * 60)

t0 = time.monotonic()
r = cr.get(
    "https://www.ozon.ru/search/?text=%D0%BD%D0%B0%D1%83%D1%88%D0%BD%D0%B8%D0%BA%D0%B8&from=global&page=2",
    headers={
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.ozon.ru/",
    },
    timeout=20, impersonate="chrome",
)
el = time.monotonic() - t0
print(f"  PAGE 2: HTTP {r.status_code}, {len(r.content)} bytes, {el:.1f}s")
print(f"  FINAL_URL={r.url}")

html = r.text

# Ищем все паттерны ссылок на товары
pats = {
    "href_product_id": r'href=["\'][^"\']*?/product/[^"\']*?(\d{5,})[^"\']*["\']',
    "href_tile": r'class="[^"]*tile[^"]*"[^>]*href="([^"]+)"',
    "data-tile-id": r'data-tile-id="(\d+)"',
    "data-pid": r'data-pid="(\d+)"',
    "aria-label": r'aria-label="([^"]{10,100})"',
    "json_state": r'window\.__STATE__\s*=\s*',
    "widget_state": r'window\.__WIDGET_STATE__\s*=\s*',
    "next_data": r'__NEXT_DATA__',
}
for name, pat in pats.items():
    matches = re.findall(pat, html)
    print(f"  {name}: {len(matches)} matches")
    if matches and len(matches) <= 5:
        for m in matches[:3]:
            print(f"    {m[:80]}")

# Дамп: где начинается контент
print("\n  --- HTML segments ---")
# Ищем <main> или <div id="content">
main_idx = html.find("<main")
content_idx = html.find('id="content"')
print(f"  <main> at pos {main_idx}, id=content at pos {content_idx}")

# Первый tile-блок
tile_idx = html.find("tile")
if tile_idx > 0:
    print(f"  'tile' first at pos {tile_idx}")
    print(f"  CONTEXT: ...{html[tile_idx-50:tile_idx+300]}...")

# JSON-LD
jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
print(f"\n  JSON-LD blocks: {len(jsonld)}")
for i, j in enumerate(jsonld[:2]):
    print(f"    [{i}] {j[:200]}")

# window.__STATE__
state_match = re.search(r'window\.__STATE__\s*=\s*(\{.*?\});\s*</script>', html, re.DOTALL)
if state_match:
    print(f"\n  __STATE__ found, {len(state_match.group(1))} bytes")
    print(f"  SAMPLE: {state_match.group(1)[:300]}")

# Сохраним фрагмент для анализа
frag_path = Path(__file__).parent / "ozon_p2_fragment.html"
frag_path.write_text(html[:20000], encoding="utf-8")
print(f"\n  Saved fragment: {frag_path}")

out["OZON_p2"] = {"status": str(r.status_code), "size": len(r.content), "elapsed": round(el, 1)}

# ============================================================
# 3. ALIEXPRESS global: redirect chain
# ============================================================
print("\n" + "=" * 60)
print("ALIEXPRESS global: redirect check")
print("=" * 60)

# curl_cffi без allow_redirects
t0 = time.monotonic()
try:
    r = cr.get(
        "https://www.aliexpress.com/wholesale?SearchText=%D0%BD%D0%B0%D1%83%D1%88%D0%BD%D0%B8%D0%BA%D0%B8&page=1",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.aliexpress.com/",
        },
        timeout=20, impersonate="chrome",
        allow_redirects=False,
    )
    el = time.monotonic() - t0
    print(f"  NO-REDIRECT: HTTP {r.status_code}, {len(r.content)} bytes, {el:.1f}s")
    loc = r.headers.get("Location", "")
    print(f"  Location: {loc}")
    print(f"  Set-Cookie: {r.headers.get('Set-Cookie', '')[:200]}")
    out["ALI_global_noredir"] = {"status": str(r.status_code), "location": loc[:200]}

    # Теперь с redirect
    r2 = cr.get(
        "https://www.aliexpress.com/wholesale?SearchText=%D0%BD%D0%B0%D1%83%D1%88%D0%BD%D0%B8%D0%BA%D0%B8&page=1",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.aliexpress.com/",
        },
        timeout=20, impersonate="chrome",
        allow_redirects=True,
    )
    el2 = time.monotonic() - t0
    print(f"\n  WITH-REDIRECT: HTTP {r2.status_code}, {len(r2.content)} bytes, {el2:.1f}s")
    print(f"  FINAL_URL: {r2.url}")
    out["ALI_global_redirect"] = {"status": str(r2.status_code), "final_url": r2.url[:200]}
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")
    out["ALI_global"] = {"status": "ERROR", "error": str(e)[:150]}

# ============================================================
# Save
# ============================================================
out_path = Path(__file__).parent / "diag_v2_results.json"
out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n\nSaved: {out_path}")

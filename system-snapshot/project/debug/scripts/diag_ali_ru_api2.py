"""Диагностика v2: поиск рабочего способа получить JSON из AliExpress RU search API."""
import sys, os, time, json, traceback
from urllib.parse import quote

print("=" * 60)
print("DIAG v2: AliExpress RU search API — finding working JSON endpoint")
print("=" * 60)

QUERY = "наушники беспроводные"
ENCODED = quote(QUERY, safe="")

# ─── А. Сохранить HTML для анализа ───
print("\n[A] Saving full HTML response for analysis...")
try:
    from curl_cffi import requests as cffi_req
    url_a = f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={ENCODED}&page=1&pageSize=40&sortType=default"
    resp = cffi_req.get(url_a, impersonate="chrome", timeout=(15, 30))
    out_dir = os.path.join(os.path.dirname(__file__), "data", "collector_output")
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "diag_api2_html.txt")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(resp.text)
    print(f"    Saved HTML ({len(resp.text)} bytes) to {html_path}")
    # Look for clues in HTML
    text = resp.text
    for marker in ["__INIT", "window.", "api", "fetch", "xhr", "ajax", "mtop", "aer-webapi", "search", "redirect", "location"]:
        idx = text.lower().find(marker.lower())
        if idx >= 0:
            snippet = text[max(0,idx-30):idx+80].replace("\n", " ")
            print(f"    Found '{marker}' at {idx}: ...{snippet}...")
except Exception as e:
    print(f"    FAIL: {e}")

# ─── Б. POST вместо GET ───
print("\n[B] Trying POST to search API...")
try:
    resp_b = cffi_req.post(
        f"https://ru.aliexpress.com/aer-webapi/v1/search",
        impersonate="chrome",
        timeout=(15, 30),
        json={"SearchText": QUERY, "page": 1, "pageSize": 40, "sortType": "default"},
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Origin": "https://ru.aliexpress.com",
            "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={ENCODED}",
        },
    )
    print(f"    Status: {resp_b.status_code}, CT: {resp_b.headers.get('content-type','?')}, size: {len(resp_b.text)}")
    print(f"    First 200: {resp_b.text[:200]}")
except Exception as e:
    print(f"    FAIL: {e}")

# ─── В. Сессия: сначала страница поиска → cookies → API ───
print("\n[C] Session-based: visit search page → get cookies → call API...")
try:
    session = cffi_req.Session(impersonate="chrome")
    # Step 1: visit wholesale page
    page_url = f"https://ru.aliexpress.com/wholesale?SearchText={ENCODED}"
    r1 = session.get(page_url, timeout=(15, 30), headers={
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    print(f"    Step1: {r1.status_code}, {len(r1.text)} bytes")
    cookies = session.cookies
    print(f"    Cookies: {len(cookies)} → {[c.name for c in cookies][:10]}")
    time.sleep(2)
    # Step 2: call API with session
    r2 = session.get(
        f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={ENCODED}&page=1&pageSize=40&sortType=default",
        timeout=(15, 30),
        headers={
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": page_url,
        },
    )
    print(f"    Step2: {r2.status_code}, CT: {r2.headers.get('content-type','?')}, size: {len(r2.text)}")
    print(f"    First 200: {r2.text[:200]}")
    if r2.headers.get('content-type','').startswith('application/json'):
        data = r2.json()
        print(f"    JSON OK! Keys: {list(data.keys())[:10]}")
        out = os.path.join(out_dir, "diag_api2_session.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"    Saved: {out}")
except Exception as e:
    print(f"    FAIL: {e}")
    traceback.print_exc()

# ─── Г. Альтернативные URL ───
print("\n[D] Alternative URLs...")
alt_urls = [
    f"https://ru.aliexpress.com/aer-webapi/v2/search?SearchText={ENCODED}&page=1&pageSize=40",
    f"https://www.aliexpress.com/aer-webapi/v1/search?SearchText={ENCODED}&page=1&pageSize=40",
    f"https://ru.aliexpress.com/api/search?SearchText={ENCODED}&page=1&pageSize=40",
    f"https://ru.aliexpress.com/wholesale/api/search?SearchText={ENCODED}&page=1&pageSize=40",
    f"https://ru.aliexpress.com/aer-webapi/v1/search?keyword={ENCODED}&page=1&pageSize=40",
]
for i, u in enumerate(alt_urls):
    try:
        r = cffi_req.get(u, impersonate="chrome", timeout=(10, 20), headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
        ct = r.headers.get('content-type', '?')
        is_json = ct.startswith('application/json')
        print(f"    [{i}] {u[:80]}...")
        print(f"        → {r.status_code}, CT={ct[:40]}, {len(r.text)} bytes, JSON={is_json}")
        if is_json and r.status_code == 200:
            print(f"        *** FOUND JSON ENDPOINT! ***")
            out = os.path.join(out_dir, f"diag_api2_alt{i}.json")
            with open(out, "w", encoding="utf-8") as f:
                f.write(r.text)
            print(f"        Saved: {out}")
            break
    except Exception as e:
        print(f"    [{i}] {u[:60]}... → ERROR: {e}")
    time.sleep(1)

# ─── Д. MTOP API ───
print("\n[E] Trying MTOP API (m.aliexpress.com)...")
try:
    mtop_url = (
        f"https://m.aliexpress.com/fn/search-pc/index.htm"
        f"?SearchText={ENCODED}&page=1&pageSize=40"
    )
    r = cffi_req.get(mtop_url, impersonate="chrome", timeout=(10, 20), headers={
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    print(f"    m.aliexpress.com: {r.status_code}, CT={r.headers.get('content-type','?')[:40]}, {len(r.text)} bytes")
    print(f"    First 200: {r.text[:200]}")
except Exception as e:
    print(f"    FAIL: {e}")

# ─── Е. Проверка: может API на aliexpress.ru (без ru.) ───
print("\n[F] Trying aliexpress.ru (no ru. subdomain)...")
try:
    r = cffi_req.get(
        f"https://aliexpress.ru/aer-webapi/v1/search?SearchText={ENCODED}&page=1&pageSize=40&sortType=default",
        impersonate="chrome", timeout=(10, 20),
        headers={"Accept": "application/json, text/plain, */*", "Accept-Language": "ru-RU,ru;q=0.9"},
    )
    print(f"    aliexpress.ru: {r.status_code}, CT={r.headers.get('content-type','?')[:40]}, {len(r.text)} bytes")
    print(f"    First 200: {r.text[:200]}")
except Exception as e:
    print(f"    FAIL: {e}")

print("\n" + "=" * 60)
print("DIAG v2 DONE")
print("=" * 60)

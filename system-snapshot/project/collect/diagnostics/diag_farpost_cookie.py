"""Диагностика Farpost cookie-flow: verify → cookie → карточка."""
import sys, re, json, time, base64
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).parent))
from r52.collectors.farpost import FarpostCollector

c = FarpostCollector(use_proxies=False)
session = c.session

UA = session.headers.get("User-Agent", "Mozilla/5.0")
TARGET_URL = "https://www.farpost.ru/vladivostok/zapchasti/datchik-kisloroda-lynxauto-art-dm-2350-g16879823067.html"

print("=== STEP 0: Warm-up — GET main page ===")
resp0 = session.get("https://www.farpost.ru/", timeout=(10, 30))
print(f"  STATUS={resp0.status_code} SIZE={len(resp0.text)}B")
print(f"  COOKIES_AFTER_MAIN: {dict(session.cookies)}")
time.sleep(2)

print("\n=== STEP 1: GET target ===")
resp1 = session.get(TARGET_URL, timeout=(10, 30))
print(f"  STATUS={resp1.status_code} SIZE={len(resp1.text)}B")
print(f"  FINAL_URL={resp1.url}")
print(f"  COOKIES: {dict(session.cookies)}")

verify_url = resp1.url
if "/verify" in verify_url:
    print("\n=== STEP 2: Parse verify params ===")
    parts = urlsplit(verify_url)
    qs = parse_qs(parts.query)
    r_val = qs.get("r", [""])[0]
    u_val = qs.get("u", [""])[0]
    f_val = qs.get("f", [""])[0]
    print(f"  r={r_val} f={f_val}")
    print(f"  u={u_val[:120]}")

    # Altcha: формируем cookie как JS
    cookie_data = {
        "t": int(time.time() * 1000),
        "r": r_val,
        "f": f_val,
        "ua": UA,
        "res": "1920x1080",
        "tz": -540,
        "lang": "ru-RU",
    }
    cookie_val = base64.b64encode(json.dumps(cookie_data, separators=(",", ":")).encode()).decode()
    print(f"  Cookie fp_verify={cookie_val[:60]}...")
    session.cookies.set("fp_verify", cookie_val, domain="www.farpost.ru", path="/")
    time.sleep(2)

    print("\n=== STEP 3: GET target with cookie ===")
    resp2 = session.get(TARGET_URL, timeout=(10, 30))
    print(f"  STATUS={resp2.status_code} SIZE={len(resp2.text)}B")
    print(f"  FINAL_URL={resp2.url}")

    html = resp2.text
    title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.I)
    t = title_m.group(1)[:120] if title_m else 'NONE'
    print(f"  TITLE: {t}")
    has_vb = bool(re.search(r'viewbull', html, re.I))
    has_ld = bool(re.search(r'application/ld\+json', html, re.I))
    print(f"  HAS_VIEWBULL: {has_vb}")
    print(f"  HAS_JSONLD: {has_ld}")

    # data-bulletin-price
    bp = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
    print(f"  DATA_BULLETIN_PRICE: {bp}")

    out = Path(__file__).parent / "diag-farpost-html"
    out.mkdir(exist_ok=True)
    (out / "farpost-cookie-test.html").write_text(html, encoding="utf-8")
    print(f"  SAVED: farpost-cookie-test.html ({len(html)}B)")

    if "/verify" in resp2.url:
        print("\n=== STEP 4: Try bulletin redirect ===")
        # Пробуем /bulletin/{id} — может быть проще
        bullet_url = "https://www.farpost.ru/bulletin/16879823067"
        resp3 = session.get(bullet_url, timeout=(10, 30), allow_redirects=True)
        print(f"  BULLETIN URL: {bullet_url}")
        print(f"  STATUS={resp3.status_code} SIZE={len(resp3.text)}B")
        print(f"  FINAL_URL={resp3.url}")
        html3 = resp3.text
        t3 = re.search(r'<title[^>]*>(.*?)</title>', html3, re.DOTALL | re.I)
        print(f"  TITLE: {(t3.group(1)[:120] if t3 else 'NONE')}")
        print(f"  HAS_VIEWBULL: {bool(re.search(r'viewbull', html3, re.I))}")
        print(f"  HAS_JSONLD: {bool(re.search(r'application/ld\+json', html3, re.I))}")
        (out / "farpost-bulletin.html").write_text(html3, encoding="utf-8")
        print(f"  SAVED: farpost-bulletin.html ({len(html3)}B)")

        # Если bulletin тоже verify — пробуем с Accept: application/json
        if "/verify" in resp3.url:
            print("\n=== STEP 5: Try JSON API ===")
            headers_json = dict(session.headers)
            headers_json["Accept"] = "application/json"
            resp4 = session.get(TARGET_URL, headers=headers_json, timeout=(10, 30))
            print(f"  STATUS={resp4.status_code} CT={resp4.headers.get('content-type','?')}")
            print(f"  FINAL_URL={resp4.url}")
            print(f"  BODY[:300]: {resp4.text[:300]}")

elif resp1.status_code == 200 and "viewbull" in resp1.text.lower():
    print("\n=== DIRECT HIT (no verify) ===")
    html = resp1.text
    title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.I)
    t = title_m.group(1)[:120] if title_m else 'NONE'
    print(f"  TITLE: {t}")
    print(f"  HAS_VIEWBULL: True")
    print(f"  HAS_JSONLD: {bool(re.search(r'application/ld\+json', html, re.I))}")
else:
    print("\n=== UNEXPECTED ===")
    print(f"  First 500: {resp1.text[:500]}")

print(f"\nStats: {c.stats}")

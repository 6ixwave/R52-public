"""diag_v120.py — WB images/prices + Farpost Altcha bypass (f=1).

WB:
  - card.json: photo_count=6, has_video=true, но нет URL
  - basket CDN: S800/S800.jpg → 404
  - wbxstatic.ru: DNS fail (ir-1/2/3)
  → Пробуем: нумерованные (1_1.jpg), imt_id, wbxstatic.net, ir-4/5,
    HTML detail.aspx с разными UA, card.wb.ru v1 (отдельно)

Farpost:
  - farpost_v118.html = Altcha challenge page
  - Form: ?u=<path>&f=2 (full challenge)
  - Fallback: ?u=<path>&f=1 ("другой вариант")
  → Пробуем: f=1, bulletin/{id} с redirect, warmup + cookie, mobile UA
"""
import json
import re
import socket
import sys
import time
from urllib.parse import quote

try:
    from curl_cffi import requests as cffi_req
    HAVE_CFFI = True
except ImportError:
    HAVE_CFFI = False
    import requests as cffi_req

UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

NM_ID = 199298871
IMT_ID = 158033827
VOL = NM_ID // 100000  # 1992
PART = NM_ID // 1000    # 199298

FP_FULL_PATH = "/ni/krasnoyarsk/zapchasti/kontraktnyj-dvigatel-1zz-fe-cena-s-navesnym-129606872.html"
FP_NUMERIC = "129606872"

def http_get(url, headers=None, timeout=12, impersonate="chrome"):
    """GET via curl_cffi or requests. Returns (status, body_bytes, content_type)."""
    h = {
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    if headers:
        h.update(headers)
    if HAVE_CFFI:
        try:
            resp = cffi_req.get(url, headers=h, impersonate=impersonate, timeout=timeout)
            return resp.status_code, resp.content, resp.headers.get("Content-Type", "")
        except Exception as e:
            return -1, str(e).encode(), "error"
    else:
        try:
            resp = cffi_req.get(url, headers=h, timeout=timeout)
            return resp.status_code, resp.content, resp.headers.get("Content-Type", "")
        except Exception as e:
            return -1, str(e).encode(), "error"

def main():
    out = []
    out.append("=" * 60)
    out.append("DIAG V120: WB IMAGES/PRICES + FARPOST ALTCHA")
    out.append("=" * 60)

    # ============================================================
    # PART 1: WB IMAGE URL PATTERNS (expanded)
    # ============================================================
    out.append("\n--- PART 1: WB IMAGE PATTERNS (nm=%d, imt=%d) ---" % (NM_ID, IMT_ID))

    basket_host = "basket-13.wbbasket.ru"
    base = f"https://{basket_host}/vol{VOL}/part{PART}/{NM_ID}/images"

    patterns = [
        # Нумерованные (1_1.jpg, 2_2.jpg ...)
        f"{base}/S800/1_1.jpg",
        f"{base}/S800/2_2.jpg",
        f"{base}/S800/1_2.jpg",
        # imt_id
        f"{base}/S800/{IMT_ID}.jpg",
        # 1000x0 numbered
        f"{base}/1000x0/1_1.jpg",
        f"{base}/1000x0/{IMT_ID}.jpg",
        # thumbnail
        f"{base}/thumbnail/1_1.jpg",
        f"{base}/thumbnail/{IMT_ID}.jpg",
        # big
        f"{base}/big/1_1.jpg",
        # 120x120
        f"{base}/120x120/1_1.jpg",
        # S120
        f"{base}/S120/1_1.jpg",
        # S50
        f"{base}/S50/1_1.jpg",
        # No subfolder
        f"{base}/1_1.jpg",
        f"{base}/{IMT_ID}.jpg",
        # wbxstatic.net (not .ru)
        f"https://ir-1.wbxstatic.net/1000x0/{NM_ID}.jpg",
        f"https://ir-2.wbxstatic.net/1000x0/{NM_ID}.jpg",
        f"https://ir-1.wbxstatic.net/S800/{NM_ID}.jpg",
        # ir-4, ir-5
        f"https://ir-4.wbxstatic.ru/1000x0/{NM_ID}.jpg",
        f"https://ir-5.wbxstatic.ru/1000x0/{NM_ID}.jpg",
        # static-basket (alternative CDN)
        f"https://static-basket-{VOL}.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/S800/1_1.jpg",
        # ir.wbxstatic.ru (no number)
        f"https://ir.wbxstatic.ru/1000x0/{NM_ID}.jpg",
        # images.wbbasket.ru
        f"https://images.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/S800/1_1.jpg",
    ]

    for url in patterns:
        status, body, ct = http_get(url, timeout=8)
        size = len(body)
        marker = "OK" if status == 200 and size > 500 else ("xx" if status == 404 else "??")
        out.append(f"  [{marker}] {status:>4d} {size:>7d}B {ct[:30]:30s} {url[-70:]}")
        if status == 200 and size > 500:
            out.append(f"         *** FOUND: {url} ***")
        time.sleep(0.3)

    # ============================================================
    # PART 2: WB HTML PAGE (detail.aspx) with different headers
    # ============================================================
    out.append("\n--- PART 2: WB HTML (detail.aspx) ---")
    wb_html_url = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"

    for label, hdrs in [
        ("desktop", {"User-Agent": UA_DESKTOP, "Accept": "text/html"}),
        ("mobile", {"User-Agent": UA_MOBILE, "Accept": "text/html"}),
        ("no-UA", {"Accept": "text/html"}),
    ]:
        status, body, ct = http_get(wb_html_url, headers=hdrs, timeout=15)
        out.append(f"  [{label}] HTTP {status}, {len(body)}B, {ct[:40]}")
        if status == 200 and len(body) > 5000:
            text = body.decode("utf-8", errors="replace")
            # Look for embedded JSON with images
            img_matches = re.findall(r'(https?://[^"\'<>\s]+(?:wbbasket|wbxstatic|wildberries)[^"\'<>\s]*\.(?:jpg|jpeg|png|webp))', text)
            out.append(f"    Image URLs found: {len(img_matches)}")
            for u in img_matches[:5]:
                out.append(f"      {u[:100]}")
            # Look for price
            price_matches = re.findall(r'(?:data-price|og:price:amount|price[^a-z])["\s:=]+([\d.]+)', text, re.I)
            out.append(f"    Price candidates: {price_matches[:5]}")
            # JSON blobs
            json_blobs = re.findall(r'<script[^>]*>(\{[^<]{100,})</script>', text)
            out.append(f"    JSON blobs: {len(json_blobs)}")
            # Save
            with open("wb_html_v120.html", "wb") as f:
                f.write(body)
            out.append(f"    -> wb_html_v120.html saved ({len(body)}B)")
            break

    # ============================================================
    # PART 3: WB PRICE — card.wb.ru v1 + basket.info
    # ============================================================
    out.append("\n--- PART 3: WB PRICE ENDPOINTS ---")

    price_urls = [
        ("card_v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
        ("card_v1_batch", f"https://card.wb.ru/cards/v1/batch?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
        ("basket_info", f"https://{basket_host}/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"),
        ("web_api_v2", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={NM_ID}"),
        ("search_api", f"https://search.wb.ru/exactmatch/ru/common/v11/search?appType=1&curr=rub&dest=-1257786&nm={NM_ID}"),
    ]

    for label, url in price_urls:
        hdrs = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}
        status, body, ct = http_get(url, headers=hdrs, timeout=12)
        out.append(f"  [{label}] HTTP {status}, {len(body)}B")
        if status == 200 and len(body) > 10:
            try:
                data = json.loads(body)
                # Extract price info
                if "data" in data:
                    items = data["data"].get("items") or data["data"].get("cards") or []
                    if items:
                        p = items[0]
                        out.append(f"    name: {str(p.get('name','?'))[:50]}")
                        out.append(f"    prices: {json.dumps(p.get('prices', p.get('price', {})))[:200]}")
                        out.append(f"    qty: {p.get('qty')}")
                        out.append(f"    images: {str(p.get('imts', p.get('images', [])))[:200]}")
                elif "products" in data:
                    p = data["products"][0] if data["products"] else {}
                    out.append(f"    name: {str(p.get('name','?'))[:50]}")
                    out.append(f"    prices: {json.dumps(p.get('price', {}))[:200]}")
                    out.append(f"    sizes: {len(p.get('sizes', []))}")
            except (json.JSONDecodeError, ValueError):
                out.append(f"    not JSON: {body[:100]}")
        time.sleep(0.5)

    # ============================================================
    # PART 4: FARPOST — Altcha challenge handling
    # ============================================================
    out.append("\n--- PART 4: FARPOST ALTCHA ---")

    # 4a. Warmup
    out.append("  [Warmup] GET https://www.farpost.ru/")
    status, body, ct = http_get("https://www.farpost.ru/", timeout=15)
    out.append(f"    HTTP {status}, {len(body)}B")

    # 4b. Try full path with f=1 fallback
    fp_f1 = f"https://www.farpost.ru?u={quote(FP_FULL_PATH, safe='')}&f=1"
    out.append(f"  [f=1] {fp_f1[:80]}")
    status, body, ct = http_get(fp_f1, timeout=15, impersonate="chrome")
    out.append(f"    HTTP {status}, {len(body)}B, {ct[:40]}")
    if status == 200:
        text = body.decode("utf-8", errors="replace")
        is_altcha = "altcha" in text.lower() or "не робот" in text.lower()
        has_price = "data-bulletin-price" in text or "bulletin-price" in text
        has_jsonld = "application/ld+json" in text
        out.append(f"    Altcha: {is_altcha}, Price: {has_price}, JSON-LD: {has_jsonld}")
        if has_price or has_jsonld:
            out.append("    *** FARPOST f=1 GAVE REAL DATA ***")
            with open("farpost_f1_v120.html", "wb") as f:
                f.write(body)

    # 4c. Try numeric bulletin path (redirect)
    fp_num = f"https://www.farpost.ru/bulletin/{FP_NUMERIC}"
    out.append(f"  [Numeric] {fp_num}")
    status, body, ct = http_get(fp_num, timeout=15)
    out.append(f"    HTTP {status}, {len(body)}B")
    if status in (200, 301, 302):
        text = body.decode("utf-8", errors="replace") if status == 200 else ""
        is_altcha = "altcha" in text.lower() or "не робот" in text.lower()
        out.append(f"    Altcha: {is_altcha}")

    # 4d. Try full path directly (with mobile UA)
    fp_full = f"https://www.farpost.ru{FP_FULL_PATH}"
    out.append(f"  [Full mobile] {fp_full[:80]}")
    status, body, ct = http_get(fp_full, headers={"User-Agent": UA_MOBILE}, timeout=15)
    out.append(f"    HTTP {status}, {len(body)}B")
    if status == 200:
        text = body.decode("utf-8", errors="replace")
        is_altcha = "altcha" in text.lower() or "не робот" in text.lower()
        has_price = "data-bulletin-price" in text
        has_jsonld = "application/ld+json" in text
        out.append(f"    Altcha: {is_altcha}, Price: {has_price}, JSON-LD: {has_jsonld}")
        if has_price or has_jsonld:
            out.append("    *** FARPOST mobile GAVE REAL DATA ***")
            with open("farpost_mobile_v120.html", "wb") as f:
                f.write(body)

    # 4e. Try with explicit cookie jar (two-step: warmup then page)
    out.append("  [Two-step cookie] warmup + full path")
    if HAVE_CFFI:
        sess = cffi_req.Session(impersonate="chrome")
        r1 = sess.get("https://www.farpost.ru/", timeout=15)
        out.append(f"    Warmup: {r1.status_code}, cookies={list(sess.cookies.keys())}")
        time.sleep(2)
        r2 = sess.get(fp_full, timeout=15)
        out.append(f"    Page: {r2.status_code}, {len(r2.content)}B")
        if r2.status_code == 200:
            text = r2.text
            is_altcha = "altcha" in text.lower() or "не робот" in text.lower()
            has_price = "data-bulletin-price" in text
            out.append(f"    Altcha: {is_altcha}, Price: {has_price}")
            if has_price:
                with open("farpost_2step_v120.html", "wb") as f:
                    f.write(r2.content)

    # 4f. Try /bulletin/ with ?f=1
    fp_b_f1 = f"https://www.farpost.ru/bulletin/{FP_NUMERIC}?f=1"
    out.append(f"  [Bulletin f=1] {fp_b_f1}")
    status, body, ct = http_get(fp_b_f1, timeout=15)
    out.append(f"    HTTP {status}, {len(body)}B")

    # ============================================================
    # PART 5: WB — try images from a different basket host
    # ============================================================
    out.append("\n--- PART 5: WB IMAGES — alternate basket hosts ---")
    # Maybe the images are on a different basket-N
    for n in [1, 5, 10, 20, 30, 40, 50, 59]:
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}/vol{VOL}/part{PART}/{NM_ID}/images/S800/1_1.jpg"
        status, body, ct = http_get(url, timeout=8)
        if status == 200 and len(body) > 500:
            out.append(f"  [OK] {host}: {len(body)}B *** FOUND ***")
        elif status != 404:
            out.append(f"  [{status}] {host}: {len(body)}B")
        time.sleep(0.2)

    out.append("\n" + "=" * 60)
    out.append("DIAG V120 DONE")
    out.append("=" * 60)

    # Write log
    log_text = "\n".join(out)
    with open("diag_v120_result.log", "w", encoding="utf-8") as f:
        f.write(log_text)
    print(log_text)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"FATAL: {e}\n{traceback.format_exc()}")
        sys.exit(1)
    sys.exit(0)

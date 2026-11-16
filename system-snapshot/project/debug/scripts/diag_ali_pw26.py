"""Диагностика AliExpress pw26: глубокий разбор HTML + прямой MTOP API.

Шаг 1: fetch HTML, сохранить, найти ВСЕ script-теги, поиск price/title/sku
Шаг 2: прямой POST/GET к MTOP API (acs.aliexpress.com) с productId
Шаг 3: если MTOP отдал данные — парсим
"""
import json
import re
import time
import sys
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

def step1_fetch_and_analyze():
    """Fetch HTML и глубоко разобрать."""
    from curl_cffi import requests as cffi_req
    url = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.aliexpress.com/",
    }
    t0 = time.monotonic()
    resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 45), headers=headers)
    elapsed = time.monotonic() - t0
    html = resp.text
    print(f"[1] HTTP {resp.status_code}, {len(html)} bytes, {elapsed:.1f}s")
    print(f"    URL: {resp.url[:120]}")

    # Сохранить полный HTML для ручного анализа
    html_path = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw26_page.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"    Saved HTML: {html_path}")

    result = {
        "http_status": resp.status_code,
        "html_len": len(html),
        "elapsed": round(elapsed, 2),
        "final_url": resp.url,
    }

    # Все script-теги: первый и последний 150 символов
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    result["script_count"] = len(scripts)
    script_summary = []
    for i, s in enumerate(scripts):
        s_stripped = s.strip()
        if len(s_stripped) < 10:
            continue
        snippet = s_stripped[:200]
        # Ищем ключевые слова
        keywords = []
        for kw in ("price", "Price", "title", "Title", "sku", "SKU", "product", "Product",
                   "INIT_DATA", "runParams", "pdp", "mtop", "GLOBAL_DATA", "seoInfo"):
            if kw in s_stripped:
                keywords.append(kw)
        script_summary.append({
            "idx": i,
            "len": len(s_stripped),
            "keywords": keywords,
            "head": snippet[:150],
        })
    result["scripts"] = script_summary
    print(f"    Scripts: {len(scripts)}, with keywords: {sum(1 for s in script_summary if s['keywords'])}")
    for s in script_summary:
        if s["keywords"]:
            print(f"      [{s['idx']}] len={s['len']} kw={s['keywords']} head={s['head'][:80]}")

    # Поиск price-паттернов в всём HTML
    price_hits = []
    for m in re.finditer(r'"(?:price|Price|salePrice|minPrice|amount|actvPrice|formattedPrice|displayPrice|priceString)"\s*:\s*("?[^,}\]]{1,80}"?)', html):
        price_hits.append(m.group(0)[:120])
    result["price_hits"] = price_hits[:15]
    print(f"    Price key hits: {len(price_hits)}")
    for h in price_hits[:5]:
        print(f"      {h}")

    # Поиск title-паттернов
    title_hits = re.findall(r'"(?:title|subject|productTitle|seoTitle|name)"\s*:\s*"([^"]{10,200})"', html)
    result["title_hits"] = title_hits[:10]
    print(f"    Title hits: {len(title_hits)}")
    for t in title_hits[:3]:
        print(f"      {t[:80]}")

    # data- attributes
    data_attrs = re.findall(r'data-(?:price|product|sku|item)[^=]*=["\']([^"\']{1,100})["\']', html, re.I)
    result["data_attrs"] = data_attrs[:10]

    # window.XXX = ...
    window_vars = re.findall(r'window\.(\w+)\s*=', html)
    result["window_vars"] = list(dict.fromkeys(window_vars))[:30]
    print(f"    window vars: {result['window_vars']}")

    # meta tags
    metas = re.findall(r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']{0,200})["\']', html, re.I)
    result["metas"] = {k: v[:150] for k, v in metas if k}
    print(f"    Meta tags: {len(metas)}")

    return result, html


def step2_mtop_api():
    """Прямой вызов MTOP API."""
    from curl_cffi import requests as cffi_req

    # MTOP endpoint для PDP
    mtop_urls = [
        # H5 API
        f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/?jsv=2.7.2&appKey=24568372&t={int(time.time()*1000)}&sign=[REDACTED_AUTH_SECRET]&api=mtop.aliexpress.pdp.pc.query&v=1.0&type=originaljson&dataType=json&timeout=20000&data=%7B%22productId%22%3A%22{PRODUCT_ID}%22%7D",
        # Альтернативный
        f"https://acs.aliexpress.com/h5/mtop.aliexpress.detail.pc.query/1.0/?jsv=2.7.2&appKey=24568372&t={int(time.time()*1000)}&api=mtop.aliexpress.detail.pc.query&v=1.0&type=originaljson&dataType=json&timeout=20000&data=%7B%22productId%22%3A%22{PRODUCT_ID}%22%7D",
    ]

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.aliexpress.com/item/{PRODUCT_ID}.html",
        "Origin": "https://www.aliexpress.com",
    }

    results = []
    for i, murl in enumerate(mtop_urls):
        print(f"\n[2.{i+1}] MTOP: {murl[:100]}...")
        try:
            resp = cffi_req.get(murl, impersonate="chrome", timeout=(10, 30), headers=headers)
            body = resp.text
            print(f"    HTTP {resp.status_code}, {len(body)} bytes")
            print(f"    Body head: {body[:300]}")

            r = {
                "url": murl[:200],
                "status": resp.status_code,
                "len": len(body),
                "head": body[:500],
            }

            # Парсинг JSONP или JSON
            clean = body.strip()
            m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', clean, re.DOTALL)
            if m:
                clean = m.group(1)
            try:
                data = json.loads(clean)
                r["parsed"] = True
                r["ret"] = data.get("ret", [])
                r["data_keys"] = list(data.get("data", {}).keys())[:20] if isinstance(data.get("data"), dict) else "not_dict"
                # Глубже
                d = data.get("data", {})
                if isinstance(d, dict):
                    for k in ("result", "pdpData", "productData", "itemData"):
                        if k in d and isinstance(d[k], dict):
                            r[f"data_{k}_keys"] = list(d[k].keys())[:30]
                            # Price
                            pd = d[k]
                            pi = pd.get("targetSkuPriceInfo", {})
                            if pi:
                                r["price_info"] = {kk: str(vv)[:100] for kk, vv in pi.items()}
                            ti = pd.get("seoInfo", {})
                            if ti:
                                r["seo_info"] = {kk: str(vv)[:100] for kk, vv in ti.items() if isinstance(vv, str)}
                            # Title
                            for tk in ("title", "subject", "name", "productTitle"):
                                if pd.get(tk):
                                    r["title_from_api"] = str(pd[tk])[:200]
                                    break
                            # Images
                            hipc = pd.get("HEADER_IMAGE_PC", {})
                            if isinstance(hipc, dict):
                                imgs = hipc.get("currentSkuImages", [])
                                r["images_count"] = len(imgs) if isinstance(imgs, list) else 0
                                r["images_sample"] = imgs[:3] if isinstance(imgs, list) else []
                            break
                results.append(r)
                print(f"    Parsed! ret={r['ret'][:2]}, keys={r.get('data_keys', [])[:10]}")
                if r.get("title_from_api"):
                    print(f"    TITLE: {r['title_from_api'][:80]}")
                if r.get("price_info"):
                    print(f"    PRICE: {r['price_info']}")
            except (json.JSONDecodeError, ValueError) as e:
                r["parsed"] = False
                r["parse_error"] = str(e)
                results.append(r)
                print(f"    Parse error: {e}")
        except Exception as e:
            results.append({"url": murl[:200], "error": str(e)})
            print(f"    Error: {e}")
        time.sleep(2)

    return results


def step3_mtop_with_cookies(html: str):
    """Попытка MTOP с cookies из HTML (x_csrf токен)."""
    from curl_cffi import requests as cffi_req

    # Ищем csrf token
    csrf = re.search(r'x_csrf["\s:=]+["\']?([\w-]+)', html)
    token = csrf.group(1) if csrf else None
    print(f"\n[3] CSRF token: {token}")

    if not token:
        # Ищем в meta или script
        csrf2 = re.search(r'(?:csrf|CSRF|_token)["\s:=]+["\']([\w-]+)', html)
        token = csrf2.group(1) if csrf2 else None
        print(f"    Alt CSRF: {token}")

    if not token:
        print("    No CSRF found, skipping")
        return {}

    data_payload = json.dumps({"productId": PRODUCT_ID, "skuId": ""})
    mtop_url = (
        f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/"
        f"?jsv=2.7.2&appKey=24568372&t={int(time.time()*1000)}"
        f"&api=mtop.aliexpress.pdp.pc.query&v=1.0"
        f"&type=originaljson&dataType=json&timeout=20000"
        f"&data={data_payload}"
    )
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"https://www.aliexpress.com/item/{PRODUCT_ID}.html",
        "Origin": "https://www.aliexpress.com",
        "x-csrf-token": token,
    }
    try:
        resp = cffi_req.post(mtop_url, impersonate="chrome", timeout=(10, 30), headers=headers, data="data=" + data_payload)
        body = resp.text
        print(f"    POST HTTP {resp.status_code}, {len(body)} bytes")
        print(f"    Head: {body[:300]}")
        return {"status": resp.status_code, "len": len(body), "head": body[:500], "csrf": token}
    except Exception as e:
        return {"error": str(e)}


def main():
    out = {"timestamp": datetime.now(timezone.utc).isoformat(), "script": "diag_ali_pw26"}

    # Step 1
    r1, html = step1_fetch_and_analyze()
    out["step1"] = r1

    time.sleep(2)

    # Step 2
    r2 = step2_mtop_api()
    out["step2"] = r2

    time.sleep(2)

    # Step 3
    r3 = step3_mtop_with_cookies(html)
    out["step3"] = r3

    # Save
    out_path = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw26_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n\nSaved: {out_path} ({len(json.dumps(out))} bytes)")


if __name__ == "__main__":
    main()

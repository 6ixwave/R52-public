"""Фокусный тест: WB catalog.wb.ru + OZON page-2 fallback."""
import sys, time, json, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curl_cffi import requests as cr

results = {}

# === WB: catalog.wb.ru ===
print("=== WB: catalog.wb.ru ===")
t0 = time.monotonic()
try:
    r = cr.get(
        "https://catalog.wb.ru/exactmatch/ru/common/v4/search"
        "?query=%D0%BD%D0%B0%D1%83%D1%88%D0%BD%D0%B8%D0%BA%D0%B8"
        "&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&page=1",
        headers={"Accept": "application/json", "Accept-Language": "ru-RU,ru;q=0.9"},
        timeout=15, impersonate="chrome",
    )
    el = time.monotonic() - t0
    print(f"  STATUS={r.status_code} SIZE={len(r.content)} ELAPSED={el:.1f}s")
    if r.status_code == 200:
        d = r.json()
        prods = d.get("products", [])
        print(f"  PRODUCTS={len(prods)}")
        for p in prods[:3]:
            print(f"    id={p.get('id')} name={str(p.get('name',''))[:50]}")
        results["WB"] = {"status": "OK", "count": len(prods), "elapsed": round(el,1)}
    else:
        print(f"  BODY[:300]={r.text[:300]}")
        results["WB"] = {"status": f"HTTP_{r.status_code}", "count": 0, "elapsed": round(el,1)}
except Exception as e:
    el = time.monotonic() - t0
    print(f"  ERROR: {type(e).__name__}: {e}")
    results["WB"] = {"status": "ERROR", "error": str(e)[:150], "elapsed": round(el,1)}

print()

# === OZON: page 1 then page 2 ===
print("=== OZON: page1 → page2 fallback ===")
for pg in [1, 2]:
    t0 = time.monotonic()
    try:
        r = cr.get(
            f"https://www.ozon.ru/search/?text=%D0%BD%D0%B0%D1%83%D1%88%D0%BD%D0%B8%D0%BA%D0%B8&from=global&page={pg}",
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": "https://www.ozon.ru/",
            },
            timeout=20, impersonate="chrome",
        )
        el = time.monotonic() - t0
        print(f"  PAGE {pg}: STATUS={r.status_code} SIZE={len(r.content)} ELAPSED={el:.1f}s")
        print(f"  FINAL_URL={r.url}")
        if r.status_code == 200:
            tiles = re.findall(r'href=["\'](?:/product/|https://www\.ozon\.ru/product/)[^"\']*?(\d+)', r.text)
            print(f"  PRODUCT_LINKS={len(tiles)} unique={len(set(tiles))}")
            results[f"OZON_p{pg}"] = {"status": "OK", "links": len(tiles), "unique": len(set(tiles)), "elapsed": round(el,1)}
            break
        elif r.status_code in (403, 429):
            print(f"  → trying next page")
            results[f"OZON_p{pg}"] = {"status": f"HTTP_{r.status_code}", "elapsed": round(el,1)}
            time.sleep(3)
        else:
            results[f"OZON_p{pg}"] = {"status": f"HTTP_{r.status_code}", "elapsed": round(el,1)}
            break
    except Exception as e:
        el = time.monotonic() - t0
        print(f"  PAGE {pg} ERROR: {type(e).__name__}: {e}")
        results[f"OZON_p{pg}"] = {"status": "ERROR", "error": str(e)[:150], "elapsed": round(el,1)}
        break

print()
print("=== СВОДКА ===")
for k, v in results.items():
    print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")

out = Path(__file__).parent / "test_wb_ozon_v2_results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")

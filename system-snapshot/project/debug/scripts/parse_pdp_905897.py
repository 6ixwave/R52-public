#!/usr/bin/env python
"""Parse mtop_pw62_pdp_905897.json: find title, price, SKU, delivery, images."""
import json, re, sys

RAW = "mtop_pw62_pdp_905897.json"
OUT = "parse_pdp_905897_out.json"

with open(RAW, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

# Strip JSONP wrapper
m = re.search(r'mtopjsonp\d+\(\s*(\{.*\})\s*\)', text, re.DOTALL)
if not m:
    m = re.search(r'\(\s*(\{.*\})\s*\)', text, re.DOTALL)
if not m:
    m = re.search(r'(\{.*\})', text, re.DOTALL)

data = json.loads(m.group(1))
print(f"Top keys: {list(data.keys())}")
print(f"api: {data.get('api')}")
print(f"ret: {data.get('ret')}")

d = data.get("data", {})
print(f"\ndata keys: {list(d.keys())}")

res = d.get("result", d)
print(f"\nresult keys ({len(res)}): {list(res.keys())}")

out = {"top": list(data.keys()), "ret": data.get("ret"), "result_keys": list(res.keys())}

# ── Title search ──────────────────────────────────────────────────────
title_found = []
def find_title(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k.lower() in ("title", "subject", "producttitle", "name", "productname") and isinstance(v, str) and len(v) > 15:
                title_found.append({"path": p, "val": v[:200]})
            find_title(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            find_title(v, f"{path}[{i}]")

find_title(res)
out["titles"] = title_found[:10]
print(f"\n=== TITLES ({len(title_found)}) ===")
for t in title_found[:8]:
    print(f"  {t['path']}: {t['val'][:100]}")

# ── Price search ──────────────────────────────────────────────────────
price_found = []
def find_price(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            kl = k.lower()
            if any(x in kl for x in ("price", "amount", "currency", "saleprice", "originalprice", "discountprice")):
                if isinstance(v, (str, int, float)) and v:
                    price_found.append({"path": p, "val": str(v)[:100]})
                elif isinstance(v, dict):
                    for k2, v2 in v.items():
                        if isinstance(v2, (str, int, float)) and v2:
                            price_found.append({"path": f"{p}.{k2}", "val": str(v2)[:100]})
            find_price(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:30]):
            find_price(v, f"{path}[{i}]")

find_price(res)
out["prices"] = price_found[:20]
print(f"\n=== PRICES ({len(price_found)}) ===")
for p in price_found[:15]:
    print(f"  {p['path']}: {p['val']}")

# ── SKU / variants ────────────────────────────────────────────────────
sku_info = {}
for k in ("SKUModule", "skuModule", "sku", "SKU", "skuInfo", "propertyList", "skuPaths"):
    if k in res:
        v = res[k]
        if isinstance(v, dict):
            sku_info[k] = {"keys": list(v.keys())[:20]}
            for sk in ("skuPaths", "skuPathList", "variants", "propertyList", "attributes", "skuPropertyList"):
                if sk in v:
                    sku_info[k][sk] = json.dumps(v[sk], ensure_ascii=False)[:3000]
        elif isinstance(v, list):
            sku_info[k] = {"len": len(v), "sample": json.dumps(v[:3], ensure_ascii=False)[:2000]}
out["sku"] = sku_info
print(f"\n=== SKU ===")
for k, v in sku_info.items():
    print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:300]}")

# ── Delivery / shipping ───────────────────────────────────────────────
delivery = {}
for k in ("deliveryOptionPanelDisplayList", "delivery", "shipping", "logistics", "freight", "deliveryInfo", "shippingInfo", "DeliveryModule"):
    if k in res:
        delivery[k] = json.dumps(res[k], ensure_ascii=False)[:2000]
out["delivery"] = delivery
print(f"\n=== DELIVERY ===")
for k, v in delivery.items():
    print(f"  {k}: {v[:200]}")

# ── Images ────────────────────────────────────────────────────────────
img_count = 0
img_sources = []
def count_imgs(obj, path=""):
    global img_count
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k.lower() in ("imglist", "imagepathlist", "currentskuimages", "mainimages", "image", "images", "gallery", "imgurl", "imageurl"):
                if isinstance(v, list):
                    img_sources.append({"path": p, "count": len(v)})
                    img_count += len(v)
                elif isinstance(v, str) and v.startswith("http"):
                    img_sources.append({"path": p, "count": 1, "url": v[:80]})
                    img_count += 1
            count_imgs(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:30]):
            count_imgs(v, f"{path}[{i}]")

count_imgs(res)
out["images"] = {"total_found": img_count, "sources": img_sources[:15]}
print(f"\n=== IMAGES: {img_count} total ===")
for s in img_sources[:10]:
    print(f"  {s['path']}: {s['count']}")

# ── Category ──────────────────────────────────────────────────────────
cat = {}
for k in ("categoryPath", "category1", "category3", "categoryId", "categoryName", "breadcrumb"):
    if k in res:
        cat[k] = str(res[k])[:300]
out["category"] = cat
print(f"\n=== CATEGORY ===")
for k, v in cat.items():
    print(f"  {k}: {v[:150]}")

# ── Product ID ────────────────────────────────────────────────────────
pid = None
for k in ("productId", "itemId", "id", "product_id"):
    if k in res:
        pid = str(res[k])
        break
out["productId"] = pid
print(f"\n=== PRODUCT ID: {pid} ===")

# ── GLOBAL_DATA / bigBossBan ──────────────────────────────────────────
gd = res.get("GLOBAL_DATA", {})
if isinstance(gd, dict):
    gdata = gd.get("globalData", {})
    out["global"] = {
        "bigBossBan": gdata.get("bigBossBan"),
        "errorCode": gdata.get("errorCode"),
        "bigBossBanTip": gdata.get("bigBossBanTip"),
        "gd_keys": list(gd.keys())[:15],
        "gdata_keys": list(gdata.keys())[:20] if isinstance(gdata, dict) else type(gdata).__name__,
    }
    print(f"\n=== GLOBAL ===")
    print(f"  bigBossBan: {gdata.get('bigBossBan')}")
    print(f"  errorCode: {gdata.get('errorCode')}")
    print(f"  gd_keys: {list(gd.keys())[:10]}")

# ── JSON-LD from result ───────────────────────────────────────────────
jsonld = res.get("JSONLD", res.get("jsonLd", res.get("JSON_LD", None)))
if jsonld:
    out["jsonld"] = json.dumps(jsonld, ensure_ascii=False)[:3000]
    print(f"\n=== JSON-LD ===")
    print(json.dumps(jsonld, ensure_ascii=False)[:500])

# ── Full result keys with types ───────────────────────────────────────
key_types = {}
for k, v in res.items():
    if isinstance(v, dict):
        key_types[k] = f"dict({len(v)})"
    elif isinstance(v, list):
        key_types[k] = f"list({len(v)})"
    elif isinstance(v, str):
        key_types[k] = f"str({len(v)})"
    else:
        key_types[k] = type(v).__name__
out["key_types"] = key_types
print(f"\n=== KEY TYPES ({len(key_types)}) ===")
for k, t in sorted(key_types.items()):
    print(f"  {k}: {t}")

# ── Save ──────────────────────────────────────────────────────────────
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print(f"\nSaved: {OUT}")

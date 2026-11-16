"""Целенаправленный разбор: PRODUCT_TITLE, PRICE_BANNER.targetSkuBanner, PRICE, GLOBAL_DATA, PC_RATING, DESC."""
import json, re, os

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mtop_pw62_pdp_905897.json")
with open(path, "r", encoding="utf-8") as f:
    body = f.read().strip()

m = re.match(r'mtopjsonp\d*\((.*)\)', body, re.DOTALL)
if m:
    body = m.group(1)

d = json.loads(body)
result = d.get("data", {}).get("result", d.get("data", {}))

# 1. PRODUCT_TITLE
print("=== PRODUCT_TITLE ===")
pt = result.get("PRODUCT_TITLE", {})
print(json.dumps(pt, ensure_ascii=False, indent=2)[:600])

# 2. PRICE_BANNER → targetSkuBanner + skuBannerMap
print("\n=== PRICE_BANNER.targetSkuBanner ===")
pb = result.get("PRICE_BANNER", {})
tsb = pb.get("targetSkuBanner", {})
if isinstance(tsb, dict):
    print("keys:", list(tsb.keys()))
    print(json.dumps(tsb, ensure_ascii=False, indent=2)[:1200])
else:
    print("type:", type(tsb), str(tsb)[:300])

sbm = pb.get("skuBannerMap", {})
print("\nskuBannerMap keys:", list(sbm.keys())[:5])
for k, v in list(sbm.items())[:2]:
    print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:300]}")

# 3. PRICE module
print("\n=== PRICE module ===")
pr = result.get("PRICE", {})
print("keys:", list(pr.keys()))
for k in pr:
    v = pr[k]
    sv = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    print(f"  {k} = {sv[:200]}")

# 4. GLOBAL_DATA.globalData
print("\n=== GLOBAL_DATA.globalData ===")
gd = result.get("GLOBAL_DATA", {})
gdd = gd.get("globalData", {})
print("keys:", list(gdd.keys())[:25])
for k in ("subject", "categoryPath", "category1", "category2", "category3",
          "productId", "image", "sellerId", "regionCountryName", "regionCityName"):
    if k in gdd:
        print(f"  {k} = {str(gdd[k])[:150]}")

# 5. PC_RATING
print("\n=== PC_RATING ===")
pr2 = result.get("PC_RATING", {})
print(json.dumps(pr2, ensure_ascii=False, indent=2)[:600])

# 6. DESC
print("\n=== DESC ===")
desc = result.get("DESC", {})
print("keys:", list(desc.keys()))
for k in ("pcDescUrl", "nativeDescUrl", "msiteDescUrl", "sellingPointInfo", "instructionInfo"):
    if k in desc:
        print(f"  {k} = {str(desc[k])[:200]}")

# 7. Regex price hunt in PRICE_BANNER
print("\n=== Price regex in PRICE_BANNER ===")
pbs = json.dumps(pb, ensure_ascii=False)
for pat in (r'"(salePrice\w*|originalPrice\w*|minPrice\w*|displayPrice\w*|formattedPrice\w*|priceLocal\w*)"\s*:\s*("?[^,}]{1,80}"?)'):
    for mm in re.finditer(pat, pbs):
        print(f"  {mm.group(1)} = {mm.group(2)}")

# 8. Regex price hunt in PRICE module
print("\n=== Price regex in PRICE ===")
prs = json.dumps(pr, ensure_ascii=False)
for pat in (r'"(salePrice\w*|originalPrice\w*|minPrice\w*|displayPrice\w*|formattedPrice\w*|priceLocal\w*|currency\w*)"\s*:\s*("?[^,}]{1,80}"?)'):
    for mm in re.finditer(pat, prs):
        print(f"  {mm.group(1)} = {mm.group(2)}")

# 9. Search for "148" or "197" (known prices) across whole result
print("\n=== Search '148' / '197' in result ===")
full = json.dumps(result, ensure_ascii=False)
for pat in (r'[^,}]{0,40}148\.\d+[^,}]{0,20}', r'[^,}]{0,40}197\.\d+[^,}]{0,20}'):
    hits = re.findall(pat, full)
    for h in hits[:6]:
        print(f"  {h.strip()[:100]}")

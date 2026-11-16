"""Разбирает структуру MTOP JSON: ключи верхнего уровня, поиск title/price."""
import json, re, os

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mtop_pw62_pdp_905897.json")
with open(path, "r", encoding="utf-8") as f:
    body = f.read().strip()

# Обёртка mtopjsonp
m = re.match(r'mtopjsonp\d*\((.*)\)', body, re.DOTALL)
if m:
    body = m.group(1)

d = json.loads(body)
result = d.get("data", {}).get("result", d.get("data", {}))

print("=== TOP-LEVEL KEYS of result ===")
for k in sorted(result.keys()):
    v = result[k]
    t = type(v).__name__
    if isinstance(v, dict):
        sub = list(v.keys())[:8]
        print(f"  {k:30s} dict({len(v)})  keys[:8]={sub}")
    elif isinstance(v, list):
        print(f"  {k:30s} list({len(v)})")
    else:
        sv = str(v)[:80]
        print(f"  {k:30s} {t}  {sv}")

print("\n=== SEARCH: subject / title / productTitle ===")
ds = json.dumps(result, ensure_ascii=False)
for pat in (r'"subject"\s*:\s*"([^"]{10,300})"',
            r'"title"\s*:\s*"([^"]{10,300})"',
            r'"productTitle"\s*:\s*"([^"]{10,300})"',
            r'"seoTitle"\s*:\s*"([^"]{10,300})"'):
    for mm in re.finditer(pat, ds):
        print(f"  [{pat[:30]}] -> {mm.group(1)[:120]}")

print("\n=== SEARCH: PRICE_BANNER module ===")
pb = result.get("PRICE_BANNER", {})
if pb:
    print(f"  keys: {list(pb.keys())}")
    tspi = pb.get("targetSkuPriceInfo")
    if tspi:
        print(f"  targetSkuPriceInfo keys: {list(tspi.keys())[:15]}")
        for k in ("salePriceLocal","salePriceString","originalPrice","monthlyLowestPrice","currency","skuIdStr"):
            if k in tspi:
                print(f"    {k} = {str(tspi[k])[:100]}")
    else:
        print("  targetSkuPriceInfo: MISSING")
        # поиск price внутри PRICE_BANNER
        pbs = json.dumps(pb, ensure_ascii=False)
        for pat in (r'"(salePrice\w*|originalPrice\w*|minPrice\w*|price\w*)"\s*:\s*("?[^,}]{1,60}"?)'):
            for mm in re.finditer(pat, pbs):
                print(f"    found: {mm.group(1)} = {mm.group(2)}")
else:
    print("  PRICE_BANNER: MISSING")
    # flat
    for k in ("targetSkuPriceInfo","skuPriceInfoMap","priceInfo"):
        if k in result:
            print(f"  flat {k}: {str(result[k])[:150]}")

print("\n=== SEARCH: skuPriceInfoMap ===")
spim = result.get("skuPriceInfoMap") or pb.get("skuPriceInfoMap") if pb else None
if spim:
    for sid, sv in list(spim.items())[:4]:
        print(f"  SKU {sid}: {json.dumps(sv, ensure_ascii=False)[:150]}")

print("\n=== SEARCH: GLOBAL_DATA / categoryPath ===")
gd = result.get("GLOBAL_DATA", {})
if gd:
    for k in ("subject","title","categoryPath","category1","category3","productId"):
        if k in gd:
            print(f"  GLOBAL_DATA.{k} = {str(gd[k])[:120]}")
else:
    print("  GLOBAL_DATA: MISSING")
    for k in ("categoryPath","category1","category3","subject"):
        if k in result:
            print(f"  flat {k} = {str(result[k])[:120]}")

print("\n=== SEARCH: PC_RATING ===")
pr = result.get("PC_RATING", {})
if pr:
    print(f"  keys: {list(pr.keys())[:10]}")
    for k in ("rating","score","averageScore","totalValidNum","starRating"):
        if k in pr:
            print(f"  {k} = {pr[k]}")
else:
    print("  PC_RATING: MISSING")

print("\n=== SEARCH: DESC module ===")
desc = result.get("DESC", {})
if desc:
    print(f"  keys: {list(desc.keys())[:10]}")
    for k in ("description","content","detailDescription"):
        if k in desc:
            print(f"  {k} = {str(desc[k])[:150]}")
else:
    print("  DESC: MISSING")

print("\n=== SEARCH: HEADER_IMAGE_PC ===")
hipc = result.get("HEADER_IMAGE_PC", {})
if hipc:
    print(f"  keys: {list(hipc.keys())}")
    il = hipc.get("imgList", [])
    print(f"  imgList: {len(il)} items, first={str(il[0])[:80] if il else 'N/A'}")

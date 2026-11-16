"""Диагностика: какие модули и ключи реально есть в mtop_pw62_pdp_905897.json"""
import json, os, sys

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mtop_pw62_pdp_905897.json")
with open(path, "r", encoding="utf-8") as f:
    raw = f.read().strip()

# Обёртка mtopjsonp1(...)
if raw.startswith("mtopjsonp"):
    raw = raw[raw.index("(") + 1 : raw.rindex(")")]

d = json.loads(raw)
print("=== TOP-LEVEL KEYS ===")
print(list(d.keys()))

ret = d.get("ret", [])
print(f"\nret = {ret}")

data = d.get("data", {})
result = data.get("result", data)

print(f"\n=== RESULT MODULES ({len(result)} keys) ===")
for k in sorted(result.keys()):
    v = result[k]
    t = type(v).__name__
    if isinstance(v, dict):
        sub_keys = list(v.keys())[:15]
        print(f"  {k} (dict, {len(v)} keys): {sub_keys}")
    elif isinstance(v, list):
        print(f"  {k} (list, len={len(v)})")
    elif isinstance(v, str):
        print(f"  {k} (str, len={len(v)}): {v[:80]}")
    else:
        print(f"  {k} ({t}): {str(v)[:80]}")

# ── Ищем title ──
print("\n=== TITLE HUNT ===")
candidates = []
def hunt_title(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k.lower() in ("title", "subject", "text", "seo_title", "product_title", "name"):
                if isinstance(v, str) and len(v) > 10:
                    candidates.append((p, v[:120]))
            hunt_title(v, p)
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:5]):
            hunt_title(item, f"{path}[{i}]")

hunt_title(result)
for p, v in candidates[:20]:
    print(f"  {p}: {v}")

# ── Ищем price ──
print("\n=== PRICE HUNT ===")
price_hits = []
def hunt_price(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if any(x in k.lower() for x in ("price", "amount", "currency", "cost", "sale", "original")):
                if isinstance(v, (str, int, float)) and v:
                    price_hits.append((p, str(v)[:100]))
                elif isinstance(v, dict):
                    price_hits.append((p, f"dict keys={list(v.keys())[:10]}"))
            hunt_price(v, p)
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:3]):
            hunt_price(item, f"{path}[{i}]")

hunt_price(result)
for p, v in price_hits[:30]:
    print(f"  {p}: {v}")

# ── SHIPPING ──
print("\n=== SHIPPING MODULE ===")
ship = result.get("SHIPPING") or result.get("shipping") or {}
if isinstance(ship, dict):
    print(json.dumps(ship, ensure_ascii=False, indent=2)[:600])
else:
    print(f"  type={type(ship).__name__}: {str(ship)[:200]}")

# ── SKU ──
print("\n=== SKU MODULE ===")
sku = result.get("SKU") or result.get("sku") or {}
if isinstance(sku, dict):
    print(f"  keys: {list(sku.keys())}")
    sp = sku.get("skuPaths", [])
    print(f"  skuPaths len={len(sp)}")
    if sp:
        print(f"  first: {json.dumps(sp[0], ensure_ascii=False)[:200]}")

# ── GLOBAL_DATA / seoInfo ──
print("\n=== GLOBAL_DATA / SEO ===")
gd = result.get("GLOBAL_DATA") or {}
if isinstance(gd, dict):
    for k in ("subject", "title", "categoryPath", "category1", "category3", "seoTitle"):
        if k in gd:
            print(f"  gd.{k} = {str(gd[k])[:120]}")
seo = result.get("seoInfo") or {}
if isinstance(seo, dict):
    print(f"  seoInfo keys: {list(seo.keys())}")
    if "title" in seo:
        print(f"  seoInfo.title = {seo['title'][:120]}")

print("\n=== DONE ===")

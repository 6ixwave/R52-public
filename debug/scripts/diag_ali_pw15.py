"""PW15: Чистый парсинг MTOP из diag_ali_pw13_result.json + тест _parse_mtop."""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── 1. Читаем результат pw13 ──
raw = (ROOT / "diag_ali_pw13_result.json").read_text(encoding="utf-8")
diag = json.loads(raw)

mtop_bodies = diag.get("mtop_bodies", [])
print(f"mtop_bodies: {len(mtop_bodies)}")

# Ищем реальный MTOP (acs.aliexpress.com)
real = None
for i, mb in enumerate(mtop_bodies):
    url = mb.get("url", "")
    print(f"  [{i}] {url[:90]}...")
    if "acs.aliexpress.com" in url:
        real = mb
        print(f"      >>> РЕАЛЬНЫЙ MTOP")

if not real:
    print("FATAL: реальный MTOP не найден")
    sys.exit(1)

body = real["body"]
print(f"\nbody len: {len(body)}")
print(f"body[:80]: {body[:80]!r}")

# ── 2. Чистим: strip + jsonp ──
body_stripped = body.strip()
print(f"\nAfter strip, body[:80]: {body_stripped[:80]!r}")

m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', body_stripped, re.DOTALL)
if m:
    inner = m.group(1)
    print(f"jsonp stripped, inner len: {len(inner)}")
    print(f"inner[:120]: {inner[:120]!r}")
else:
    # Может быть без jsonp
    inner = body_stripped
    print("No jsonp wrapper, using raw body")

# ── 3. Парсим JSON ──
try:
    d = json.loads(inner)
    print(f"\nJSON parsed OK!")
    print(f"ret: {d.get('ret')}")
    print(f"api: {d.get('api')}")
except json.JSONDecodeError as e:
    print(f"\nJSON PARSE ERROR: {e}")
    print(f"inner[:200]: {inner[:200]!r}")
    print(f"inner[-100:]: {inner[-100:]!r}")
    sys.exit(1)

data = d.get("data", {})
result = data.get("result", data)
print(f"\nresult keys ({len(result)}): {list(result.keys())[:40]}")

# ── 4. Извлекаем ключевые поля ──
print("\n" + "=" * 60)
print("КЛЮЧЕВЫЕ ПОЛЯ ТОВАРА")
print("=" * 60)

# GLOBAL_DATA
gd = result.get("GLOBAL_DATA", {})
gdata = gd.get("globalData", {})
print(f"\n[GLOBAL_DATA]")
print(f"  bigBossBan: {gdata.get('bigBossBan')}")
print(f"  errorCode: {gdata.get('errorCode')}")
print(f"  bigBossBanTip: {str(gdata.get('bigBossBanTip',''))[:120]}")

# SKUModule
skm = result.get("SKUModule", {})
print(f"\n[SKUModule] keys: {list(skm.keys())[:20]}")
print(f"  title: {str(skm.get('title',''))[:150]}")
print(f"  subTitle: {str(skm.get('subTitle',''))[:100]}")

# targetSkuPriceInfo
tspi = result.get("targetSkuPriceInfo", {})
print(f"\n[targetSkuPriceInfo]")
print(f"  keys: {list(tspi.keys())[:20]}")
print(f"  salePriceLocal: {tspi.get('salePriceLocal')}")
print(f"  salePriceString: {tspi.get('salePriceString')}")
print(f"  originalPrice: {tspi.get('originalPrice')}")
print(f"  currency: {tspi.get('currency')}")

# HEADER_IMAGE_PC
hipc = result.get("HEADER_IMAGE_PC", {})
print(f"\n[HEADER_IMAGE_PC] keys: {list(hipc.keys())[:15]}")
ci = hipc.get("currentSkuImages", [])
print(f"  currentSkuImages: {len(ci)} items")
for img in ci[:3]:
    print(f"    {str(img)[:100]}")

# seoInfo
seo = result.get("seoInfo", {})
print(f"\n[seoInfo]")
print(f"  title: {str(seo.get('title',''))[:150]}")
print(f"  keywords: {str(seo.get('keywords',''))[:100]}")

# CATEGORY
cat = result.get("CATEGORY", result.get("categoryModule", {}))
print(f"\n[CATEGORY] keys: {list(cat.keys())[:15]}")
if isinstance(cat, dict):
    for k in list(cat.keys())[:5]:
        v = cat[k]
        print(f"  {k}: {str(v)[:100]}")

# DESCRIPTION
desc = result.get("DESCRIPTION", result.get("descriptionModule", {}))
print(f"\n[DESCRIPTION] keys: {list(desc.keys())[:10]}")

# SHIP
ship = result.get("SHIP", result.get("shipModule", {}))
print(f"\n[SHIP] keys: {list(ship.keys())[:10]}")

# ── 5. Тест через AliExpressCollector._parse_mtop ──
print("\n" + "=" * 60)
print("ТЕСТ _parse_mtop ИЗ COLLECTOR")
print("=" * 60)

from r52.collectors.aliexpress import AliExpressCollector
collector = AliExpressCollector(data_dir=None, use_proxies=False)

obs = {
    "fields": {},
    "price_candidates": [],
    "media": {"items": []},
    "availability": "UNKNOWN",
    "category_path": None,
}

try:
    parsed = collector._parse_mtop(body, obs)
    print(f"_parse_mtop returned: {parsed}")
except Exception as e:
    print(f"_parse_mtop EXCEPTION: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print(f"\n  title: {obs['fields'].get('title', {}).get('value', 'N/A')[:120]}")
print(f"  prices: {len(obs['price_candidates'])}")
for p in obs["price_candidates"][:5]:
    print(f"    {p}")
print(f"  images: {len(obs['media']['items'])}")
for img in obs["media"]["items"][:3]:
    print(f"    {str(img)[:100]}")
print(f"  availability: {obs['availability']}")
print(f"  category: {obs['category_path']}")

# ── 6. Сохраняем очищенный MTOP для отладки ──
out = ROOT / "diag_ali_pw15_mtop_clean.json"
out.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nClean MTOP saved: {out.name} ({out.stat().st_size} bytes)")

print("\nDONE")

#!/usr/bin/env python
"""
extract_pdp_r52.py — точечный экстрактор полей из PDP MTOP AliExpress.
Выводит JSON с полями для normalize_eye() / intake / SQLite.
"""
import json, re, sys, traceback

RAW = "mtop_pw62_pdp_905897.json"
OUT = "extract_pdp_r52_out.json"

with open(RAW, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

m = re.search(r'mtopjsonp\d+\(\s*(\{.*\})\s*\)', text, re.DOTALL)
if not m:
    m = re.search(r'\(\s*(\{.*\})\s*\)', text, re.DOTALL)
if not m:
    m = re.search(r'(\{.*\})', text, re.DOTALL)
if not m:
    print("FATAL: cannot parse JSONP wrapper")
    sys.exit(1)

data = json.loads(m.group(1))
res = data["data"]["result"]

out = {}
errors = []

def safe_mod(name):
    try:
        v = res.get(name, {})
        if isinstance(v, dict):
            return v
        return {}
    except Exception as e:
        errors.append(f"{name}: {e}")
        return {}

def dump(obj, max_len=5000):
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
        return s[:max_len]
    except Exception:
        return str(obj)[:max_len]

# 1. TITLE
try:
    pt = safe_mod("PRODUCT_TITLE")
    out["product_title_module"] = {k: str(v)[:300] for k, v in pt.items()}
except Exception as e:
    errors.append(f"PRODUCT_TITLE: {e}")

gd = {}
try:
    gd = res.get("GLOBAL_DATA", {}).get("globalData", {})
    if not isinstance(gd, dict):
        gd = {}
except Exception as e:
    errors.append(f"GLOBAL_DATA: {e}")

out["title"] = str(gd.get("subject", ""))[:500]
out["title_full"] = str(gd.get("subject", ""))

# 2. PRICE
try:
    price_mod = safe_mod("PRICE")
    out["price_keys"] = list(price_mod.keys())
    out["price"] = price_mod
except Exception as e:
    errors.append(f"PRICE: {e}")

try:
    pe = safe_mod("PRICE_EXTEND")
    out["price_extend"] = pe
except Exception as e:
    errors.append(f"PRICE_EXTEND: {e}")

# 3. SKU
try:
    sku = safe_mod("SKU")
    out["sku_keys"] = list(sku.keys())
    out["sku_properties"] = sku.get("skuProperties", [])
    out["sku_paths_count"] = len(sku.get("skuPaths", []))
    out["sku_paths_sample"] = sku.get("skuPaths", [])[:5]
    out["sku_selected"] = {
        "id": sku.get("selectedSkuId"),
        "idStr": sku.get("selectedSkuIdStr"),
        "saleable": sku.get("selectedSkuSaleable"),
        "attr": str(sku.get("selectedSkuAttr", ""))[:300],
    }
    out["sku_has_image"] = sku.get("hasSkuImage")
except Exception as e:
    errors.append(f"SKU: {e}")

# 4. SHIPPING
try:
    ship = safe_mod("SHIPPING")
    out["shipping_keys"] = list(ship.keys())
    out["shipping_delivery_layout"] = ship.get("deliveryLayoutInfo", [])[:5]
    out["shipping_original"] = ship.get("originalLayoutResultList", [])[:5]
    dl = ship.get("deliveryLayoutInfo", [])
    if dl and isinstance(dl, list) and len(dl) > 0:
        bd = dl[0].get("bizData", {}) if isinstance(dl[0], dict) else {}
        out["shipping_currency"] = bd.get("currency")
        out["shipping_delivery_text"] = str(bd.get("deliveryText", bd.get("text", "")))[:300]
except Exception as e:
    errors.append(f"SHIPPING: {e}")

# 5. IMAGES
try:
    img = safe_mod("HEADER_IMAGE_PC")
    out["images_keys"] = list(img.keys())
    out["images_path"] = img.get("imagePathList", [])
    out["images_img_list"] = img.get("imgList", [])
    main_imgs = img.get("mainImages", [])
    out["images_main"] = [m.get("imageUrl") for m in main_imgs if isinstance(m, dict) and m.get("imageUrl")]
    out["images_current_sku"] = img.get("currentSkuImages", [])
except Exception as e:
    errors.append(f"HEADER_IMAGE_PC: {e}")

# 6. PRODUCT PROPS
try:
    props = safe_mod("PRODUCT_PROP_PC")
    out["props_keys"] = list(props.keys())
    prop_list = props.get("propertyList", props.get("properties", []))
    out["props_count"] = len(prop_list) if isinstance(prop_list, list) else 0
    out["props_sample"] = prop_list[:15] if isinstance(prop_list, list) else []
except Exception as e:
    errors.append(f"PRODUCT_PROP_PC: {e}")

# 7. RATING
try:
    rating = safe_mod("PC_RATING")
    out["rating"] = rating
except Exception as e:
    errors.append(f"PC_RATING: {e}")

# 8. SHOP
try:
    shop = safe_mod("SHOP_CARD_PC")
    out["shop_keys"] = list(shop.keys())
    out["shop_name"] = shop.get("shopName", shop.get("storeName", ""))
    out["shop_id"] = shop.get("shopId", shop.get("storeId", shop.get("sellerId")))
    out["shop_rating"] = shop.get("rating", shop.get("score"))
    out["shop_followers"] = shop.get("followers", shop.get("fans"))
    out["shop_url"] = shop.get("shopUrl", shop.get("storeUrl", ""))
except Exception as e:
    errors.append(f"SHOP_CARD_PC: {e}")

# 9. QUANTITY
try:
    qty = safe_mod("QUANTITY_PC")
    out["quantity"] = qty
except Exception as e:
    errors.append(f"QUANTITY_PC: {e}")

# 10. GLOBAL_DATA extras
try:
    out["global_keys"] = list(gd.keys())[:40]
    out["global_product_id"] = gd.get("productId", gd.get("itemId", gd.get("id")))
    out["global_currency"] = gd.get("currency")
    out["global_locale"] = gd.get("locale")
    out["global_country"] = gd.get("country")
    pinfo = gd.get("priceInfo", gd.get("curPagePriceInfo", {}))
    out["global_price_info"] = pinfo if isinstance(pinfo, dict) else str(pinfo)[:500]
except Exception as e:
    errors.append(f"GLOBAL extras: {e}")

# 11. BOTTOM BAR
try:
    bottom = safe_mod("BOTTOM_BAR_PC")
    out["bottom_bar_keys"] = list(bottom.keys())
except Exception as e:
    errors.append(f"BOTTOM_BAR_PC: {e}")

# 12. INTEREST POINTS
try:
    interest = safe_mod("INTEREST_POINTS")
    out["interest_points"] = interest
except Exception as e:
    errors.append(f"INTEREST_POINTS: {e}")

# 13. COUPON
try:
    coupon = safe_mod("COUPON_BLOCK_PC")
    out["coupon_keys"] = list(coupon.keys())
    out["coupon"] = coupon
except Exception as e:
    errors.append(f"COUPON_BLOCK_PC: {e}")

# 14. SELLING POINTS
try:
    sp = safe_mod("SELLING_POINT_PC")
    out["selling_points"] = sp
except Exception as e:
    errors.append(f"SELLING_POINT_PC: {e}")

# 15. INSTALLMENT
try:
    inst = safe_mod("INSTALLMENT")
    out["installment"] = inst
except Exception as e:
    errors.append(f"INSTALLMENT: {e}")

out["_errors"] = errors

# Save
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2, ensure_ascii=False, default=str)

# Print summary
print("=" * 60)
print(f"TITLE: {out.get('title', '')[:120]}")
print(f"PRICE KEYS: {out.get('price_keys', [])}")
print(f"PRICE MODULE: {dump(out.get('price', {}), 600)}")
print(f"PRICE EXTEND: {dump(out.get('price_extend', {}), 400)}")
print(f"CURRENCY: {out.get('shipping_currency')}")
print(f"SKU PROPS: {len(out.get('sku_properties', []))}")
print(f"SKU PATHS: {out.get('sku_paths_count', 0)}")
print(f"SKU SELECTED: {out.get('sku_selected', {})}")
print(f"IMAGES: {len(out.get('images_path', []))} path, {len(out.get('images_main', []))} main")
print(f"PROPS: {out.get('props_count', 0)} specs")
print(f"PROPS SAMPLE: {dump(out.get('props_sample', []), 400)}")
print(f"RATING: {dump(out.get('rating', {}), 300)}")
print(f"SHOP: {out.get('shop_name')} (id={out.get('shop_id')}, rating={out.get('shop_rating')})")
print(f"QUANTITY: {dump(out.get('quantity', {}), 300)}")
print(f"GLOBAL PID: {out.get('global_product_id')}")
print(f"GLOBAL CURRENCY: {out.get('global_currency')}")
print(f"GLOBAL PRICE INFO: {dump(out.get('global_price_info', {}), 400)}")
print(f"COUPON: {dump(out.get('coupon', {}), 300)}")
print(f"ERRORS: {errors}")
print(f"\nSaved: {OUT}")

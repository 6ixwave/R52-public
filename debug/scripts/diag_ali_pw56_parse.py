"""pw56: Разбор mtop_pw53_pdp_323763.json — что реально в ответе PDP."""
import json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "mtop_pw53_pdp_323763.json")
OUT = os.path.join(BASE, "diag_ali_pw56_result.json")

def strip_jsonp(raw: str) -> str:
    """Убирает mtopjsonpN(...) обёртку."""
    raw = raw.strip()
    if raw.startswith("mtopjsonp"):
        # mtopjsonp1({...})
        start = raw.index("(")
        end = raw.rindex(")")
        return raw[start + 1 : end]
    return raw

def walk(obj, prefix="", depth=0, max_depth=4):
    """Рекурсивный обход ключей до max_depth."""
    lines = []
    if depth > max_depth:
        return lines
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                lines.append(f"{'  ' * depth}{key}: [dict, {len(v)} keys]")
                lines.extend(walk(v, key, depth + 1, max_depth))
            elif isinstance(v, list):
                lines.append(f"{'  ' * depth}{key}: [list, {len(v)} items]")
                if v and isinstance(v[0], dict):
                    lines.append(f"{'  ' * (depth+1)}[0]: {list(v[0].keys())[:10]}")
            else:
                val_str = str(v)[:120]
                lines.append(f"{'  ' * depth}{key}: {val_str}")
    return lines

def extract_product_fields(data: dict) -> dict:
    """Извлекает конкретные товарные поля из PDP-ответа."""
    result = {}
    res = data.get("data", {}).get("result", {})
    
    # GLOBAL_DATA
    gd = res.get("GLOBAL_DATA", {}).get("globalData", {})
    result["globalData"] = {
        "bigBossBan": gd.get("bigBossBan"),
        "errorCode": gd.get("errorCode"),
        "bigBossBanTip": gd.get("bigBossBanTip"),
    }
    
    # HEADER_IMAGE_PC — галерея
    hip = res.get("HEADER_IMAGE_PC", {})
    imgs = hip.get("currentSkuImages", [])
    result["images"] = {
        "count": len(imgs),
        "first_3": [i.get("imageUrl", "")[:100] for i in imgs[:3]],
    }
    
    # targetSkuPriceInfo — цена
    ts = res.get("targetSkuPriceInfo", {})
    result["price"] = {
        "salePriceString": ts.get("salePriceString"),
        "originalPrice": ts.get("originalPrice"),
        "monthlyLowestPrice": ts.get("monthlyLowestPrice"),
        "salePriceLocal": ts.get("salePriceLocal"),
        "currency": ts.get("currency"),
    }
    
    # SKUModule
    sku = res.get("SKUModule", {})
    result["sku"] = {
        "keys": list(sku.keys())[:15] if isinstance(sku, dict) else type(sku).__name__,
    }
    if isinstance(sku, dict):
        sku_list = sku.get("skuPaths", [])
        result["sku"]["paths_count"] = len(sku_list)
        if sku_list:
            result["sku"]["first_path"] = str(sku_list[0])[:200]
    
    # Category
    result["category"] = {
        "categoryPath": res.get("categoryPath"),
        "category1": res.get("category1"),
        "category3": res.get("category3"),
    }
    
    # Delivery
    deliv = res.get("deliveryOptionPanelDisplayList", [])
    result["delivery"] = {
        "count": len(deliv) if isinstance(deliv, list) else 0,
        "first": str(deliv[0])[:200] if deliv else None,
    }
    
    # Buyer protection
    bp = res.get("buyerProtection", {})
    result["buyerProtection"] = {
        "keys": list(bp.keys())[:10] if isinstance(bp, dict) else type(bp).__name__,
    }
    
    # Title / name
    result["title"] = res.get("title") or res.get("subject") or gd.get("title")
    
    # Product ID
    result["productId"] = res.get("productId") or res.get("itemId")
    
    # All top-level keys in result
    result["_all_result_keys"] = list(res.keys())
    
    return result

def main():
    with open(SRC, "r", encoding="utf-8") as f:
        raw = f.read()
    
    print(f"[pw56] Raw size: {len(raw)} bytes")
    print(f"[pw56] First 200 chars: {raw[:200]}")
    
    json_str = strip_jsonp(raw)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"[pw56] JSON parse error: {e}")
        print(f"[pw56] Around error pos: {json_str[max(0,e.pos-50):e.pos+50]}")
        # Try to fix: sometimes trailing comma or extra chars
        # Save partial
        out = {"parse_error": str(e), "raw_head": raw[:500], "raw_tail": raw[-500:]}
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return
    
    print(f"[pw56] Parsed OK. Top keys: {list(data.keys())}")
    print(f"[pw56] api: {data.get('api')}")
    
    # Structure walk
    struct = walk(data, max_depth=3)
    print("\n=== STRUCTURE (depth 3) ===")
    for line in struct[:120]:
        print(line)
    if len(struct) > 120:
        print(f"... ({len(struct)} total lines)")
    
    # Extract fields
    fields = extract_product_fields(data)
    print("\n=== EXTRACTED FIELDS ===")
    print(json.dumps(fields, ensure_ascii=False, indent=2))
    
    # Save full result
    out = {
        "source": "mtop_pw53_pdp_323763.json",
        "raw_bytes": len(raw),
        "api": data.get("api"),
        "v": data.get("v"),
        "ret": data.get("ret"),
        "structure_sample": struct[:80],
        "extracted_fields": fields,
        "all_result_keys": list(data.get("data", {}).get("result", {}).keys()),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[pw56] Saved → {OUT}")

if __name__ == "__main__":
    main()

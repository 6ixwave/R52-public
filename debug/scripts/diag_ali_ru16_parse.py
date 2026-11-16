"""V16: Parse existing search_api.json → clean product list.
Walks the nested snippetContainer structure, extracts per-item data."""
import json, os, sys, re, logging
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
API_JSON = os.path.join(BASE, "diag_ali_ru15_out", "search_api.json")
OUT_JSON = os.path.join(BASE, "diag_ali_ru16_parsed.json")

log = logging.getLogger("diag_ali_ru16")
log.setLevel(logging.INFO)
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
log.addHandler(_sh)

def find_snippets(obj, path=""):
    """Recursively find all snippetContainer objects."""
    results = []
    if isinstance(obj, dict):
        if "snippetContainer" in obj:
            results.append((path, obj["snippetContainer"]))
        for k, v in obj.items():
            results.extend(find_snippets(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            results.extend(find_snippets(item, f"{path}[{i}]"))
    return results

def extract_texts(obj, prefix=""):
    """Extract all text values from a nested structure."""
    texts = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "text" and isinstance(v, dict) and "text" in v:
                t = v["text"].strip()
                if t:
                    texts.append(t)
            elif k == "text" and isinstance(v, str) and v.strip():
                texts.append(v.strip())
            else:
                texts.extend(extract_texts(v, f"{prefix}.{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            texts.extend(extract_texts(item, f"{prefix}[{i}]"))
    return texts

def extract_images(obj):
    """Extract all image URLs from gallery/element structures."""
    imgs = []
    if isinstance(obj, dict):
        if "gallery" in obj and isinstance(obj["gallery"], dict):
            if "images" in obj["gallery"] and isinstance(obj["gallery"]["images"], list):
                imgs.extend(obj["gallery"]["images"])
        for k, v in obj.items():
            if k == "url" and isinstance(v, str) and ("alicdn" in v or "aliexpress-media" in v):
                imgs.append(v)
            else:
                imgs.extend(extract_images(v))
    elif isinstance(obj, list):
        for item in obj:
            imgs.extend(extract_images(item))
    return imgs

def extract_aer_events(obj):
    """Extract all aerEvent dicts."""
    events = []
    if isinstance(obj, dict):
        if "aerEvent" in obj and isinstance(obj["aerEvent"], dict):
            events.append(obj["aerEvent"])
        for k, v in obj.items():
            events.extend(extract_aer_events(v))
    elif isinstance(obj, list):
        for item in obj:
            events.extend(extract_aer_events(item))
    return events

def extract_layout_areas(snippet):
    """Get layoutAreas from snippet presentations."""
    areas = []
    if "presentations" in snippet:
        for pres in snippet["presentations"]:
            if "layoutAreas" in pres:
                areas.extend(pres["layoutAreas"])
    return areas

def parse_product(snippet, path):
    """Parse a single snippetContainer into a product dict."""
    product = {
        "source_path": path,
        "item_id": None,
        "sku": None,
        "price": None,
        "currency": None,
        "discount": None,
        "title": None,
        "images": [],
        "url": None,
        "seller": None,
        "raw_texts": [],
    }
    
    # aerEvents
    events = extract_aer_events(snippet)
    for ev in events:
        if ev.get("itemId") and not product["item_id"]:
            product["item_id"] = ev["itemId"]
        if ev.get("sku") and not product["sku"]:
            product["sku"] = ev["sku"]
        if ev.get("finalPrice") and not product["price"]:
            product["price"] = ev["finalPrice"]
        if ev.get("currency") and not product["currency"]:
            product["currency"] = ev["currency"]
    
    # Layout areas
    areas = extract_layout_areas(snippet)
    for area in areas:
        area_id = area.get("id", {}).get("name", "")
        
        # Title area
        if "title" in area_id.lower():
            texts = extract_texts(area)
            # Filter out short/generic texts
            good_texts = [t for t in texts if len(t) > 10 and t != "Выберите категорию"]
            if good_texts and not product["title"]:
                product["title"] = good_texts[0]
        
        # Price areas
        if "price" in area_id.lower():
            texts = extract_texts(area)
            for t in texts:
                if "₽" in t or "RUB" in t.upper():
                    # Extract numeric price
                    nums = re.findall(r'[\d\s]+(?:\.\d+)?', t)
                    if nums:
                        raw = nums[0].replace(" ", "").replace("\u00a0", "")
                        try:
                            val = float(raw)
                            if not product["price"] or val < product["price"]:
                                product["price"] = val
                        except ValueError:
                            pass
                if "%" in t and not product["discount"]:
                    product["discount"] = t
        
        # Gallery / images
        if "gallery" in area_id.lower() or "image" in area_id.lower():
            imgs = extract_images(area)
            for img in imgs:
                if img and img not in product["images"]:
                    product["images"].append(img)
        
        # Seller
        if "seller" in area_id.lower() or "store" in area_id.lower():
            texts = extract_texts(area)
            if texts and not product["seller"]:
                product["seller"] = texts[0]
        
        # URL / link
        if "link" in area_id.lower() or "url" in area_id.lower():
            # Look for href patterns
            def find_urls(o):
                urls = []
                if isinstance(o, dict):
                    for k, v in o.items():
                        if k in ("href", "url", "link") and isinstance(v, str) and "aliexpress" in v:
                            urls.append(v)
                        else:
                            urls.extend(find_urls(v))
                elif isinstance(o, list):
                    for item in o:
                        urls.extend(find_urls(item))
                return urls
            urls = find_urls(area)
            if urls and not product["url"]:
                product["url"] = urls[0]
    
    # Build URL from item_id if not found
    if product["item_id"] and not product["url"]:
        product["url"] = f"https://aliexpress.ru/item/{product['item_id']}.html"
    if product["sku"] and product["url"]:
        if "?" not in product["url"]:
            product["url"] += f"?sku_id={product['sku']}"
        elif "sku_id" not in product["url"]:
            product["url"] += f"&sku_id={product['sku']}"
    
    # Collect all texts for debugging
    product["raw_texts"] = extract_texts(snippet)[:30]
    
    return product

def main():
    log.info(f"Loading {API_JSON} ...")
    with open(API_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    log.info(f"Loaded. Root keys: {list(data.keys())}")
    
    # Find all snippets
    snippets = find_snippets(data.get("data", data))
    log.info(f"Found {len(snippets)} snippetContainer objects")
    
    products = []
    for path, snippet in snippets:
        p = parse_product(snippet, path)
        if p["item_id"] or p["price"] or p["images"]:
            products.append(p)
    
    # Deduplicate by item_id
    seen = set()
    unique = []
    for p in products:
        key = p["item_id"] or p["url"] or str(hash(json.dumps(p, sort_keys=True)))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    
    log.info(f"Unique products: {len(unique)}")
    
    # Summary
    for i, p in enumerate(unique[:5]):
        log.info(f"  [{i}] id={p['item_id']} price={p['price']} {p['currency']} "
                 f"title={str(p['title'])[:60]} imgs={len(p['images'])}")
    
    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source_file": API_JSON,
        "total_snippets": len(snippets),
        "unique_products": len(unique),
        "products": unique,
    }
    
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"Saved → {OUT_JSON} ({os.path.getsize(OUT_JSON)} bytes)")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

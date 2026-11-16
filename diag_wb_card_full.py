"""WB: полный дамп card.json — все 24 ключа, поиск price/qty."""
import json, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("wb_card")

NM_ID = 199298871
VOL = NM_ID // 100000
PART = NM_ID // 1000
HOST = "basket-13.wbbasket.ru"
URL = f"https://{HOST}/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"

try:
    from curl_cffi import requests as creq
    r = creq.get(URL, impersonate="chrome", timeout=15)
except ImportError:
    import requests
    r = requests.get(URL, timeout=15)

print(f"=== WB CARD.JSON FULL DUMP ===")
print(f"URL: {URL}")
print(f"Status: {r.status_code}, Size: {len(r.content)}B")

if r.status_code == 200:
    data = r.json()
    print(f"\nTotal keys: {len(data)}")
    print(f"ALL KEYS: {list(data.keys())}")
    print()

    # Dump each key with type and truncated value
    for k, v in data.items():
        if isinstance(v, (str, int, float, bool)):
            print(f"  {k}: {repr(v)[:120]}")
        elif isinstance(v, (list, dict)):
            s = json.dumps(v, ensure_ascii=False)
            print(f"  {k} ({type(v).__name__}, len={len(v)}): {s[:200]}")
        else:
            print(f"  {k} ({type(v).__name__}): {repr(v)[:100]}")

    # Search for price-related fields anywhere
    print("\n--- PRICE SEARCH ---")
    s = json.dumps(data, ensure_ascii=False)
    for pat in ["price", "Price", "priceU", "priceD", "salePrice", "totalQty", "qty", "amount", "oldPrice", "discount"]:
        if pat in s:
            idx = s.find(pat)
            print(f"  FOUND '{pat}': ...{s[max(0,idx-30):idx+100]}...")

    # Check 'options' in detail
    opts = data.get("options", [])
    if opts:
        print(f"\n--- OPTIONS ({len(opts)} items) ---")
        for i, o in enumerate(opts[:5]):
            print(f"  [{i}]: {json.dumps(o, ensure_ascii=False)[:150]}")

    # Check 'selling' in detail
    selling = data.get("selling", {})
    if selling:
        print(f"\n--- SELLING ---")
        print(f"  {json.dumps(selling, ensure_ascii=False)[:300]}")

    # Save full JSON for reference
    with open("wb_card_full.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to wb_card_full.json")
else:
    print(f"ERROR: HTTP {r.status_code}")
    print(r.text[:300])

print("\n=== DONE ===")

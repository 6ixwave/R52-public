"""Диагностика v115: Farpost живой ID + WB media pattern + WB data field."""
import sys, json, re, time, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

SEP = "=" * 60
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# ============================================================
# 1. FARPOST: полный парсинг живого объявления
# ============================================================
def test_farpost_live():
    print(f"\n{SEP}\nFARPOST: LIVE PARSER TEST (ID=129606872)\n{SEP}")
    from r52.collectors.farpost import FarpostCollector
    
    fp = FarpostCollector(use_proxies=False)
    
    # Test 1: numeric ID via /bulletin/
    print("\n--- Test A: numeric ID (build_url -> /bulletin/129606872) ---")
    url_a = fp.build_url("129606872")
    print(f"URL: {url_a}")
    t0 = time.monotonic()
    result_a = fp.collect_product("129606872", url_a)
    elapsed_a = time.monotonic() - t0
    if result_a:
        obs = result_a["observation"]
        print(f"Status: {obs.get('status')}, Title: {obs['fields'].get('title',{}).get('value','?')[:60]}")
        prices = obs.get("price_candidates", [])
        for p in prices:
            print(f"  Price: {p.get('amount','?')} {p.get('currency')} [{p.get('label')}]")
        media = obs.get("media",{}).get("items",[])
        print(f"Media: {len(media)} items")
        print(f"Fields: {list(obs['fields'].keys())}")
        for k in ("brand","model","description","seller","specs","category"):
            v = obs["fields"].get(k)
            if v and v.get("value"):
                print(f"  {k}: {str(v['value'])[:80]}")
    else:
        print(f"RESULT: None ({elapsed_a:.1f}s), stats={fp.stats}")
    
    # Test 2: full path
    print("\n--- Test B: full path ---")
    fp2 = FarpostCollector(use_proxies=False)
    path_b = "/ni/krasnoyarsk/zapchasti/kontraktnyj-dvigatel-1zz-fe-cena-s-navesnym-129606872.html"
    url_b = fp2.build_url(path_b)
    print(f"URL: {url_b}")
    t0 = time.monotonic()
    result_b = fp2.collect_product("129606872", url_b)
    elapsed_b = time.monotonic() - t0
    if result_b:
        obs = result_b["observation"]
        print(f"Status: {obs.get('status')}, Title: {obs['fields'].get('title',{}).get('value','?')[:60]}")
        prices = obs.get("price_candidates", [])
        for p in prices:
            print(f"  Price: {p.get('amount','?')} {p.get('currency')} [{p.get('label')}]")
        media = obs.get("media",{}).get("items",[])
        photos = sum(1 for m in media if m["type"]=="image")
        videos = sum(1 for m in media if m["type"]=="video")
        print(f"Media: {len(media)} items ({photos} photos, {videos} videos)")
        for m in media[:8]:
            print(f"  {m['type']:5s}: {m['source_url'][:90]}")
        print(f"Availability: {obs.get('availability')}")
        print(f"Category: {obs.get('category_path')}")
        print(f"Fields: {list(obs['fields'].keys())}")
        for k in ("brand","model","description","seller","specs","options","bulletin_id"):
            v = obs["fields"].get(k)
            if v and v.get("value"):
                print(f"  {k}: {str(v['value'])[:100]}")
    else:
        print(f"RESULT: None ({elapsed_b:.1f}s), stats={fp2.stats}")
    
    return result_a, result_b


# ============================================================
# 2. WB: Media URL pattern (S800, S800-2, S800-3 ...)
# ============================================================
def test_wb_media_pattern():
    print(f"\n{SEP}\nWB: MEDIA URL PATTERN TEST\n{SEP}")
    import requests
    
    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000
    host = "basket-13.wbbasket.ru"
    
    # Try S800, S800-2, S800-3, S800-4, S800-5, S800-6
    # photo_count from card.json = 6
    for i in range(1, 8):
        if i == 1:
            suffix = "S800/S800.jpg"
        else:
            suffix = f"S800/S800-{i}.jpg"
        url = f"https://{host}/vol{vol}/part{part}/{nm_id}/images/{suffix}"
        try:
            r = requests.get(url, timeout=(5, 10), headers={"User-Agent": UA})
            ct = r.headers.get("Content-Type", "?")
            print(f"  [{i}] {suffix:20s}: HTTP {r.status_code}, {len(r.content):>8}B, {ct}")
        except Exception as e:
            print(f"  [{i}] {suffix:20s}: ERROR {e}")
        time.sleep(1)
    
    # Also try different size prefix
    print("\n  --- S1000 pattern ---")
    for i in range(1, 4):
        if i == 1:
            suffix = "S1000/S1000.jpg"
        else:
            suffix = f"S1000/S1000-{i}.jpg"
        url = f"https://{host}/vol{vol}/part{part}/{nm_id}/images/{suffix}"
        try:
            r = requests.get(url, timeout=(5, 10), headers={"User-Agent": UA})
            print(f"  [{i}] {suffix:20s}: HTTP {r.status_code}, {len(r.content):>8}B")
        except Exception as e:
            print(f"  [{i}] {suffix:20s}: ERROR {e}")
        time.sleep(1)


# ============================================================
# 3. WB: data field in card.json (might have price)
# ============================================================
def test_wb_data_field():
    print(f"\n{SEP}\nWB: CARD.JSON 'data' FIELD\n{SEP}")
    dump = Path(__file__).parent / "wb_card_json_dump.json"
    if not dump.exists():
        print("  Dump not found, fetching fresh...")
        import requests
        nm_id = 199298871
        vol = nm_id // 100000
        part = nm_id // 1000
        url = f"https://basket-13.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        r = requests.get(url, timeout=(10, 20), headers={"User-Agent": UA, "Accept": "application/json"})
        data = r.json()
    else:
        data = json.loads(dump.read_text(encoding="utf-8"))
    
    # 'data' field
    d = data.get("data")
    if d:
        print(f"  data type: {type(d).__name__}")
        if isinstance(d, dict):
            print(f"  data keys: {sorted(d.keys())}")
            for k, v in d.items():
                sv = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                print(f"    {k}: {sv[:120]}")
        elif isinstance(d, list):
            print(f"  data len: {len(d)}")
            for i, item in enumerate(d[:3]):
                print(f"    [{i}]: {json.dumps(item, ensure_ascii=False)[:150]}")
    
    # 'properties' field
    props = data.get("properties")
    if props:
        print(f"\n  properties type: {type(props).__name__}")
        if isinstance(props, dict):
            print(f"  properties keys: {sorted(props.keys())}")
            for k, v in props.items():
                sv = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                print(f"    {k}: {sv[:120]}")
    
    # 'grouped_options'
    go = data.get("grouped_options")
    if go:
        print(f"\n  grouped_options: {json.dumps(go, ensure_ascii=False)[:200]}")
    
    # 'contents'
    contents = data.get("contents")
    if contents:
        print(f"\n  contents: {json.dumps(contents, ensure_ascii=False)[:200]}")
    
    # 'user_flags'
    uf = data.get("user_flags")
    if uf:
        print(f"\n  user_flags: {json.dumps(uf, ensure_ascii=False)[:100]}")


def main():
    fp_a, fp_b = test_farpost_live()
    test_wb_media_pattern()
    test_wb_data_field()
    
    print(f"\n{SEP}\nSUMMARY\n{SEP}")
    print(f"  Farpost /bulletin/: {'OK' if fp_a else 'FAIL'}")
    print(f"  Farpost full path:   {'OK' if fp_b else 'FAIL'}")
    print(f"  DONE")


if __name__ == "__main__":
    main()

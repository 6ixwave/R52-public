"""Диагностика: почему collect_by_search возвращает 0 товаров."""
import sys, os, time, json, logging, traceback
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, os.path.dirname(__file__))

print("=" * 60)
print("DIAG: AliExpressRUCollector search API")
print("=" * 60)

# 1. Импорты
print("\n[1] Importing AliExpressRUCollector...")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    print("    OK: class imported")
except Exception as e:
    print(f"    FAIL: {e}")
    traceback.print_exc()
    sys.exit(1)

# 2. Создание инстанса
print("\n[2] Creating instance (use_proxies=False)...")
t0 = time.monotonic()
try:
    col = AliExpressRUCollector(data_dir=None, use_proxies=False)
    print(f"    OK: created in {time.monotonic()-t0:.1f}s")
    print(f"    stats: {col.stats}")
    print(f"    pool: {col.pool.stats()}")
except Exception as e:
    print(f"    FAIL: {e}")
    traceback.print_exc()
    sys.exit(1)

# 3. Прямой вызов _fetch_search_api
print("\n[3] Calling _fetch_search_api('наушники', page=1, page_size=40)...")
t0 = time.monotonic()
try:
    data = col._fetch_search_api("наушники", page=1, page_size=40)
    elapsed = time.monotonic() - t0
    if data is None:
        print(f"    RESULT: None (API returned nothing) in {elapsed:.1f}s")
        print("    -> This means curl_cffi failed or HTTP != 200")
    else:
        print(f"    RESULT: dict with keys={list(data.keys())[:10]} in {elapsed:.1f}s")
        print(f"    Size: {len(json.dumps(data))} bytes")
        # Save for inspection
        out = os.path.join(os.path.dirname(__file__), "data", "collector_output", "diag_search_api.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"    Saved to: {out}")
except Exception as e:
    elapsed = time.monotonic() - t0
    print(f"    EXCEPTION after {elapsed:.1f}s: {e}")
    traceback.print_exc()
    data = None

# 4. Парсинг
if data:
    print("\n[4] Parsing search API response...")
    t0 = time.monotonic()
    products = col._parse_search_api(data)
    elapsed = time.monotonic() - t0
    print(f"    Parsed {len(products)} products in {elapsed:.1f}s")
    if products:
        p = products[0]
        print(f"    First: id={p.get('item_id')}, title={str(p.get('title',''))[:60]}, price={p.get('price')}")
        print(f"    URL: {p.get('url','')[:80]}")

# 5. collect_by_search (полный путь)
print("\n[5] Calling collect_by_search('наушники')...")
t0 = time.monotonic()
try:
    results = col.collect_by_search("наушники", page=1, page_size=40)
    elapsed = time.monotonic() - t0
    print(f"    Got {len(results)} observations in {elapsed:.1f}s")
    if results:
        r = results[0]
        obs = r.get("observation", {})
        print(f"    First obs: id={obs.get('id')}, status={obs.get('status')}")
        fields = obs.get("fields", {})
        print(f"    Fields: {list(fields.keys())}")
        if "title" in fields:
            print(f"    Title: {fields['title'].get('value','')[:80]}")
        pc = obs.get("price_candidates", [])
        if pc:
            print(f"    Price: {pc[0].get('amount')} {pc[0].get('currency')}")
    else:
        print("    EMPTY! Checking stats...")
        print(f"    stats: {col.stats}")
except Exception as e:
    elapsed = time.monotonic() - t0
    print(f"    EXCEPTION after {elapsed:.1f}s: {e}")
    traceback.print_exc()

# 6. Cleanup
print("\n[6] Closing collector...")
try:
    col.close()
    print("    OK")
except Exception as e:
    print(f"    close() error: {e}")

print("\n" + "=" * 60)
print("DIAG DONE")
print("=" * 60)

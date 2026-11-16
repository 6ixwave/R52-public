"""Изолированный тест PwFetcher.get_xhr (WB + OZON) вне collect_by_search.
v10: catch_json — ловим JSON по content-type, не только по URL-паттерну."""
import sys, time, json, traceback, re
from urllib.parse import quote

sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
from r52.collectors.pw_fetch import PwFetcher


def probe(fetcher, label, url, patterns, extra_wait_ms, xhr_timeout_ms,
          product_key=None, catch_json=False, min_json=1500):
    print(f"\n=== {label} ===")
    print("URL:", url[:100])
    t0 = time.monotonic()
    try:
        r = fetcher.get_xhr(url, patterns=patterns,
                            extra_wait_ms=extra_wait_ms,
                            xhr_timeout_ms=xhr_timeout_ms,
                            product_key=product_key,
                            catch_json=catch_json, min_json=min_json)
    except Exception:
        traceback.print_exc()
        return
    dt = time.monotonic() - t0
    if not r:
        print(f"{label}: get_xhr -> None ({dt:.1f}s)")
        return
    bodies = r.meta.get("xhr_bodies", [])
    print(f"{label}: bodies={len(bodies)} best_bytes={len(r.html)} status={r.status} ({dt:.1f}s)")
    for u, t in bodies:
        print(f"   body {len(t)}B  {u[:100]}")
    # WB: products из best
    prods = None
    try:
        d = json.loads(r.html)
        if isinstance(d, dict):
            prods = d.get("products")
            if not prods and isinstance(d.get("data"), dict):
                prods = d["data"].get("products")
    except (ValueError, TypeError):
        prods = None
    if prods:
        print(f"{label}: PRODUCTS={len(prods)}")
    # OZON: ids по всем bodies
    ids = []
    for u, t in bodies:
        for m in re.findall(r'/product/(\d{6,})/', t):
            if m not in ids:
                ids.append(m)
        try:
            d = json.loads(t)
            def walk(o):
                if isinstance(o, dict):
                    for k in ("tileId", "productId", "itemId"):
                        v = o.get(k)
                        if isinstance(v, (int, str)) and str(v).isdigit() and len(str(v)) >= 6:
                            if str(v) not in ids:
                                ids.append(str(v))
                    for vv in o.values():
                        walk(vv)
                elif isinstance(o, list):
                    for vv in o:
                        walk(vv)
            walk(d)
        except (ValueError, TypeError):
            pass
    if ids:
        print(f"{label}: IDS={len(ids)} sample={ids[:6]}")


def main():
    f = PwFetcher(headless=False, proxies=None, require_proxy=False)
    if not f.start():
        print("PwFetcher start() -> False (fail-closed)")
        return
    try:
        probe(f, "WB",
              "https://www.wildberries.ru/catalog/0/ru/?search=" + quote("видеокарты") + "&page=1",
              ["__internal/", "u-recom", "search"], 5000, 25000, "products")
        probe(f, "OZON",
              "https://www.ozon.ru/search/?text=" + quote("видеокарты") + "&from_global=true&page=2",
              ["/api/", "widget", "search", "tile", "entrypoint", "composer"],
              7000, 20000, catch_json=True, min_json=2000)
    finally:
        f.stop()
    print("\nDIAG_XHR_DONE")


if __name__ == "__main__":
    main()

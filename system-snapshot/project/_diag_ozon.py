"""Дамп XHR-тел OZON (p2, catch_json): сохранить каждое тело на диск,
показать размеры, top-level ключи JSON и образец товара — чтобы понять
реальную структуру entrypoint-JSON и формат product-ссылок."""
import sys, time, json, re, os
from urllib.parse import quote

sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
from r52.collectors.pw_fetch import PwFetcher

OUT = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\_ozon_bodies"


def find_products(d, depth=0, path=""):
    """Рекурсивно ищем список, похожий на товары (dict с id/price/name)."""
    hits = []
    if depth > 8:
        return hits
    if isinstance(d, dict):
        for k, v in d.items():
            hits += find_products(v, depth + 1, path + "/" + str(k))
    elif isinstance(d, list):
        if d and all(isinstance(x, dict) for x in d[:3]):
            keys = set()
            for x in d[:5]:
                keys |= set(x.keys())
            prodish = {"id", "price", "name", "title", "tileId", "productId",
                       "itemId", "sku", "url", "image", "images"}
            if keys & prodish:
                hits.append((path, len(d), sorted(keys)[:20], d[0]))
        for i, x in enumerate(d[:3]):
            hits += find_products(x, depth + 1, path + f"[{i}]")
    return hits


def main():
    if os.path.isdir(OUT):
        for f in os.listdir(OUT):
            try:
                os.remove(os.path.join(OUT, f))
            except OSError:
                pass
    os.makedirs(OUT, exist_ok=True)

    f = PwFetcher(headless=False, proxies=None, require_proxy=False)
    if not f.start():
        print("PwFetcher start() -> False")
        return
    try:
        url = ("https://www.ozon.ru/search/?text=" + quote("видеокарты")
               + "&from_global=true&page=2")
        r = f.get_xhr(url,
                      patterns=["/api/", "widget", "search", "tile", "entrypoint", "composer"],
                      extra_wait_ms=7000, xhr_timeout_ms=20000,
                      catch_json=True, min_json=2000)
        if not r:
            print("get_xhr -> None")
            return
        bodies = r.meta.get("xhr_bodies", [])
        print(f"bodies={len(bodies)} best={len(r.html)}B")
        for i, (u, t) in enumerate(bodies):
            if len(t) < 1500:
                continue
            fn = os.path.join(OUT, f"body_{i:02d}_{len(t)}.txt")
            with open(fn, "w", encoding="utf-8") as fh:
                fh.write(t)
            tag = ""
            try:
                d = json.loads(t)
                if isinstance(d, dict):
                    tag = "JSON keys=" + str(list(d.keys())[:12])
            except (ValueError, TypeError):
                tag = "HTML/other"
            print(f"[{i:02d}] {len(t)}B  {u[:75]}  | {tag}")
            if "entrypoint" in u or "composer" in u:
                try:
                    d = json.loads(t)
                    for path, n, keys, sample in find_products(d):
                        print(f"     PRODUCT-LIST {path} n={n} keys={keys}")
                        print(f"     sample={json.dumps(sample, ensure_ascii=False)[:300]}")
                        break
                except (ValueError, TypeError):
                    pass
        best = r.html
        slug_ids = re.findall(r'/product/[^"\'/]+?-(\d{6,})/', best)
        plain_ids = re.findall(r'/product/(\d{6,})/', best)
        print(f"\nbest HTML {len(best)}B: slug_ids={len(set(slug_ids))} plain_ids={len(set(plain_ids))}")
        print("slug sample:", list(set(slug_ids))[:8])
    finally:
        f.stop()
    print("\nDIAG_OZON_DONE")


if __name__ == "__main__":
    main()

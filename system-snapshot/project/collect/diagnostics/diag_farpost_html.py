"""Диагностика Farpost: что реально приходит в HTML и почему парсер не находит товар."""
import sys, re, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from r52.collectors.farpost import FarpostCollector

c = FarpostCollector(use_proxies=False)

URLS = [
    # Подтверждённый в v44 (45KB, PARSED)
    ("16879823067", "https://www.farpost.ru/vladivostok/zapchasti/datchik-kisloroda-lynxauto-art-dm-2350-g16879823067.html"),
    # "Общий" URL из теста (89KB, title=Фарпост)
    ("125103161", "https://www.farpost.ru/vladivostok/zapchasti/zaпчасти-na-auto-g125103161.html"),
    # Подтверждённый в v44 (64KB, PARSED)
    ("17260583280", "https://www.farpost.ru/vladivostok/zapchasti/filtr-toplivnyj-lynxauto-art-lf-1073m-g17260583280.html"),
]

OUT = Path(__file__).parent / "diag-farpost-html"
OUT.mkdir(exist_ok=True)

for lid, url in URLS:
    print(f"\n{'='*70}")
    print(f"ID={lid}")
    print(f"URL={url}")
    t0 = time.monotonic()
    resp = c.fetch(url)
    elapsed = time.monotonic() - t0
    if resp is None:
        print(f"  FETCH FAILED ({elapsed:.1f}s)")
        continue
    html = resp.text
    print(f"  STATUS={resp.status_code} SIZE={len(html)}B TIME={elapsed:.1f}s")
    print(f"  FINAL_URL={resp.url}")
    print(f"  CONTENT_TYPE={resp.headers.get('content-type','?')}")

    # Сохраняем HTML для инспекции
    html_path = OUT / f"farpost-{lid}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"  SAVED: {html_path.name}")

    # Ключевые маркеры
    title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.I)
    print(f"  TITLE_TAG: {(title_m.group(1)[:120] if title_m else 'NONE')}")

    h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
    h1_text = re.sub(r'<[^>]+>', '', h1_m.group(1)).strip()[:120] if h1_m else 'NONE'
    print(f"  H1: {h1_text}")

    # JSON-LD
    ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.I)
    print(f"  JSONLD_BLOCKS: {len(ld_blocks)}")
    for i, blk in enumerate(ld_blocks[:3]):
        try:
            data = json.loads(blk.strip())
            t = data.get("@type", "?") if isinstance(data, dict) else "list"
            name = data.get("name", "")[:60] if isinstance(data, dict) else ""
            print(f"    [{i}] type={t} name={name}")
        except Exception:
            print(f"    [{i}] PARSE_ERR len={len(blk)}")

    # data-bulletin-price
    prices = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
    print(f"  DATA_BULLETIN_PRICE: {prices}")

    # viewbull / bulletinText / subject
    print(f"  HAS_VIEWBULL: {bool(re.search(r'viewbull', html, re.I))}")
    print(f"  HAS_BULLETINTEXT: {bool(re.search(r'bulletinText', html, re.I))}")
    print(f"  HAS_SUBJECT: {bool(re.search(r'data-field=["\']subject', html, re.I))}")
    print(f"  HAS_LIGHTBOX: {bool(re.search(r'lightbox', html, re.I))}")
    print(f"  HAS_DATA_IMAGE_INFO: {bool(re.search(r'data-image-info', html, re.I))}")

    # OG
    og_t = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']*)', html, re.I)
    print(f"  OG_TITLE: {(og_t.group(1)[:100] if og_t else 'NONE')}")

    # Проверка: это SPA или серверный рендер?
    print(f"  HAS_NG_APP: {bool(re.search(r'ng-app|ng-controller', html, re.I))}")
    print(f"  HAS_REACT: {bool(re.search(r'__NEXT_DATA__|__react|data-react', html, re.I))}")
    print(f"  HAS_VUE: {bool(re.search(r'__vue|data-vue', html, re.I))}")
    print(f"  HAS_JQ: {bool(re.search(r'jquery|jQuery', html, re.I))}")

    # Первые 300 символов body
    body_m = re.search(r'<body[^>]*>(.{0,300})', html, re.DOTALL | re.I)
    if body_m:
        snippet = re.sub(r'\s+', ' ', body_m.group(1))[:250]
        print(f"  BODY_START: {snippet}")

    # data-field="price"
    price_field = re.search(r'data-field=["\']price["\'][^>]*>(.*?)</', html, re.DOTALL | re.I)
    print(f"  PRICE_FIELD: {(price_field.group(1).strip()[:50] if price_field else 'NONE')}")

    # viewbull-summary-price
    vsp = re.search(r'viewbull-summary-price__value[^>]*>(.*?)</', html, re.DOTALL | re.I)
    print(f"  VIEWBULL_PRICE: {(vsp.group(1).strip()[:50] if vsp else 'NONE')}")

    time.sleep(2)

print(f"\n{'='*70}")
print(f"Stats: {c.stats}")
print(f"Done. HTML files in: {OUT}")

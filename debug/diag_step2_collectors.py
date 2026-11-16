"""Диагностика step2: OZON Composer API + FARPOST валидный товар + антибот-детект.

Проверяет:
1. OZON Composer API для product 286156832 (обход антибота)
2. OZON parse_page с сохранённым антибот-HTML (должен вернуть None)
3. FARPOST: поиск валидного товара через discovery + collect_product
4. FARPOST parse_page с 404-HTML (должен вернуть None)
"""
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_OUT_DIR = Path(__file__).parent / "diag_step2_output"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            _OUT_DIR / "run.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("diag.step2")

OUT = Path(__file__).parent / "diag_step2_output"
OUT.mkdir(parents=True, exist_ok=True)

report = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "tests": []}


def add_test(source, name, data):
    report["tests"].append({"source": source, "name": name, **data})
    log.info("[TEST] %s / %s: %s", source, name, json.dumps(data, ensure_ascii=False)[:200])


# ============================================================
# TEST 1: OZON Composer API — product 286156832
# ============================================================
log.info("=" * 60)
log.info("TEST 1: OZON Composer API (286156832)")
log.info("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)
    result = col._try_composer_api("286156832", "https://www.ozon.ru/product/286156832/", "")
    if result:
        obs = result["observation"]
        add_test("OZON", "composer_api", {
            "status": "SUCCESS",
            "title": obs.get("fields", {}).get("title", {}).get("value", "?")[:80],
            "prices": len(obs.get("price_candidates", [])),
            "price_detail": [
                {"role": p.get("role"), "amount": p.get("amount"), "currency": p.get("currency")}
                for p in obs.get("price_candidates", [])[:5]
            ],
            "media_count": len(obs.get("media", {}).get("items", [])),
            "availability": obs.get("availability"),
            "evidence_level": obs.get("evidence_level"),
        })
    else:
        add_test("OZON", "composer_api", {"status": "NONE", "detail": "all endpoints failed"})
    col.close()
except Exception as e:
    add_test("OZON", "composer_api", {"status": "ERROR", "error": str(e)})

# ============================================================
# TEST 2: OZON parse_page с антибот-HTML (должен вернуть None)
# ============================================================
log.info("=" * 60)
log.info("TEST 2: OZON parse_page anti-bot detection")
log.info("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)

    # Загружаем сохранённый антибот-HTML
    ab_path = Path(__file__).parent / "diag_live_output" / "ozon_286156832_raw.html"
    ab_html = ab_path.read_text(encoding="utf-8") if ab_path.exists() else ""

    class _FakeResp:
        def __init__(self, text, status=200):
            self.text = text
            self.status_code = status

    # 2a: Антибот-HTML → должен вернуть None
    result_ab = col.parse_page(_FakeResp(ab_html), "286156832", "https://www.ozon.ru/product/286156832/", "")
    add_test("OZON", "parse_anti_bot", {
        "input_len": len(ab_html),
        "result": "NONE" if result_ab is None else f"UNEXPECTED: title={result_ab.get('fields',{}).get('title',{}).get('value','?')[:40]}",
        "pass": result_ab is None,
    })

    # 2b: Короткий HTML → None
    result_short = col.parse_page(_FakeResp("short"), "123", "http://x", "")
    add_test("OZON", "parse_short", {
        "result": "NONE" if result_short is None else "UNEXPECTED",
        "pass": result_short is None,
    })

    col.close()
except Exception as e:
    add_test("OZON", "parse_anti_bot", {"status": "ERROR", "error": str(e)})

# ============================================================
# TEST 3: FARPOST — discovery + collect_product
# ============================================================
log.info("=" * 60)
log.info("TEST 3: FARPOST discovery + collect_product")
log.info("=" * 60)

try:
    from r52.collectors.farpost import FarpostCollector
    fcol = FarpostCollector(data_dir=None, use_proxies=False)

    # 3a: Discovery — поиск "смартфоны"
    log.info("FARPOST discovery: query='смартфоны'")
    tiles = fcol.search("смартфоны", page=1, limit=5)
    if tiles:
        t = tiles[0]
        add_test("FARPOST", "discovery", {
            "query": "смартфоны",
            "tiles_found": len(tiles),
            "first_tile": {
                "id": t.get("id", "?"),
                "title": t.get("title", "?")[:60],
                "url": t.get("url", "?")[:80],
            },
        })
        # 3b: collect_product для первого найденного
        lid = t.get("id", "")
        url = t.get("url", "") or fcol.build_url(lid)
        log.info("FARPOST collect_product: id=%s url=%s", lid, url)
        result = fcol.collect_product(lid, url, t.get("title", ""))
        if result:
            obs = result["observation"]
            add_test("FARPOST", "collect_product", {
                "id": lid,
                "status": obs.get("status"),
                "title": obs.get("fields", {}).get("title", {}).get("value", "?")[:60],
                "prices": len(obs.get("price_candidates", [])),
                "price_detail": [
                    {"role": p.get("role"), "amount": p.get("amount"), "currency": p.get("currency")}
                    for p in obs.get("price_candidates", [])[:3]
                ],
                "media_count": len(obs.get("media", {}).get("items", [])),
                "evidence_level": obs.get("evidence_level"),
            })
        else:
            add_test("FARPOST", "collect_product", {"id": lid, "status": "NONE"})
    else:
        add_test("FARPOST", "discovery", {"query": "смартфоны", "tiles_found": 0})

    # 3c: parse_page с 404-HTML
    fp404_path = Path(__file__).parent / "diag_live_output" / "farpost_287654321_raw.html"
    fp404_html = fp404_path.read_text(encoding="windows-1251", errors="replace") if fp404_path.exists() else ""

    class _FakeResp2:
        def __init__(self, content, headers=None):
            self.content = content.encode("windows-1251") if isinstance(content, str) else content
            self.text = content
            self.status_code = 200
            self.headers = headers or {"Content-Type": "text/html; charset=windows-1251"}

    result_404 = fcol.parse_page(_FakeResp2(fp404_html), "287654321", "https://www.farpost.ru/vladivostok/287654321.html", "")
    add_test("FARPOST", "parse_404", {
        "input_len": len(fp404_html),
        "result": "NONE" if result_404 is None else f"UNEXPECTED: title={result_404.get('fields',{}).get('title',{}).get('value','?')[:40]}",
        "pass": result_404 is None,
    })

    fcol.close()
except Exception as e:
    import traceback
    add_test("FARPOST", "discovery_collect", {"status": "ERROR", "error": str(e), "trace": traceback.format_exc()[-500:]})

# ============================================================
# TEST 4: OZON discovery (через search)
# ============================================================
log.info("=" * 60)
log.info("TEST 4: OZON discovery")
log.info("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    ocol = OzonCollector(data_dir=None, use_proxies=False)
    tiles = ocol.search("смартфоны", page=1, limit=5)
    if tiles:
        t = tiles[0]
        add_test("OZON", "discovery", {
            "query": "смартфоны",
            "tiles_found": len(tiles),
            "first_tile": {
                "id": t.get("id", "?"),
                "title": t.get("title", "?")[:60],
                "url": t.get("url", "?")[:80],
            },
        })
        # collect_product для первого
        lid = t.get("id", "")
        url = t.get("url", "") or ocol.build_url(lid)
        log.info("OZON collect_product: id=%s url=%s", lid, url)
        result = ocol.collect_product(lid, url, t.get("title", ""))
        if result:
            obs = result["observation"]
            add_test("OZON", "collect_product", {
                "id": lid,
                "status": obs.get("status"),
                "title": obs.get("fields", {}).get("title", {}).get("value", "?")[:60],
                "prices": len(obs.get("price_candidates", [])),
                "price_detail": [
                    {"role": p.get("role"), "amount": p.get("amount"), "currency": p.get("currency")}
                    for p in obs.get("price_candidates", [])[:3]
                ],
                "media_count": len(obs.get("media", {}).get("items", [])),
                "evidence_level": obs.get("evidence_level"),
            })
        else:
            add_test("OZON", "collect_product", {"id": lid, "status": "NONE"})
    else:
        add_test("OZON", "discovery", {"query": "смартфоны", "tiles_found": 0})
    ocol.close()
except Exception as e:
    import traceback
    add_test("OZON", "discovery_collect", {"status": "ERROR", "error": str(e), "trace": traceback.format_exc()[-500:]})

# ============================================================
# Сохранение отчёта
# ============================================================
report_path = OUT / "diag_step2_report.json"
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Report saved: %s", report_path)

# Сводка
print("\n" + "=" * 60)
print("СВОДКА ДИАГНОСТИКИ STEP2")
print("=" * 60)
for t in report["tests"]:
    src = t["source"]
    name = t["name"]
    status = t.get("status", t.get("result", "?"))
    extra = ""
    if "title" in t:
        extra = f" title={str(t['title'])[:40]}"
    if "prices" in t:
        extra += f" prices={t['prices']}"
    if "pass" in t:
        extra += f" PASS={t['pass']}"
    print(f"  [{src}] {name}: {status}{extra}")
print("=" * 60)

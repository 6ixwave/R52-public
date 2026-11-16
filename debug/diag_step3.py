"""Диагностика step3: OZON Composer API + collect_product + FARPOST collect_product.

Фокус:
1. OZON Composer API (SSL fix)
2. OZON collect_product (полный цикл: composer → PW → HTTP)
3. FARPOST collect_product с валидным ID
4. FARPOST discovery через collect_links
5. Проверка anti-bot / 404 детекта
"""
import json
import logging
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_OUT_DIR = Path(__file__).parent / "diag_step3_output"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_OUT_DIR / "run.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("diag.step3")

report = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "tests": []}


def add_test(source, name, **data):
    report["tests"].append({"source": source, "name": name, **data})
    log.info("[TEST] %s/%s: %s", source, name, json.dumps(data, ensure_ascii=False)[:300])


# ============================================================
# TEST 1: OZON Composer API
# ============================================================
log.info("=" * 60)
log.info("TEST 1: OZON Composer API (286156832)")
log.info("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)
    t0 = time.time()
    result = col._try_composer_api("286156832", "https://www.ozon.ru/product/286156832/", "")
    elapsed = time.time() - t0
    if result:
        obs = result["observation"]
        add_test("OZON", "composer_api",
                 status="SUCCESS",
                 elapsed_s=round(elapsed, 2),
                 title=str(obs.get("fields", {}).get("title", {}).get("value", "?"))[:80],
                 prices=len(obs.get("price_candidates", [])),
                 price_detail=[
                     {"role": p.get("role"), "amount": p.get("amount"), "currency": p.get("currency")}
                     for p in obs.get("price_candidates", [])[:5]
                 ],
                 media_count=len(obs.get("media", {}).get("items", [])),
                 availability=obs.get("availability"),
                 evidence=obs.get("evidence_level"),
        )
    else:
        add_test("OZON", "composer_api", status="NONE", elapsed_s=round(elapsed, 2),
                 detail="all endpoints failed or no data")
    col.close()
except Exception as e:
    add_test("OZON", "composer_api", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-400:])

# ============================================================
# TEST 2: OZON collect_product (full: composer → PW → HTTP)
# ============================================================
log.info("=" * 60)
log.info("TEST 2: OZON collect_product (full pipeline)")
log.info("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)
    t0 = time.time()
    result = col.collect_product("286156832", "https://www.ozon.ru/product/286156832/", "Test")
    elapsed = time.time() - t0
    if result:
        obs = result["observation"]
        add_test("OZON", "collect_product",
                 status="SUCCESS",
                 elapsed_s=round(elapsed, 2),
                 obs_status=obs.get("status"),
                 title=str(obs.get("fields", {}).get("title", {}).get("value", "?"))[:80],
                 prices=len(obs.get("price_candidates", [])),
                 price_detail=[
                     {"role": p.get("role"), "amount": p.get("amount"), "currency": p.get("currency")}
                     for p in obs.get("price_candidates", [])[:3]
                 ],
                 media_count=len(obs.get("media", {}).get("items", [])),
                 evidence=obs.get("evidence_level"),
        )
    else:
        add_test("OZON", "collect_product", status="NONE", elapsed_s=round(elapsed, 2))
    col.close()
except Exception as e:
    add_test("OZON", "collect_product", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-400:])

# ============================================================
# TEST 3: OZON anti-bot detection (saved HTML)
# ============================================================
log.info("=" * 60)
log.info("TEST 3: OZON anti-bot / 404 detection")
log.info("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)

    class _FakeResp:
        def __init__(self, text, status=200):
            self.text = text
            self.status_code = status

    # 3a: Anti-bot
    ab_path = Path(__file__).parent / "diag_live_output" / "ozon_286156832_raw.html"
    ab_html = ab_path.read_text(encoding="utf-8") if ab_path.exists() else ""
    r_ab = col.parse_page(_FakeResp(ab_html), "286156832", "https://www.ozon.ru/product/286156832/", "")
    add_test("OZON", "anti_bot_detect",
             input_len=len(ab_html),
             result="NONE" if r_ab is None else "UNEXPECTED",
             pass_=r_ab is None)

    # 3b: 404 widget
    html_404 = '<html><body><div data-widget="error">Такой страницы не существует</div></body></html>' * 20
    r_404 = col.parse_page(_FakeResp(html_404), "999", "http://x", "")
    add_test("OZON", "404_detect",
             result="NONE" if r_404 is None else "UNEXPECTED",
             pass_=r_404 is None)

    col.close()
except Exception as e:
    add_test("OZON", "anti_bot_detect", status="ERROR", error=str(e))

# ============================================================
# TEST 4: FARPOST collect_product
# ============================================================
log.info("=" * 60)
log.info("TEST 4: FARPOST collect_product")
log.info("=" * 60)

try:
    from r52.collectors.farpost import FarpostCollector
    fcol = FarpostCollector(data_dir=None, use_proxies=False)

    # Попробуем несколько ID
    test_ids = ["139122082", "200000001", "150000001"]
    for lid in test_ids:
        url = fcol.build_url(lid)
        log.info("FARPOST try: id=%s url=%s", lid, url)
        t0 = time.time()
        result = fcol.collect_product(lid, url, "")
        elapsed = time.time() - t0
        if result:
            obs = result["observation"]
            add_test("FARPOST", f"collect_{lid}",
                     status="SUCCESS",
                     elapsed_s=round(elapsed, 2),
                     obs_status=obs.get("status"),
                     title=str(obs.get("fields", {}).get("title", {}).get("value", "?"))[:60],
                     prices=len(obs.get("price_candidates", [])),
                     price_detail=[
                         {"role": p.get("role"), "amount": p.get("amount"), "currency": p.get("currency")}
                         for p in obs.get("price_candidates", [])[:3]
                     ],
                     media_count=len(obs.get("media", {}).get("items", [])),
                     evidence=obs.get("evidence_level"),
            )
            break  # Нашли валидный — дальше не идём
        else:
            add_test("FARPOST", f"collect_{lid}", status="NONE", elapsed_s=round(elapsed, 2))
    else:
        add_test("FARPOST", "collect_all", status="ALL_NONE",
                 detail="all test IDs returned None")

    fcol.close()
except Exception as e:
    add_test("FARPOST", "collect_product", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-400:])

# ============================================================
# TEST 5: FARPOST 404 detection
# ============================================================
log.info("=" * 60)
log.info("TEST 5: FARPOST 404 detection")
log.info("=" * 60)

try:
    from r52.collectors.farpost import FarpostCollector
    fcol = FarpostCollector(data_dir=None, use_proxies=False)

    class _FakeResp2:
        def __init__(self, content, headers=None):
            self.content = content.encode("windows-1251", errors="replace")
            self.text = content
            self.status_code = 200
            self.headers = headers or {"Content-Type": "text/html; charset=windows-1251"}

    # 5a: 404 page
    html_404 = '<html><head><title>Страница не найдена</title></head><body><h1>Страница не найдена</h1></body></html>' * 20
    r_404 = fcol.parse_page(_FakeResp2(html_404), "999", "http://x", "")
    add_test("FARPOST", "404_detect",
             result="NONE" if r_404 is None else "UNEXPECTED",
             pass_=r_404 is None)

    # 5b: Short page
    r_short = fcol.parse_page(_FakeResp2("short"), "111", "http://x", "")
    add_test("FARPOST", "short_detect",
             result="NONE" if r_short is None else "UNEXPECTED",
             pass_=r_short is None)

    fcol.close()
except Exception as e:
    add_test("FARPOST", "404_detect", status="ERROR", error=str(e))

# ============================================================
# TEST 6: FARPOST discovery via collect_links
# ============================================================
log.info("=" * 60)
log.info("TEST 6: FARPOST discovery (collect_links)")
log.info("=" * 60)

try:
    from r52.collectors.farpost import FarpostCollector
    from r52.collectors.discovery import collect_links, SearchError
    fcol = FarpostCollector(data_dir=None, use_proxies=False)

    # Farpost search URL
    search_url = "https://www.farpost.ru/vladivostok/search/?q=смартфоны"
    log.info("FARPOST discovery: %s", search_url)
    t0 = time.time()
    items = collect_links(fcol, "FARPOST", search_url, limit=3, page=1)
    elapsed = time.time() - t0
    if items:
        first = items[0]
        obs = first.get("observation", {})
        add_test("FARPOST", "discovery",
                 status="SUCCESS",
                 elapsed_s=round(elapsed, 2),
                 items_found=len(items),
                 first_id=obs.get("id", "?"),
                 first_title=str(obs.get("fields", {}).get("title", {}).get("value", "?"))[:60],
                 first_prices=len(obs.get("price_candidates", [])),
        )
    else:
        add_test("FARPOST", "discovery", status="EMPTY", elapsed_s=round(elapsed, 2))
    fcol.close()
except SearchError as e:
    add_test("FARPOST", "discovery", status="SEARCH_ERROR", error=str(e))
except Exception as e:
    add_test("FARPOST", "discovery", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-400:])

# ============================================================
# Сохранение
# ============================================================
report_path = _OUT_DIR / "diag_step3_report.json"
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Report: %s", report_path)

print("\n" + "=" * 60)
print("СВОДКА STEP3")
print("=" * 60)
for t in report["tests"]:
    src = t["source"]
    name = t["name"]
    status = t.get("status", t.get("result", "?"))
    extra = ""
    if "title" in t:
        extra += f" title={str(t['title'])[:40]}"
    if "prices" in t:
        extra += f" prices={t['prices']}"
    if "pass_" in t:
        extra += f" PASS={t['pass_']}"
    if "elapsed_s" in t:
        extra += f" ({t['elapsed_s']}s)"
    print(f"  [{src}] {name}: {status}{extra}")
print("=" * 60)

"""Диагностика step5:
1. FARPOST discovery по КАТЕГОРИИ (рабочий URL /vladivostok/auto/wheel/tire/)
2. FARPOST collect_product по найденным ID
3. OZON: graceful handling — что возвращает collect_product при полном 403
4. OZON: проверка ID из auto_target (если есть)
5. Проверка intake-маршрута: JSON → folder.py → eye.py
"""
import json
import logging
import sqlite3
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_OUT_DIR = Path(__file__).parent / "diag_step5_output"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_OUT_DIR / "run.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("diag.step5")

report = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "tests": []}


def add_test(source, name, **data):
    report["tests"].append({"source": source, "name": name, **data})
    log.info("[TEST] %s/%s: %s", source, name, json.dumps(data, ensure_ascii=False)[:400])


# ============================================================
# TEST 1: FARPOST discovery by CATEGORY
# ============================================================
log.info("=" * 60)
log.info("TEST 1: FARPOST discovery by category")
log.info("=" * 60)

try:
    from r52.collectors.farpost import FarpostCollector
    from r52.collectors.discovery import collect_links, SearchError

    fcol = FarpostCollector(data_dir=None, use_proxies=False)
    fcol._warmup()

    # Рабочий URL категории (подтверждён step4: 200, 416KB)
    cat_url = "https://www.farpost.ru/vladivostok/auto/wheel/tire/"
    log.info("FARPOST category: %s", cat_url)
    t0 = time.time()
    items = collect_links(fcol, "FARPOST", cat_url, limit=3, page=1)
    elapsed = time.time() - t0

    if items:
        for i, item in enumerate(items):
            obs = item.get("observation", {})
            add_test("FARPOST", f"discovery_cat_item{i}",
                     status="SUCCESS",
                     id=obs.get("id", "?"),
                     title=str(obs.get("fields", {}).get("title", {}).get("value", "?"))[:60],
                     obs_status=obs.get("status"),
                     prices=len(obs.get("price_candidates", [])),
                     price_detail=[
                         {"role": p.get("role"), "amount": p.get("amount"), "currency": p.get("currency")}
                         for p in obs.get("price_candidates", [])[:3]
                     ],
                     media_count=len(obs.get("media", {}).get("items", [])),
                     evidence=obs.get("evidence_level"),
            )
    else:
        add_test("FARPOST", "discovery_cat", status="EMPTY",
                 elapsed_s=round(elapsed, 2),
                 detail="collect_links returned empty list")

    # Также проверим raw fetch + parse для понимания
    log.info("FARPOST: raw fetch of category for link extraction")
    resp = fcol.session.get(cat_url, timeout=(10, 20),
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"})
    # Extract bulletin links manually
    import re
    links = re.findall(r'href="(https?://www\.farpost\.ru/[^"]*?(\d{6,})\.html)"', resp.text)
    unique_ids = list(dict.fromkeys(lid for _, lid in links))[:10]
    add_test("FARPOST", "raw_links",
             status="INFO",
             http_status=resp.status_code,
             page_len=len(resp.text),
             total_links=len(links),
             unique_ids=unique_ids,
    )
    fcol.close()
except SearchError as e:
    add_test("FARPOST", "discovery_cat", status="SEARCH_ERROR", error=str(e))
except Exception as e:
    add_test("FARPOST", "discovery_cat", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-500:])

# ============================================================
# TEST 2: OZON graceful handling (full 403 pipeline)
# ============================================================
log.info("=" * 60)
log.info("TEST 2: OZON graceful handling (all 403)")
log.info("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)

    # Используем ID из auto_target если есть, иначе тестовый
    ozon_ids = []
    db_path = Path(__file__).parent.parent / "data" / "test_home" / "data" / "r52.sqlite3"
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Сначала узнаем колонки
        cur.execute("PRAGMA table_info(auto_target)")
        cols = [r[1] for r in cur.fetchall()]
        log.info("auto_target columns: %s", cols)
        # Адаптивный SELECT
        if "target_id" in cols:
            id_col = "target_id"
        elif "id" in cols:
            id_col = "id"
        else:
            id_col = cols[1] if len(cols) > 1 else "target_id"
        cur.execute(f"SELECT {id_col}, value FROM auto_target WHERE source='OZON' AND enabled=1 LIMIT 3")
        rows = cur.fetchall()
        for r in rows:
            ozon_ids.append({
                "id": str(r[0]),
                "url": str(r[1]) if r[1] else f"https://www.ozon.ru/product/{r[0]}/"
            })
        conn.close()
        add_test("OZON", "db_ids", status="INFO",
                 count=len(ozon_ids), ids=[o["id"] for o in ozon_ids])
    else:
        ozon_ids = [{"id": "286156832", "url": "https://www.ozon.ru/product/286156832/"}]

    # Тест: collect_product должен вернуть None (не crash) при полном 403
    test_id = ozon_ids[0]["id"] if ozon_ids else "286156832"
    test_url = ozon_ids[0]["url"] if ozon_ids else f"https://www.ozon.ru/product/{test_id}/"
    log.info("OZON test: id=%s url=%s", test_id, test_url)

    t0 = time.time()
    try:
        result = col.collect_product(test_id, test_url, "")
        elapsed = time.time() - t0
        if result:
            obs = result.get("observation", {})
            add_test("OZON", "collect_graceful",
                     status="RETURNED",
                     obs_status=obs.get("status"),
                     title=str(obs.get("fields", {}).get("title", {}).get("value", "?"))[:50],
                     prices=len(obs.get("price_candidates", [])),
                     elapsed_s=round(elapsed, 2),
            )
        else:
            add_test("OZON", "collect_graceful",
                     status="NONE_OK",
                     detail="collect_product returned None (graceful, no crash)",
                     elapsed_s=round(elapsed, 2),
            )
    except Exception as e:
        add_test("OZON", "collect_graceful",
                 status="CRASH",
                 error=str(e),
                 trace=traceback.format_exc()[-300:],
                 elapsed_s=round(time.time() - t0, 2),
        )
    col.close()
except Exception as e:
    add_test("OZON", "collect_graceful", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-300:])

# ============================================================
# TEST 3: OZON — try different product (maybe not all are 403)
# ============================================================
log.info("=" * 60)
log.info("TEST 3: OZON different product IDs")
log.info("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)

    # Пробуем 2 разных ID (если есть из БД)
    test_ids = [o["id"] for o in ozon_ids[:2]] or ["286156832", "1700000001"]
    for tid in test_ids:
        turl = f"https://www.ozon.ru/product/{tid}/"
        t0 = time.time()
        # Быстрый check: только cffi HTTP (без PW, чтобы не ждать 30с)
        if col._cffi:
            resp = col._cffi.get(turl, impersonate="chrome", timeout=15,
                                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                                           "Accept-Language": "ru-RU,ru;q=0.9"},
                                  verify=False)
            elapsed = time.time() - t0
            is_anti = "fab_chlg" in (resp.text or "")[:3000] or "Antibot" in (resp.text or "")[:3000]
            add_test("OZON", f"http_{tid}",
                     status=resp.status_code,
                     len=len(resp.text) if resp.text else 0,
                     is_anti=is_anti,
                     elapsed_s=round(elapsed, 2),
            )
        time.sleep(3)
    col.close()
except Exception as e:
    add_test("OZON", "http_ids", status="ERROR", error=str(e))

# ============================================================
# TEST 4: Intake pipeline check (JSON format validation)
# ============================================================
log.info("=" * 60)
log.info("TEST 4: Intake pipeline (eye.py validation)")
log.info("=" * 60)

try:
    from r52.intake.eye import parse_export_bytes, check_export
    import tempfile, os

    # Создаём тестовый JSON в формате JARVIS_BR02_COLLECTOR_AUTONOMOUS
    test_json = {
        "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
        "capture_action": "COLLECTOR_RUN",
        "fully_autonomous": True,
        "collector_source": "FARPOST",
        "observation": {
            "source": "FARPOST",
            "id": "139122082",
            "url": "https://www.farpost.ru/vladivostok/139122082.html",
            "observed_at": "2026-11-14T15:10:00+10:00",
            "status": "FULL_SUCCESS",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
            "fields": {
                "title": {"status": "KNOWN", "value": "Шины LingLong GREEN-Max Winter Grip Van 2 205/75 R16", "method": "SSR"},
            },
            "price_candidates": [
                {"role": "regular", "amount": "8499.00", "currency": "RUB", "amount_minor": 849900}
            ],
            "media": {"items": [{"type": "image", "url": "https://static.baza.farpost.ru/img/139122082_bulletin.jpg"}]},
            "availability": "IN_STOCK",
        }
    }

    # Save to temp file
    tmp = _OUT_DIR / "test_intake.json"
    tmp.write_text(json.dumps(test_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # Try validate
    try:
        doc = parse_export_bytes(tmp.read_bytes())
        result = check_export(doc)
        add_test("INTAKE", "validate_autonomous",
                 status="OK" if result else "REJECTED",
                 detail=str(result)[:200] if result else "validate returned None/False",
        )
    except Exception as ve:
        add_test("INTAKE", "validate_autonomous",
                 status="EXCEPTION",
                 error=str(ve),
                 trace=traceback.format_exc()[-300:],
        )

    # Также проверим OZON format (BLOCKED)
    test_json_ozon = {
        "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
        "capture_action": "COLLECTOR_RUN",
        "fully_autonomous": True,
        "collector_source": "OZON",
        "observation": {
            "source": "OZON",
            "id": "286156832",
            "url": "https://www.ozon.ru/product/286156832/",
            "observed_at": "2026-11-14T15:10:00+10:00",
            "status": "BLOCKED",
            "product_content": False,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 403,
            "fields": {},
            "price_candidates": [],
            "media": {"items": []},
            "availability": "UNKNOWN",
        }
    }
    tmp2 = _OUT_DIR / "test_intake_ozon_blocked.json"
    tmp2.write_text(json.dumps(test_json_ozon, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        doc2 = parse_export_bytes(tmp2.read_bytes())
        result2 = check_export(doc2)
        add_test("INTAKE", "validate_ozon_blocked",
                 status="OK" if result2 else "REJECTED",
                 detail=str(result2)[:200] if result2 else "validate returned None/False",
        )
    except Exception as ve:
        add_test("INTAKE", "validate_ozon_blocked",
                 status="EXCEPTION",
                 error=str(ve),
        )
except ImportError as e:
    add_test("INTAKE", "import", status="IMPORT_ERROR", error=str(e))
except Exception as e:
    add_test("INTAKE", "validate", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-300:])

# ============================================================
# Save
# ============================================================
report_path = _OUT_DIR / "diag_step5_report.json"
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Report: %s", report_path)

print("\n" + "=" * 60)
print("СВОДКА STEP5")
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
    if "id" in t:
        extra += f" id={t['id']}"
    if "unique_ids" in t:
        extra += f" ids={t['unique_ids'][:5]}"
    if "elapsed_s" in t:
        extra += f" ({t['elapsed_s']}s)"
    if "detail" in t:
        extra += f" {str(t['detail'])[:60]}"
    if "error" in t:
        extra += f" ERR={str(t['error'])[:60]}"
    print(f"  [{src}] {name}: {status}{extra}")
print("=" * 60)

"""Диагностика step4:
1. FARPOST search URL — точный HTTP-статус и тело
2. OZON — мобильный API + альтернативные эндпоинты
3. БД auto_target — какие цели реально сконфигурированы
4. OZON — Playwright с задержкой и scroll (обход антибота)
"""
import json
import logging
import sqlite3
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_OUT_DIR = Path(__file__).parent / "diag_step4_output"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_OUT_DIR / "run.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("diag.step4")

report = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "tests": []}


def add_test(source, name, **data):
    report["tests"].append({"source": source, "name": name, **data})
    log.info("[TEST] %s/%s: %s", source, name, json.dumps(data, ensure_ascii=False)[:400])


# ============================================================
# TEST 1: FARPOST search URL — raw HTTP
# ============================================================
log.info("=" * 60)
log.info("TEST 1: FARPOST search URL raw HTTP")
log.info("=" * 60)

try:
    from r52.collectors.farpost import FarpostCollector
    fcol = FarpostCollector(data_dir=None, use_proxies=False)
    fcol._warmup()

    search_urls = [
        "https://www.farpost.ru/vladivostok/search/?q=смартфоны",
        "https://www.farpost.ru/search/?q=смартфоны",
        "https://www.farpost.ru/vladivostok/catalog/?q=смартфоны",
        "https://www.farpost.ru/vladivostok/auto/wheel/tire/",
    ]
    for su in search_urls:
        t0 = time.time()
        try:
            resp = fcol.session.get(su, timeout=(10, 20),
                                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                                             "Accept-Language": "ru-RU,ru;q=0.9"})
            elapsed = time.time() - t0
            body_preview = resp.text[:300].replace("\n", " ") if resp.text else ""
            add_test("FARPOST", f"search_{su.split('?')[0].split('/')[-1] or 'root'}",
                     url=su,
                     status=resp.status_code,
                     final_url=str(resp.url)[:120],
                     len=len(resp.text) if resp.text else 0,
                     preview=body_preview[:200],
                     elapsed_s=round(elapsed, 2),
                     has_links="<a" in (resp.text or "")[:5000],
                     link_count=(resp.text or "").count("<a ") if resp.text else 0,
            )
        except Exception as e:
            add_test("FARPOST", f"search_{su.split('?')[0].split('/')[-1] or 'root'}",
                     url=su, status="EXCEPTION", error=str(e),
                     elapsed_s=round(time.time() - t0, 2))
        time.sleep(2)
    fcol.close()
except Exception as e:
    add_test("FARPOST", "search_raw", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-400:])

# ============================================================
# TEST 2: OZON mobile / alternative APIs
# ============================================================
log.info("=" * 60)
log.info("TEST 2: OZON mobile / alternative APIs")
log.info("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    has_cffi = True
except ImportError:
    has_cffi = False
    import requests as cffi_req

    class _Wrap:
        def __init__(self, r):
            self.status_code = r.status_code
            self.text = r.text
            self.json = r.json
    cffi_req.get = lambda url, **kw: _Wrap(requests.get(url, **{k: v for k, v in kw.items() if k != 'impersonate'}))

    has_cffi = False

OZON_ID = "286156832"
ozon_endpoints = [
    # Mobile web
    f"https://m.ozon.ru/product/{OZON_ID}/",
    # Composer v2 with mobile param
    f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/&isMobile=true",
    # Entry point
    f"https://www.ozon.ru/api/entrypoint/pdp/{OZON_ID}",
    # Mobile API
    f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/m/product/{OZON_ID}/",
    # Direct product API (older)
    f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/",
    # Mobile PDP
    f"https://m.ozon.ru/api/entrypoint/pdp/{OZON_ID}",
]

headers_common = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.ozon.ru/",
}

for ep in ozon_endpoints:
    name = ep.replace("https://", "").split("?")[0][:60]
    t0 = time.time()
    try:
        if has_cffi:
            resp = cffi_req.get(ep, impersonate="chrome", timeout=15,
                                headers=headers_common, verify=False)
        else:
            resp = cffi_req.get(ep, timeout=15, headers=headers_common)
        elapsed = time.time() - t0
        body = resp.text[:200].replace("\n", " ") if hasattr(resp, 'text') and resp.text else ""
        is_json = False
        try:
            data = json.loads(resp.text) if hasattr(resp, 'text') else None
            is_json = isinstance(data, dict)
        except Exception:
            pass
        add_test("OZON", f"api_{name}",
                 status=resp.status_code,
                 len=len(resp.text) if hasattr(resp, 'text') and resp.text else 0,
                 is_json=is_json,
                 preview=body[:150],
                 elapsed_s=round(elapsed, 2),
        )
    except Exception as e:
        add_test("OZON", f"api_{name}", status="EXC", error=str(e)[:100],
                 elapsed_s=round(time.time() - t0, 2))
    time.sleep(3)

# ============================================================
# TEST 3: OZON Playwright with longer wait + scroll
# ============================================================
log.info("=" * 60)
log.info("TEST 3: OZON Playwright (longer wait + scroll)")
log.info("=" * 60)

try:
    from r52.collectors.pw_fetch import PwFetcher
    pw = PwFetcher(headless=True)
    pw.start()
    t0 = time.time()
    result = pw.get(f"https://www.ozon.ru/product/{OZON_ID}/", extra_wait_ms=8000)
    elapsed = time.time() - t0
    if result:
        # Save HTML for inspection
        html_path = _OUT_DIR / "ozon_pw.html"
        html_path.write_text(result.html, encoding="utf-8")
        has_title = "<title>" in result.html or "og:title" in result.html
        has_price = "price" in result.html.lower()[:10000]
        has_anti = "abt-challenge" in result.html or "Antibot" in result.html
        add_test("OZON", "pw_long_wait",
                 status=result.status,
                 len=len(result.html),
                 has_title=has_title,
                 has_price=has_price,
                 has_anti=has_anti,
                 title=result.title[:80] if result.title else "",
                 elapsed_s=round(elapsed, 2),
        )
    else:
        add_test("OZON", "pw_long_wait", status="NONE", elapsed_s=round(elapsed, 2))
    pw.stop()
except Exception as e:
    add_test("OZON", "pw_long_wait", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-300:])

# ============================================================
# TEST 4: DB auto_target — configured targets
# ============================================================
log.info("=" * 60)
log.info("TEST 4: DB auto_target")
log.info("=" * 60)

try:
    db_path = Path(__file__).parent.parent / "data" / "test_home" / "data" / "r52.sqlite3"
    if not db_path.exists():
        # Try other locations
        for alt in [
            Path(__file__).parent.parent / "data" / "r52.sqlite3",
            Path.home() / ".local" / "share" / "r52" / "data" / "r52.sqlite3",
        ]:
            if alt.exists():
                db_path = alt
                break
    log.info("DB: %s (exists=%s)", db_path, db_path.exists())
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # auto_target
        cur.execute("SELECT COUNT(*) as cnt FROM auto_target WHERE enabled=1")
        total = cur.fetchone()["cnt"]
        cur.execute("SELECT source, COUNT(*) as cnt FROM auto_target WHERE enabled=1 GROUP BY source")
        by_src = {r["source"]: r["cnt"] for r in cur.fetchall()}
        cur.execute("SELECT source, listing_id, url, enabled FROM auto_target WHERE enabled=1 LIMIT 20")
        samples = [dict(r) for r in cur.fetchall()]
        add_test("DB", "auto_target",
                 total_enabled=total,
                 by_source=by_src,
                 samples=samples[:10],
        )
        # schema_migrations
        try:
            cur.execute("SELECT * FROM schema_migrations ORDER BY version")
            migrations = [dict(r) for r in cur.fetchall()]
            add_test("DB", "migrations", count=len(migrations),
                     versions=[m.get("version", m) for m in migrations[:15]])
        except Exception as e:
            add_test("DB", "migrations", error=str(e))
        # user_version
        cur.execute("PRAGMA user_version")
        uv = cur.fetchone()[0]
        add_test("DB", "user_version", value=uv)
        conn.close()
    else:
        add_test("DB", "auto_target", status="DB_NOT_FOUND", path=str(db_path))
except Exception as e:
    add_test("DB", "auto_target", status="ERROR", error=str(e),
             trace=traceback.format_exc()[-300:])

# ============================================================
# Save
# ============================================================
report_path = _OUT_DIR / "diag_step4_report.json"
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Report: %s", report_path)

print("\n" + "=" * 60)
print("СВОДКА STEP4")
print("=" * 60)
for t in report["tests"]:
    src = t["source"]
    name = t["name"]
    status = t.get("status", t.get("result", "?"))
    extra = ""
    if "url" in t:
        extra += f" url={str(t['url'])[:50]}"
    if "len" in t:
        extra += f" len={t['len']}"
    if "is_json" in t:
        extra += f" json={t['is_json']}"
    if "has_anti" in t:
        extra += f" anti={t['has_anti']}"
    if "title" in t:
        extra += f" title={str(t['title'])[:40]}"
    if "by_source" in t:
        extra += f" src={t['by_source']}"
    if "total_enabled" in t:
        extra += f" total={t['total_enabled']}"
    if "elapsed_s" in t:
        extra += f" ({t['elapsed_s']}s)"
    print(f"  [{src}] {name}: {status}{extra}")
print("=" * 60)

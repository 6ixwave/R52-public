"""Диагностика V8 (быстрая): DB + 3 сетевых теста. Target <70s."""
import json, os, sys, time, logging, re
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("DIAG")
TZ = timezone(timedelta(hours=10))

def db_counts(db_path):
    p = Path(db_path)
    if not p.exists():
        return {"exists": False}
    import sqlite3
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    r = {"exists": True, "size": p.stat().st_size}
    tables = [x[0] for x in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    r["tables"] = tables
    for t in ("product","observation","price_point","inbox_item"):
        if t in tables:
            c = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
            r[t] = c
    conn.close()
    return r

log.info("=== DIAG V8 FAST START ===")
t0 = time.monotonic()

# 1. Local state
log.info("--- Local ---")
for name in ("e2e_v8_home", "e2e_test_home"):
    h = BASE / name
    log.info("%s: exists=%s", name, h.exists())
    if h.exists():
        for f in sorted(h.rglob("*")):
            if f.is_file():
                log.info("  %s (%d)", f.relative_to(h), f.stat().st_size)

log.info("DB v8: %s", json.dumps(db_counts(BASE / "e2e_v8_home" / "data" / "r52.sqlite3"), default=str))
log.info("DB test: %s", json.dumps(db_counts(BASE / "e2e_test_home" / "data" / "r52.sqlite3"), default=str))

# 2. Import smoke
log.info("--- Imports ---")
try:
    os.environ["R52_HOME"] = str(BASE / "e2e_v8_home")
    from r52.config import Config, Paths
    from r52.db import Database, migrate
    from r52.intake.eye import Inbox
    from r52.sources import REGISTRY, collectors_status
    log.info("OK registry=%s", list(REGISTRY.keys()))
except Exception as e:
    log.error("Import: %s", e, exc_info=True)

# 3. WB basket (1 host, 8s)
log.info("--- WB basket ---")
t1 = time.monotonic()
try:
    from curl_cffi import requests as cffi
    nm = 1097975275
    vol, part = nm // 100000, nm // 1000
    path = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
    H = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}
    r = cffi.get(f"https://basket-1.wbbasket.ru{path}", impersonate="chrome", timeout=8, headers=H)
    log.info("basket-1: HTTP=%d len=%d t=%.1fs", r.status_code, len(r.content), time.monotonic()-t1)
    if r.status_code == 200:
        d = r.json()
        log.info("  title=%s brand=%s opts=%d", d.get("imt_name","")[:50], d.get("selling",{}).get("brand_name",""), len(d.get("options",[])))
except Exception as e:
    log.error("WB basket: %s t=%.1fs", e, time.monotonic()-t1)

# 4. WB price v2 (10s)
log.info("--- WB price v2 ---")
t1 = time.monotonic()
try:
    r = cffi.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}", impersonate="chrome", timeout=10, headers=H)
    log.info("price_v2: HTTP=%d len=%d t=%.1fs", r.status_code, len(r.content), time.monotonic()-t1)
    if r.status_code == 200:
        items = r.json().get("data",{}).get("items",[])
        if items:
            p = items[0].get("price",{})
            log.info("  basic=%s product=%s sale=%s total=%s", p.get("basic"), p.get("product"), p.get("sale"), p.get("total"))
        else:
            log.info("  no items")
except Exception as e:
    log.error("WB price: %s t=%.1fs", e, time.monotonic()-t1)

# 5. Farpost (warmup + card, 20s total)
log.info("--- Farpost ---")
t1 = time.monotonic()
try:
    sess = cffi.Session(impersonate="chrome")
    w = sess.get("https://www.farpost.ru/", timeout=10)
    log.info("FP warm: HTTP=%d t=%.1fs", w.status_code, time.monotonic()-t1)
    time.sleep(1)
    t2 = time.monotonic()
    r = sess.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=15)
    html = r.text
    log.info("FP card: HTTP=%d len=%d url=%s t=%.1fs", r.status_code, len(html), str(r.url)[:60], time.monotonic()-t2)
    if r.status_code == 200 and len(html) > 3000 and "/verify" not in str(r.url):
        lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        log.info("  LD blocks: %d", len(lds))
        for i, ld in enumerate(lds[:3]):
            try:
                o = json.loads(ld.strip())
                log.info("  LD[%d] type=%s", i, o.get("@type","?"))
            except: pass
        pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        log.info("  attr_price=%s", pm.group(1) if pm else "NONE")
        og = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        log.info("  og_price=%s", og.group(1) if og else "NONE")
    elif "/verify" in str(r.url):
        log.info("  /verify redirect!")
except Exception as e:
    log.error("FP: %s t=%.1fs", e, time.monotonic()-t1)

log.info("=== DIAG V8 FAST DONE t=%.1fs ===", time.monotonic()-t0)

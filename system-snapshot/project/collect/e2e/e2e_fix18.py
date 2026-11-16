"""e2e_fix18 — E2E для 4 площадок: Collector → Inbox → SQLite.

Исправления vs fix17:
- Ручная сборка URL (WB/Ozon не имеют build_url в BaseCollector)
- notifier.add_event в mock
- cmd /c для вывода
- Пошаговый traceback
"""
import sys, os, json, time, sqlite3, logging, traceback
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("e2e18")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()
DB_PATH = r"C:\Users\aleks\AppData\Local\R52\data\r52.sqlite3"

# ============================================================
# Mock App
# ============================================================
class _DBCtx:
    def __init__(self, db_path):
        self.db_path = db_path
    def write(self):
        return _Conn(self.db_path)

class _Conn:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
    def execute(self, *a, **kw):
        return self.conn.execute(*a, **kw)
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        if not exc[1]:
            self.conn.commit()
        self.conn.close()

class _Clock:
    def now(self):
        return datetime.now(TZ)

class _Settings:
    def get(self, key, default=None):
        return default

class _Notifier:
    def add_event(self, conn, **kw):
        # Минимальный: создаёт event если таблица есть
        try:
            cur = conn.execute(
                "INSERT INTO event(kind, fact_key, product_id, payload_json, title, body, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (kw.get("kind"), kw.get("fact_key"), kw.get("product_id"),
                 json.dumps(kw.get("payload", {}), ensure_ascii=False, default=str),
                 kw.get("title"), kw.get("body"), NOW))
            return cur.lastrowid
        except Exception:
            return None
    def notify(self, *a, **kw):
        pass

class _App:
    def __init__(self, db_path):
        self.db = _DBCtx(db_path)
        self.clock = _Clock()
        self.settings = _Settings()
        self.notifier = _Notifier()

app = _App(DB_PATH)

# ============================================================
# Imports
# ============================================================
from r52.intake.eye import Inbox
from r52.collectors.wb import WBCollector
from r52.collectors.ozon import OzonCollector
from r52.collectors.farpost import FarpostCollector
from r52.collectors.avito import AvitoCollector

inbox = Inbox(app)
results = {}

# ============================================================
# URL builders (не полагаемся на build_url у WB/Ozon)
# ============================================================
def wb_url(nm_id):
    return f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"

def ozon_url(oz_id):
    return f"https://www.ozon.ru/product/{oz_id}/"

def fp_url(fp_id):
    return f"https://www.farpost.ru/vladivostok/{fp_id}.html"

def avito_url(av_id):
    return f"https://www.avito.ru/ad/{av_id}"

# ============================================================
# E2E runner
# ============================================================
def run_e2e(platform, collector, listing_id, url, title_hint=""):
    t0 = time.monotonic()
    log.info("[%s] URL: %s", platform, url)

    # 1. Collector
    try:
        doc = collector.collect_product(listing_id, url, title_hint)
    except Exception as e:
        log.error("[%s] collect_product exception:\n%s", platform, traceback.format_exc())
        results[platform] = {"verdict": "COLLECT_FAIL", "error": str(e), "elapsed": round(time.monotonic()-t0, 1)}
        return

    if doc is None:
        results[platform] = {"verdict": "COLLECT_NONE", "elapsed": round(time.monotonic()-t0, 1)}
        log.warning("[%s] collect_product returned None", platform)
        return

    obs = doc.get("observation", {})
    title = obs.get("fields", {}).get("title", {}).get("value", "")
    prices = obs.get("price_candidates", [])
    status = obs.get("status", "UNKNOWN")
    media_count = len(obs.get("media", {}).get("items", []))
    log.info("[%s] collect: status=%s title=%s prices=%d media=%d",
             platform, status, str(title)[:60], len(prices), media_count)

    # 2. Stage bytes
    try:
        data = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        item, created = inbox.stage_bytes(data, filename=f"e2e18_{platform}_{listing_id}.json", origin="E2E_TEST")
        log.info("[%s] stage: created=%s status=%s err=%s", platform, created, item.get("status"), item.get("error_code"))
    except Exception as e:
        log.error("[%s] stage_bytes exception:\n%s", platform, traceback.format_exc())
        results[platform] = {"verdict": "STAGE_FAIL", "error": str(e), "elapsed": round(time.monotonic()-t0, 1)}
        return

    if item.get("status") == "INVALID":
        results[platform] = {
            "verdict": "STAGE_INVALID", "error_code": item.get("error_code"),
            "title": title, "prices": len(prices), "elapsed": round(time.monotonic()-t0, 1)
        }
        return

    # 3. Accept
    item_id = item.get("id")
    if item.get("status") != "PENDING":
        if item.get("status") == "ACCEPTED":
            pass  # auto_accept
        else:
            results[platform] = {"verdict": "STAGE_NOT_PENDING", "status": item.get("status"),
                                  "elapsed": round(time.monotonic()-t0, 1)}
            return

    try:
        result = inbox.accept(item_id)
    except Exception as e:
        log.error("[%s] accept exception:\n%s", platform, traceback.format_exc())
        results[platform] = {"verdict": "ACCEPT_FAIL", "error": str(e), "elapsed": round(time.monotonic()-t0, 1)}
        return

    pid = result.get("product_id")
    obs_created = result.get("observation_created")
    log.info("[%s] accept: product_id=%s obs_created=%s", platform, pid, obs_created)

    # 4. Verify SQLite
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        p_row = conn.execute(
            "SELECT id, source, listing_id, title, last_regular_minor, last_lowest_minor, observation_count "
            "FROM product WHERE id=?", (pid,)).fetchone()
        o_rows = conn.execute(
            "SELECT id, product_id, observed_at, availability FROM observation WHERE product_id=? ORDER BY id DESC LIMIT 3",
            (pid,)).fetchall()
        pp_rows = conn.execute(
            "SELECT id, observation_id, role, amount_minor, currency, label FROM price_point WHERE product_id=? ORDER BY id DESC LIMIT 5",
            (pid,)).fetchall()
        conn.close()
    except Exception as e:
        log.error("[%s] verify exception:\n%s", platform, traceback.format_exc())
        results[platform] = {"verdict": "VERIFY_FAIL", "error": str(e), "elapsed": round(time.monotonic()-t0, 1)}
        return

    verdict = "E2E_PASS" if (p_row and o_rows and pp_rows) else "E2E_PARTIAL"
    if p_row and not o_rows:
        verdict = "E2E_NO_OBS"
    elif p_row and o_rows and not pp_rows:
        verdict = "E2E_NO_PP"

    results[platform] = {
        "verdict": verdict,
        "product_id": pid,
        "title": p_row["title"] if p_row else None,
        "listing_id": p_row["listing_id"] if p_row else None,
        "last_regular_minor": p_row["last_regular_minor"] if p_row else None,
        "last_lowest_minor": p_row["last_lowest_minor"] if p_row else None,
        "obs_count": len(o_rows),
        "pp_count": len(pp_rows),
        "pp_sample": [dict(r) for r in pp_rows[:3]],
        "collector_status": status,
        "elapsed": round(time.monotonic()-t0, 1),
    }
    log.info("[%s] VERDICT: %s (pid=%s, obs=%d, pp=%d, %.1fs)",
             platform, verdict, pid, len(o_rows), len(pp_rows), time.monotonic()-t0)

# ============================================================
# 1. WILDBERRIES
# ============================================================
log.info("=" * 60)
log.info("WB: listing 1097975275")
log.info("=" * 60)
try:
    wb = WBCollector(data_dir=None, use_proxies=False)
    run_e2e("WB", wb, "1097975275", wb_url("1097975275"), "iPhone 17 Pro Max")
except Exception as e:
    log.exception("WB fatal: %s", e)
    results["WB"] = {"verdict": "FATAL", "error": str(e)}

# ============================================================
# 2. OZON
# ============================================================
log.info("=" * 60)
log.info("OZON: listing 2067947177")
log.info("=" * 60)
try:
    oz = OzonCollector(data_dir=None, use_proxies=False)
    run_e2e("OZON", oz, "2067947177", ozon_url("2067947177"), "Palit RTX 5060 Ti")
    oz.close()
except Exception as e:
    log.exception("OZON fatal: %s", e)
    results["OZON"] = {"verdict": "FATAL", "error": str(e)}

# ============================================================
# 3. FARPOST
# ============================================================
log.info("=" * 60)
log.info("FARPOST: поиск товара")
log.info("=" * 60)
try:
    fp = FarpostCollector(data_dir=None, use_proxies=False)
    # Берём ID из SQLite или ищем
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    fp_prod = conn.execute("SELECT listing_id FROM product WHERE source='FARPOST' LIMIT 1").fetchone()
    conn.close()

    if fp_prod and fp_prod["listing_id"]:
        fp_id = fp_prod["listing_id"]
        log.info("FARPOST: existing listing_id=%s", fp_id)
    else:
        # Поиск через Farpost
        import re
        fp._warmup()
        time.sleep(1)
        search_url = "https://www.farpost.ru/vladivostok/search/?query=iphone"
        log.info("FARPOST: search %s", search_url)
        resp = fp.session.get(search_url, timeout=(5, 20))
        log.info("FARPOST: search HTTP %d, len=%d", resp.status_code, len(resp.text))
        ids = re.findall(r'/(\d{8,12})\.html', resp.text)
        log.info("FARPOST: found IDs: %s", ids[:5])
        fp_id = ids[0] if ids else "139122082"
        log.info("FARPOST: using ID=%s", fp_id)

    run_e2e("FARPOST", fp, fp_id, fp_url(fp_id), "Farpost test")
except Exception as e:
    log.exception("FARPOST fatal: %s", e)
    results["FARPOST"] = {"verdict": "FATAL", "error": str(e)}

# ============================================================
# 4. AVITO
# ============================================================
log.info("=" * 60)
log.info("AVITO: mobile URL")
log.info("=" * 60)
try:
    av = AvitoCollector(data_dir=None, use_proxies=False)
    # Ищем ID через mobile
    import re
    av_id = None
    search_urls = [
        "https://m.avito.ru/moskva/smartfony-i-telefony",
        "https://m.avito.ru/spb/smartfony-i-telefony",
    ]
    for su in search_urls:
        try:
            log.info("AVITO: search %s", su)
            resp = av.session.get(su, headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
            }, timeout=(5, 15))
            log.info("AVITO: search HTTP %d, len=%d", resp.status_code, len(resp.text))
            if resp.status_code == 200 and len(resp.text) > 1000:
                ids = re.findall(r'/ad/(\d{8,15})', resp.text)
                if not ids:
                    ids = re.findall(r'"id":\s*(\d{8,15})', resp.text)
                log.info("AVITO: IDs: %s", ids[:5])
                if ids:
                    av_id = ids[0]
                    break
        except Exception as e2:
            log.warning("AVITO: search error: %s", e2)

    if not av_id:
        av_id = "1097975275"
        log.info("AVITO: fallback ID=%s", av_id)

    run_e2e("AVITO", av, av_id, f"https://m.avito.ru/ad/{av_id}", "Avito test")
except Exception as e:
    log.exception("AVITO fatal: %s", e)
    results["AVITO"] = {"verdict": "FATAL", "error": str(e)}

# ============================================================
# SUMMARY
# ============================================================
log.info("=" * 60)
log.info("E2E FIX18 SUMMARY")
log.info("=" * 60)
for plat in ("WB", "OZON", "FARPOST", "AVITO"):
    r = results.get(plat, {})
    v = r.get("verdict", "UNKNOWN")
    mark = "PASS" if v == "E2E_PASS" else "FAIL"
    log.info("  [%s] %s: %s (pid=%s, obs=%s, pp=%s, %.1fs)",
             mark, plat, v, r.get("product_id"), r.get("obs_count"), r.get("pp_count"), r.get("elapsed", 0))

# SQLite snapshot
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
snap = {}
for t in ("product", "observation", "price_point"):
    if t == "product":
        rows = conn.execute("SELECT source, COUNT(*) as cnt FROM product GROUP BY source").fetchall()
    else:
        rows = conn.execute(
            f"SELECT p.source, COUNT(*) as cnt FROM {t} x JOIN product p ON x.product_id=p.id GROUP BY p.source"
        ).fetchall()
    snap[t] = {r["source"]: r["cnt"] for r in rows}
conn.close()
log.info("SQLite: %s", json.dumps(snap, ensure_ascii=False))

out_path = os.path.join(HERE, "e2e_fix18_result.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"ts": NOW, "results": results, "sqlite_snapshot": snap}, f, ensure_ascii=False, indent=2, default=str)
log.info("Result → %s", out_path)
log.info("DONE")

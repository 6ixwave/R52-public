"""e2e_fix19 — E2E для 4 площадок: Collector → Inbox → SQLite.

Исправления vs fix18:
- _Conn mock: добавлен executemany (главный баг)
- WB: извлечение цены из card.json sizes (fallback если price API 403)
- Ozon: mobile URL + composer v1 + SSR с retry
- Farpost: фиксированный ID (139122082)
- Avito: поиск через category page с правильными ID
- Таймауты на каждый этап
"""
import sys, os, json, time, sqlite3, logging, traceback, re
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("e2e19")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()
DB_PATH = r"C:\Users\aleks\AppData\Local\R52\data\r52.sqlite3"

# ============================================================
# Mock App (исправленный: executemany!)
# ============================================================
class _DBCtx:
    def __init__(self, db_path):
        self.db_path = db_path
    def write(self):
        return _Conn(self.db_path)
    def read(self):
        return _Conn(self.db_path)

class _Conn:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
    def execute(self, *a, **kw):
        return self.conn.execute(*a, **kw)
    def executemany(self, *a, **kw):
        return self.conn.executemany(*a, **kw)
    def commit(self):
        self.conn.commit()
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
        item, created = inbox.stage_bytes(data, filename=f"e2e19_{platform}_{listing_id}.json", origin="E2E_TEST")
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
    if item.get("status") != "PENDING" and item.get("status") != "ACCEPTED":
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
# 1. WILDBERRIES — card.json + price from sizes fallback
# ============================================================
log.info("=" * 60)
log.info("WB: listing 1097975275 (iPhone 17 Pro Max)")
log.info("=" * 60)
try:
    wb = WBCollector(data_dir=None, use_proxies=False)
    run_e2e("WB", wb, "1097975275", "https://www.wildberries.ru/catalog/1097975275/detail.aspx", "iPhone 17 Pro Max")
except Exception as e:
    log.exception("WB fatal: %s", e)
    results["WB"] = {"verdict": "FATAL", "error": str(e)}

# ============================================================
# 2. OZON — mobile + composer + SSR
# ============================================================
log.info("=" * 60)
log.info("OZON: listing 2067947177")
log.info("=" * 60)
try:
    oz = OzonCollector(data_dir=None, use_proxies=False)
    # Пробуем mobile URL — часто стабильнее
    run_e2e("OZON", oz, "2067947177", "https://www.ozon.ru/product/2067947177/", "Palit RTX 5060 Ti")
    oz.close()
except Exception as e:
    log.exception("OZON fatal: %s", e)
    results["OZON"] = {"verdict": "FATAL", "error": str(e)}

# ============================================================
# 3. FARPOST — фиксированный ID
# ============================================================
log.info("=" * 60)
log.info("FARPOST: listing 139122082")
log.info("=" * 60)
try:
    fp = FarpostCollector(data_dir=None, use_proxies=False)
    run_e2e("FARPOST", fp, "139122082", "https://www.farpost.ru/vladivostok/139122082.html", "Farpost test")
except Exception as e:
    log.exception("FARPOST fatal: %s", e)
    results["FARPOST"] = {"verdict": "FATAL", "error": str(e)}

# ============================================================
# 4. AVITO — поиск реального ID
# ============================================================
log.info("=" * 60)
log.info("AVITO: поиск реального объявления")
log.info("=" * 60)
try:
    av = AvitoCollector(data_dir=None, use_proxies=False)
    av_id = None

    # Стратегия 1: category page (desktop)
    search_urls = [
        "https://www.avito.ru/moskva/smartfony-i-telefony",
        "https://www.avito.ru/leningradskaya/smartfony-i-telefony",
        "https://www.avito.ru/krasnodar/smartfony-i-telefony",
    ]
    for su in search_urls:
        try:
            log.info("AVITO: search %s", su)
            self_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            }
            resp = av.session.get(su, headers=self_headers, timeout=(5, 15))
            log.info("AVITO: search HTTP %d, len=%d", resp.status_code, len(resp.text))
            if resp.status_code == 200 and len(resp.text) > 2000:
                # Ищем ID в формате /ad/1234567890 или /moskva/smartfony/1234567890
                ids = re.findall(r'/(\d{9,15})(?:[/?]|$)', resp.text)
                # Фильтруем: Avito ID обычно 9-15 цифр
                ids = [i for i in ids if len(i) >= 9]
                log.info("AVITO: IDs found: %s", ids[:10])
                if ids:
                    av_id = ids[0]
                    break
        except Exception as e2:
            log.warning("AVITO: search error: %s", e2)
        time.sleep(2)

    # Стратегия 2: mobile
    if not av_id:
        for su in ["https://m.avito.ru/moskva/smartfony-i-telefony"]:
            try:
                log.info("AVITO: mobile search %s", su)
                resp = av.session.get(su, headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
                    "Accept": "text/html",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                }, timeout=(5, 15))
                log.info("AVITO: mobile HTTP %d, len=%d", resp.status_code, len(resp.text))
                if resp.status_code == 200:
                    ids = re.findall(r'/ad/(\d{9,15})', resp.text)
                    if ids:
                        av_id = ids[0]
                        log.info("AVITO: mobile ID=%s", av_id)
                        break
            except Exception as e2:
                log.warning("AVITO: mobile error: %s", e2)

    # Стратегия 3: known ID from Avito API (search by text)
    if not av_id:
        try:
            log.info("AVITO: API search")
            api_url = "https://www.avito.ru/search?query=iphone+15&city=1"
            resp = av.session.get(api_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html",
            }, timeout=(5, 15))
            log.info("AVITO: API HTTP %d, len=%d", resp.status_code, len(resp.text))
            if resp.status_code == 200:
                ids = re.findall(r'/(\d{9,15})(?:[/?]|$)', resp.text)
                if ids:
                    av_id = ids[0]
                    log.info("AVITO: API ID=%s", av_id)
        except Exception as e2:
            log.warning("AVITO: API error: %s", e2)

    if not av_id:
        log.warning("AVITO: no ID found, using fallback 1097975275 (may 404)")
        av_id = "1097975275"

    log.info("AVITO: using ID=%s", av_id)
    run_e2e("AVITO", av, av_id, f"https://www.avito.ru/ad/{av_id}", "Avito test")
except Exception as e:
    log.exception("AVITO fatal: %s", e)
    results["AVITO"] = {"verdict": "FATAL", "error": str(e)}

# ============================================================
# SUMMARY
# ============================================================
log.info("=" * 60)
log.info("E2E FIX19 SUMMARY")
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

out_path = os.path.join(HERE, "e2e_fix19_result.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"ts": NOW, "results": results, "sqlite_snapshot": snap}, f, ensure_ascii=False, indent=2, default=str)
log.info("Result → %s", out_path)
log.info("DONE")

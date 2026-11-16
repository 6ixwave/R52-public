"""E2E Farpost через ШТАТНЫЙ intake: HTML → парсер → обёртка → Inbox → SQLite.

Проверяет полный путь без обхода:
  1. Чтение сохранённого HTML
  2. FarpostCollector.parse_page()
  3. Обёртка в формат intake (как BaseCollector.collect_product)
  4. Запись jarvis-eye-farpost-*.json
  5. Inbox.stage_bytes() → PENDING
  6. Inbox.accept() → upsert_product + add_observation + refresh_summary
  7. Проверка записей в SQLite

Запуск:
    C:\\Python315\\python.exe e2e-final-work\\e2e_fp_intake.py
"""
import json
import os
import sys
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HTML_FILE = ROOT / "e2e-final-work" / "fp_card_ip18pro.html"
DB_FILE = ROOT / "r52" / "data" / "r52.db"
INTAKE_DIR = ROOT / "e2e-final-work" / "intake_inbox"

results = {"steps": {}}
log_lines = []

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    log_lines.append(line)
    print(line)

def save_results():
    results["log"] = log_lines
    out = ROOT / "e2e-final-work" / "e2e_fp_intake.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    log(f"Результаты: {out}")

# ============================================================
# ШАГ 1: Чтение HTML
# ============================================================
log("=== ШАГ 1: Чтение HTML ===")
if not HTML_FILE.exists():
    log(f"ОШИБКА: {HTML_FILE} не найден")
    results["steps"]["html"] = {"ok": False, "error": "file_not_found"}
    save_results()
    raise SystemExit(1)

raw_bytes = HTML_FILE.read_bytes()
log(f"HTML: {len(raw_bytes)} байт")
results["steps"]["html"] = {"ok": True, "bytes": len(raw_bytes)}

# ============================================================
# ШАГ 2: Парсинг через FarpostCollector
# ============================================================
log("=== ШАГ 2: Парсинг ===")

class FakeResp:
    def __init__(self, content, url):
        self.content = content
        self.url = url
        self.headers = {"Content-Type": "text/html; charset=windows-1251"}
        self.status_code = 200
        self.text = content.decode("windows-1251", errors="replace")

resp = FakeResp(raw_bytes, "https://www.farpost.ru/vladivostok/tech/communication/cellphones/iphone-18-pro-max-256gb-trade-in-kredit-garantija-1-god-29958239.html")

from r52.collectors.farpost import FarpostCollector
collector = FarpostCollector(data_dir=ROOT / "r52" / "data", use_proxies=False)
collector._warmed_up = True  # офлайн

t0 = time.monotonic()
parsed = collector.parse_page(resp, "29958239", resp.url, "IPhone 18 Pro Max")
elapsed = time.monotonic() - t0

if parsed is None:
    log("ОШИБКА: parse_page вернул None")
    results["steps"]["parse"] = {"ok": False, "error": "parse_none"}
    save_results()
    raise SystemExit(1)

fields = parsed.get("fields", {})
prices = parsed.get("price_candidates", [])
media_items = parsed.get("media", {}).get("items", [])
availability = parsed.get("availability", "UNKNOWN")
title_val = fields.get("title", {}).get("value", "MISSING")
log(f"За {elapsed:.3f}s: title={title_val[:50]}... prices={len(prices)} media={len(media_items)} avail={availability}")
results["steps"]["parse"] = {
    "ok": True, "elapsed": round(elapsed, 3),
    "title": title_val, "prices": len(prices),
    "media_count": len(media_items), "availability": availability,
}

# ============================================================
# ШАГ 3: Обёртка в формат intake (как BaseCollector.collect_product)
# ============================================================
log("=== ШАГ 3: Обёртка в формат intake ===")

# Пост-обработка (как в base.py)
collector._ensure_evidence(parsed)
collector._ensure_price_amounts(parsed)
collector._ensure_availability_in_fields(parsed)

now_iso = datetime.now(timezone(timedelta(hours=10))).isoformat()

# Формат, который принимает eye.check_export()
intake_doc = {
    "format": "JARVIS_BR02_EYE_USER_CAPTURE",
    "capture_action": "EXPLICIT_POPUP_CLICK",
    "fully_autonomous": False,
    "collector_source": "FARPOST",
    "observation": {
        **parsed,
        "source": "FARPOST",
        "id": "29958239",
        "listing_id": "29958239",
        "url": resp.url,
        "observed_at": now_iso,
        "status": "PARTIAL_SUCCESS",
        "product_content": True,
        "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
        "http_status": 200,
    },
}

# Проверка контракта ДО записи
log(f"format={intake_doc['format']}")
log(f"capture_action={intake_doc['capture_action']}")
log(f"fully_autonomous={intake_doc['fully_autonomous']}")
log(f"observation keys: {list(intake_doc['observation'].keys())[:10]}...")

# Запись JSON-файла (имя соответствует PATTERN "jarvis-eye*.json")
INTAKE_DIR.mkdir(parents=True, exist_ok=True)
fname = f"jarvis-eye-farpost-{uuid.uuid4().hex[:8]}.json"
fpath = INTAKE_DIR / fname
fpath.write_text(json.dumps(intake_doc, ensure_ascii=False, indent=2), encoding="utf-8")
log(f"Файл: {fpath} ({fpath.stat().st_size} байт)")
results["steps"]["wrapper"] = {"ok": True, "file": str(fpath), "size": fpath.stat().st_size}

# ============================================================
# ШАГ 4: Inbox.stage_bytes() — через штатный App
# ============================================================
log("=== ШАГ 4: Inbox.stage_bytes() ===")

from r52.db import Database, migrate
from r52.core.settings import Settings
from r52.core.notify import Notifier
from r52.timeutil import Clock, iso
from r52.intake.eye import Inbox
from r52.errors import AppError

# Минимальный App-объект (без сервера, без watcher)
class MinimalApp:
    pass

app = MinimalApp()
app.clock = Clock()
app.tz = timezone(timedelta(hours=10))

# База: используем существующую
DB_FILE.parent.mkdir(parents=True, exist_ok=True)
app.db = Database(str(DB_FILE))
app.db.initialize()
migrate(app.db, backup_dir=str(DB_FILE.parent / "backups"), now=app.clock.now())

app.settings = Settings(app.db, app.clock)
app.notifier = Notifier(app.settings, app.clock, app.tz)
app.inbox = Inbox(app)

# stage_bytes
data = fpath.read_bytes()
try:
    item, created = app.inbox.stage_bytes(data, filename=fname, origin="FOLDER")
    log(f"stage_bytes: status={item['status']} created={created} id={item['id']}")
    log(f"  error_code={item.get('error_code')}")
    log(f"  preview={json.dumps(item.get('preview'), ensure_ascii=False)[:200]}")
    results["steps"]["stage"] = {
        "ok": item["status"] in ("PENDING", "ACCEPTED"),
        "status": item["status"], "created": created,
        "item_id": item["id"], "error_code": item.get("error_code"),
        "preview_title": (item.get("preview") or {}).get("title", ""),
    }
except AppError as e:
    log(f"stage_bytes AppError: {e.code} — {e}")
    results["steps"]["stage"] = {"ok": False, "error": e.code}
    save_results()
    raise SystemExit(1)
except Exception as e:
    log(f"stage_bytes ОШИБКА: {type(e).__name__}: {e}")
    results["steps"]["stage"] = {"ok": False, "error": str(e)}
    save_results()
    raise SystemExit(1)

# Если auto_accept включён — item уже ACCEPTED
if item["status"] == "ACCEPTED":
    log("auto_accept: файл уже принят")
    accept_item = item
    accept_created = created
else:
    # ============================================================
    # ШАГ 5: Inbox.accept()
    # ============================================================
    log("=== ШАГ 5: Inbox.accept() ===")
    try:
        accept_item = app.inbox.accept(item["id"])
        log(f"accept: status={accept_item['status']} product_id={accept_item.get('product_id')} "
            f"obs_created={accept_item.get('observation_created')}")
        log(f"  events={accept_item.get('events')}")
        accept_created = accept_item.get("observation_created", False)
        results["steps"]["accept"] = {
            "ok": accept_item["status"] == "ACCEPTED",
            "status": accept_item["status"],
            "product_id": accept_item.get("product_id"),
            "observation_created": accept_item.get("observation_created"),
            "events": accept_item.get("events"),
        }
    except AppError as e:
        log(f"accept AppError: {e.code}")
        results["steps"]["accept"] = {"ok": False, "error": e.code}
        save_results()
        raise SystemExit(1)
    except Exception as e:
        log(f"accept ОШИБКА: {type(e).__name__}: {e}")
        results["steps"]["accept"] = {"ok": False, "error": str(e)}
        save_results()
        raise SystemExit(1)

# ============================================================
# ШАГ 6: Проверка SQLite
# ============================================================
log("=== ШАГ 6: Проверка SQLite ===")

product_id = accept_item.get("product_id")
conn = sqlite3.connect(str(DB_FILE))
conn.row_factory = sqlite3.Row

# Товар
prod = conn.execute(
    "SELECT id, source, listing_id, title, brand, model, origin, "
    "last_regular_minor, last_lowest_minor, last_availability, observation_count "
    "FROM product WHERE id = ?", (product_id,)
).fetchone()
if prod:
    log(f"Product #{prod['id']}: [{prod['source']}] {prod['listing_id']} | {str(prod['title'])[:60]}")
    log(f"  price_lowest={prod['last_lowest_minor']} avail={prod['last_availability']} obs_count={prod['observation_count']}")
    results["steps"]["sqlite_product"] = {"ok": True, "row": dict(prod)}
else:
    log(f"ОШИБКА: товар #{product_id} не найден")
    results["steps"]["sqlite_product"] = {"ok": False}

# Наблюдение
obs_rows = conn.execute(
    "SELECT id, product_id, observed_at, origin, evidence_level, availability, fingerprint "
    "FROM observation WHERE product_id = ? ORDER BY id DESC LIMIT 5", (product_id,)
).fetchall()
log(f"Наблюдений товара: {len(obs_rows)}")
for o in obs_rows:
    log(f"  obs#{o['id']} {o['observed_at']} {o['origin']} {o['evidence_level']} {o['availability']}")
results["steps"]["sqlite_obs"] = {"ok": len(obs_rows) > 0, "count": len(obs_rows),
                                   "rows": [dict(r) for r in obs_rows]}

# Точки цен
pp_rows = conn.execute(
    "SELECT role, amount_minor, currency, label FROM price_point WHERE product_id = ?", (product_id,)
).fetchall()
log(f"Точек цен: {len(pp_rows)}")
for p in pp_rows:
    log(f"  {p['role']} {p['amount_minor']} {p['currency']} {str(p['label'])[:40]}")
results["steps"]["sqlite_prices"] = {"ok": len(pp_rows) > 0, "count": len(pp_rows),
                                      "rows": [dict(r) for r in pp_rows]}

# Галерея
gallery = conn.execute("SELECT gallery_json FROM product WHERE id = ?", (product_id,)).fetchone()
gallery_items = json.loads(gallery["gallery_json"] or "[]") if gallery else []
log(f"Галерея: {len(gallery_items)} медиа")
results["steps"]["sqlite_gallery"] = {"ok": len(gallery_items) > 0, "count": len(gallery_items)}

# Проверка origin
log(f"Origin товара: {prod['origin'] if prod else 'N/A'}")
results["origin_check"] = {
    "product_origin": prod["origin"] if prod else None,
    "expected": "EYE",
    "match": (prod["origin"] if prod else None) == "EYE",
}

conn.close()

# ============================================================
# ШАГ 7: Повторный stage (проверка дедупликации)
# ============================================================
log("=== ШАГ 7: Дедупликация ===")
try:
    item2, created2 = app.inbox.stage_bytes(data, filename=fname + "-dup", origin="FOLDER")
    log(f"Повторный stage: created={created2} status={item2['status']}")
    results["steps"]["dedup"] = {"ok": not created2, "created": created2, "status": item2["status"]}
except Exception as e:
    log(f"Дедупликация: {e}")
    results["steps"]["dedup"] = {"ok": False, "error": str(e)}

# ============================================================
# ИТОГ
# ============================================================
log("=== ИТОГ ===")
all_ok = all(
    results["steps"].get(s, {}).get("ok", False)
    for s in ("html", "parse", "wrapper", "stage", "sqlite_product", "sqlite_obs", "sqlite_prices")
)
results["all_ok"] = all_ok
results["summary"] = {
    "all_ok": all_ok,
    "product_id": product_id,
    "title": results["steps"].get("sqlite_product", {}).get("row", {}).get("title", "")[:80],
    "price_lowest": results["steps"].get("sqlite_product", {}).get("row", {}).get("last_lowest_minor"),
    "availability": results["steps"].get("sqlite_product", {}).get("row", {}).get("last_availability"),
    "obs_count": results["steps"].get("sqlite_obs", {}).get("count"),
    "price_points": results["steps"].get("sqlite_prices", {}).get("count"),
    "gallery": results["steps"].get("sqlite_gallery", {}).get("count"),
    "origin": results["steps"].get("sqlite_product", {}).get("row", {}).get("origin"),
}
log(json.dumps(results["summary"], ensure_ascii=False, indent=2))
save_results()
print("\nDONE" if all_ok else "\nDONE (with issues)")

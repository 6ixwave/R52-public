"""Полная диагностика v2: фикс БД, ядро, UI, scheduler, export, backup/restore."""
import json, os, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("R52_HOME", Path(os.environ.get("LOCALAPPDATA", "")) / "R52"))
DB = HOME / "data" / "r52.sqlite3"
LOCK = HOME / "data" / "core.lock"
RUN_DIR = HOME / "run"
PORT = 8423

results = []
def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))

# ═══════════════════════════════════════════
# 1. ФИКС БД
# ═══════════════════════════════════════════
print("=== 1. Фикс БД ===")
if not DB.exists():
    print(f"[FATAL] БД не найдена: {DB}")
    sys.exit(1)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")

existing = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
)}
applied = {r[0]: r for r in conn.execute("SELECT version, name, checksum FROM schema_migrations")} if "schema_migrations" in existing else {}
print(f"  Таблиц: {len(existing)}, миграций: {sorted(applied.keys())}")

# Создаём недостающие таблицы по финальной схеме
TABLES_SQL = {
    "assistant_analysis_request": """
        CREATE TABLE IF NOT EXISTS assistant_analysis_request(
          key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
          result_json TEXT,
          request_json TEXT CHECK(request_json IS NULL OR json_valid(request_json)),
          scope_json TEXT CHECK(scope_json IS NULL OR json_valid(scope_json))
        ) STRICT;
        CREATE INDEX IF NOT EXISTS analysis_request_created ON assistant_analysis_request(created_at);
    """,
    "assistant_llm_call": """
        CREATE TABLE IF NOT EXISTS assistant_llm_call(
          id INTEGER PRIMARY KEY, model TEXT NOT NULL, created_at TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN('PENDING','DONE','FAILED','UNKNOWN')),
          reserve_usd REAL,
          usage_json TEXT CHECK(usage_json IS NULL OR json_valid(usage_json)),
          cost_usd REAL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS llm_call_created ON assistant_llm_call(created_at);
    """,
    "assistant_request": """
        CREATE TABLE IF NOT EXISTS assistant_request(
          key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN('STARTED','DONE','FAILED')),
          created_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS assistant_request_created ON assistant_request(created_at);
    """,
}

# Если v8 уже в schema_migrations, но таблицы нет — создаём в финальном виде
# Если v8 pending — тоже создаём (v8 пересоздаст через _new)
for tbl, sql in TABLES_SQL.items():
    if tbl not in existing:
        conn.executescript(sql)
        print(f"  [CREATE] {tbl}")

# Если v8 в applied, но assistant_llm_call в старом виде (без UNKNOWN) — пересоздаём
if 8 in applied and "assistant_llm_call" in existing:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(assistant_llm_call)")]
    # Проверяем CHECK constraint через sqlite_master
    tbl_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='assistant_llm_call'").fetchone()[0]
    if "'UNKNOWN'" not in tbl_sql:
        print("  [RECREATE] assistant_llm_call (v4→v8)")
        conn.executescript("""
            CREATE TABLE assistant_llm_call_new(
              id INTEGER PRIMARY KEY, model TEXT NOT NULL, created_at TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN('PENDING','DONE','FAILED','UNKNOWN')),
              reserve_usd REAL,
              usage_json TEXT CHECK(usage_json IS NULL OR json_valid(usage_json)),
              cost_usd REAL
            ) STRICT;
            INSERT INTO assistant_llm_call_new SELECT id,model,created_at,status,reserve_usd,usage_json,cost_usd FROM assistant_llm_call;
            DROP TABLE assistant_llm_call;
            ALTER TABLE assistant_llm_call_new RENAME TO assistant_llm_call;
            CREATE INDEX IF NOT EXISTS llm_call_created ON assistant_llm_call(created_at);
        """)

conn.commit()
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.close()
check("Фикс БД", True, "таблицы на месте")

# ═══════════════════════════════════════════
# 2. ОЧИСТКА
# ═══════════════════════════════════════════
print("\n=== 2. Очистка ===")
for f in [LOCK, RUN_DIR / "ui-sessions.json"]:
    if f.exists():
        f.unlink()
        print(f"  Удалён {f.name}")

# ═══════════════════════════════════════════
# 3. ЗАПУСК ЯДРА
# ═══════════════════════════════════════════
print("\n=== 3. Запуск ядра ===")
env = os.environ.copy()
env["R52_HOME"] = str(HOME)

# Убиваем старые
for p in subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
                        capture_output=True, text=True).stdout.splitlines():
    if "python" in p.lower():
        pass  # не убиваем все python, только r52

proc = subprocess.Popen(
    [sys.executable, "-m", "r52.cli", "core", "--port", str(PORT), "--console"],
    cwd=str(ROOT), env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace",
)
print(f"  PID: {proc.pid}")

# Ждём HTTP (до 25 сек)
ready = False
status_data = {}
deadline = time.time() + 25
while time.time() < deadline:
    if proc.poll() is not None:
        break
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=2)
        status_data = json.loads(req.read())
        if status_data.get("status") == "ok" or "version" in status_data:
            ready = True
            break
    except Exception:
        time.sleep(0.5)

if proc.poll() is not None:
    out = proc.stdout.read() if proc.stdout else ""
    check("Ядро запущено", False, f"exit={proc.returncode} | last: {out[-400:]}")
    print(f"\nИТОГ: {sum(1 for s,_,_ in results if s=='PASS')} PASS / {sum(1 for s,_,_ in results if s=='FAIL')} FAIL")
    sys.exit(1)

if ready:
    check("Ядро запущено", True, f"port={PORT}, ver={status_data.get('version','?')}")
else:
    check("Ядро запущено", False, "не ответил за 25с")
    proc.terminate()
    print(f"\nИТОГ: {sum(1 for s,_,_ in results if s=='PASS')} PASS / {sum(1 for s,_,_ in results if s=='FAIL')} FAIL")
    sys.exit(1)

time.sleep(1)

# ═══════════════════════════════════════════
# 4. UI
# ═══════════════════════════════════════════
print("\n=== 4. UI ===")
# HTML
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5)
    html = req.read().decode("utf-8", errors="replace")
    has_ui = "<!DOCTYPE" in html or "<html" in html.lower()
    check("UI HTML", has_ui, f"len={len(html)}")
except Exception as e:
    check("UI HTML", False, str(e))

# API products
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/products", timeout=5)
    prods = json.loads(req.read())
    pcount = len(prods) if isinstance(prods, list) else (prods.get("count", len(prods)) if isinstance(prods, dict) else "?")
    check("API /api/products", True, f"products={pcount}")
except Exception as e:
    check("API /api/products", False, str(e))

# API inbox
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/inbox", timeout=5)
    inbox = json.loads(req.read())
    icount = len(inbox) if isinstance(inbox, list) else (inbox.get("count", "?") if isinstance(inbox, dict) else "?")
    check("API /api/inbox", True, f"items={icount}")
except Exception as e:
    check("API /api/inbox", False, str(e))

# Открываем Edge
edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
if edge.exists():
    subprocess.Popen([str(edge), f"http://127.0.0.1:{PORT}/"], creationflags=0x00000008)
    check("Edge → UI", True, f"http://127.0.0.1:{PORT}/")
    time.sleep(2)
else:
    check("Edge → UI", False, "msedge.exe не найден")

# ═══════════════════════════════════════════
# 5. SCHEDULER
# ═══════════════════════════════════════════
print("\n=== 5. Scheduler ===")
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/schedules", timeout=5)
    scheds = json.loads(req.read())
    slist = scheds if isinstance(scheds, list) else scheds.get("schedules", [])
    check("Scheduler — schedules", True, f"count={len(slist)}")
    for s in slist[:5]:
        print(f"    {s.get('key','?')}: rule={s.get('rule','?')}, enabled={s.get('enabled','?')}, next={s.get('next_due_at','?')}")
except Exception as e:
    check("Scheduler — schedules", False, str(e))

try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/tasks?limit=10", timeout=5)
    tasks = json.loads(req.read())
    tlist = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
    check("Scheduler — tasks", True, f"recent={len(tlist)}")
    for t in tlist[:5]:
        print(f"    {t.get('id','?')[:8]}: {t.get('kind','?')} {t.get('status','?')}")
except Exception as e:
    check("Scheduler — tasks", False, str(e))

# ═══════════════════════════════════════════
# 6. EXPORT
# ═══════════════════════════════════════════
print("\n=== 6. Export ===")
export_out = ROOT / "diag_v2_export.csv"
if export_out.exists():
    export_out.unlink()
try:
    r = subprocess.run(
        [sys.executable, "-m", "r52.cli", "export", "--format", "csv", "--out", str(export_out)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    ok = r.returncode == 0 and export_out.exists()
    size = export_out.stat().st_size if export_out.exists() else 0
    detail = f"exit={r.returncode}, size={size}B"
    if r.returncode != 0:
        detail += f" | err={r.stderr[-200:]}"
    check("Export CSV", ok, detail)
except subprocess.TimeoutExpired:
    check("Export CSV", False, "timeout 30s")
except Exception as e:
    check("Export CSV", False, str(e))

# ═══════════════════════════════════════════
# 7. BACKUP / RESTORE
# ═══════════════════════════════════════════
print("\n=== 7. Backup / Restore ===")
backup_dir = HOME / "backups"
backup_dir.mkdir(parents=True, exist_ok=True)

# Backup
try:
    r = subprocess.run(
        [sys.executable, "-m", "r52.cli", "backup"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    check("Backup", r.returncode == 0, f"exit={r.returncode}" + (f" | {r.stdout.strip()[-150:]}" if r.returncode == 0 else f" | {r.stderr[-150:]}"))
except Exception as e:
    check("Backup", False, str(e))

# Найти последний бэкап
backups = sorted(backup_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
if backups:
    latest = backups[0]
    print(f"    Бэкап: {latest.name} ({latest.stat().st_size}B)")

    # Restore
    try:
        r = subprocess.run(
            [sys.executable, "-m", "r52.cli", "restore", "--from", str(latest)],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        check("Restore", r.returncode == 0, f"exit={r.returncode}" + (f" | {r.stdout.strip()[-150:]}" if r.returncode == 0 else f" | {r.stderr[-150:]}"))
    except Exception as e:
        check("Restore", False, str(e))
else:
    check("Restore", False, "нет бэкапов")

# DB после restore
try:
    conn2 = sqlite3.connect(str(DB))
    conn2.row_factory = sqlite3.Row
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    prods = conn2.execute("SELECT COUNT(*) as c FROM product").fetchone()["c"]
    obs = conn2.execute("SELECT COUNT(*) as c FROM observation").fetchone()["c"]
    check("DB после restore", len(tables) >= 20, f"tables={len(tables)}, products={prods}, obs={obs}")
    conn2.close()
except Exception as e:
    check("DB после restore", False, str(e))

# ═══════════════════════════════════════════
# ИТОГ
# ═══════════════════════════════════════════
print("\n" + "=" * 55)
p = sum(1 for s, _, _ in results if s == "PASS")
f = sum(1 for s, _, _ in results if s == "FAIL")
print(f"ИТОГ: {p} PASS / {f} FAIL из {len(results)}")
for s, n, d in results:
    print(f"  [{s}] {n}" + (f" — {d}" if d else ""))

# Остановка ядра
print("\nОстановка ядра...")
try:
    proc.terminate()
    proc.wait(timeout=10)
except Exception:
    proc.kill()
print("Готово.")

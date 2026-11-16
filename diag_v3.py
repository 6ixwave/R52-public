"""Диагностика v3: ядро + UI + scheduler + export + backup/restore.
Ключевое отличие: читаем core.log после выхода, правильный путь lock."""
import json, os, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("R52_HOME", Path(os.environ.get("LOCALAPPDATA", "")) / "R52"))
DB = HOME / "data" / "r52.sqlite3"
RUN_DIR = HOME / "run"
LOG_FILE = HOME / "logs" / "core.log"
PORT = 8423

results = []
def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))

# ═══ 1. Очистка ═══
print("=== 1. Очистка ===")
# Lock может быть в data/ или run/ — удаляем оба
for lock_path in [HOME / "data" / "core.lock", RUN_DIR / "core.lock"]:
    if lock_path.exists():
        lock_path.unlink()
        print(f"  Удалён lock: {lock_path}")

# WAL checkpoint
if DB.exists():
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    print("  WAL checkpoint OK")

# ═══ 2. Проверка БД перед стартом ═══
print("\n=== 2. БД ===")
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
migs = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
has_llm = "assistant_llm_call" in tables
print(f"  Таблиц: {len(tables)}, миграции: {sorted(migs)}, assistant_llm_call: {'YES' if has_llm else 'NO'}")

# Если v8 pending и таблицы нет — создаём
if 8 not in migs and not has_llm:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS assistant_llm_call(
          id INTEGER PRIMARY KEY, model TEXT NOT NULL, created_at TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN('PENDING','DONE','FAILED','UNKNOWN')),
          reserve_usd REAL,
          usage_json TEXT CHECK(usage_json IS NULL OR json_valid(usage_json)),
          cost_usd REAL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS llm_call_created ON assistant_llm_call(created_at);
    """)
    conn.commit()
    print("  [CREATE] assistant_llm_call (для v8)")

conn.close()
check("БД готова", True, f"tables={len(tables)}, migs={sorted(migs)}")

# ═══ 3. Запуск ядра ═══
print("\n=== 3. Запуск ядра ===")
env = os.environ.copy()
env["R52_HOME"] = str(HOME)

# Запоминаем размер core.log до старта (чтобы прочитать только новый хвост)
log_size_before = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0

proc = subprocess.Popen(
    [sys.executable, "-m", "r52.cli", "core", "--port", str(PORT), "--console"],
    cwd=str(ROOT), env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace",
)
print(f"  PID: {proc.pid}")

# Ждём до 20 сек
ready = False
status_data = {}
deadline = time.time() + 20
while time.time() < deadline:
    if proc.poll() is not None:
        break
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=2)
        status_data = json.loads(req.read())
        ready = True
        break
    except Exception:
        time.sleep(0.5)

# Читаем новый хвост core.log
log_tail = ""
if LOG_FILE.exists():
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        f.seek(log_size_before)
        log_tail = f.read()

if proc.poll() is not None:
    rc = proc.returncode
    stdout_out = proc.stdout.read() if proc.stdout else ""
    check("Ядро запущено", False, f"exit={rc}")
    print(f"  --- STDOUT ---\n{stdout_out[-600:]}")
    print(f"  --- CORE.LOG (новый хвост) ---\n{log_tail[-800:]}")
    print(f"\nИТОГ: {sum(1 for s,_,_ in results if s=='PASS')} PASS / {sum(1 for s,_,_ in results if s=='FAIL')} FAIL")
    sys.exit(1)

if ready:
    check("Ядро запущено", True, f"port={PORT}, ver={status_data.get('version','?')}")
else:
    check("Ядро запущено", False, "не ответил за 20с")
    print(f"  --- CORE.LOG ---\n{log_tail[-600:]}")
    proc.terminate()
    print(f"\nИТОГ: {sum(1 for s,_,_ in results if s=='PASS')} PASS / {sum(1 for s,_,_ in results if s=='FAIL')} FAIL")
    sys.exit(1)

time.sleep(1)

# ═══ 4. UI ═══
print("\n=== 4. UI ===")
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5)
    html = req.read().decode("utf-8", errors="replace")
    has_ui = "<!DOCTYPE" in html or "<html" in html.lower()
    check("UI HTML отдаётся", has_ui, f"len={len(html)}B")
except Exception as e:
    check("UI HTML отдаётся", False, str(e))

try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/products", timeout=5)
    prods = json.loads(req.read())
    pcount = len(prods) if isinstance(prods, list) else "?"
    check("API /api/products", True, f"count={pcount}")
except Exception as e:
    check("API /api/products", False, str(e))

try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/inbox", timeout=5)
    inbox = json.loads(req.read())
    icount = len(inbox) if isinstance(inbox, list) else "?"
    check("API /api/inbox", True, f"items={icount}")
except Exception as e:
    check("API /api/inbox", False, str(e))

# Edge
edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
if edge.exists():
    subprocess.Popen([str(edge), f"http://127.0.0.1:{PORT}/"], creationflags=0x00000008)
    check("Edge → UI", True, f"открыт http://127.0.0.1:{PORT}/")
    time.sleep(2)
else:
    check("Edge → UI", False, "msedge.exe не найден")

# ═══ 5. Scheduler ═══
print("\n=== 5. Scheduler ===")
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/schedules", timeout=5)
    scheds = json.loads(req.read())
    slist = scheds if isinstance(scheds, list) else scheds.get("schedules", [])
    check("Schedules", True, f"count={len(slist)}")
    for s in slist[:5]:
        print(f"    {s.get('key','?')}: rule={s.get('rule','?')}, enabled={s.get('enabled','?')}, next={s.get('next_due_at','?')}")
except Exception as e:
    check("Schedules", False, str(e))

try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/tasks?limit=10", timeout=5)
    tasks = json.loads(req.read())
    tlist = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
    check("Tasks", True, f"recent={len(tlist)}")
    for t in tlist[:5]:
        print(f"    {str(t.get('id','?'))[:12]}: {t.get('kind','?')} → {t.get('status','?')}")
except Exception as e:
    check("Tasks", False, str(e))

# ═══ 6. Export ═══
print("\n=== 6. Export ===")
export_out = ROOT / "diag_v3_export.csv"
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
    if not ok:
        detail += f" | err={(r.stderr or r.stdout)[-200:]}"
    check("Export CSV", ok, detail)
except Exception as e:
    check("Export CSV", False, str(e))

# ═══ 7. Backup / Restore ═══
print("\n=== 7. Backup / Restore ===")
backup_dir = HOME / "backups"
backup_dir.mkdir(parents=True, exist_ok=True)

try:
    r = subprocess.run(
        [sys.executable, "-m", "r52.cli", "backup"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    check("Backup", r.returncode == 0, f"exit={r.returncode}" + (f" | {(r.stdout or '').strip()[-120:]}" if r.returncode == 0 else f" | {(r.stderr or '')[-120:]}"))
except Exception as e:
    check("Backup", False, str(e))

backups = sorted(backup_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
if backups:
    latest = backups[0]
    print(f"    Бэкап: {latest.name} ({latest.stat().st_size}B)")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "r52.cli", "restore", "--from", str(latest)],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        check("Restore", r.returncode == 0, f"exit={r.returncode}" + (f" | {(r.stdout or '').strip()[-120:]}" if r.returncode == 0 else f" | {(r.stderr or '')[-120:]}"))
    except Exception as e:
        check("Restore", False, str(e))
else:
    check("Restore", False, "нет бэкапов")

# DB после restore
try:
    conn2 = sqlite3.connect(str(DB))
    conn2.row_factory = sqlite3.Row
    t2 = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    p2 = conn2.execute("SELECT COUNT(*) as c FROM product").fetchone()["c"]
    o2 = conn2.execute("SELECT COUNT(*) as c FROM observation").fetchone()["c"]
    check("DB после restore", len(t2) >= 20, f"tables={len(t2)}, prod={p2}, obs={o2}")
    conn2.close()
except Exception as e:
    check("DB после restore", False, str(e))

# ═══ ИТОГ ═══
print("\n" + "=" * 55)
p = sum(1 for s, _, _ in results if s == "PASS")
f = sum(1 for s, _, _ in results if s == "FAIL")
print(f"ИТОГ: {p} PASS / {f} FAIL из {len(results)}")
for s, n, d in results:
    print(f"  [{s}] {n}" + (f" — {d}" if d else ""))

# Остановка
print("\nОстановка ядра...")
try:
    proc.terminate()
    proc.wait(timeout=10)
except Exception:
    proc.kill()
print("Готово.")

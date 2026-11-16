"""Полная диагностика: ядро, UI, scheduler, export, backup/restore."""
import json, os, shutil, signal, socket, sqlite3, subprocess, sys, time, urllib.request
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

# ─── 1. Очистка ───
print("=== 1. Очистка ===")
for f in [LOCK, RUN_DIR / "ui-sessions.json"]:
    if f.exists():
        f.unlink()
        print(f"  Удалён {f.name}")

# WAL checkpoint
if DB.exists():
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    print("  WAL checkpoint выполнен")

# ─── 2. Запуск ядра ───
print("\n=== 2. Запуск ядра ===")
env = os.environ.copy()
env["R52_HOME"] = str(HOME)
proc = subprocess.Popen(
    [sys.executable, "-m", "r52.cli", "core", "--port", str(PORT), "--console"],
    cwd=str(ROOT), env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace",
)

# Ждём готовности (до 30с)
ready = False
deadline = time.time() + 30
while time.time() < deadline:
    if proc.poll() is not None:
        break
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=2)
        data = json.loads(req.read())
        if data.get("status") == "ok" or "version" in data:
            ready = True
            break
    except Exception:
        time.sleep(0.5)

if proc.poll() is not None:
    out = proc.stdout.read() if proc.stdout else ""
    check("Ядро запущено", False, f"exit={proc.returncode} | {out[-500:]}")
    for s, n, d in results:
        pass
    print(f"\nИТОГ: {sum(1 for s,_,_ in results if s=='PASS')} PASS / {sum(1 for s,_,_ in results if s=='FAIL')} FAIL")
    sys.exit(1)

if ready:
    check("Ядро запущено", True, f"port={PORT}, version={data.get('version','?')}")
else:
    check("Ядро запущено", False, "не ответил за 30с")
    proc.terminate()
    print(f"\nИТОГ: {sum(1 for s,_,_ in results if s=='PASS')} PASS / {sum(1 for s,_,_ in results if s=='FAIL')} FAIL")
    sys.exit(1)

time.sleep(1)  # даём ядру стабилизироваться

# ─── 3. UI в браузере ───
print("\n=== 3. UI ===")
# Проверяем что web-статика отдаётся
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5)
    html = req.read().decode("utf-8", errors="replace")
    has_title = "<title" in html.lower() or "<!DOCTYPE" in html
    check("UI HTML отдаётся", has_title, f"len={len(html)}, title={'<title' in html.lower()}")
except Exception as e:
    check("UI HTML отдаётся", False, str(e))

# Проверяем API-эндпоинты UI
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/products", timeout=5)
    prods = json.loads(req.read())
    check("API /api/products", True, f"count={len(prods) if isinstance(prods, list) else 'obj'}")
except Exception as e:
    check("API /api/products", False, str(e))

# Открываем Edge
edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
if edge.exists():
    subprocess.Popen([str(edge), f"http://127.0.0.1:{PORT}/"], creationflags=0x00000008)  # DETACHED
    check("Edge запущен", True, "UI в браузере")
    time.sleep(3)
else:
    check("Edge запущен", False, "msedge.exe не найден")

# ─── 4. Scheduler ───
print("\n=== 4. Scheduler ===")
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/schedules", timeout=5)
    schedules = json.loads(req.read())
    slist = schedules if isinstance(schedules, list) else schedules.get("schedules", [])
    check("Scheduler — расписания", True, f"count={len(slist)}")
    for s in slist[:5]:
        print(f"    {s.get('key','?')}: rule={s.get('rule','?')}, enabled={s.get('enabled','?')}, next_due={s.get('next_due_at','?')}")
except Exception as e:
    check("Scheduler — расписания", False, str(e))

try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/tasks?limit=5", timeout=5)
    tasks = json.loads(req.read())
    tlist = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
    check("Scheduler — задачи", True, f"recent={len(tlist)}")
except Exception as e:
    check("Scheduler — задачи", False, str(e))

# ─── 5. Export ───
print("\n=== 5. Export ===")
export_out = ROOT / "diag_export.csv"
try:
    r = subprocess.run(
        [sys.executable, "-m", "r52.cli", "export", "--format", "csv", "--out", str(export_out)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    ok = r.returncode == 0 and export_out.exists()
    size = export_out.stat().st_size if export_out.exists() else 0
    check("Export CSV", ok, f"exit={r.returncode}, size={size}B" + (f" | {r.stderr[-200:]}" if r.returncode != 0 else ""))
except subprocess.TimeoutExpired:
    check("Export CSV", False, "timeout 30s")
except Exception as e:
    check("Export CSV", False, str(e))

# ─── 6. Backup / Restore ───
print("\n=== 6. Backup / Restore ===")
backup_dir = HOME / "backups"
backup_dir.mkdir(parents=True, exist_ok=True)

# Backup
try:
    r = subprocess.run(
        [sys.executable, "-m", "r52.cli", "backup"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    check("Backup", r.returncode == 0, f"exit={r.returncode}" + (f" | {r.stdout[-200:]}" if r.returncode == 0 else f" | {r.stderr[-200:]}"))
except Exception as e:
    check("Backup", False, str(e))

# Найти последний бэкап
backups = sorted(backup_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
if backups:
    latest = backups[0]
    print(f"    Последний бэкап: {latest.name} ({latest.stat().st_size}B)")
    
    # Restore
    try:
        r = subprocess.run(
            [sys.executable, "-m", "r52.cli", "restore", "--from", str(latest)],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        check("Restore", r.returncode == 0, f"exit={r.returncode}" + (f" | {r.stderr[-200:]}" if r.returncode != 0 else ""))
    except Exception as e:
        check("Restore", False, str(e))
else:
    check("Restore", False, "нет бэкапов для restore")

# Проверка БД после restore
try:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    prods = conn.execute("SELECT COUNT(*) as c FROM product").fetchone()["c"]
    obs = conn.execute("SELECT COUNT(*) as c FROM observation").fetchone()["c"]
    check("DB после restore", len(tables) >= 20, f"tables={len(tables)}, products={prods}, obs={obs}")
    conn.close()
except Exception as e:
    check("DB после restore", False, str(e))

# ─── Итог ───
print("\n" + "=" * 50)
p = sum(1 for s, _, _ in results if s == "PASS")
f = sum(1 for s, _, _ in results if s == "FAIL")
print(f"ИТОГ: {p} PASS / {f} FAIL из {len(results)}")
for s, n, d in results:
    print(f"  [{s}] {n}" + (f" — {d}" if d else ""))

# ─── Остановка ядра ───
print("\nОстановка ядра...")
try:
    proc.terminate()
    proc.wait(timeout=10)
except Exception:
    proc.kill()
print("Готово.")

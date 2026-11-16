"""Диагностика v4: пошаговый старт ядра + все проверки.
Ключевое: ручной импорт и вызов каждого шага, ловля ВСЕХ исключений."""
import json, os, sqlite3, subprocess, sys, time, traceback, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("R52_HOME", Path(os.environ.get("LOCALAPPDATA", "")) / "R52"))
DB = HOME / "data" / "r52.sqlite3"
RUN_DIR = HOME / "run"
LOGS_DIR = HOME / "logs"
PORT = 8423

results = []
def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""), flush=True)

# ═══ 0. Подготовка ═══
print("=== 0. Подготовка ===", flush=True)
sys.path.insert(0, str(ROOT))
os.environ["R52_HOME"] = str(HOME)

# Lock
for lock_path in [HOME / "data" / "core.lock", RUN_DIR / "core.lock"]:
    if lock_path.exists():
        lock_path.unlink()
        print(f"  Удалён: {lock_path}", flush=True)

# WAL
if DB.exists():
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    print("  WAL checkpoint OK", flush=True)

# ═══ 1. Пошаговый старт ═══
print("\n=== 1. Пошаговый старт ядра ===", flush=True)
proc = None
try:
    print("  [1] load_config...", flush=True)
    from r52.config import load_config
    config = load_config()
    print(f"       port={config.api_port}, home={config.paths.home}, tz={config.timezone}", flush=True)

    print("  [2] setup_logging...", flush=True)
    from r52.log import setup_logging
    setup_logging(config.paths.logs_dir, config.log_level, console=True)
    print("       OK", flush=True)

    print("  [3] App(config)...", flush=True)
    from r52.service import App
    app = App(config)
    print(f"       instance={app.instance[:8]}", flush=True)

    print("  [4] app.open()...", flush=True)
    app.open()
    print(f"       db_version={app.db}", flush=True)

    print("  [5] app.start(port=%d)..." % PORT, flush=True)
    app.start(port=PORT)
    print(f"       server_port={app.port}", flush=True)

    print("  [6] HTTP check...", flush=True)
    time.sleep(1)
    req = urllib.request.urlopen(f"http://127.0.0.1:{app.port}/api/status", timeout=3)
    status_data = json.loads(req.read())
    print(f"       status={status_data}", flush=True)
    check("Ядро запущено", True, f"port={app.port}, ver={status_data.get('version','?')}")

except Exception as e:
    print(f"\n  [EXCEPTION] {type(e).__name__}: {e}", flush=True)
    traceback.print_exc()
    check("Ядро запущено", False, f"{type(e).__name__}: {e}")
    # Выводим хвост core.log
    log_file = LOGS_DIR / "core.log"
    if log_file.exists():
        tail = log_file.read_text(encoding="utf-8", errors="replace")[-800:]
        print(f"\n  --- core.log tail ---\n{tail}", flush=True)
    print(f"\nИТОГ: {sum(1 for s,_,_ in results if s=='PASS')} PASS / {sum(1 for s,_,_ in results if s=='FAIL')} FAIL", flush=True)
    sys.exit(1)

# ═══ 2. UI ═══
print("\n=== 2. UI ===", flush=True)
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{app.port}/", timeout=5)
    html = req.read().decode("utf-8", errors="replace")
    has_ui = "<!DOCTYPE" in html or "<html" in html.lower()
    check("UI HTML", has_ui, f"len={len(html)}B")
except Exception as e:
    check("UI HTML", False, str(e))

try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{app.port}/api/products", timeout=5)
    prods = json.loads(req.read())
    pcount = len(prods) if isinstance(prods, list) else "?"
    check("API /api/products", True, f"count={pcount}")
except Exception as e:
    check("API /api/products", False, str(e))

try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{app.port}/api/inbox", timeout=5)
    inbox = json.loads(req.read())
    icount = len(inbox) if isinstance(inbox, list) else "?"
    check("API /api/inbox", True, f"items={icount}")
except Exception as e:
    check("API /api/inbox", False, str(e))

# Edge
edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
if edge.exists():
    subprocess.Popen([str(edge), f"http://127.0.0.1:{app.port}/"], creationflags=0x00000008)
    check("Edge → UI", True, f"http://127.0.0.1:{app.port}/")
    time.sleep(2)
else:
    check("Edge → UI", False, "msedge.exe не найден")

# ═══ 3. Scheduler ═══
print("\n=== 3. Scheduler ===", flush=True)
try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{app.port}/api/schedules", timeout=5)
    scheds = json.loads(req.read())
    slist = scheds if isinstance(scheds, list) else scheds.get("schedules", [])
    check("Schedules", True, f"count={len(slist)}")
    for s in slist[:5]:
        print(f"    {s.get('key','?')}: rule={s.get('rule','?')}, enabled={s.get('enabled','?')}, next={s.get('next_due_at','?')}", flush=True)
except Exception as e:
    check("Schedules", False, str(e))

try:
    req = urllib.request.urlopen(f"http://127.0.0.1:{app.port}/api/tasks?limit=10", timeout=5)
    tasks = json.loads(req.read())
    tlist = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
    check("Tasks", True, f"recent={len(tlist)}")
    for t in tlist[:5]:
        print(f"    {str(t.get('id','?'))[:12]}: {t.get('kind','?')} → {t.get('status','?')}", flush=True)
except Exception as e:
    check("Tasks", False, str(e))

# ═══ 4. Export ═══
print("\n=== 4. Export ===", flush=True)
export_out = ROOT / "diag_v4_export.csv"
if export_out.exists():
    export_out.unlink()
try:
    env = os.environ.copy()
    env["R52_HOME"] = str(HOME)
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

# ═══ 5. Backup / Restore ═══
print("\n=== 5. Backup / Restore ===", flush=True)
backup_dir = HOME / "backups"
backup_dir.mkdir(parents=True, exist_ok=True)

try:
    env = os.environ.copy()
    env["R52_HOME"] = str(HOME)
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
    print(f"    Бэкап: {latest.name} ({latest.stat().st_size}B)", flush=True)
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
print("\n" + "=" * 55, flush=True)
p = sum(1 for s, _, _ in results if s == "PASS")
f = sum(1 for s, _, _ in results if s == "FAIL")
print(f"ИТОГ: {p} PASS / {f} FAIL из {len(results)}", flush=True)
for s, n, d in results:
    print(f"  [{s}] {n}" + (f" — {d}" if d else ""), flush=True)

# Остановка
print("\nОстановка ядра...", flush=True)
try:
    app.stop()
except Exception as e:
    print(f"  stop error: {e}", flush=True)
print("Готово.", flush=True)

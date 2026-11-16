"""R-52 DIAG V6: полный прогон — ядро, UI, scheduler, export, backup/restore.
Авторизация: Bearer token (in-process) + UI code для браузера."""
import json, os, sqlite3, subprocess, sys, time, traceback, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("R52_HOME", Path(os.environ.get("LOCALAPPDATA", "")) / "R52"))
DB = HOME / "data" / "r52.sqlite3"
RUN_DIR = HOME / "run"
LOGS_DIR = HOME / "logs"
PORT = 8423
OUT = ROOT / "diag_v6_report.txt"

lines = []
def log(msg=""):
    lines.append(msg)
    print(msg, flush=True)

def check(name, ok, detail=""):
    s = "PASS" if ok else "FAIL"
    log(f"[{s}] {name}" + (f" — {detail}" if detail else ""))
    return ok

def api_get(path, token):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())

def api_post(path, token, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"},
                                 data=data, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())

# ═══ Подготовка ═══
t0 = time.time()
log(f"=== R-52 DIAG V6 — {time.strftime('%H:%M:%S')} ===")
log(f"HOME={HOME}")
log(f"Python={sys.version}")

sys.path.insert(0, str(ROOT))
os.environ["R52_HOME"] = str(HOME)

for lp in [HOME / "data" / "core.lock", RUN_DIR / "core.lock"]:
    if lp.exists():
        lp.unlink()
        log(f"  Lock removed: {lp.name}")

if DB.exists():
    c = sqlite3.connect(str(DB))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    c.close()
    log("  WAL checkpoint OK")

core_log = LOGS_DIR / "core.log"
log_before = core_log.stat().st_size if core_log.exists() else 0

app = None
try:
    # 1. Config
    log(f"\n[1] load_config... t={time.time()-t0:.1f}s")
    from r52.config import load_config
    config = load_config()
    log(f"    port={config.api_port}, tz={config.timezone}")

    # 2. Logging
    log(f"\n[2] setup_logging...")
    from r52.log import setup_logging
    setup_logging(config.paths.logs_dir, "DEBUG", console=True)

    # 3. App + start
    log(f"\n[3] App + start... t={time.time()-t0:.1f}s")
    from r52.service import App
    app = App(config)
    app.start(port=PORT)
    log(f"    port={app.port}, token={app.token[:12]}...")

    # 4. HTTP status
    log(f"\n[4] /api/status... t={time.time()-t0:.1f}s")
    time.sleep(1)
    status = api_get("/api/status", app.token)
    log(f"    version={status.get('version')}, health={status.get('health')}")
    check("Ядро запущено", True, f"port={app.port}, ver={status.get('version','?')}")

except BaseException as e:
    log(f"\n[EXCEPTION] {type(e).__name__}: {e}")
    log(traceback.format_exc())
    check("Ядро запущено", False, f"{type(e).__name__}: {e}")
    if core_log.exists():
        with open(core_log, "r", encoding="utf-8", errors="replace") as f:
            f.seek(log_before)
            log(f"\n--- core.log ---\n{f.read()[-1500:]}")
    log(f"\nИТОГ: {sum(1 for l in lines if '[PASS]' in l)}P / {sum(1 for l in lines if '[FAIL]' in l)}F")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    sys.exit(1)

# ═══ 5. UI ═══
log(f"\n[5] UI... t={time.time()-t0:.1f}s")

# HTML (public or token)
try:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/",
                                 headers={"Authorization": f"Bearer {app.token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        html = r.read().decode("utf-8", errors="replace")
    check("UI HTML", "<!DOCTYPE" in html or "<html" in html.lower(), f"len={len(html)}B")
except Exception as e:
    check("UI HTML", False, str(e))

# API products
try:
    prods = api_get("/api/products", app.token)
    pc = len(prods) if isinstance(prods, list) else "?"
    check("API /api/products", True, f"count={pc}")
except Exception as e:
    check("API /api/products", False, str(e))

# API inbox
try:
    inbox = api_get("/api/inbox", app.token)
    ic = len(inbox) if isinstance(inbox, list) else "?"
    check("API /api/inbox", True, f"items={ic}")
except Exception as e:
    check("API /api/inbox", False, str(e))

# UI code → Edge
try:
    code_data = api_post("/api/ui/code", app.token)
    code = code_data.get("code", "")
    log(f"    UI code: {code[:8]}...")
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if edge.exists():
        url = f"http://127.0.0.1:{PORT}/?code={code}"
        subprocess.Popen([str(edge), url], creationflags=0x00000008)
        check("Edge → UI", True, url)
        time.sleep(3)
    else:
        check("Edge → UI", False, "msedge.exe not found")
except Exception as e:
    check("Edge → UI", False, f"code: {e}")

# ═══ 6. Scheduler ═══
log(f"\n[6] Scheduler... t={time.time()-t0:.1f}s")
try:
    scheds = api_get("/api/schedules", app.token)
    sl = scheds if isinstance(scheds, list) else scheds.get("schedules", [])
    check("Schedules", True, f"count={len(sl)}")
    for s in sl[:6]:
        log(f"    {s.get('key','?')}: rule={s.get('rule','?')}, enabled={s.get('enabled','?')}, next={s.get('next_due_at','?')}")
except Exception as e:
    check("Schedules", False, str(e))

try:
    tasks = api_get("/api/tasks?limit=10", app.token)
    tl = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
    check("Tasks", True, f"recent={len(tl)}")
    for t in tl[:5]:
        log(f"    {str(t.get('id','?'))[:12]}: {t.get('kind','?')} → {t.get('status','?')}")
except Exception as e:
    check("Tasks", False, str(e))

# ═══ 7. Export ═══
log(f"\n[7] Export... t={time.time()-t0:.1f}s")
export_out = ROOT / "diag_v6_export.csv"
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
    sz = export_out.stat().st_size if export_out.exists() else 0
    d = f"exit={r.returncode}, size={sz}B"
    if not ok:
        d += f" | out={(r.stdout or '')[-150:]} | err={(r.stderr or '')[-150:]}"
    check("Export CSV", ok, d)
except Exception as e:
    check("Export CSV", False, str(e))

# ═══ 8. Backup / Restore ═══
log(f"\n[8] Backup/Restore... t={time.time()-t0:.1f}s")
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
    d = f"exit={r.returncode}"
    d += f" | {(r.stdout or '').strip()[-120:]}" if r.returncode == 0 else f" | {(r.stderr or '')[-120:]}"
    check("Backup", r.returncode == 0, d)
except Exception as e:
    check("Backup", False, str(e))

backups = sorted(backup_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
if backups:
    latest = backups[0]
    log(f"    Latest: {latest.name} ({latest.stat().st_size}B)")
    try:
        env = os.environ.copy()
        env["R52_HOME"] = str(HOME)
        r = subprocess.run(
            [sys.executable, "-m", "r52.cli", "restore", "--from", str(latest)],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        d = f"exit={r.returncode}"
        d += f" | {(r.stdout or '').strip()[-120:]}" if r.returncode == 0 else f" | {(r.stderr or '')[-120:]}"
        check("Restore", r.returncode == 0, d)
    except Exception as e:
        check("Restore", False, str(e))
else:
    check("Restore", False, "no backups found")

# DB integrity after restore
try:
    c2 = sqlite3.connect(str(DB))
    c2.row_factory = sqlite3.Row
    t2 = {r[0] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    p2 = c2.execute("SELECT COUNT(*) as c FROM product").fetchone()["c"]
    o2 = c2.execute("SELECT COUNT(*) as c FROM observation").fetchone()["c"]
    llm2 = c2.execute("SELECT COUNT(*) as c FROM assistant_llm_call").fetchone()["c"]
    check("DB после restore", len(t2) >= 20, f"tables={len(t2)}, prod={p2}, obs={o2}, llm_calls={llm2}")
    c2.close()
except Exception as e:
    check("DB после restore", False, str(e))

# ═══ Итог ═══
log(f"\n{'='*55}")
p = sum(1 for l in lines if "[PASS]" in l)
f = sum(1 for l in lines if "[FAIL]" in l)
log(f"ИТОГ: {p} PASS / {f} FAIL | t={time.time()-t0:.1f}s")
for l in lines:
    if "[PASS]" in l or "[FAIL]" in l:
        log(f"  {l}")

# Core.log tail
if core_log.exists():
    with open(core_log, "r", encoding="utf-8", errors="replace") as f:
        f.seek(log_before)
        tail = f.read()
    log(f"\n--- core.log ({len(tail)}B) ---")
    log(tail[-1200:])

# Stop
log("\nStopping...")
try:
    app.stop()
    log("  OK")
except Exception as e:
    log(f"  stop: {e}")

log(f"\nDONE t={time.time()-t0:.1f}s")
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
log(f"Report: {OUT}")

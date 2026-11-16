"""Полная проверка R-52: API, UI, scheduler, export, backup/restore.
Запускает ядро в-процессе, делает все проверки, открывает Edge, пишет отчёт."""
import sys, os, json, time, subprocess, urllib.request, urllib.error
from pathlib import Path

LOG = Path(r"C:\Users\aleks\AppData\Local\R52\logs\full_check_report.txt")
LOG.parent.mkdir(parents=True, exist_ok=True)
lines = []

def log(msg=""):
    print(msg, flush=True)
    lines.append(msg)

def api(port, token, method, path, body=None, cookie=None):
    url = f"http://127.0.0.1:{port}{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

try:
    log(f"=== FULL CHECK R-52 — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    log(f"Python {sys.version}")
    log(f"CWD: {os.getcwd()}")

    # --- 1. START CORE ---
    log("\n[1] Starting core...")
    sys.path.insert(0, str(Path(__file__).parent))
    from r52.config import load_config
    from r52.service import App

    # Clean stale
    run_dir = Path(r"C:\Users\aleks\AppData\Local\R52\run")
    for f in ("core.lock", "runtime.json"):
        p = run_dir / f
        if p.exists():
            p.unlink()
            log(f"  Removed stale {f}")

    config = load_config(None)
    app = App(config)
    app.start(port=8423)
    log(f"  Core started: PID={os.getpid()} port={app.port} token={app.token[:12]}...")
    log(f"  Version: {app.runtime_info()['version']}")

    # --- 2. API STATUS ---
    log("\n[2] API /api/status (Bearer)...")
    code, data = api(app.port, app.token, "GET", "/api/status")
    log(f"  HTTP {code}")
    if code == 200:
        log(f"  health.ok={data['health']['ok']}")
        log(f"  components: {json.dumps(data['health']['components'])}")
        log(f"  products={data['database']['products']} obs={data['database']['observations']} "
            f"prices={data['database']['price_points']} schema=v{data['database']['schema_version']}")
        log(f"  inbox_pending={data['inbox_pending']}")
        log(f"  tasks={json.dumps(data['tasks'])}")
        log(f"  sources={json.dumps(data['sources'], ensure_ascii=False)}")
        log(f"  backups={json.dumps(data['backups'], ensure_ascii=False)}")
    else:
        log(f"  DATA: {json.dumps(data, ensure_ascii=False)}")

    # --- 3. UI AUTH ---
    log("\n[3] UI auth (code → redeem → cookie)...")
    code, data = api(app.port, app.token, "POST", "/api/ui/code", {})
    log(f"  /api/ui/code: HTTP {code} → {json.dumps(data)}")
    ui_code = data.get("code", "")
    if not ui_code:
        log("  WARNING: no code, trying /api/ui/issue")
        code, data = api(app.port, app.token, "POST", "/api/ui/issue", {})
        log(f"  /api/ui/issue: HTTP {code} → {json.dumps(data)}")
        ui_code = data.get("code", "")

    # Redeem
    code, data = api(app.port, app.token, "POST", f"/api/ui/redeem?code=[REDACTED_AUTH_SECRET]")
    log(f"  /api/ui/redeem: HTTP {code} → {json.dumps(data)}")
    session = data.get("session", data.get("cookie", ""))

    # Verify with cookie
    if session:
        code2, data2 = api(app.port, None, "GET", "/api/status", cookie=f"r52_session={session}")
        log(f"  Cookie-auth /api/status: HTTP {code2}")
    else:
        log("  No session cookie returned (may use Set-Cookie header)")

    # --- 4. OPEN EDGE ---
    log("\n[4] Opening Edge...")
    edge_url = f"http://127.0.0.1:{app.port}/?code=[REDACTED_AUTH_SECRET]"
    log(f"  URL: {edge_url}")
    try:
        subprocess.Popen(
            ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
             "--new-window", edge_url],
            creationflags=0x00000008)  # DETACHED_PROCESS
        log("  Edge launched (detached)")
    except Exception as e:
        log(f"  Edge launch failed: {e}")
    time.sleep(3)

    # --- 5. SCHEDULER ---
    log("\n[5] Scheduler (/api/schedules)...")
    code, data = api(app.port, app.token, "GET", "/api/schedules")
    log(f"  HTTP {code}")
    items = data.get("items", [])
    log(f"  Count: {len(items)}")
    for s in items:
        log(f"    [{s.get('id')}] {s.get('name')} | kind={s.get('kind')} "
            f"enabled={s.get('enabled')} next={s.get('next_run_at')}")

    # Scheduler component health
    code, data = api(app.port, app.token, "GET", "/api/status")
    if code == 200:
        log(f"  Scheduler alive: {data['health']['components'].get('scheduler')}")

    # --- 6. EXPORT (via API) ---
    log("\n[6] Export (POST /api/export)...")
    code, data = api(app.port, app.token, "POST", "/api/export", {})
    log(f"  HTTP {code} → {json.dumps(data, ensure_ascii=False)}")

    # --- 7. BACKUP (via API) ---
    log("\n[7] Backup (POST /api/backups)...")
    code, data = api(app.port, app.token, "POST", "/api/backups", {})
    log(f"  HTTP {code} → {json.dumps(data, ensure_ascii=False)}")
    task_id = (data.get("task") or {}).get("id", "")
    if task_id:
        # Wait for task
        for i in range(30):
            time.sleep(1)
            code, tdata = api(app.port, app.token, "GET", f"/api/tasks/{task_id}")
            status = tdata.get("status", "")
            if status in ("COMPLETED", "FAILED", "PARTIAL"):
                log(f"  Task {task_id}: {status}")
                if tdata.get("result"):
                    log(f"  Result: {json.dumps(tdata['result'], ensure_ascii=False)}")
                if tdata.get("error_message"):
                    log(f"  Error: {tdata['error_message']}")
                break
        else:
            log("  Task timeout (30s)")

    # --- 8. BACKUPS LIST ---
    log("\n[8] Backups list...")
    backups_dir = Path(r"C:\Users\aleks\AppData\Local\R52\backups")
    if backups_dir.exists():
        files = sorted(backups_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
        log(f"  Count: {len(files)}")
        for f in files[:5]:
            log(f"    {f.name} ({f.stat().st_size} bytes)")
    else:
        log("  backups dir not found")

    # --- 9. EXPORTS DIR ---
    log("\n[9] Exports dir...")
    exports_dir = Path(r"C:\Users\aleks\AppData\Local\R52\exports")
    if exports_dir.exists():
        files = sorted(exports_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        log(f"  Count: {len(files)}")
        for f in files[:5]:
            log(f"    {f.name} ({f.stat().st_size} bytes)")
    else:
        log("  exports dir not found")

    # --- 10. CLI COMMANDS (subprocess, core is running) ---
    log("\n[10] CLI commands (core running)...")
    cli_base = [sys.executable, "-m", "r52.cli"]
    cli_cwd = str(Path(__file__).parent)

    for label, args in [
        ("status", ["status"]),
        ("doctor", ["doctor"]),
        ("migrate", ["migrate"]),
        ("backups", ["backups"]),
    ]:
        try:
            r = subprocess.run(cli_base + args, capture_output=True, text=True,
                               cwd=cli_cwd, timeout=15)
            out = r.stdout.strip()
            err = r.stderr.strip()
            log(f"  {label}: exit={r.returncode}")
            if out:
                for line in out.splitlines()[:10]:
                    log(f"    {line}")
            if err:
                for line in err.splitlines()[:5]:
                    log(f"    ERR: {line}")
        except Exception as e:
            log(f"  {label}: exception {e}")

    # --- 11. RESTORE TEST ---
    log("\n[11] Restore test (stop core → restore → restart)...")
    # Stop core
    app.stop()
    log("  Core stopped")
    time.sleep(1)

    # Pick latest backup
    if backups_dir.exists():
        files = sorted(backups_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            target = files[0].name
            log(f"  Restoring from: {target}")
            try:
                r = subprocess.run(cli_base + ["restore", target, "--yes"],
                                   capture_output=True, text=True, cwd=cli_cwd, timeout=20)
                log(f"  restore: exit={r.returncode}")
                if r.stdout.strip():
                    log(f"    {r.stdout.strip()}")
                if r.stderr.strip():
                    log(f"    ERR: {r.stderr.strip()}")
            except Exception as e:
                log(f"  restore exception: {e}")

            # Restart core after restore
            log("  Restarting core after restore...")
            app2 = App(config)
            app2.start(port=8423)
            time.sleep(2)
            code, data = api(app2.port, app2.token, "GET", "/api/status")
            log(f"  Post-restore /api/status: HTTP {code}")
            if code == 200:
                log(f"    health.ok={data['health']['ok']} products={data['database']['products']}")
                # Keep this core alive for Edge
                log("\n[12] Core alive for Edge. Waiting 90s...")
                for i in range(180):
                    time.sleep(0.5)
                    if app2.shutdown_requested.is_set():
                        break
                app2.stop()
            else:
                log(f"    DATA: {json.dumps(data, ensure_ascii=False)}")
        else:
            log("  No backups to restore")
    else:
        log("  No backups dir")

    log("\n=== DONE ===")

except Exception as e:
    import traceback
    log(f"\nFATAL: {type(e).__name__}: {e}")
    log(traceback.format_exc())
    # Try to stop core if it started
    try:
        app.stop()
    except Exception:
        pass

finally:
    LOG.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport saved to {LOG}", flush=True)

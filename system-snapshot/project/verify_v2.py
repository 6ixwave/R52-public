"""Проверка v2: UI в Edge, scheduler, export, backup/restore.
Ядро живёт 90 сек после проверок — хватает на визуальный осмотр в Edge."""
import sys, os, time, json, urllib.request, urllib.error, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

LOG = Path(r"C:\Users\aleks\AppData\Local\R52\logs\verify_v2.log")
LOG.parent.mkdir(parents=True, exist_ok=True)
LOG.write_text("", encoding="utf-8")

def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def api(port, token, method, path, body=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"raw": e.read().decode("utf-8", errors="replace")[:300]}
    except Exception as e:
        return 0, {"error": str(e)}

try:
    from r52.config import load_config
    from r52.service import App

    config = load_config(None)
    app = App(config)
    app.open()
    app.start(port=8423)
    token = app.token
    port = app.port
    log(f"Core: port={port} pid={os.getpid()}")

    # === 1. HEALTH ===
    code, data = api(port, token, "GET", "/healthz")
    log(f"[01] /healthz: {code} ok={data.get('ok')}")

    # === 2. STATUS ===
    code, data = api(port, token, "GET", "/api/status")
    log(f"[02] /api/status: {code} v{data.get('version')} products_health={data.get('health',{}).get('ok')}")

    # === 3. PRODUCTS ===
    code, data = api(port, token, "GET", "/api/products?limit=5")
    items = data.get("items", [])
    log(f"[03] /api/products: {code} total={data.get('total')} first={items[0].get('title','?')[:40] if items else 'none'}")

    # === 4. UI SESSION (get one-time code) ===
    code, data = api(port, token, "POST", "/api/ui/session", {})
    log(f"[04] POST /api/ui/session: {code} {json.dumps(data, ensure_ascii=False)[:150]}")
    ui_code = data.get("code") if isinstance(data, dict) else None
    ui_url = f"http://127.0.0.1:{port}/ui?code=[REDACTED_AUTH_SECRET]" if ui_code else None
    log(f"[04] UI URL: {ui_url}")

    # === 5. OPEN EDGE ===
    if ui_url:
        try:
            os.startfile(ui_url)
            log("[05] Edge opened with UI URL")
        except Exception as e:
            log(f"[05] Edge open ERR: {e}")
            # fallback: webbrowser
            import webbrowser
            webbrowser.open(ui_url)
            log("[05] webbrowser fallback opened")
    else:
        log("[05] No UI code, cannot open Edge")

    # === 6. SCHEDULER: list ===
    code, data = api(port, token, "GET", "/api/schedules")
    scheds = data.get("items", [])
    log(f"[06] GET /api/schedules: {code} count={len(scheds)}")
    for s in scheds[:5]:
        log(f"     - {s.get('key')}: kind={s.get('kind')} rule={s.get('rule')} enabled={s.get('enabled')}")

    # === 7. SCHEDULER: trigger via tasks (collect) ===
    code, data = api(port, token, "POST", "/api/tasks", {"kind": "collect", "params": {}})
    log(f"[07] POST /api/tasks (collect): {code} {json.dumps(data, ensure_ascii=False)[:200]}")
    task_id = data.get("task", {}).get("id") if isinstance(data, dict) else None

    # === 8. SCHEDULER: check task result (wait 3s) ===
    if task_id:
        time.sleep(3)
        code, data = api(port, token, "GET", f"/api/tasks/{task_id}")
        log(f"[08] Task {task_id[:8]}...: {code} status={data.get('status')}")
    else:
        log("[08] No task id")

    # === 9. EXPORT ===
    code, data = api(port, token, "POST", "/api/export", {})
    log(f"[09] POST /api/export: {code} path={data.get('path','?')} size={data.get('size')} products={data.get('products')}")

    # === 10. EXPORT: verify file ===
    exp_path = data.get("path", "")
    if exp_path and Path(exp_path).exists():
        sz = Path(exp_path).stat().st_size
        log(f"[10] Export file: {Path(exp_path).name} ({sz} bytes) EXISTS")
    else:
        log(f"[10] Export file MISSING: {exp_path}")

    # === 11. BACKUP: create (POST /api/backups) ===
    code, data = api(port, token, "POST", "/api/backups", {})
    log(f"[11] POST /api/backups: {code} {json.dumps(data, ensure_ascii=False)[:200]}")
    bk_task = data.get("task", {})
    bk_task_id = bk_task.get("id") if isinstance(bk_task, dict) else None

    # Wait for backup task
    if bk_task_id:
        time.sleep(3)
        code, data = api(port, token, "GET", f"/api/tasks/{bk_task_id}")
        log(f"[11b] Backup task: status={data.get('status')}")

    # === 12. BACKUPS: list ===
    code, data = api(port, token, "GET", "/api/backups")
    bks = data.get("items", [])
    log(f"[12] GET /api/backups: {code} count={len(bks)}")
    for b in bks[:3]:
        log(f"     - {b.get('name')} ({b.get('size')} bytes) {b.get('label')}")

    # === 13. RESTORE: via CLI ===
    log("[13] RESTORE via CLI...")
    cli_path = str(Path(__file__).parent / "r52" / "__main__.py")
    # Use: python -m r52.cli restore --latest
    r = subprocess.run(
        [sys.executable, "-m", "r52.cli", "restore", "--latest", "--yes"],
        capture_output=True, text=True, timeout=30,
        cwd=str(Path(__file__).parent)
    )
    log(f"[13] CLI restore: exit={r.returncode}")
    if r.stdout:
        log(f"     stdout: {r.stdout.strip()[:200]}")
    if r.stderr:
        log(f"     stderr: {r.stderr.strip()[:200]}")

    # === 14. POST-RESTORE: status + products ===
    time.sleep(1)
    code, data = api(port, token, "GET", "/api/status")
    log(f"[14] Post-restore /api/status: {code} ok={data.get('health',{}).get('ok')}")
    code, data = api(port, token, "GET", "/api/products?limit=1")
    log(f"[14b] Post-restore products: {code} total={data.get('total')}")

    # === 15. BACKUP FILES on disk ===
    bk_dir = Path(r"C:\Users\aleks\AppData\Local\R52\backups")
    if bk_dir.exists():
        bfiles = sorted(bk_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        log(f"[15] Backup files: {[(f.name, f.stat().st_size) for f in bfiles]}")

    # === 16. EXPORT FILES on disk ===
    ex_dir = Path(r"C:\Users\aleks\AppData\Local\R52\exports")
    if ex_dir.exists():
        efiles = sorted(ex_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        log(f"[16] Export files: {[(f.name, f.stat().st_size) for f in efiles]}")

    # === SUMMARY ===
    log("")
    log(f"=== UI URL (open in Edge): {ui_url} ===")
    log(f"=== Core alive 90s for visual check ===")
    log("=== VERIFY V2 DONE ===")

    # Keep alive 90s
    for i in range(180):
        time.sleep(0.5)
        if app.shutdown_requested.is_set():
            log("Shutdown requested, breaking wait")
            break

except Exception as e:
    import traceback
    log(f"EXCEPTION: {type(e).__name__}: {e}")
    log(traceback.format_exc())
finally:
    try:
        app.stop()
        log("Core stopped.")
    except Exception:
        pass

"""Финальная проверка: UI в Edge + scheduler + export + backup/restore.
Ядро живёт 120 сек после проверок — время на визуальный осмотр."""
import sys, os, time, json, urllib.request, urllib.error, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

LOG = Path(r"C:\Users\aleks\AppData\Local\R52\logs\verify_v3.log")
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

    # === 2. PRODUCTS (get first id for scheduler) ===
    code, data = api(port, token, "GET", "/api/products?limit=3")
    items = data.get("items", [])
    first_pid = items[0].get("id") if items else 1
    log(f"[02] /api/products: {code} total={data.get('total')} first_id={first_pid}")

    # === 3. UI SESSION ===
    code, data = api(port, token, "POST", "/api/ui/session", {})
    ui_code = data.get("code") if isinstance(data, dict) else None
    ui_url = f"http://127.0.0.1:{port}/ui?code={ui_code}" if ui_code else None
    log(f"[03] UI session: {code} url={ui_url}")

    # === 4. FETCH UI HTML (confirm real page) ===
    if ui_code:
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        # Exchange code for cookie
        try:
            r1 = opener.open(f"http://127.0.0.1:{port}/ui?code={ui_code}", timeout=5)
            html = r1.read().decode("utf-8", errors="replace")
            log(f"[04] UI page: HTTP {r1.status}, len={len(html)}")
            # Check for meaningful content
            markers = ["r-52", "r52", "product", "dashboard", "app", "vue", "react", "html", "div", "main"]
            found = [m for m in markers if m in html.lower()]
            log(f"[04] markers found: {found}")
            if len(html) > 200:
                log(f"[04] html head: {html[:200].strip()}")
            cookies = [c.name for c in cj]
            log(f"[04] cookies: {cookies}")
        except Exception as e:
            log(f"[04] UI fetch ERR: {type(e).__name__}: {e}")

    # === 5. OPEN EDGE ===
    if ui_url:
        try:
            os.startfile(ui_url)
            log("[05] Edge opened")
        except Exception as e:
            log(f"[05] startfile ERR: {e}")
            import webbrowser
            webbrowser.open(ui_url)
            log("[05] webbrowser opened")

    # === 6. SCHEDULER: list ===
    code, data = api(port, token, "GET", "/api/schedules")
    scheds = data.get("items", [])
    log(f"[06] Schedules: {code} count={len(scheds)}")
    for s in scheds:
        log(f"     {s.get('key')}: kind={s.get('kind')} rule={s.get('rule')} enabled={s.get('enabled')} last_run={str(s.get('last_run_at',''))[:19]}")

    # === 7. SCHEDULER: trigger collect with product_id ===
    code, data = api(port, token, "POST", "/api/tasks",
                     {"kind": "collect", "params": {"product_id": first_pid}})
    log(f"[07] Task collect(pid={first_pid}): {code} {json.dumps(data, ensure_ascii=False)[:200]}")
    task_id = data.get("task", {}).get("id") if isinstance(data, dict) else None

    # === 8. Wait for task ===
    if task_id:
        time.sleep(4)
        code, data = api(port, token, "GET", f"/api/tasks/{task_id}")
        log(f"[08] Task {task_id[:12]}: status={data.get('status')} result={json.dumps(data.get('result',{}), ensure_ascii=False)[:150]}")
    else:
        log("[08] No task id, skip")

    # === 9. EXPORT ===
    code, data = api(port, token, "POST", "/api/export", {})
    exp_path = data.get("path", "")
    log(f"[09] Export: {code} file={Path(exp_path).name if exp_path else '?'} size={data.get('size')} products={data.get('products')} prices={data.get('prices')}")

    # === 10. BACKUP ===
    code, data = api(port, token, "POST", "/api/backups", {})
    bk_task_id = data.get("task", {}).get("id") if isinstance(data, dict) else None
    log(f"[10] Backup task: {code} id={bk_task_id[:12] if bk_task_id else '?'}")
    if bk_task_id:
        time.sleep(3)
        code, data = api(port, token, "GET", f"/api/tasks/{bk_task_id}")
        log(f"[10b] Backup status: {data.get('status')}")

    # === 11. BACKUPS list ===
    code, data = api(port, token, "GET", "/api/backups")
    bks = data.get("items", [])
    log(f"[11] Backups: {code} count={len(bks)} latest={bks[0].get('name') if bks else '?'}")

    # === 12. RESTORE via CLI ===
    log("[12] CLI restore --latest...")
    r = subprocess.run(
        [sys.executable, "-m", "r52.cli", "restore", "--latest", "--yes"],
        capture_output=True, text=True, timeout=30,
        cwd=str(Path(__file__).parent)
    )
    log(f"[12] CLI restore: exit={r.returncode} stdout={r.stdout.strip()[:150]}")
    if r.stderr:
        log(f"[12] stderr: {r.stderr.strip()[:150]}")

    # === 13. POST-RESTORE ===
    time.sleep(1)
    code, data = api(port, token, "GET", "/api/status")
    log(f"[13] Post-restore status: {code} ok={data.get('health',{}).get('ok')}")
    code, data = api(port, token, "GET", "/api/products?limit=1")
    log(f"[13b] Post-restore products: {code} total={data.get('total')}")

    # === 14. FILES ON DISK ===
    ex_dir = Path(r"C:\Users\aleks\AppData\Local\R52\exports")
    bk_dir = Path(r"C:\Users\aleks\AppData\Local\R52\backups")
    if ex_dir.exists():
        ef = sorted(ex_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:2]
        log(f"[14] Exports: {[(f.name, f.stat().st_size) for f in ef]}")
    if bk_dir.exists():
        bf = sorted(bk_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:2]
        log(f"[14] Backups: {[(f.name, f.stat().st_size) for f in bf]}")

    # === SUMMARY ===
    log("")
    log(f"=== UI URL: {ui_url} ===")
    log(f"=== Core alive 120s — LOOK AT EDGE NOW ===")
    log("=== VERIFY V3 DONE ===")

    # Keep alive 120s
    for i in range(240):
        time.sleep(0.5)
        if app.shutdown_requested.is_set():
            log("Shutdown requested")
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

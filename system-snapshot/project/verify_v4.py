"""Финальная проверка v4: UI + scheduler(WB) + export + backup/restore.
После правки handlers.py (инстанцирование коллектора)."""
import sys, os, time, json, urllib.request, urllib.error, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

LOG = Path(r"C:\Users\aleks\AppData\Local\R52\logs\verify_v4.log")
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
        resp = urllib.request.urlopen(req, timeout=15)
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

    # 1. Health
    code, data = api(port, token, "GET", "/healthz")
    log(f"[01] /healthz: {code} ok={data.get('ok')}")

    # 2. UI session
    code, data = api(port, token, "POST", "/api/ui/session", {})
    ui_code = data.get("code") if isinstance(data, dict) else None
    ui_url = f"http://127.0.0.1:{port}/ui?code=[REDACTED_AUTH_SECRET]" if ui_code else None
    log(f"[02] UI: {code} url={ui_url}")

    # 3. Fetch UI HTML
    if ui_code:
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        r1 = opener.open(f"http://127.0.0.1:{port}/ui?code=[REDACTED_AUTH_SECRET]", timeout=5)
        html = r1.read().decode("utf-8", errors="replace")
        log(f"[03] UI HTML: {r1.status} len={len(html)}")
        markers = [m for m in ["r-52","r52","product","div","main","html","vue","react"] if m in html.lower()]
        log(f"[03] markers: {markers}")

    # 4. Open Edge
    if ui_url:
        os.startfile(ui_url)
        log("[04] Edge opened")

    # 5. Scheduler list
    code, data = api(port, token, "GET", "/api/schedules")
    scheds = data.get("items", [])
    log(f"[05] Schedules: {code} count={len(scheds)}")
    for s in scheds:
        log(f"     {s.get('key')}: kind={s.get('kind')} enabled={s.get('enabled')}")

    # 6. Collect WB product (id=1)
    code, data = api(port, token, "POST", "/api/tasks",
                     {"kind": "collect", "params": {"product_id": 1}})
    log(f"[06] Collect WB(id=1): {code} {json.dumps(data, ensure_ascii=False)[:200]}")
    task_id = data.get("task", {}).get("id") if isinstance(data, dict) else None

    # 7. Wait for collect task
    if task_id:
        time.sleep(8)
        code, data = api(port, token, "GET", f"/api/tasks/{task_id}")
        log(f"[07] Task {task_id[:12]}: status={data.get('status')} result={json.dumps(data.get('result',{}), ensure_ascii=False)[:200]}")
    else:
        log("[07] No task id")

    # 8. Export
    code, data = api(port, token, "POST", "/api/export", {})
    exp_path = data.get("path", "")
    log(f"[08] Export: {code} {Path(exp_path).name if exp_path else '?'} size={data.get('size')} products={data.get('products')}")

    # 9. Backup
    code, data = api(port, token, "POST", "/api/backups", {})
    bk_id = data.get("task", {}).get("id", "")
    log(f"[09] Backup: {code} task={bk_id[:12]}")
    time.sleep(3)
    code, data = api(port, token, "GET", f"/api/tasks/{bk_id}")
    log(f"[09b] Backup status: {data.get('status')}")

    # 10. Backups list
    code, data = api(port, token, "GET", "/api/backups")
    bks = data.get("items", [])
    log(f"[10] Backups: {code} count={len(bks)} latest={bks[0].get('name') if bks else '?'}")

    # 11. CLI restore
    r = subprocess.run(
        [sys.executable, "-m", "r52.cli", "restore", "--latest", "--yes"],
        capture_output=True, text=True, timeout=30,
        cwd=str(Path(__file__).parent)
    )
    log(f"[11] CLI restore: exit={r.returncode} out={r.stdout.strip()[:100]}")

    # 12. Post-restore
    time.sleep(1)
    code, data = api(port, token, "GET", "/api/status")
    log(f"[12] Post-restore: {code} ok={data.get('health',{}).get('ok')}")
    code, data = api(port, token, "GET", "/api/products?limit=1")
    log(f"[12b] Products: {code} total={data.get('total')}")

    # 13. Files
    ex_dir = Path(r"C:\Users\aleks\AppData\Local\R52\exports")
    bk_dir = Path(r"C:\Users\aleks\AppData\Local\R52\backups")
    if ex_dir.exists():
        ef = sorted(ex_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:2]
        log(f"[13] Exports: {[(f.name, f.stat().st_size) for f in ef]}")
    if bk_dir.exists():
        bf = sorted(bk_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:2]
        log(f"[13] Backups: {[(f.name, f.stat().st_size) for f in bf]}")

    log("")
    log(f"=== UI: {ui_url} ===")
    log("=== 120s alive ===")
    log("=== V4 DONE ===")

    for i in range(240):
        time.sleep(0.5)
        if app.shutdown_requested.is_set():
            break

except Exception as e:
    import traceback
    log(f"EXCEPTION: {type(e).__name__}: {e}")
    log(traceback.format_exc())
finally:
    try:
        app.stop()
        log("Stopped.")
    except Exception:
        pass

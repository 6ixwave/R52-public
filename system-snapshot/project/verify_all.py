"""Полная проверка: UI, scheduler, export, backup/restore.
Запускает ядро, гоняет API, печатает URL для Edge."""
import sys, os, time, json, urllib.request, urllib.error, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

LOG = Path(r"C:\Users\aleks\AppData\Local\R52\logs\verify_all.log")
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
    # --- Start core ---
    from r52.config import load_config
    from r52.service import App

    config = load_config(None)
    app = App(config)
    app.open()
    app.start(port=8423)
    token = app.token
    port = app.port
    log(f"Core: port={port} token={token[:8]}...")

    # --- 1. Health ---
    code, data = api(port, token, "GET", "/healthz")
    log(f"[01] /healthz: {code} {json.dumps(data, ensure_ascii=False)[:200]}")

    # --- 2. Status ---
    code, data = api(port, token, "GET", "/api/status")
    log(f"[02] /api/status: {code} {json.dumps(data, ensure_ascii=False)[:250]}")

    # --- 3. Products count ---
    code, data = api(port, token, "GET", "/api/products?limit=1")
    total = data.get("total", "?") if isinstance(data, dict) else "?"
    log(f"[03] /api/products: {code} total={total}")

    # --- 4. UI: get one-time code ---
    code, data = api(port, token, "GET", "/api/ui/code")
    log(f"[04] /api/ui/code: {code} {json.dumps(data, ensure_ascii=False)[:200]}")
    ui_code = data.get("code") if isinstance(data, dict) else None

    # --- 5. UI: enter (exchange code -> cookie), fetch page ---
    ui_url = f"http://127.0.0.1:{port}/ui?code=[REDACTED_AUTH_SECRET]" if ui_code else None
    if ui_code:
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        try:
            resp = opener.open(f"http://127.0.0.1:{port}/api/ui/enter?code=[REDACTED_AUTH_SECRET]", timeout=5)
            cookies = [c.name for c in cj]
            log(f"[05] /api/ui/enter: {resp.status} cookies={cookies}")
            # Fetch the actual UI page
            resp2 = opener.open(f"http://127.0.0.1:{port}/", timeout=5)
            html = resp2.read().decode("utf-8", errors="replace")
            log(f"[05b] GET /: {resp2.status} len={len(html)}")
            for marker in ["R-52", "r52", "products", "dashboard", "app", "vue", "react"]:
                if marker.lower() in html.lower():
                    log(f"[05b]   marker found: {marker}")
            if len(html) > 50:
                log(f"[05b]   html head: {html[:150]}")
        except Exception as e:
            log(f"[05] UI enter ERR: {type(e).__name__}: {e}")
    else:
        log("[05] No UI code, skipping enter")

    # --- 6. Schedules: list ---
    code, data = api(port, token, "GET", "/api/schedules")
    log(f"[06] GET /api/schedules: {code} {json.dumps(data, ensure_ascii=False)[:300]}")

    # --- 7. Schedules: create ---
    sched_body = {"name": "verify-test", "cron": "*/5 * * * *", "action": "collect", "source_id": 1}
    code, data = api(port, token, "POST", "/api/schedules", sched_body)
    log(f"[07] POST /api/schedules: {code} {json.dumps(data, ensure_ascii=False)[:300]}")
    sched_id = data.get("id") if isinstance(data, dict) else None

    # --- 8. Schedules: trigger ---
    if sched_id:
        code, data = api(port, token, "POST", f"/api/schedules/{sched_id}/trigger", {})
        log(f"[08] POST /api/schedules/{sched_id}/trigger: {code} {json.dumps(data, ensure_ascii=False)[:200]}")
    else:
        log("[08] No schedule id, skip trigger")

    # --- 9. Export: trigger ---
    code, data = api(port, token, "POST", "/api/export", {})
    log(f"[09] POST /api/export: {code} {json.dumps(data, ensure_ascii=False)[:300]}")

    # --- 10. Export: check files ---
    exports_dir = Path(r"C:\Users\aleks\AppData\Local\R52\exports")
    if exports_dir.exists():
        files = sorted(exports_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        log(f"[10] Export files: {[(f.name, f.stat().st_size) for f in files]}")
    else:
        log("[10] Exports dir missing")

    # --- 11. Backup: create ---
    code, data = api(port, token, "POST", "/api/backup", {})
    log(f"[11] POST /api/backup: {code} {json.dumps(data, ensure_ascii=False)[:300]}")
    backup_id = data.get("id") if isinstance(data, dict) else None

    # --- 12. Backups: list ---
    code, data = api(port, token, "GET", "/api/backups")
    log(f"[12] GET /api/backups: {code} {json.dumps(data, ensure_ascii=False)[:300]}")

    # --- 13. Restore ---
    restore_id = backup_id
    if not restore_id and isinstance(data, dict):
        items = data.get("items", data.get("backups", []))
        if items:
            restore_id = items[0].get("id")
    if restore_id:
        code, data = api(port, token, "POST", "/api/restore", {"id": restore_id})
        log(f"[13] POST /api/restore: {code} {json.dumps(data, ensure_ascii=False)[:300]}")
    else:
        log("[13] No backup id, skip restore")

    # --- 14. Post-restore status ---
    time.sleep(1)
    code, data = api(port, token, "GET", "/api/status")
    log(f"[14] Post-restore /api/status: {code} {json.dumps(data, ensure_ascii=False)[:250]}")

    # --- 15. Post-restore products ---
    code, data = api(port, token, "GET", "/api/products?limit=1")
    total2 = data.get("total", "?") if isinstance(data, dict) else "?"
    log(f"[15] Post-restore products: {code} total={total2}")

    # --- 16. Backup files on disk ---
    backups_dir = Path(r"C:\Users\aleks\AppData\Local\R52\backups")
    if backups_dir.exists():
        bfiles = sorted(backups_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        log(f"[16] Backup files: {[(f.name, f.stat().st_size) for f in bfiles]}")

    # --- Summary ---
    log(f"")
    log(f"=== UI URL for Edge: {ui_url} ===")
    log(f"=== VERIFY ALL DONE ===")

except Exception as e:
    import traceback
    log(f"EXCEPTION: {type(e).__name__}: {e}")
    log(traceback.format_exc())
finally:
    try:
        app.stop()
    except Exception:
        pass

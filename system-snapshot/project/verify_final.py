"""ФИНАЛЬНАЯ проверка: UI в Edge + scheduler + export + backup/restore.
Scheduler проверяем через maintenance (локальная, без сети) и backup task.
Collect — бонус, если WB/OZON откликнется."""
import sys, os, time, json, urllib.request, urllib.error, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

LOG = Path(r"C:\Users\aleks\AppData\Local\R52\logs\verify_final.log")
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

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    log(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

try:
    from r52.config import load_config
    from r52.service import App

    config = load_config(None)
    app = App(config)
    app.open()
    app.start(port=8423)
    token = app.token
    port = app.port
    log(f"Core started: port={port} pid={os.getpid()}")

    # ===== 1. UI =====
    log("\n--- 1. UI в Edge ---")
    code, data = api(port, token, "POST", "/api/ui/session", {})
    ui_code = data.get("code") if isinstance(data, dict) else None
    ui_url = f"http://127.0.0.1:{port}/ui?code=[REDACTED_AUTH_SECRET]" if ui_code else None
    check("UI session", code == 201 and ui_code, f"code={code}")

    ui_html_ok = False
    if ui_code:
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        try:
            r1 = opener.open(f"http://127.0.0.1:{port}/ui?code=[REDACTED_AUTH_SECRET]", timeout=5)
            html = r1.read().decode("utf-8", errors="replace")
            ui_html_ok = r1.status == 200 and len(html) > 500
            check("UI HTML", ui_html_ok, f"status={r1.status} len={len(html)}")
        except Exception as e:
            check("UI HTML", False, str(e))

    edge_ok = False
    if ui_url:
        try:
            os.startfile(ui_url)
            edge_ok = True
            check("Edge opened", True, ui_url)
        except Exception as e:
            check("Edge opened", False, str(e))

    # ===== 2. SCHEDULER =====
    log("\n--- 2. Scheduler ---")
    code, data = api(port, token, "GET", "/api/schedules")
    scheds = data.get("items", [])
    check("Schedules list", code == 200 and len(scheds) > 0, f"count={len(scheds)}")
    for s in scheds:
        log(f"    {s.get('key')}: kind={s.get('kind')} enabled={s.get('enabled')}")

    # Trigger maintenance (local, no network)
    code, data = api(port, token, "POST", "/api/tasks",
                     {"kind": "maintenance", "params": {}})
    maint_id = data.get("task", {}).get("id") if isinstance(data, dict) else None
    check("Task maintenance", code in (200, 201) and maint_id, f"id={str(maint_id)[:12]}")

    if maint_id:
        time.sleep(4)
        code, data = api(port, token, "GET", f"/api/tasks/{maint_id}")
        st = data.get("status")
        check("Maintenance result", st == "COMPLETED", f"status={st}")

    # Trigger digest (local)
    code, data = api(port, token, "POST", "/api/tasks",
                     {"kind": "digest", "params": {}})
    dig_id = data.get("task", {}).get("id") if isinstance(data, dict) else None
    check("Task digest", code in (200, 201) and dig_id, f"id={str(dig_id)[:12]}")

    if dig_id:
        time.sleep(4)
        code, data = api(port, token, "GET", f"/api/tasks/{dig_id}")
        st = data.get("status")
        check("Digest result", st == "COMPLETED", f"status={st}")

    # Collect (bonus — network)
    code, data = api(port, token, "GET", "/api/products?limit=1")
    items = data.get("items", [])
    if items:
        pid = items[0].get("id")
        src = items[0].get("source", "?")
        code, data = api(port, token, "POST", "/api/tasks",
                         {"kind": "collect", "params": {"product_id": pid}})
        col_id = data.get("task", {}).get("id") if isinstance(data, dict) else None
        if col_id:
            time.sleep(10)
            code, data = api(port, token, "GET", f"/api/tasks/{col_id}")
            st = data.get("status")
            check(f"Collect (bonus, {src} pid={pid})", st in ("COMPLETED", "PARTIAL"), f"status={st}")
        else:
            check("Collect (bonus)", False, "no task id")
    else:
        check("Collect (bonus)", False, "no products")

    # ===== 3. EXPORT =====
    log("\n--- 3. Export ---")
    code, data = api(port, token, "POST", "/api/export", {})
    exp_path = data.get("path", "")
    exp_ok = code == 201 and exp_path and Path(exp_path).exists()
    check("Export trigger", exp_ok, f"file={Path(exp_path).name if exp_path else '?'} size={data.get('size')} products={data.get('products')}")

    # ===== 4. BACKUP =====
    log("\n--- 4. Backup ---")
    code, data = api(port, token, "POST", "/api/backups", {})
    bk_id = data.get("task", {}).get("id", "")
    check("Backup trigger", code == 202 and bk_id, f"task={bk_id[:12]}")

    if bk_id:
        time.sleep(4)
        code, data = api(port, token, "GET", f"/api/tasks/{bk_id}")
        st = data.get("status")
        check("Backup task", st == "COMPLETED", f"status={st}")

    code, data = api(port, token, "GET", "/api/backups")
    bks = data.get("items", [])
    check("Backups list", code == 200 and len(bks) > 0, f"count={len(bks)} latest={bks[0].get('name','?') if bks else '?'}")

    # ===== 5. RESTORE (CLI) =====
    log("\n--- 5. Restore (CLI) ---")
    r = subprocess.run(
        [sys.executable, "-m", "r52.cli", "restore", "--latest", "--yes"],
        capture_output=True, text=True, timeout=30,
        cwd=str(Path(__file__).parent)
    )
    check("CLI restore", r.returncode == 0, f"exit={r.returncode} out={r.stdout.strip()[:80]}")

    # Post-restore
    time.sleep(1)
    code, data = api(port, token, "GET", "/api/status")
    ok = data.get("health", {}).get("ok")
    check("Post-restore status", code == 200 and ok, f"ok={ok}")

    code, data = api(port, token, "GET", "/api/products?limit=1")
    total = data.get("total", 0)
    check("Post-restore products", total > 0, f"total={total}")

    # ===== 6. FILES ON DISK =====
    log("\n--- 6. Files ---")
    ex_dir = Path(r"C:\Users\aleks\AppData\Local\R52\exports")
    bk_dir = Path(r"C:\Users\aleks\AppData\Local\R52\backups")
    if ex_dir.exists():
        ef = sorted(ex_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:2]
        for f in ef:
            log(f"    export: {f.name} ({f.stat().st_size} B)")
    if bk_dir.exists():
        bf = sorted(bk_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:2]
        for f in bf:
            log(f"    backup: {f.name} ({f.stat().st_size} B)")

    # ===== SUMMARY =====
    log("\n" + "=" * 50)
    log("ИТОГ:")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    for name, ok, detail in results:
        mark = "✅" if ok else "❌"
        log(f"  {mark} {name}")
    log(f"\n  Всего: {passed} PASS / {failed} FAIL")
    log(f"  UI URL: {ui_url}")
    log("=" * 50)
    log("=== CORE ALIVE 90s ===")

    # Keep alive 90s for visual check
    for i in range(180):
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
        log("Core stopped.")
    except Exception:
        pass

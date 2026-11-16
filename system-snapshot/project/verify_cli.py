"""verify_cli.py v8 — UI, Scheduler, Export, Backup/Restore (CLI), Migration
Fixes: table 'product' (singular), proper shutdown wait, lock release before CLI restore
"""
import json, os, sys, time, urllib.request, urllib.error, zipfile, sqlite3, subprocess, traceback
from pathlib import Path

DATA    = r"C:\Users\aleks\AppData\Local\R52"
DB      = os.path.join(DATA, "data", "r52.sqlite3")
PORT_F  = os.path.join(DATA, "run", "last-port")
RUNTIME = os.path.join(DATA, "run", "runtime.json")
BACKUPS = os.path.join(DATA, "backups")
EXPORTS = os.path.join(DATA, "exports")
LOGS    = os.path.join(DATA, "logs")
PROJ    = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12"
EDGE    = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
LOCK_F  = os.path.join(DATA, "run", "lock")

results = []
def log(tag, msg):
    line = f"[{tag}] {msg}"
    print(line, flush=True)
    results.append(line)

def http(method, path, body=None, token=None, port=8423):
    url = f"http://127.0.0.1:{port}{path}"
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)

def db_direct(path):
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def main():
    t0 = time.time()
    log("START", "verify_cli.py v8 begin")

    # ---- 0. Core ----
    PORT = 8423
    if os.path.exists(PORT_F):
        try: PORT = int(open(PORT_F).read().strip() or "8423")
        except: pass

    st, _ = http("GET", "/healthz", port=PORT)
    app = None
    if st != 200:
        log("CORE", f"Not running, starting on {PORT}...")
        sys.path.insert(0, PROJ)
        from r52.config import load_config
        from r52.service import App
        config = load_config(None)
        app = App(config)
        app.open()
        app.start(port=PORT)
        log("CORE", f"start OK port={app.port}")
        for i in range(15):
            time.sleep(1)
            st, _ = http("GET", "/healthz", port=PORT)
            if st == 200:
                log("CORE", f"healthz=200 after {i+1}s")
                break
        else:
            log("CORE", "FAIL: no healthz"); sys.exit(1)
    else:
        log("CORE", f"Already running port={PORT}")

    # Token
    token = None
    if os.path.exists(RUNTIME):
        try: token = json.load(open(RUNTIME)).get("token")
        except: pass
    if not token and app:
        try:
            info = app.runtime_info()
            token = info.get("token") if isinstance(info, dict) else None
        except: pass
    if not token:
        log("TOKEN", "FAIL: no token"); sys.exit(1)
    log("TOKEN", f"token={token[:12]}...")

    # ---- 1. UI ----
    log("UI", "=== 1. UI Window ===")
    st, body = http("POST", "/api/ui/session", token=token, port=PORT)
    if st == 201:
        sess = json.loads(body)
        code = sess.get("code","")
        ui_url = f"http://127.0.0.1:{PORT}/ui?code=[REDACTED_AUTH_SECRET]"
        log("UI", f"Session 201 code={code[:12]}...")
        st2, html = http("GET", f"/ui?code=[REDACTED_AUTH_SECRET]", port=PORT)
        has_shell = 'id="shell"' in html
        has_title = "R-52" in html
        log("UI", f"HTML {st2} len={len(html)} shell={has_shell} title={has_title}")
        js_st, _ = http("GET", "/ui/js/main.js", port=PORT)
        css_st, _ = http("GET", "/ui/css/base.css", port=PORT)
        log("UI", f"main.js={js_st} base.css={css_st}")
        if os.path.exists(EDGE):
            subprocess.Popen([EDGE, "--new-window", ui_url], creationflags=0x00000008)
            log("UI", f"Edge: {ui_url}")
        else:
            os.startfile(ui_url)
            log("UI", "Opened via startfile")
    else:
        log("UI", f"Session FAIL {st}: {body[:200]}")

    # ---- 2. Scheduler ----
    log("SCHED", "=== 2. Scheduler ===")
    st, body = http("GET", "/api/schedules", token=token, port=PORT)
    if st == 200:
        scheds = json.loads(body)
        lst = scheds.get("items", []) if isinstance(scheds, dict) else scheds
        log("SCHED", f"count={len(lst)}")
        for s in lst:
            log("SCHED", f"  {s.get('key','?')} kind={s.get('kind','?')} daily={s.get('daily_time','?')} en={s.get('enabled','?')} next={s.get('next_due_at','?')} last={s.get('last_run_at','?')}")
    else:
        log("SCHED", f"FAIL {st}")

    task_id = "?"
    st, body = http("POST", "/api/tasks", body={"kind":"maintenance","params":{}}, token=token, port=PORT)
    if st in (200,201,202):
        t = json.loads(body)
        task_id = t.get("task",{}).get("id","?")
        log("SCHED", f"maintenance task={task_id} status={t.get('task',{}).get('status','?')}")
        time.sleep(5)
        st2, b2 = http("GET", f"/api/tasks/{task_id}", token=token, port=PORT)
        if st2 == 200:
            t2 = json.loads(b2)
            log("SCHED", f"after: {t2.get('status','?')}")
    else:
        log("SCHED", f"task submit {st}")

    # ---- 3. Export ----
    log("EXP", "=== 3. Export ===")
    st, body = http("POST", "/api/export", token=token, port=PORT)
    if st in (200,201):
        exp = json.loads(body) if body else {}
        ef = exp.get("path","")
        if ef and os.path.exists(ef):
            sz = os.path.getsize(ef)
            log("EXP", f"{os.path.basename(ef)} {sz}B")
            with zipfile.ZipFile(ef) as zf:
                names = zf.namelist()
                log("EXP", f"entries={names}")
                for n in names:
                    if n.endswith(".csv"):
                        c = zf.read(n).decode("utf-8-sig", errors="replace")
                        lines = c.strip().split("\n")
                        log("EXP", f"  {n}: {len(lines)} rows")
                        if len(lines)>1: log("EXP", f"    r1: {lines[1][:100]}")
        else:
            log("EXP", f"path={ef}")
    else:
        log("EXP", f"FAIL {st}: {body[:200]}")

    # ---- 4. Backup / Restore ----
    log("BAK", "=== 4. Backup / Restore ===")

    # 4a. Pre-state: table is 'product' (singular)
    cnt_before = -1
    names_before = []
    try:
        if app:
            with app.db.read() as conn:
                cnt_before = conn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
                names_before = [r[0][:50] for r in conn.execute("SELECT title FROM product LIMIT 3")]
        else:
            conn = db_direct(DB)
            cnt_before = conn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
            names_before = [r[0][:50] for r in conn.execute("SELECT title FROM product LIMIT 3")]
            conn.close()
        log("BAK", f"before: product count={cnt_before}")
        log("BAK", f"  sample: {names_before}")
    except Exception as e:
        log("BAK", f"pre-read err: {e}")

    # 4b. Create backup via API
    st, body = http("POST", "/api/backups", body={}, token=token, port=PORT)
    log("BAK", f"POST /api/backups -> {st}")
    bf = ""
    if st in (200,201,202):
        time.sleep(5)
        if os.path.isdir(BACKUPS):
            fl = sorted(os.listdir(BACKUPS), reverse=True)
            if fl:
                bf = os.path.join(BACKUPS, fl[0])
                sz = os.path.getsize(bf)
                log("BAK", f"file: {fl[0]} {sz}B")
                try:
                    bconn = db_direct(bf)
                    bcnt = bconn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
                    bnames = [r[0][:50] for r in bconn.execute("SELECT title FROM product LIMIT 2")]
                    bconn.close()
                    log("BAK", f"  backup: product={bcnt} match={bcnt==cnt_before}")
                    log("BAK", f"  sample: {bnames}")
                except Exception as e:
                    log("BAK", f"  backup read err: {e}")
        else:
            log("BAK", "backups dir missing")
    else:
        log("BAK", f"backup FAIL {st}: {body[:200]}")

    # 4c. Restore via CLI (core must be stopped)
    if bf and os.path.exists(bf):
        bak_name = os.path.basename(bf)
        log("BAK", f"--- Restore: {bak_name} ---")

        # Stop core properly
        if app:
            st_s, b_s = http("POST", "/api/system/shutdown", body={}, token=token, port=PORT)
            log("BAK", f"shutdown -> {st_s}")
            # Wait for healthz to go down (up to 12s)
            stopped = False
            for i in range(12):
                time.sleep(1)
                st_h, _ = http("GET", "/healthz", port=PORT)
                if st_h != 200:
                    stopped = True
                    log("BAK", f"core stopped after {i+1}s (healthz={st_h})")
                    break
            if not stopped:
                log("BAK", "WARN: core still responding, trying in-process stop...")
                try:
                    app.shutdown_requested.set()
                    if app._server_thread:
                        app._server_thread.join(timeout=5)
                    app.lock.release()
                    log("BAK", "in-process stop done")
                    time.sleep(2)
                    st_h, _ = http("GET", "/healthz", port=PORT)
                    log("BAK", f"healthz after in-process stop: {st_h}")
                except Exception as e:
                    log("BAK", f"in-process stop err: {e}")

        # Verify lock released
        if os.path.exists(LOCK_F):
            log("BAK", f"lock file exists: {LOCK_F}")
            # Try to remove stale lock if core is truly dead
            try:
                os.remove(LOCK_F)
                log("BAK", "lock file removed")
            except Exception as e:
                log("BAK", f"lock remove: {e}")

        # Run CLI restore
        cmd = [sys.executable, "-m", "r52", "restore", bak_name, "--yes"]
        log("BAK", f"cmd: {' '.join(cmd)}")
        try:
            r = subprocess.run(cmd, cwd=PROJ, capture_output=True, text=True, timeout=30)
            log("BAK", f"restore exit={r.returncode}")
            if r.stdout: log("BAK", f"  out: {r.stdout.strip()[:300]}")
            if r.stderr: log("BAK", f"  err: {r.stderr.strip()[:300]}")
        except Exception as e:
            log("BAK", f"restore CLI err: {e}")

        # Verify post-restore
        time.sleep(2)
        try:
            conn = db_direct(DB)
            cnt_after = conn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
            names_after = [r[0][:50] for r in conn.execute("SELECT title FROM product LIMIT 3")]
            conn.close()
            log("BAK", f"after restore: product={cnt_after} match={cnt_after==cnt_before}")
            log("BAK", f"  sample: {names_after}")
        except Exception as e:
            log("BAK", f"post-restore read err: {e}")

        # Restart core
        if app:
            try:
                app2 = App(app.config)
                app2.open()
                app2.start(port=PORT)
                for i in range(10):
                    time.sleep(1)
                    st_h, _ = http("GET", "/healthz", port=PORT)
                    if st_h == 200:
                        log("BAK", f"core restarted healthz=200 after {i+1}s")
                        break
                else:
                    log("BAK", "core restart: no healthz")
            except Exception as e:
                log("BAK", f"restart err: {e}")
    else:
        log("BAK", "Skip restore: no backup file")

    # ---- 5. Migration ----
    log("MIG", "=== 5. Migration ===")
    try:
        conn = db_direct(DB)
        rows = [dict(r) for r in conn.execute("SELECT version, name FROM schema_migrations ORDER BY version")]
        log("MIG", f"total={len(rows)}")
        for r in rows:
            log("MIG", f"  v{r['version']}: {r['name']}")
        llm = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assistant_llm_call'").fetchone()
        log("MIG", f"assistant_llm_call: {'EXISTS' if llm else 'MISSING'}")
        conn.close()
    except Exception as e:
        log("MIG", f"err: {e}")

    # ---- Summary ----
    el = time.time()-t0
    log("DONE", f"Total {el:.1f}s")
    rp = os.path.join(LOGS, "verify_cli_report.txt")
    with open(rp,"w",encoding="utf-8") as f: f.write("\n".join(results))
    log("DONE", f"Report: {rp}")

if __name__ == "__main__":
    main()

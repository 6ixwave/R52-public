"""Диагностика запуска ядра: построчный вывод в файл."""
import sys, os, traceback, time, json, logging
from pathlib import Path

LOG = Path(r"C:\Users\aleks\AppData\Local\R52\logs\start_diag.log")
LOG.parent.mkdir(parents=True, exist_ok=True)
LOG.write_text("", encoding="utf-8")  # clean

def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

try:
    log(f"Python {sys.version}")
    log(f"CWD: {os.getcwd()}")
    log(f"PID: {os.getpid()}")

    # Step 1: import
    log("Import r52...")
    sys.path.insert(0, str(Path(__file__).parent))
    import r52
    log(f"  r52 {r52.__version__} from {r52.__file__}")

    # Step 2: config
    log("Load config...")
    from r52.config import load_config
    config = load_config(None)
    log(f"  home: {config.paths.home}")
    log(f"  db: {config.paths.db_file}")
    log(f"  port: {config.api_port}")

    # Step 3: DB check
    log("Check DB...")
    import sqlite3
    conn = sqlite3.connect(str(config.paths.db_file))
    conn.row_factory = sqlite3.Row
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    log(f"  tables ({len(tables)}): {tables}")
    try:
        mig = [dict(r) for r in conn.execute("SELECT * FROM schema_migrations ORDER BY version")]
        log(f"  migrations: {mig}")
    except Exception as e:
        log(f"  schema_migrations error: {e}")
    try:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(assistant_llm_call)")]
        log(f"  assistant_llm_call cols: {cols}")
    except Exception as e:
        log(f"  assistant_llm_call error: {e}")
    conn.close()

    # Step 4: App open
    log("App.open()...")
    from r52.service import App
    app = App(config)
    app.open()
    log("  open() OK")

    # Step 5: App start
    log("App.start()...")
    app.start(port=8423)
    log(f"  start() OK, port={app.port}, token={app.token[:8]}...")

    # Step 6: write runtime
    rt = app.runtime_info()
    log(f"  runtime: {json.dumps(rt, ensure_ascii=False)}")

    # Step 7: quick API self-test
    log("API self-test...")
    import urllib.request
    req = urllib.request.Request(
        f"http://127.0.0.1:{app.port}/api/status",
        headers={"Authorization": f"Bearer {app.token}"})
    resp = urllib.request.urlopen(req, timeout=5)
    data = json.loads(resp.read())
    log(f"  /api/status: v{data.get('version')} health={data.get('health')}")

    # Step 8: keep alive
    log("Core is RUNNING. Waiting (Ctrl+C or 120s timeout to stop)...")
    for i in range(240):  # 120 seconds
        time.sleep(0.5)
        if app.shutdown_requested.is_set():
            log("Shutdown requested")
            break
    log("Stopping...")
    app.stop()
    log("Done.")

except Exception as e:
    log(f"EXCEPTION: {type(e).__name__}: {e}")
    log(traceback.format_exc())
    sys.exit(1)

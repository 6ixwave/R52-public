"""Диагностика R-52: UI, scheduler, export, backup/restore.
Запуск: python diag_ui_sched_export_backup.py
"""
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["PYTHONPATH"] = str(ROOT)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))

RESULTS = []

def report(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    RESULTS.append((name, ok, detail))

def api(port, method, path, body=None, token=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode(errors="replace")}
    except Exception as e:
        return {"_http_error": -1, "_body": str(e)}

def clean_orphan_tables(db_path):
    """Удаляет таблицы, которые есть в БД но нет в schema_migrations (орфаны от crashed-миграций)."""
    from r52.db.schema import MIGRATIONS
    known = {v: (name, sql) for v, name, sql in MIGRATIONS}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        # Таблицы, которые создаются миграциями
        mig_tables = {
            7: "assistant_request",
            8: "assistant_llm_call",
        }
        removed = []
        for ver, tbl in mig_tables.items():
            if ver not in applied:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
                ).fetchone()
                if exists:
                    conn.execute(f"DROP TABLE IF EXISTS {tbl}")
                    removed.append(tbl)
        if removed:
            conn.commit()
            print(f"  удалены орфаны: {', '.join(removed)}", flush=True)
        return removed
    finally:
        conn.close()

def start_core(paths):
    """Запускает ядро, ждёт runtime.json. Возвращает (proc, port, token)."""
    cmd = [sys.executable, "-m", "r52", "serve", "--parent-stdin", "--ready-stdout",
           "--home", str(paths.home), "--console"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    stderr_path = ROOT / "diag_core_stderr.log"
    stderr_file = open(stderr_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=stderr_file, cwd=str(ROOT), env=env, creationflags=flags)

    # Читаем stdout в фоне (R52_READY + логи)
    lines_q = queue.Queue()
    def reader():
        try:
            for raw in iter(proc.stdout.readline, b""):
                lines_q.put(raw.decode("utf-8", "replace").strip())
        except Exception:
            pass
        lines_q.put(None)
    threading.Thread(target=reader, daemon=True).start()

    # Ждём runtime.json (надёжнее, чем парсить stdout)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr_file.flush()
            err = stderr_path.read_text(encoding="utf-8", errors="replace")
            print(f"  [core exited code={proc.returncode}]", flush=True)
            print(f"  [stderr last 800]:\n{err[-800:]}", flush=True)
            core_log = paths.logs_dir / "core.log"
            if core_log.exists():
                print(f"  [core.log tail]:\n{core_log.read_text(errors='replace')[-800:]}", flush=True)
            stderr_file.close()
            return None, 0, ""
        # runtime.json появляется после bind
        if paths.runtime_file.exists():
            try:
                info = json.loads(paths.runtime_file.read_text(encoding="utf-8"))
                port = info.get("port", 0)
                token = info.get("token", "")
                if port:
                    stderr_file.close()
                    return proc, port, token
            except (json.JSONDecodeError, OSError):
                pass
        # Также ловим R52_READY из stdout
        try:
            line = lines_q.get_nowait()
            if line and line.startswith("R52_READY "):
                info = json.loads(line[len("R52_READY "):])
                stderr_file.close()
                return proc, info["port"], info["token"]
        except queue.Empty:
            pass
        time.sleep(0.3)

    # Timeout
    print("  [timeout 40s]", flush=True)
    stderr_file.flush()
    err = stderr_path.read_text(encoding="utf-8", errors="replace")
    print(f"  [stderr last 500]:\n{err[-500:]}", flush=True)
    core_log = paths.logs_dir / "core.log"
    if core_log.exists():
        print(f"  [core.log tail]:\n{core_log.read_text(errors='replace')[-800:]}", flush=True)
    proc.kill()
    stderr_file.close()
    return None, 0, ""

def main():
    from r52.config import load_config
    from r52.lease import is_core_running

    config = load_config()
    paths = config.paths
    paths.ensure()

    # --- 0. Очистка ---
    print("\n=== 0. Очистка ===", flush=True)
    for f in (paths.lock_file, paths.runtime_file):
        if f.exists():
            f.unlink()
            print(f"  удалён {f.name}", flush=True)
    if paths.db_file.exists():
        try:
            conn = sqlite3.connect(str(paths.db_file))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            print("  WAL checkpoint", flush=True)
        except Exception as e:
            print(f"  WAL: {e}", flush=True)
        # Чистим орфанные таблицы
        clean_orphan_tables(paths.db_file)

    # --- 1. Запуск ядра ---
    print("\n=== 1. Запуск ядра ===", flush=True)
    proc, port, token = start_core(paths)
    if not proc:
        report("Ядро запущено", False, "не ответил за 40с")
        print("\n=== ИТОГ: ядро не запустилось ===", flush=True)
        return
    report("Ядро запущено", True, f"port={port}")
    time.sleep(2)

    # Verify API reachable
    st = api(port, "GET", "/api/status", token=token)
    if "_http_error" in st:
        report("API reachable", False, f"HTTP {st['_http_error']}")
        print("\n=== ИТОГ: API недоступен ===", flush=True)
        proc.stdin.close()
        return
    report("API reachable", True, f"pid={st.get('pid')}")

    # --- 2. UI-окно в браузере ---
    print("\n=== 2. UI-окно ===", flush=True)
    try:
        session = api(port, "POST", "/api/ui/session", {}, token)
        code = session.get("code", "")
        login_url = f"http://127.0.0.1:{port}/ui/login?code={code}"
        report("UI session", bool(code), f"code={code[:12]}...")

        from r52.desktop.window import find_browser, app_command
        browser = find_browser()
        if browser:
            report("Браузер", True, Path(browser).name)
            profile = paths.home / "ui" / "browser"
            first_run = not profile.is_dir()
            cmd2 = app_command(browser, login_url, profile, first_run=first_run)
            print(f"  Открываю: {Path(cmd2[0]).name} --app=...", flush=True)
            subprocess.Popen(cmd2, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
            time.sleep(7)
            presence = api(port, "GET", "/api/ui/presence", token=token)
            pages = presence.get("pages", 0)
            seen = presence.get("seen", False)
            report("UI presence", pages > 0 or seen, f"pages={pages}, seen={seen}")
            # HTTP check
            req = urllib.request.Request(login_url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode(errors="replace")
            has_ui = ("R-52" in html or "r52" in html.lower() or "login" in html.lower())
            report("UI login HTTP 200", True, f"len={len(html)}, has_UI={has_ui}")
            print(f"\n  >>> UI-окно: {login_url}", flush=True)
            print("  >>> Закройте окно вручную после проверки.", flush=True)
        else:
            report("Браузер", False, "не найден")
            req = urllib.request.Request(login_url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode(errors="replace")
            report("UI login HTTP 200", True, f"len={len(html)}")
    except Exception as e:
        report("UI-окно", False, repr(e))

    # --- 3. Scheduler ---
    print("\n=== 3. Scheduler ===", flush=True)
    try:
        status = api(port, "GET", "/api/status", token=token)
        health = status.get("health", {})
        sched_ok = health.get("components", {}).get("scheduler", False)
        report("Scheduler alive (/api/status)", sched_ok)

        from r52.service import App
        app = App(config).open()
        entries = app.scheduler.list()
        report("Schedule entries", len(entries) >= 3,
               f"{len(entries)}: " + ", ".join(e["key"] for e in entries))
        for e in entries:
            print(f"    {e['key']}: daily={e.get('daily_time')}, enabled={e.get('enabled')}, "
                  f"next_due={e.get('next_due_at')}", flush=True)
        tick = app.scheduler.tick()
        report("Scheduler.tick()", True,
               f"submitted={tick['submitted']}, released={tick['released']}")
        report("Scheduler thread alive", app.scheduler.alive())
        app.stop()
    except Exception as e:
        report("Scheduler", False, repr(e))

    # --- 4. Export ---
    print("\n=== 4. Export ===", flush=True)
    try:
        result = api(port, "POST", "/api/export", {}, token)
        if "_http_error" in result:
            report("Export API", False,
                   f"HTTP {result['_http_error']}: {result.get('_body','')[:200]}")
        else:
            path = result.get("path", "")
            exists = Path(path).is_file() if path else False
            size = Path(path).stat().st_size if exists else 0
            report("Export API", exists, f"{Path(path).name if path else '?'}, {size}B")
            if exists:
                import zipfile
                zf = zipfile.ZipFile(path)
                names = zf.namelist()
                report("Export ZIP valid", len(names) > 0, f"{len(names)} файлов: {names[:4]}...")
                zf.close()
    except Exception as e:
        report("Export", False, repr(e))

    # --- 5. Backup / Restore (CLI) ---
    print("\n=== 5. Backup / Restore (CLI) ===", flush=True)
    try:
        env2 = dict(os.environ)
        env2["PYTHONPATH"] = str(ROOT)
        env2["PYTHONIOENCODING"] = "utf-8"

        # 5a. backup (через API, т.к. ядро работает)
        r = subprocess.run(
            [sys.executable, "-m", "r52", "backup", "--home", str(paths.home)],
            capture_output=True, text=True, cwd=str(ROOT), env=env2, timeout=60)
        out = r.stdout.strip()
        ok = r.returncode == 0 or "уже есть" in out
        report("CLI backup", ok, (out + " " + r.stderr.strip())[:200])

        # 5b. backups list
        r = subprocess.run(
            [sys.executable, "-m", "r52", "backups", "--home", str(paths.home)],
            capture_output=True, text=True, cwd=str(ROOT), env=env2, timeout=30)
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        report("CLI backups list", r.returncode == 0 and len(lines) > 0, f"{len(lines)} копий")
        for l in lines[:3]:
            print(f"    {l}", flush=True)

        backup_name = lines[0].split()[0] if lines else None
        if backup_name:
            # 5c. Restore (ядро должно быть остановлено)
            print("  Останавливаю ядро для restore...", flush=True)
            proc.stdin.close()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
            for _ in range(15):
                if not is_core_running(paths.lock_file):
                    break
                time.sleep(1)

            r = subprocess.run(
                [sys.executable, "-m", "r52", "restore", backup_name, "--yes",
                 "--home", str(paths.home)],
                capture_output=True, text=True, cwd=str(ROOT), env=env2, timeout=60)
            report("CLI restore", r.returncode == 0,
                   (r.stdout.strip() + " " + r.stderr.strip())[:200])

            # 5d. Verify DB
            from r52.db import Database
            db = Database(paths.db_file)
            with db.read() as conn:
                count = conn.execute("SELECT count(*) FROM product").fetchone()[0]
            report("DB after restore", count >= 1, f"products={count}")
            db.close()
        else:
            report("CLI restore", False, "нет копий")
    except Exception as e:
        report("Backup/Restore", False, repr(e))

    # --- Summary ---
    print("\n" + "=" * 55, flush=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    for name, ok, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" ({detail})" if detail and not ok else ""), flush=True)
    print(f"\n  {passed} PASS / {failed} FAIL из {len(RESULTS)}", flush=True)

    # Cleanup
    if proc and proc.poll() is None:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    main()

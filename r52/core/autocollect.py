"""Autonomous orchestration. The core only handles local files and SQLite."""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from ..errors import AppError
from ..fsutil import atomic_write_bytes
from ..intake.eye import MAX_BYTES, check_export, parse_export_bytes
from ..domain.normalize import normalize_eye
from ..timeutil import iso
from .tasks import TaskOutcome

log = logging.getLogger("r52.autocollect")
# Увеличили лимит до 1 часа, чтобы фоновое обновление успевало всё
WORKER_MAX_SECONDS = 3600
WORKER_ITEM_SECONDS = 120


def enabled(app, source=None):
    if app.shutdown_requested.is_set() or not app.settings.get("autocollect.enabled") or not app.settings.get("intake.enabled"):
        return False
    if source:
        with app.db.read() as conn:
            return bool(conn.execute("SELECT enabled FROM auto_source WHERE source=?", (source,)).fetchone()[0])
    return True


class SourceDisabled(Exception):
    pass


class BatchFailure(ValueError):
    """A worker failed, but some independently validated exports may survive."""

    def __init__(self, reason, published, diagnostic):
        super().__init__(reason)
        self.published = published
        self.diagnostic = diagnostic


def check_enabled(app, source):
    if not enabled(app):
        raise TaskOutcome("CANCELLED", "AUTOCOLLECT_DISABLED")
    with app.db.read() as conn:
        active = conn.execute("SELECT enabled FROM auto_source WHERE source=?", (source,)).fetchone()
    if not active or not active[0]:
        raise SourceDisabled()


def accept_deliveries(app):
    # Match the watcher's path AND content digest; manual Eye exports keep their own workflow.
    if not app.settings.get("intake.enabled"):
        return
    
    # ИСПРАВЛЕНИЕ БАГА С ГАЛОЧКОЙ
    if not app.settings.get("intake.auto_accept"):
        return

    with app.db.read() as conn:
        pending_paths = [Path(r[0]) for r in conn.execute("SELECT path FROM auto_delivery")]
        rows = conn.execute("""
            SELECT d.path, d.source, d.listing_id, i.id, i.status FROM auto_delivery d
            JOIN intake_file f ON f.path=d.path AND f.content_sha256=d.digest
            JOIN inbox_item i ON i.content_sha256=d.digest LIMIT 100
        """).fetchall()
    for target in pending_paths:
        staged = target.with_suffix(".pending")
        if staged.is_file() and not target.exists():
            try:
                staged.replace(target)
            except FileNotFoundError:
                pass  # Worker published it during the check.
    for row in rows:
        if row["status"] == "PENDING":
            try:
                app.inbox.accept(row["id"])
            except AppError as error:
                if error.code != "INBOX_ITEM_DECIDED":
                    log.warning("Auto intake %s: %s", row["id"], error.code)
                    continue
        with app.db.write() as conn:
            decision = conn.execute("SELECT status,observation_id,error_code FROM inbox_item WHERE id=?",
                                    (row["id"],)).fetchone()
            log.log(logging.INFO if decision["status"] == "ACCEPTED" else logging.WARNING,
                    "Autocollect phase=intake source=%s listing=%s inbox=%s status=%s observation=%s error=%s",
                    row["source"], row["listing_id"], row["id"], decision["status"],
                    decision["observation_id"], decision["error_code"])
            conn.execute("DELETE FROM auto_delivery WHERE path=?", (row["path"],))


def invoke(app, ctx, params, directory):
    (directory / "request").write_text(json.dumps(dict(params, home=str(app.paths.home))), encoding="utf-8")
    command = [sys.executable, "-m", "r52.collectors.runner", "--auto-job", str(directory), str(os.getpid())]
    with (directory / "worker.log").open("wb") as output, subprocess.Popen(
        command, cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
        stdout=output, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    ) as proc:
        deadline = time.monotonic() + min(WORKER_MAX_SECONDS, WORKER_ITEM_SECONDS * (1 + params["limit"]))
        try:
            while proc.poll() is None:
                ctx.check()
                check_enabled(app, params["source"])
                if time.monotonic() >= deadline:
                    log.error("Autocollect %s worker pid=%s exceeded deadline; see %s",
                              params["source"], proc.pid, directory.name + ".log")
                    raise TimeoutError("WORKER_TIMEOUT")
                time.sleep(0.25)
            if proc.returncode:
                reason = directory / "error.txt"
                code = reason.read_text("ascii") if reason.is_file() else ""
                if not re.fullmatch(r"[A-Z0-9_]{1,80}", code):
                    code = "WORKER_EXIT_" + str(proc.returncode)
                raise ValueError(code)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def publish(app, params, directory):
    dest = app.paths.home / "auto-inbox"
    dest.mkdir(exist_ok=True)
    found = set()
    already_known = 0
    errors = 0
    for path in sorted(directory.glob("*.json")):
        if len(found) >= params["limit"]:
            break
        try:
            if path.is_symlink():
                raise ValueError("EXPORT_SYMLINK")
            if path.stat().st_size > MAX_BYTES:
                raise ValueError("EXPORT_TOO_LARGE")
            raw = path.read_bytes()
            doc = parse_export_bytes(raw)
            if doc.get("format") != "JARVIS_BR02_COLLECTOR_AUTONOMOUS":
                raise ValueError("BAD_FORMAT")
            obs = check_export(doc)
            if obs.get("status") in ("FAILED", "BLOCKED"):
                raise ValueError("FAILED_OBSERVATION")
            item, _ = normalize_eye(obs)
            listing = item["listing_id"]
            if item["source"] != params["source"] or (params["mode"] == "refresh" and listing not in params["ids"]):
                raise ValueError("WRONG_LISTING")
            if listing in found:
                continue
            digest = hashlib.sha256(raw).hexdigest()
            target = dest / ("jarvis-eye-auto-" + digest + ".json")
            with app.db.write() as conn:
                known = conn.execute("""
                    SELECT 1 FROM product WHERE source=:source AND listing_id=:listing
                    UNION ALL SELECT 1 FROM auto_delivery WHERE source=:source AND listing_id=:listing LIMIT 1
                """, {"source":item["source"], "listing":listing}).fetchone()
                if params["mode"] == "discover" and known:
                    already_known += 1
                    continue
                if not target.exists():
                    atomic_write_bytes(target.with_suffix(".pending"), raw)
                conn.execute("INSERT OR IGNORE INTO auto_delivery VALUES(?,?,?,?)",
                             (str(target), digest, item["source"], listing))
            if not target.exists():
                try:
                    target.with_suffix(".pending").replace(target)
                except FileNotFoundError:
                    if not target.is_file():
                        raise
            found.add(listing)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, AppError) as error:
            errors += 1
            log.warning("Rejected auto export source=%s file=%s reason=%s",
                        params["source"], path.name, getattr(error, "code", str(error)), exc_info=True)
    return len(found), errors, already_known


def batch(app, ctx, params):
    ctx.check()
    check_enabled(app, params["source"])
    with tempfile.TemporaryDirectory(dir=app.paths.run_dir, prefix="collect-") as folder:
        directory = Path(folder)
        completed = False
        try:
            worker_error = None
            try:
                invoke(app, ctx, params, directory)
            except (ValueError, TimeoutError, OSError) as error:
                worker_error = error
            ctx.check()
            check_enabled(app, params["source"])
            result = publish(app, params, directory)
            log.info("Autocollect source=%s mode=%s published=%s rejected=%s known=%s diagnostic=%s",
                     params["source"], params["mode"], *result, directory.name + ".log")
            if worker_error is not None:
                raise BatchFailure(str(worker_error), result, directory.name + ".log") from worker_error
            completed = True
            return result
        finally:
            if not completed:
                archive = app.paths.logs_dir / (directory.name + "-exports")
                for path in sorted(directory.glob("*.json"))[:params.get("limit", 50)]:
                    try:
                        if not path.is_symlink() and path.stat().st_size <= MAX_BYTES:
                            archive.mkdir(exist_ok=True)
                            shutil.copyfile(path, archive / path.name)
                    except OSError:
                        log.exception("Could not preserve interrupted export %s", path.name)
                if archive.is_dir():
                    log.info("Autocollect %s retained exports: %s", params["source"], archive)
            worker_log = directory / "worker.log"
            if worker_log.is_file():
                saved_log = app.paths.logs_dir / (directory.name + ".log")
                try:
                    shutil.copyfile(worker_log, saved_log)
                    log.info("Autocollect %s diagnostic log: %s", params["source"], saved_log)
                except OSError:
                    log.exception("Could not preserve autocollect diagnostic log")


def run(app, ctx, params):
    if not enabled(app):
        raise TaskOutcome("BLOCKED", "AUTOCOLLECT_DISABLED")
    now = app.clock.now()
    stamp = iso(now)
    settings = app.settings.all()
    budget = settings["autocollect.new_limit"]
    cutoff = iso(now - timedelta(seconds=settings["autocollect.refresh_seconds"]))
    totals = {"found": 0, "refreshed": 0, "errors": 0, "sources": {}}
    with app.db.read() as conn:
        sources = [r[0] for r in conn.execute(
            """SELECT s.source FROM auto_source s WHERE s.enabled AND (? OR s.retry_after IS NULL OR s.retry_after<=?)
               ORDER BY (SELECT MIN(COALESCE(last_run,'')) FROM auto_target t
                         WHERE t.source=s.source AND t.enabled AND (t.origin='USER' OR ?)),s.last_run,s.source""",
            (bool(params.get("force")), stamp, settings["autocollect.broad_search"]))]
    if params.get("source"):
        sources = [source for source in sources if source == params["source"]]
    for source in sources:
        try:
            ctx.check()
        except AppError as error:
            if error.code != "LOCAL_TASK_DEADLINE":
                raise
            raise TaskOutcome("PARTIAL", error.code, totals) from error
        if not enabled(app, source):
            continue
        result = {"found": 0, "refreshed": 0, "errors": 0}
        worked = False
        mode = "discover"
        deadline_reached = False
        try:
            with app.db.read() as conn:
                targets = conn.execute("SELECT * FROM auto_target WHERE enabled AND source=? ORDER BY last_run,id", (source,)).fetchall()
                
                products = conn.execute("""
                    SELECT p.id,p.listing_id FROM product p LEFT JOIN auto_refresh r ON p.id=r.product_id
                    WHERE p.source=? AND (r.attempted IS NULL OR r.attempted<=?)
                    ORDER BY (p.last_lowest_minor IS NOT NULL), r.attempted, p.id LIMIT 15
                """, (source, cutoff)).fetchall()
                
                previous = conn.execute("SELECT last_result FROM auto_source WHERE source=?", (source,)).fetchone()[0]
            catalog = [t for t in targets if t["origin"] == "CATALOG"]
            targets = [t for t in targets if t["origin"] == "USER"]
            last_catalog_run = max((t["last_run"] or "" for t in catalog), default="")
            catalog_due = not last_catalog_run or last_catalog_run <= iso(now - timedelta(seconds=settings["autocollect.discovery_seconds"]))
            if settings["autocollect.broad_search"] and catalog and (params.get("force") or catalog_due):
                targets.append(catalog[0])
            if products and previous and json.loads(previous).get("failed_mode") == "discover":
                targets = []
            for t in targets:
                if t["origin"] == "CATALOG" and not app.settings.get("autocollect.broad_search"):
                    continue
                interval = t["interval_seconds"] or settings["autocollect.discovery_seconds"]
                if budget <= 0 or not enabled(app, source):
                    break
                if not params.get("force") and t["last_run"] and t["last_run"] > iso(now - timedelta(seconds=interval)):
                    continue
                with app.db.read() as conn:
                    current = conn.execute("SELECT enabled,value,kind,source FROM auto_target WHERE id=?", (t["id"],)).fetchone()
                if not current or not current["enabled"] or any(current[k] != t[k] for k in ("value", "kind", "source")):
                    continue
                worked = True
                n = known = 0
                result["search"] = {"origin": t["origin"], "query": t["value"], "page": t["page"]}
                try:
                    n, errors, known = batch(app, ctx, dict(source=source, mode="discover", kind=t["kind"], value=t["value"],
                                                      page=t["page"], limit=min(t["item_limit"], budget)))
                    result["found"] += n
                    budget -= n
                    result["errors"] += errors
                    if not n and not known:
                        raise ValueError("EMPTY_OR_UNAVAILABLE")
                finally:
                    with app.db.write() as conn:
                        conn.execute("UPDATE auto_target SET last_run=?,page=? WHERE id=? AND value=? AND kind=?",
                                     (iso(app.clock.now()), t["page"] + 1 if n or known else 1, t["id"], t["value"], t["kind"]))
            
            # Если это РУЧНОЙ запуск (force=True) — мы НЕ обновляем старые товары, а сразу выходим
            if not params.get("force") and products and enabled(app, source):
                mode = "refresh"
                worked = True
                ids = [p["listing_id"] for p in products]
                n, errors, _ = batch(app, ctx, dict(source=source, mode="refresh", ids=ids, limit=len(ids)))
                result["refreshed"] = n
                result["errors"] += max(errors, len(ids)-n)
                with app.db.write() as conn:
                    conn.executemany("INSERT INTO auto_refresh VALUES(?,?) ON CONFLICT(product_id) DO UPDATE SET attempted=excluded.attempted",
                                     [(p["id"], iso(app.clock.now())) for p in products])
        except SourceDisabled:
            continue
        except AppError as error:
            if error.code != "LOCAL_TASK_DEADLINE":
                raise
            deadline_reached = True
            result.update(reason=error.code, failed_mode=mode)
            result["errors"] += 1
        except (ValueError, TimeoutError, OSError) as error:
            if isinstance(error, BatchFailure):
                n, rejected, _ = error.published
                result["found" if mode == "discover" else "refreshed"] += n
                if mode == "discover":
                    budget -= n
                result["errors"] += rejected
                result["diagnostic"] = error.diagnostic
            result["errors"] += 1
            result["failed_mode"] = mode
            result["reason"] = str(error)[:180]
            log.warning("Autocollect %s: %s", source, error)
        if worked:
            finished = app.clock.now()
            retry = iso(finished + timedelta(seconds=settings["autocollect.discovery_seconds"])) if result["errors"] else None
            with app.db.write() as conn:
                conn.execute("UPDATE auto_source SET last_run=?,last_result=?,retry_after=? WHERE source=?",
                             (iso(finished), json.dumps(result), retry, source))
            totals["sources"][source] = result
            for key in ("found", "refreshed", "errors"):
                totals[key] += result[key]
        if deadline_reached:
            raise TaskOutcome("PARTIAL", "LOCAL_TASK_DEADLINE", totals)
    log.info("Autocollect completed: %s", totals)
    if totals["errors"]:
        raise TaskOutcome("PARTIAL", "SOURCE_ERRORS", totals)
    return totals
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


def enabled(app, source=None):
    if app.shutdown_requested.is_set() or not app.settings.get("autocollect.enabled") or not app.settings.get("intake.enabled"):
        return False
    if source:
        with app.db.read() as conn:
            return bool(conn.execute("SELECT enabled FROM auto_source WHERE source=?", (source,)).fetchone()[0])
    return True


class SourceDisabled(Exception):
    pass


def check_enabled(app, source):
    if not enabled(app):
        raise TaskOutcome("CANCELLED", "AUTOCOLLECT_DISABLED")
    with app.db.read() as conn:
        active = conn.execute("SELECT enabled FROM auto_source WHERE source=?", (source,)).fetchone()
    if not active or not active[0]:
        raise SourceDisabled()


def accept_deliveries(app):
    # Match the watcher's path AND content digest; manual Eye exports keep their own workflow.
    with app.db.read() as conn:
        pending_paths = [Path(r[0]) for r in conn.execute("SELECT path FROM auto_delivery")]
        rows = conn.execute("""
            SELECT d.path, i.id, i.status FROM auto_delivery d
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
            conn.execute("DELETE FROM auto_delivery WHERE path=?", (row["path"],))


def invoke(app, ctx, params, directory):
    (directory / "request").write_text(json.dumps(dict(params, home=str(app.paths.home))), encoding="utf-8")
    command = [sys.executable, "-m", "r52.collectors.runner", "--auto-job", str(directory), str(os.getpid())]
    with (directory / "worker.log").open("wb") as output, subprocess.Popen(
        command, cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
        stdout=output, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    ) as proc:
        deadline = time.monotonic() + min(900, 120 * (1 + params["limit"]))
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
        except (ValueError, AttributeError, AppError) as error:
            errors += 1
            log.warning("Rejected auto export source=%s file=%s reason=%s",
                        params["source"], path.name, getattr(error, "code", str(error)), exc_info=True)
    return len(found), errors, already_known


def batch(app, ctx, params):
    ctx.check()
    check_enabled(app, params["source"])
    with tempfile.TemporaryDirectory(dir=app.paths.run_dir, prefix="collect-") as folder:
        directory = Path(folder)
        try:
            invoke(app, ctx, params, directory)
            ctx.check()
            check_enabled(app, params["source"])
            result = publish(app, params, directory)
            log.info("Autocollect source=%s mode=%s published=%s rejected=%s known=%s diagnostic=%s",
                     params["source"], params["mode"], *result, directory.name + ".log")
            return result
        finally:
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
    for source in sources:
        ctx.check()
        if not enabled(app, source):
            continue
        result = {"found": 0, "refreshed": 0, "errors": 0}
        worked = False
        mode = "discover"
        try:
            with app.db.read() as conn:
                targets = conn.execute("SELECT * FROM auto_target WHERE enabled AND source=? ORDER BY last_run,id", (source,)).fetchall()
                products = conn.execute("""
                    SELECT p.id,p.listing_id FROM product p LEFT JOIN auto_refresh r ON p.id=r.product_id
                    WHERE p.source=? AND (r.attempted IS NULL OR r.attempted<=?)
                    ORDER BY r.attempted,p.id LIMIT 5
                """, (source, cutoff)).fetchall()
                previous = conn.execute("SELECT last_result FROM auto_source WHERE source=?", (source,)).fetchone()[0]
            catalog = [t for t in targets if t["origin"] == "CATALOG"]
            targets = [t for t in targets if t["origin"] == "USER"]
            last_catalog_run = max((t["last_run"] or "" for t in catalog), default="")
            catalog_due = not last_catalog_run or last_catalog_run <= iso(now - timedelta(seconds=settings["autocollect.discovery_seconds"]))
            if settings["autocollect.broad_search"] and catalog and (params.get("force") or catalog_due):
                targets.append(catalog[0])
            # After a failed discovery round, give due refreshes the next turn.
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
                                     (stamp, t["page"] + 1 if n or known else 1, t["id"], t["value"], t["kind"]))
            if products and enabled(app, source):
                mode = "refresh"
                worked = True
                ids = [p["listing_id"] for p in products]
                n, errors, _ = batch(app, ctx, dict(source=source, mode="refresh", ids=ids, limit=len(ids)))
                result["refreshed"] = n
                result["errors"] += max(errors, len(ids)-n)
                with app.db.write() as conn:
                    conn.executemany("INSERT INTO auto_refresh VALUES(?,?) ON CONFLICT(product_id) DO UPDATE SET attempted=excluded.attempted",
                                     [(p["id"], stamp) for p in products])
        except SourceDisabled:
            continue
        except (ValueError, TimeoutError, OSError) as error:
            result["errors"] += 1
            result["failed_mode"] = mode
            result["reason"] = str(error)[:180]
            log.warning("Autocollect %s: %s", source, error)
        if worked:
            retry = iso(now + timedelta(seconds=settings["autocollect.discovery_seconds"])) if result["errors"] else None
            with app.db.write() as conn:
                conn.execute("UPDATE auto_source SET last_run=?,last_result=?,retry_after=? WHERE source=?",
                             (stamp, json.dumps(result), retry, source))
            totals["sources"][source] = result
            for key in ("found", "refreshed", "errors"):
                totals[key] += result[key]
    log.info("Autocollect completed: %s", totals)
    if totals["errors"]:
        raise TaskOutcome("PARTIAL", "SOURCE_ERRORS", totals)
    return totals


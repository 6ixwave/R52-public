r"""e2e_unified.py — E2E для 4 площадок с изоляцией ошибок.
Каждая площадка в try/except. Итоговая сводка в конце.
Run: C:\Python315\python.exe e2e_unified.py
"""
import sys, os, json, time, sqlite3, logging, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-18s %(levelname)-5s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("E2E_UNIFIED")

DB_HOME = ROOT / "e2e_test_home"
RESULTS = {}


def baseline(db_path):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    b = {}
    for t in ("product", "observation", "price_point"):
        b[t] = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    for src in ("FARPOST", "WB", "OZON", "AVITO"):
        b[f"src_{src}_product"] = cur.execute(
            "SELECT COUNT(*) FROM product WHERE source=?", (src,)).fetchone()[0]
        b[f"src_{src}_obs"] = cur.execute(
            "SELECT COUNT(*) FROM observation o JOIN product p ON p.id=o.product_id WHERE p.source=?", (src,)
        ).fetchone()[0]
        b[f"src_{src}_pp"] = cur.execute(
            "SELECT COUNT(*) FROM price_point pp JOIN product p ON p.id=pp.product_id WHERE p.source=?", (src,)
        ).fetchone()[0]
    con.close()
    return b


def verify(db_path, source, base):
    """Проверяет дельту записей для конкретной площадки."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    info = {}
    for t in ("product", "observation", "price_point"):
        cnt = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        info[t] = cnt - base[t]
    # Last product row for this source
    rows = cur.execute(
        "SELECT id, listing_id, title, last_regular_minor, last_availability, observation_count "
        "FROM product WHERE source=? ORDER BY id DESC LIMIT 1", (source,)
    ).fetchall()
    info["last_product"] = [dict(r) for r in rows]
    # Last price points
    rows = cur.execute(
        "SELECT pp.id, pp.role, pp.amount_minor, pp.currency, pp.label, p.listing_id "
        "FROM price_point pp JOIN product p ON p.id=pp.product_id "
        "WHERE p.source=? ORDER BY pp.id DESC LIMIT 5", (source,)
    ).fetchall()
    info["last_pp"] = [dict(r) for r in rows]
    # Per-source deltas — реальный критерий PASS
    info["src_product"] = cur.execute(
        "SELECT COUNT(*) FROM product WHERE source=?", (source,)
    ).fetchone()[0] - base.get(f"src_{source}_product", 0)
    info["src_obs"] = cur.execute(
        "SELECT COUNT(*) FROM observation o JOIN product p ON p.id=o.product_id WHERE p.source=?", (source,)
    ).fetchone()[0] - base.get(f"src_{source}_obs", 0)
    info["src_pp"] = cur.execute(
        "SELECT COUNT(*) FROM price_point pp JOIN product p ON p.id=pp.product_id WHERE p.source=?", (source,)
    ).fetchone()[0] - base.get(f"src_{source}_pp", 0)
    con.close()
    return info


def run_farpost(app, inbox, db_path, base):
    """Farpost: 3 подтверждённых объявления (d19)."""
    from r52.collectors.farpost import FarpostCollector
    col = FarpostCollector(data_dir=None, use_proxies=False)
    listing_ids = ["134693091", "121063385", "136303743"]
    new_pp_total = 0
    for lid in listing_ids:
        url = col.build_url(lid)
        log.info("[FARPOST] %s → %s", lid, url)
        t0 = time.time()
        result = col.collect_product(lid, url, f"FP-{lid}")
        dt = time.time() - t0
        if result is None:
            log.warning("[FARPOST] %s: collector None in %.1fs", lid, dt)
            continue
        obs = result.get("observation", {})
        prices = obs.get("price_candidates", [])
        log.info("[FARPOST] %s: OK %.1fs prices=%d avail=%s", lid, dt, len(prices), obs.get("availability"))
        # Normalize dry-run
        from r52.domain.normalize import normalize_eye
        normalized, _ = normalize_eye(obs)
        log.info("[FARPOST] %s: normalized title='%s' prices=%d", lid,
                 (normalized.get("title") or "")[:50], len(normalized.get("prices", [])))
        # Inbox
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        staged, created = inbox.stage_bytes(payload, filename=f"e2e-fp-{lid}.json", origin="E2E_UNIFIED")
        if staged.get("status") != "PENDING":
            log.warning("[FARPOST] %s: stage not PENDING: %s", lid, staged.get("error_code"))
            continue
        accepted = inbox.accept(staged["id"])
        log.info("[FARPOST] %s: accept product_id=%s obs=%s events=%d",
                 lid, accepted.get("product_id"), accepted.get("observation_created"),
                 len(accepted.get("events", [])))
        new_pp_total += 1
    col.session.close()
    return new_pp_total


def run_wb(app, inbox, db_path, base):
    """WB: nm_id 1097975275 (iPhone 17 Pro Max)."""
    from r52.collectors.wb import WBCollector
    col = WBCollector(data_dir=None, use_proxies=False)
    nm_id = "1097975275"
    url = col.build_url(nm_id)
    log.info("[WB] %s → %s", nm_id, url)
    t0 = time.time()
    result = col.collect_product(nm_id, url, "WB-iPhone17")
    dt = time.time() - t0
    if result is None:
        log.warning("[WB] %s: collector None in %.1fs", nm_id, dt)
        col.close()
        return 0
    obs = result.get("observation", {})
    prices = obs.get("price_candidates", [])
    log.info("[WB] %s: OK %.1fs prices=%d avail=%s", nm_id, dt, len(prices), obs.get("availability"))
    for p in prices[:5]:
        log.info("[WB]   role=%s minor=%s cur=%s label=%s", p.get("role"), p.get("amount_minor"), p.get("currency"), p.get("label"))
    # Normalize
    from r52.domain.normalize import normalize_eye
    normalized, _ = normalize_eye(obs)
    log.info("[WB] normalized: title='%s' prices=%d", (normalized.get("title") or "")[:50], len(normalized.get("prices", [])))
    # Inbox
    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
    staged, created = inbox.stage_bytes(payload, filename="e2e-wb.json", origin="E2E_UNIFIED")
    if staged.get("status") != "PENDING":
        log.warning("[WB]: stage not PENDING: %s", staged.get("error_code"))
        col.close()
        return 0
    accepted = inbox.accept(staged["id"])
    log.info("[WB]: accept product_id=%s obs=%s events=%d",
             accepted.get("product_id"), accepted.get("observation_created"),
             len(accepted.get("events", [])))
    col.session.close()
    return 1


def run_ozon(app, inbox, db_path, base):
    """Ozon: публичный product URL."""
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)
    # Публичный товар — используем ID из предыдущих попыток
    ozon_ids = ["3481320800"]
    new_pp = 0
    for oid in ozon_ids:
        url = f"https://www.ozon.ru/product/{oid}/"
        log.info("[OZON] %s → %s", oid, url)
        t0 = time.time()
        try:
            result = col.collect_product(oid, url, f"OZ-{oid}")
        except Exception as e:
            log.warning("[OZON] %s: exception %s in %.1fs", oid, e, time.time() - t0)
            continue
        dt = time.time() - t0
        if result is None:
            log.warning("[OZON] %s: collector None in %.1fs", oid, dt)
            continue
        obs = result.get("observation", {})
        prices = obs.get("price_candidates", [])
        log.info("[OZON] %s: OK %.1fs prices=%d avail=%s status=%s", oid, dt, len(prices), obs.get("availability"), obs.get("status"))
        for p in prices[:5]:
            log.info("[OZON]   role=%s minor=%s cur=%s label=%s", p.get("role"), p.get("amount_minor"), p.get("currency"), p.get("label"))
        from r52.domain.normalize import normalize_eye
        normalized, _ = normalize_eye(obs)
        log.info("[OZON] normalized: title='%s' prices=%d", (normalized.get("title") or "")[:50], len(normalized.get("prices", [])))
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        staged, created = inbox.stage_bytes(payload, filename=f"e2e-ozon-{oid}.json", origin="E2E_UNIFIED")
        if staged.get("status") != "PENDING":
            log.warning("[OZON] %s: stage not PENDING: %s", oid, staged.get("error_code"))
            continue
        accepted = inbox.accept(staged["id"])
        log.info("[OZON] %s: accept product_id=%s obs=%s events=%d",
                 oid, accepted.get("product_id"), accepted.get("observation_created"),
                 len(accepted.get("events", [])))
        new_pp += 1
    col.session.close()
    return new_pp


def run_avito(app, inbox, db_path, base):
    """Avito: публичный ad URL с таймаутом."""
    from r52.collectors.avito import AvitoCollector
    col = AvitoCollector(data_dir=None, use_proxies=False)
    # Используем ID из предыдущих попыток + альтернативные
    avito_ids = ["1045231001"]
    new_pp = 0
    for aid in avito_ids:
        url = f"https://www.avito.ru/ad/{aid}"
        log.info("[AVITO] %s → %s", aid, url)
        t0 = time.time()
        try:
            # Ограничиваем время: обёртка с таймаутом
            import threading
            result_holder = [None]
            def _collect():
                try:
                    result_holder[0] = col.collect_product(aid, url, f"AV-{aid}")
                except Exception as e:
                    log.warning("[AVITO] collect exception: %s", e)
            th = threading.Thread(target=_collect, daemon=True)
            th.start()
            th.join(timeout=45)  # 45 сек максимум
            if th.is_alive():
                log.warning("[AVITO] %s: TIMEOUT 45s, skipping", aid)
                continue
            result = result_holder[0]
        except Exception as e:
            log.warning("[AVITO] %s: outer exception %s", aid, e)
            continue
        dt = time.time() - t0
        if result is None:
            log.warning("[AVITO] %s: collector None in %.1fs", aid, dt)
            continue
        obs = result.get("observation", {})
        prices = obs.get("price_candidates", [])
        log.info("[AVITO] %s: OK %.1fs prices=%d avail=%s status=%s", aid, dt, len(prices), obs.get("availability"), obs.get("status"))
        for p in prices[:5]:
            log.info("[AVITO]   role=%s minor=%s cur=%s label=%s", p.get("role"), p.get("amount_minor"), p.get("currency"), p.get("label"))
        from r52.domain.normalize import normalize_eye
        normalized, _ = normalize_eye(obs)
        log.info("[AVITO] normalized: title='%s' prices=%d", (normalized.get("title") or "")[:50], len(normalized.get("prices", [])))
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        staged, created = inbox.stage_bytes(payload, filename=f"e2e-avito-{aid}.json", origin="E2E_UNIFIED")
        if staged.get("status") != "PENDING":
            log.warning("[AVITO] %s: stage not PENDING: %s", aid, staged.get("error_code"))
            continue
        accepted = inbox.accept(staged["id"])
        log.info("[AVITO] %s: accept product_id=%s obs=%s events=%d",
                 aid, accepted.get("product_id"), accepted.get("observation_created"),
                 len(accepted.get("events", [])))
        new_pp += 1
    col.session.close()
    return new_pp


def main():
    log.info("=" * 60)
    log.info("UNIFIED E2E  %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("DB home: %s", DB_HOME)

    # App
    from r52.config import load_config
    from r52.service import App
    config = load_config(home=str(DB_HOME))
    app = App(config, install_guard=False)
    app.open()
    inbox = app.inbox
    db_path = app.paths.db_file
    log.info("App open. DB: %s", db_path)

    base = baseline(db_path)
    log.info("BASELINE: %s", base)

    platforms = [
        ("FARPOST", "FARPOST", run_farpost),
        ("WB", "WB", run_wb),
        ("OZON", "OZON", run_ozon),
        ("AVITO", "AVITO", run_avito),
    ]

    for label, source, fn in platforms:
        log.info("-" * 40)
        log.info("PLATFORM: %s", label)
        log.info("-" * 40)
        t0 = time.time()
        try:
            n = fn(app, inbox, db_path, base)
            dt = time.time() - t0
            info = verify(db_path, source, base)
            verdict = "PASS" if info["src_pp"] > 0 else ("PARTIAL" if info["src_obs"] > 0 else "FAIL")
            RESULTS[label] = {
                "verdict": verdict,
                "seconds": round(dt, 1),
                "new_pp": info["price_point"],
                "new_obs": info["observation"],
                "new_prod": info["product"],
                "src_pp": info["src_pp"],
                "src_obs": info["src_obs"],
                "src_prod": info["src_product"],
                "last_pp": info["last_pp"][:3],
            }
            log.info("[%s] %s in %.1fs  Δpp=%d Δobs=%d Δprod=%d  (src: pp=%d obs=%d prod=%d)",
                     label, verdict, dt, info["price_point"], info["observation"], info["product"],
                     info["src_pp"], info["src_obs"], info["src_product"])
        except Exception as e:
            dt = time.time() - t0
            RESULTS[label] = {"verdict": "ERROR", "seconds": round(dt, 1), "error": str(e)}
            log.error("[%s] ERROR in %.1fs: %s", label, dt, e)
            log.error(traceback.format_exc())

    # Final summary
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    for label, r in RESULTS.items():
        log.info("  %-10s %-8s  pp=%s obs=%s prod=%s  (%.1fs)",
                 label, r.get("verdict", "?"),
                 r.get("new_pp", 0), r.get("new_obs", 0), r.get("new_prod", 0),
                 r.get("seconds", 0))
    passed = sum(1 for r in RESULTS.values() if r.get("verdict") == "PASS")
    log.info("  %d/4 PASS", passed)

    # Save result
    out = ROOT / "e2e_unified_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "baseline": base, "results": RESULTS}, f, indent=2, ensure_ascii=False)
    log.info("Result saved: %s", out)

    app.stop()
    log.info("DONE")


if __name__ == "__main__":
    main()

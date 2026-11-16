"""Интеграционный тест: реальный HTTP-сбор + валидация формата eye.py/normalize.py.

Запуск:
    python -m r52.collectors.integration_test [--source WB] [--id 12345] [--no-proxies]

Без аргументов — тестирует все 6 источников по одному товару каждый.
"""

import argparse
import json
import logging
import sys
import time
import traceback
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("r52.integration")

# --- Тестовые ID (заполняются после web_search) ---
DEFAULT_IDS = {
    "WB": "199298871",
    "OZON": "1600562267",
    "AVITO": "1096783029",
    "FARPOST": "116361427",
    "ALIEXPRESS": "1005006564853571",
    "ALIEXPRESS_RU": "1005006564853571",
}

# --- Валидация формата (дублирует логику eye.py без импорта app) ---

EYE_FORMATS = ("JARVIS_BR02_EYE_USER_CAPTURE", "JARVIS_EYE_REVIEW_1", "JARVIS_BR02_COLLECTOR_AUTONOMOUS")


def validate_eye_format(wrapper: dict) -> list[str]:
    """Проверяет, что wrapper проходит check_export() из eye.py."""
    errors = []
    if not isinstance(wrapper, dict):
        return ["wrapper is not dict"]
    fmt = wrapper.get("format")
    if fmt not in EYE_FORMATS:
        errors.append(f"format={fmt!r} not in {EYE_FORMATS}")
    if fmt == "JARVIS_BR02_COLLECTOR_AUTONOMOUS":
        if wrapper.get("capture_action") != "COLLECTOR_RUN":
            errors.append(f"capture_action={wrapper.get('capture_action')!r} (need COLLECTOR_RUN)")
        if wrapper.get("fully_autonomous") is not True:
            errors.append(f"fully_autonomous={wrapper.get('fully_autonomous')!r} (need True)")
    else:
        if wrapper.get("capture_action") != "EXPLICIT_POPUP_CLICK":
            errors.append(f"capture_action={wrapper.get('capture_action')!r}")
        if wrapper.get("fully_autonomous") is not False:
            errors.append(f"fully_autonomous={wrapper.get('fully_autonomous')!r} (need False)")
    obs = wrapper.get("observation")
    if not isinstance(obs, dict):
        errors.append("observation is not dict")
        return errors
    for field in ("source", "id", "url", "observed_at"):
        if not obs.get(field):
            errors.append(f"observation.{field} missing/empty")
    if obs.get("status") not in ("PARTIAL_SUCCESS", "FULL_SUCCESS"):
        errors.append(f"observation.status={obs.get('status')!r}")
    return errors


def validate_normalized(normalized: dict) -> list[str]:
    """Проверяет ключевые поля после normalize_eye()."""
    errors = []
    for field in ("source", "listing_id", "url", "title", "observed_at",
                  "prices", "availability", "completeness", "media"):
        if field not in normalized:
            errors.append(f"normalized.{field} missing")
    if normalized.get("prices") is not None and not isinstance(normalized["prices"], list):
        errors.append("normalized.prices not list")
    return errors


def summarize_media(obs: dict) -> dict:
    """Подсчёт фото/видео в observation.media.items."""
    items = (obs.get("media") or {}).get("items") or []
    photos = [i for i in items if i.get("type") == "image"]
    videos = [i for i in items if i.get("type") == "video"]
    return {"total": len(items), "photos": len(photos), "videos": len(videos)}


def run_one(source: str, listing_id: str, use_proxies: bool = False) -> dict:
    """Запускает один сборщик, валидирует, возвращает отчёт."""
    report = {
        "source": source,
        "listing_id": listing_id,
        "url": "",
        "status": "PENDING",
        "eye_errors": [],
        "normalize_errors": [],
        "title": None,
        "prices": [],
        "media": {"total": 0, "photos": 0, "videos": 0},
        "availability": None,
        "fields_count": 0,
        "elapsed_sec": 0,
        "error": None,
    }

    # Импорт коллектора
    mapping = {
        "WB": ("r52.collectors.wb", "WBCollector"),
        "OZON": ("r52.collectors.ozon", "OzonCollector"),
        "AVITO": ("r52.collectors.avito", "AvitoCollector"),
        "FARPOST": ("r52.collectors.farpost", "FarpostCollector"),
        "ALIEXPRESS": ("r52.collectors.aliexpress", "AliExpressCollector"),
        "ALIEXPRESS_RU": ("r52.collectors.aliexpress", "AliExpressRUCollector"),
    }
    if source.upper() not in mapping:
        report["status"] = "ERROR"
        report["error"] = f"Unknown source {source}"
        return report

    mod_path, cls_name = mapping[source.upper()]
    module = __import__(mod_path, fromlist=[cls_name])
    cls = getattr(module, cls_name)

    collector = cls(use_proxies=use_proxies)
    url = collector.build_url(listing_id)
    report["url"] = url
    log.info("=== %s %s → %s ===", source, listing_id, url)

    start = time.monotonic()
    try:
        wrapper = collector.collect_product(listing_id, url)
    except Exception as e:
        report["status"] = "ERROR"
        report["error"] = f"{type(e).__name__}: {e}"
        report["elapsed_sec"] = round(time.monotonic() - start, 1)
        log.exception("Collector %s exception", source)
        return report

    report["elapsed_sec"] = round(time.monotonic() - start, 1)

    if wrapper is None:
        report["status"] = "NO_DATA"
        report["error"] = "collect_product returned None (fetch failed or parse empty)"
        return report

    # Валидация eye format
    eye_errors = validate_eye_format(wrapper)
    report["eye_errors"] = eye_errors

    obs = wrapper.get("observation", {})
    report["title"] = (obs.get("fields") or {}).get("title", {}).get("value")
    report["availability"] = obs.get("availability")
    report["fields_count"] = len(obs.get("fields") or {})
    report["media"] = summarize_media(obs)

    # Цены
    prices = obs.get("price_candidates") or []
    report["prices"] = [
        {"role": p.get("role"), "amount": p.get("amount"), "minor": p.get("amount_minor"),
         "currency": p.get("currency"), "label": p.get("label")}
        for p in prices[:5]
    ]

    # Валидация normalize_eye (попытка)
    try:
        from r52.domain.normalize import normalize_eye
        normalized, _cleaned = normalize_eye(obs)
        norm_errors = validate_normalized(normalized)
        report["normalize_errors"] = norm_errors
        if not norm_errors:
            report["status"] = "OK" if not eye_errors else "EYE_WARN"
        else:
            report["status"] = "NORM_WARN"
    except Exception as e:
        report["normalize_errors"] = [f"normalize_eye exception: {type(e).__name__}: {e}"]
        report["status"] = "NORM_ERR" if not eye_errors else "EYE_WARN"

    if eye_errors and not report["normalize_errors"]:
        report["status"] = "EYE_ERR"

    log.info(
        "%s %s: status=%s title=%r prices=%d media=%s avail=%s fields=%d in %.1fs",
        source, listing_id, report["status"],
        (report["title"] or "")[:60], len(report["prices"]),
        report["media"], report["availability"], report["fields_count"],
        report["elapsed_sec"],
    )
    return report


def main():
    parser = argparse.ArgumentParser(description="R52 Collector Integration Test")
    parser.add_argument("--source", type=str, default=None,
                        help="Один источник (WB/OZON/AVITO/FARPOST) или все")
    parser.add_argument("--id", type=str, default=None, help="ID товара")
    parser.add_argument("--no-proxies", action="store_true", help="Без прокси")
    parser.add_argument("--output", type=str, default=None, help="Файл для JSON-отчёта")
    args = parser.parse_args()

    sources = [args.source.upper()] if args.source else list(DEFAULT_IDS.keys())
    reports = []

    for src in sources:
        lid = args.id if args.id else DEFAULT_IDS.get(src, "0")
        try:
            rep = run_one(src, lid, use_proxies=not args.no_proxies)
        except Exception as e:
            rep = {
                "source": src, "listing_id": lid, "url": "",
                "status": "CRASH", "eye_errors": [], "normalize_errors": [],
                "title": None, "prices": [], "media": {"total": 0, "photos": 0, "videos": 0},
                "availability": None, "fields_count": 0, "elapsed_sec": 0,
                "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            }
        reports.append(rep)
        # Пауза между источниками
        if src != sources[-1]:
            time.sleep(3)

    # Сводка
    print("\n" + "=" * 70)
    print("INTEGRATION TEST SUMMARY")
    print("=" * 70)
    for r in reports:
        icon = "✓" if r["status"] == "OK" else "⚠" if "WARN" in r["status"] else "✗"
        print(f"\n{icon} {r['source']} [{r['listing_id']}] → {r['status']} ({r['elapsed_sec']}s)")
        print(f"  URL: {r['url']}")
        print(f"  Title: {(r['title'] or '—')[:80]}")
        print(f"  Prices: {len(r['prices'])} " +
              (f"first={r['prices'][0]['amount']} {r['prices'][0]['currency']}" if r['prices'] else ""))
        m = r["media"]
        print(f"  Media: {m['total']} total ({m['photos']} photos, {m['videos']} videos)")
        print(f"  Availability: {r['availability']}")
        print(f"  Fields: {r['fields_count']}")
        if r["eye_errors"]:
            print(f"  EYE ERRORS: {r['eye_errors']}")
        if r["normalize_errors"]:
            print(f"  NORM ERRORS: {r['normalize_errors']}")
        if r["error"]:
            print(f"  ERROR: {r['error'][:200]}")
    print("\n" + "=" * 70)

    # JSON-отчёт
    out_path = args.output or str(Path(__file__).parent / "integration_report.json")
    Path(out_path).write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Report: {out_path}")

    # Exit code: 0 если все OK, 1 если есть WARN, 2 если есть ERR
    worst = max(
        (0 if r["status"] == "OK" else 1 if "WARN" in r["status"] else 2)
        for r in reports
    )
    sys.exit(worst)


if __name__ == "__main__":
    main()

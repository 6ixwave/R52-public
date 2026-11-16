"""Subprocess-раннер для сборщиков маркетплейсов.

Запускает сборщик в отдельном процессе (сеть не попадает в ядро R52).
Результат — JSON-файлы в папке приёма, которые FolderWatcher подхватывает.

Запуск:
    python -m r52.collectors.runner --source WB --ids 123,456 --output-dir <path>

Автоматический запуск из ядра:
    from r52.collectors.runner import run_collector_subprocess
    result = run_collector_subprocess("WB", ["123", "456"], output_dir)
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("r52.collectors.runner")

# Таймаут на один товар (сек)
PER_ITEM_TIMEOUT = 120
# Таймаут на весь запуск (сек)
TOTAL_TIMEOUT = 3600


def _get_collector_class(source: str):
    """Импортирует класс сборщика по имени."""
    source_upper = source.upper()
    mapping = {
        "WB": ("r52.collectors.wb", "WBCollector"),
        "OZON": ("r52.collectors.ozon", "OzonCollector"),
        "AVITO": ("r52.collectors.avito", "AvitoCollector"),
        "FARPOST": ("r52.collectors.farpost", "FarpostCollector"),
        "ALIEXPRESS": ("r52.collectors.aliexpress", "AliExpressCollector"),
        "ALIEXPRESS_RU": ("r52.collectors.aliexpress", "AliExpressRUCollector"),
    }
    if source_upper not in mapping:
        raise ValueError(f"Неизвестный источник: {source}. Доступны: {list(mapping.keys())}")
    module_path, class_name = mapping[source_upper]
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


def run_collector(source: str, listing_ids: list[str], output_dir: Path,
                  data_dir: Optional[Path] = None, use_proxies: bool = True,
                  query: str = None) -> dict:
    """Запускает сборщик в текущем процессе (для subprocess-режима)."""
    collector_cls = _get_collector_class(source)
    collector = collector_cls(data_dir=data_dir, use_proxies=use_proxies)

    results = []
    total_start = time.monotonic()

    # Query mode: collect by search (ALIEXPRESS_RU)
    if query and hasattr(collector, "collect_by_search"):
        log.info("Runner %s: search mode, query=%r", source, query[:50])
        results = collector.collect_by_search(query)
        written = collector.write_observations(results, output_dir)
        collector.close()
        summary = {
            "source": source,
            "mode": "search",
            "query": query,
            "total_items": len(results),
            "collected": len(results),
            "failed": 0,
            "files_written": len(written),
            "elapsed_sec": round(time.monotonic() - total_start, 1),
            "collector_stats": collector.stats,
            "proxy_stats": collector.pool.stats(),
        }
        log.info("Runner %s (search): итог %s", source, json.dumps(summary, ensure_ascii=False))
        return summary

    for i, lid in enumerate(listing_ids):
        if time.monotonic() - total_start > TOTAL_TIMEOUT:
            log.warning("Runner %s: общий таймаут %ds исчерпан", source, TOTAL_TIMEOUT)
            break

        item_start = time.monotonic()
        try:
            url = collector.build_url(lid)
            observation = collector.collect_product(lid, url)
            if observation:
                results.append(observation)
            else:
                results.append({
                    "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                    "capture_action": "COLLECTOR_RUN",
                    "fully_autonomous": True,
                    "collector_source": source,
                    "observation": {
                        "source": source,
                        "id": str(lid),
                        "url": url,
                        "status": "FAILED",
                        "error": "parse_returned_none",
                        "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                        "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                        "http_status": 200,
                    },
                })
        except Exception as e:
            log.exception("Runner %s: ошибка товара %s: %s", source, lid, e)
            results.append({
                "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                "capture_action": "COLLECTOR_RUN",
                "fully_autonomous": True,
                "collector_source": source,
                "observation": {
                    "source": source,
                    "id": str(lid),
                    "url": collector.build_url(lid),
                    "status": "FAILED",
                    "error": str(e)[:500],
                    "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                    "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                    "http_status": 200,
                },
            })

        elapsed = time.monotonic() - item_start
        log.info("Runner %s: товар %d/%d (%s) за %.1fs",
                 source, i + 1, len(listing_ids), lid, elapsed)

        # Пауза между товарами (rate limit уже в fetch, но добавим буфер)
        if i < len(listing_ids) - 1:
            time.sleep(3)

    # Запись результатов
    written = collector.write_observations(results, output_dir)

    summary = {
        "source": source,
        "total_items": len(listing_ids),
        "collected": sum(1 for r in results if r.get("observation", {}).get("status") == "PARTIAL_SUCCESS"),
        "failed": sum(1 for r in results if r.get("observation", {}).get("status") == "FAILED"),
        "files_written": len(written),
        "elapsed_sec": round(time.monotonic() - total_start, 1),
        "collector_stats": collector.stats,
        "proxy_stats": collector.pool.stats(),
    }
    log.info("Runner %s: итог %s", source, json.dumps(summary, ensure_ascii=False))
    return summary


def run_collector_subprocess(source: str, listing_ids: list[str], output_dir: Path,
                             data_dir: Optional[Path] = None, use_proxies: bool = True,
                             query: str = None) -> dict:
    """Запускает сборщик в отдельном subprocess (изоляция сети от ядра)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON-файл с параметрами для subprocess
    params_file = output_dir / f"runner-{source.lower()}-params.json"
    params = {
        "source": source,
        "listing_ids": listing_ids,
        "output_dir": str(output_dir),
        "data_dir": str(data_dir) if data_dir else None,
        "use_proxies": use_proxies,
        "query": query,
    }
    params_file.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")

    # Команда: python -m r52.collectors.runner --params <file>
    cmd = [
        sys.executable, "-m", "r52.collectors.runner",
        "--params", str(params_file),
    ]

    log.info("Runner %s: запуск subprocess: %s", source, " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TOTAL_TIMEOUT + 60,
            cwd=str(Path(__file__).parent.parent.parent),  # корень проекта
        )
        if proc.returncode == 0:
            # Читаем результат
            result_file = output_dir / f"runner-{source.lower()}-result.json"
            if result_file.exists():
                return json.loads(result_file.read_text(encoding="utf-8"))
            return {"source": source, "status": "OK", "note": "result file not found"}
        else:
            return {
                "source": source,
                "status": "ERROR",
                "exit_code": proc.returncode,
                "stderr": (proc.stderr or "")[:2000],
            }
    except subprocess.TimeoutExpired:
        return {"source": source, "status": "TIMEOUT", "timeout_sec": TOTAL_TIMEOUT + 60}
    except Exception as e:
        log.exception("Runner %s: ошибка subprocess: %s", source, e)
        return {"source": source, "status": "ERROR", "error": str(e)[:500]}


def main():
    parser = argparse.ArgumentParser(description="R52 Collector Runner")
    parser.add_argument("--source", type=str, help="Источник: WB, OZON, AVITO, FARPOST, ALIEXPRESS_RU")
    parser.add_argument("--ids", type=str, help="ID товаров через запятую")
    parser.add_argument("--query", type=str, default=None, help="Поисковый запрос (для ALIEXPRESS_RU)")
    parser.add_argument("--output-dir", type=str, help="Папка для вывода")
    parser.add_argument("--data-dir", type=str, default=None, help="Папка данных (proxies.json)")
    parser.add_argument("--no-proxies", action="store_true", help="Без прокси (direct)")
    parser.add_argument("--auto-job", nargs=2, metavar=("DIRECTORY", "CORE_PID"))
    parser.add_argument("--params", type=str, help="JSON-файл с параметрами")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.auto_job:
        from .auto_worker import main as auto_main
        return auto_main(Path(args.auto_job[0]), int(args.auto_job[1]))

    if args.params:
        params = json.loads(Path(args.params).read_text(encoding="utf-8"))
        source = params["source"]
        listing_ids = params.get("listing_ids", [])
        output_dir = Path(params["output_dir"])
        data_dir = Path(params["data_dir"]) if params.get("data_dir") else None
        use_proxies = params.get("use_proxies", True)
        query = params.get("query")
    else:
        if not args.source or not args.output_dir:
            parser.error("Нужно --source, --output-dir (и --ids или --query) или --params")
        if not args.ids and not args.query:
            parser.error("Нужно --ids или --query")
        source = args.source
        listing_ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids else []
        output_dir = Path(args.output_dir)
        data_dir = Path(args.data_dir) if args.data_dir else None
        use_proxies = not args.no_proxies
        query = args.query

    summary = run_collector(source, listing_ids, output_dir, data_dir, use_proxies, query=query)

    # Записываем результат
    result_file = output_dir / f"runner-{source.lower()}-result.json"
    result_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()

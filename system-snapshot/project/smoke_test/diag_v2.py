"""Диагностика v2: поочерёдный запуск каждого сборщика с DEBUG-логом.

Запуск:
    python smoke_test/diag_v2.py [--source OZON]

Результат: smoke_test/diag_v2_output.txt
"""
import sys
import os
import time
import json
import logging
import traceback
from pathlib import Path

# --- Настройка логирования: DEBUG для r52, INFO для остального ---
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
for noisy in ("urllib3", "requests", "asyncio", "httpx"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

OUT = Path(__file__).resolve().parent / "diag_v2_output.txt"

# --- Тестовые ID (из integration_test.py) ---
IDS = {
    "WB": "199298871",
    "OZON": "1600562267",
    "AVITO": "1096783029",
    "FARPOST": "18530297982",
    "ALIEXPRESS": "1005006564853571",
    "ALIEXPRESS_RU": "1005006564853571",
}

MAP = {
    "WB": ("r52.collectors.wb", "WBCollector"),
    "OZON": ("r52.collectors.ozon", "OzonCollector"),
    "AVITO": ("r52.collectors.avito", "AvitoCollector"),
    "FARPOST": ("r52.collectors.farpost", "FarpostCollector"),
    "ALIEXPRESS": ("r52.collectors.aliexpress", "AliExpressCollector"),
    "ALIEXPRESS_RU": ("r52.collectors.aliexpress", "AliExpressRUCollector"),
}


def run_one(source: str, lid: str) -> dict:
    mod_path, cls_name = MAP[source]
    module = __import__(mod_path, fromlist=[cls_name])
    cls = getattr(module, cls_name)

    lines = []
    lines.append(f"\n{'='*70}")
    lines.append(f"SOURCE: {source}  ID: {lid}")
    lines.append(f"CLASS: {cls.__module__}.{cls.__qualname__}")
    lines.append(f"{'='*70}")

    t0 = time.monotonic()
    result = {"source": source, "id": lid, "ok": False, "error": None, "elapsed": 0}

    try:
        collector = cls(use_proxies=False)
        url = collector.build_url(lid)
        lines.append(f"URL: {url}")

        # Быстрый HTTP-пинг URL-а до запуска полного collect_product
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            })
            resp = urllib.request.urlopen(req, timeout=15)
            status = resp.status
            body_len = len(resp.read())
            lines.append(f"HTTP PING: {status}, body={body_len} bytes")
            result["http_ping"] = {"status": status, "body_len": body_len}
        except urllib.error.HTTPError as he:
            lines.append(f"HTTP PING: {he.code} {he.reason}")
            result["http_ping"] = {"status": he.code, "reason": he.reason}
        except urllib.error.URLError as ue:
            lines.append(f"HTTP PING: URLError {ue.reason}")
            result["http_ping"] = {"status": "URLError", "reason": str(ue.reason)}
        except Exception as pe:
            lines.append(f"HTTP PING: {type(pe).__name__}: {pe}")
            result["http_ping"] = {"status": "EXC", "error": str(pe)}

        # Полный collect_product
        wrapper = collector.collect_product(lid, url)
        elapsed = round(time.monotonic() - t0, 1)
        result["elapsed"] = elapsed

        if wrapper is None:
            lines.append(f"RESULT: None (elapsed={elapsed}s)")
            result["error"] = "collect_product returned None"
        else:
            obs = wrapper.get("observation", {})
            fields = obs.get("fields", {})
            title = (fields.get("title") or {}).get("value", "")
            lines.append(f"RESULT: OK  title={title[:80]!r}  fields={len(fields)}  elapsed={elapsed}s")
            result["ok"] = True
            result["title"] = title[:100]
            result["fields_count"] = len(fields)

    except Exception as e:
        elapsed = round(time.monotonic() - t0, 1)
        result["elapsed"] = elapsed
        result["error"] = f"{type(e).__name__}: {e}"
        lines.append(f"EXCEPTION: {type(e).__name__}: {e}")
        lines.append(traceback.format_exc())

    lines.append(f"\n--- {source} summary: ok={result['ok']} elapsed={result['elapsed']}s ---")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return result


def main():
    src_arg = None
    for i, a in enumerate(sys.argv):
        if a == "--source" and i + 1 < len(sys.argv):
            src_arg = sys.argv[i + 1].upper()

    sources = [src_arg] if src_arg else list(IDS.keys())
    results = []

    for src in sources:
        lid = IDS.get(src, "0")
        print(f"\n>>> Running {src} ({lid}) ...")
        try:
            r = run_one(src, lid)
        except Exception as e:
            r = {"source": src, "id": lid, "ok": False,
                 "error": f"{type(e).__name__}: {e}", "elapsed": 0}
        results.append(r)
        status = "OK" if r["ok"] else "FAIL"
        print(f"    {status}  {r['elapsed']}s  {r.get('error', '')[:100]}")
        if src != sources[-1]:
            time.sleep(2)

    # Сводка
    print("\n" + "=" * 70)
    print("DIAG V2 SUMMARY")
    print("=" * 70)
    for r in results:
        icon = "V" if r["ok"] else "X"
        ping = r.get("http_ping", {})
        ping_s = f"http={ping.get('status', '?')}" if ping else ""
        print(f"  [{icon}] {r['source']:15s} {r['elapsed']:7.1f}s  {ping_s:15s}  {r.get('error', '')[:60]}")

    # JSON
    out_json = Path(__file__).resolve().parent / "diag_v2.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {out_json}")
    print(f"LOG:  {OUT}")


if __name__ == "__main__":
    main()
"""Диагностика v3: построчная запись в лог с flush+fsync.

Запуск:
    python smoke_test/diag_v3.py FARPOST
    python smoke_test/diag_v3.py OZON
    (и т.д.)

Результат: smoke_test/diag_v3.log (перезаписывается при каждом запуске)
"""
import sys
import os
import time
import json
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

OUT = Path(__file__).resolve().parent / "diag_v3.log"


def log(msg: str):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
        f.flush()
        os.fsync(f.fileno())


def main():
    source = sys.argv[1].upper() if len(sys.argv) > 1 else "FARPOST"
    OUT.write_text("", encoding="utf-8")
    log(f"=== DIAG V3 START: {source} at {time.strftime('%H:%M:%S')} ===")
    log(f"Python: {sys.version}")
    log(f"CWD: {os.getcwd()}")

    IDS = {
        "WB": "199298871",
        "OZON": "1600562267",
        "AVITO": "1096783029",
        "FARPOST": "18530297982",
        "ALIEXPRESS": "1005006564853571",
        "ALIEXPRESS_RU": "1005006564853571",
    }
    MAPPING = {
        "WB": ("r52.collectors.wb", "WBCollector"),
        "OZON": ("r52.collectors.ozon", "OzonCollector"),
        "AVITO": ("r52.collectors.avito", "AvitoCollector"),
        "FARPOST": ("r52.collectors.farpost", "FarpostCollector"),
        "ALIEXPRESS": ("r52.collectors.aliexpress", "AliExpressCollector"),
        "ALIEXPRESS_RU": ("r52.collectors.aliexpress", "AliExpressRUCollector"),
    }

    lid = IDS.get(source, "0")
    mod_path, cls_name = MAPPING.get(source, ("?", "?"))

    try:
        # --- Step 1: Import ---
        log(f"Step 1: import {mod_path} (class={cls_name}) ...")
        t0 = time.monotonic()
        module = __import__(mod_path, fromlist=[cls_name])
        cls = getattr(module, cls_name)
        log(f"Step 1 OK ({time.monotonic()-t0:.1f}s): {cls.__module__}.{cls.__qualname__}")

        # --- Step 2: Constructor ---
        log(f"Step 2: {cls.__name__}(use_proxies=False) ...")
        t0 = time.monotonic()
        collector = cls(use_proxies=False)
        log(f"Step 2 OK ({time.monotonic()-t0:.1f}s): type={type(collector).__name__}")

        # --- Step 3: build_url ---
        log(f"Step 3: build_url('{lid}') ...")
        t0 = time.monotonic()
        url = collector.build_url(lid)
        log(f"Step 3 OK ({time.monotonic()-t0:.1f}s): {url}")

        # --- Step 4: HTTP ping (urllib, no requests) ---
        log(f"Step 4: HTTP ping {url} ...")
        t0 = time.monotonic()
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            })
            resp = urllib.request.urlopen(req, timeout=20)
            body = resp.read()
            log(f"Step 4 OK ({time.monotonic()-t0:.1f}s): HTTP {resp.status}, {len(body)} bytes")
        except urllib.error.HTTPError as he:
            log(f"Step 4: HTTP {he.code} {he.reason} ({time.monotonic()-t0:.1f}s)")
        except urllib.error.URLError as ue:
            log(f"Step 4: URLError {ue.reason} ({time.monotonic()-t0:.1f}s)")
        except Exception as pe:
            log(f"Step 4: {type(pe).__name__}: {pe} ({time.monotonic()-t0:.1f}s)")

        # --- Step 5: collect_product ---
        log(f"Step 5: collect_product('{lid}', url) ...")
        t0 = time.monotonic()
        wrapper = collector.collect_product(lid, url)
        elapsed = round(time.monotonic() - t0, 1)
        if wrapper is None:
            log(f"Step 5: returned None ({elapsed}s)")
        else:
            obs = wrapper.get("observation", {})
            fields = obs.get("fields", {})
            title = (fields.get("title") or {}).get("value", "")
            prices = obs.get("price_candidates", [])
            media_items = (obs.get("media") or {}).get("items", [])
            log(f"Step 5 OK ({elapsed}s): title={title[:80]!r}")
            log(f"  fields={len(fields)} prices={len(prices)} media={len(media_items)}")
            log(f"  availability={obs.get('availability')}")
            # Save wrapper for inspection
            wpath = OUT.parent / f"diag_v3_wrapper_{source}.json"
            wpath.write_text(json.dumps(wrapper, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"  wrapper saved: {wpath.name}")

        log("=== DIAG V3 DONE (no exception) ===")

    except Exception as e:
        log(f"EXCEPTION at step: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        log("=== DIAG V3 CRASH ===")

    log(f"=== END at {time.strftime('%H:%M:%S')} ===")
    print(f"diag_v3 done for {source}. Log: {OUT}")


if __name__ == "__main__":
    main()

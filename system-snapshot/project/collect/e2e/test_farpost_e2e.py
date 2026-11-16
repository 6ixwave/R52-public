"""End-to-end тест Farpost: full URL → collect → JSON → normalize_eye check."""
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, str(Path(__file__).parent))

# 3 полных URL из v44-диагностики (подтверждённые 200 + PARSED)
FARPOST_URLS = [
    "https://www.farpost.ru/vladivostok/zapchasti/datchik-kisloroda-lynxauto-art-dm-2350-g16879823067.html",
    "https://www.farpost.ru/vladivostok/zapchasti/zaпчасти-na-auto-g125103161.html",
    "https://www.farpost.ru/vladivostok/zapchasti/zaпчасти-na-auto-g17260583280.html",
]

from r52.collectors.farpost import FarpostCollector

OUT = Path(__file__).parent / "test-intake-e2e"
OUT.mkdir(exist_ok=True)

collector = FarpostCollector(use_proxies=False)
results = []

for i, url in enumerate(FARPOST_URLS):
    # Извлекаем ID из URL для logging
    import re
    m = re.search(r'g(\d+)\.html', url)
    lid = m.group(1) if m else f"url{i}"
    print(f"\n[{i+1}/{len(FARPOST_URLS)}] ID={lid}")
    print(f"  URL: {url}")

    t0 = time.monotonic()
    try:
        resp = collector.fetch(url)
        elapsed = time.monotonic() - t0
        if resp is None:
            print(f"  FETCH FAILED ({elapsed:.1f}s), stats={collector.stats}")
            results.append({"id": lid, "status": "FETCH_FAIL"})
            continue
        print(f"  FETCH OK: {resp.status_code}, {len(resp.text)} bytes, {elapsed:.1f}s")

        obs = collector.parse_page(resp, lid, url, "")
        if obs is None:
            print(f"  PARSE FAILED")
            results.append({"id": lid, "status": "PARSE_FAIL"})
            continue

        # Пост-обработка из base.py
        collector._ensure_evidence(obs)
        collector._ensure_price_amounts(obs)
        collector._ensure_availability_in_fields(obs)

        # Обёртка в intake-формат
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        wrapped = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "FARPOST",
            "observation": {
                **obs,
                "source": "FARPOST",
                "id": str(lid),
                "url": url,
                "observed_at": now,
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
            },
        }

        # Проверка через normalize_eye
        from r52.domain.normalize import normalize_eye
        try:
            normalized, cleaned = normalize_eye(wrapped["observation"])
            print(f"  NORMALIZE OK: title={normalized['title'][:60]!r}")
            print(f"  Prices: {len(normalized['prices'])}, Media: {len((normalized.get('media') or {}).get('items', []))}")
            print(f"  Availability: {normalized['availability']}")
            results.append({"id": lid, "status": "OK", "title": normalized["title"][:60],
                            "prices": len(normalized["prices"]),
                            "media": len((normalized.get('media') or {}).get('items', []))})
        except Exception as e:
            print(f"  NORMALIZE FAILED: {e}")
            results.append({"id": lid, "status": "NORMALIZE_FAIL", "error": str(e)[:200]})

        # Запись JSON
        fname = f"jarvis-eye-farpost-{lid}.json"
        (OUT / fname).write_text(json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  Written: {fname}")

    except Exception as e:
        print(f"  ERROR: {e}")
        results.append({"id": lid, "status": "ERROR", "error": str(e)[:200]})

    if i < len(FARPOST_URLS) - 1:
        time.sleep(3)

print(f"\n{'='*60}")
print(f"SUMMARY: {sum(1 for r in results if r['status']=='OK')}/{len(results)} OK")
for r in results:
    print(f"  {r['id']}: {r['status']} {r.get('title','')}")
print(f"Stats: {collector.stats}")

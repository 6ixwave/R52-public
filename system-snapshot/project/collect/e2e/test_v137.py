"""v137: Точечная диагностика Farpost parse_returned_none.

Цель:
1. Fetch объявления напрямую (без прокси), логировать реальный HTTP-статус,
   длину, Content-Type, первые 300 символов.
2. Вызвать parse_page с детальным трассированием каждого этапа.
3. Проверить 3 объявления: 130693618, 129606872, 98836529.
4. Сохранить HTML для post-mortem.
"""

import json
import logging
import sys
import time
from pathlib import Path

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("test_v137_out.log", mode="w", encoding="utf-8"),
    ],
)
log = logging.getLogger("v137")

# Путь к проекту
PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

# Импортируем сборщик
from r52.collectors.farpost import FarpostCollector
from r52.collectors.base import BaseCollector

BULLETINS = [
    ("130693618", "https://www.farpost.ru/vladivostok/130693618.html"),
    ("129606872", "https://www.farpost.ru/vladivostok/129606872.html"),
    ("98836529", "https://www.farpost.ru/vladivostok/98836529.html"),
]

results = []

print("=" * 70)
print("V137: Farpost parse_returned_none диагностика")
print("=" * 70)

# Создаём сборщик БЕЗ прокси (direct)
collector = FarpostCollector(data_dir=None, use_proxies=False)
log.info("Collector создан. use_proxies=False, pool.stats=%s", collector.pool.stats())

for lid, url in BULLETINS:
    print(f"\n{'='*60}")
    print(f"Буллетин: {lid}")
    print(f"URL: {url}")
    print(f"{'='*60}")
    
    item_result = {
        "id": lid,
        "url": url,
        "http_status": None,
        "content_length": None,
        "content_type": None,
        "html_head_300": None,
        "parse_result": None,
        "parse_error": None,
        "parse_none_reason": None,
        "title": None,
        "price_candidates": [],
        "media_count": 0,
        "fields_keys": [],
        "elapsed_sec": 0,
    }
    
    t0 = time.monotonic()
    
    # === STAGE 1: Fetch ===
    log.info("[%s] STAGE 1: fetch", lid)
    try:
        # Обходим _throttle (8-20 сек) для диагностики — ставим _last_request = now
        collector._last_request = time.monotonic()
        resp = collector.fetch(url)
        
        if resp is None:
            item_result["parse_none_reason"] = "fetch_returned_none"
            item_result["elapsed_sec"] = round(time.monotonic() - t0, 2)
            results.append(item_result)
            log.warning("[%s] fetch returned None (HTTP не 200 или ошибка сети)", lid)
            continue
        
        item_result["http_status"] = resp.status_code
        item_result["content_length"] = len(resp.content)
        item_result["content_type"] = resp.headers.get("Content-Type", "")
        item_result["html_head_300"] = resp.content[:300].decode("windows-1251", errors="replace")
        
        log.info("[%s] HTTP %d, %d bytes, CT=%s", 
                 lid, resp.status_code, len(resp.content), item_result["content_type"])
        
        # Сохраняем HTML для post-mortem
        html_file = PROJECT / f"v137_html_{lid}.html"
        html_file.write_bytes(resp.content)
        log.info("[%s] HTML сохранён: %s (%d bytes)", lid, html_file.name, len(resp.content))
        
    except Exception as e:
        item_result["parse_error"] = f"fetch_exception: {type(e).__name__}: {e}"
        item_result["elapsed_sec"] = round(time.monotonic() - t0, 2)
        results.append(item_result)
        log.exception("[%s] fetch exception", lid)
        continue
    
    # === STAGE 2: Parse ===
    log.info("[%s] STAGE 2: parse_page", lid)
    try:
        # Декодируем как в parse_page
        ct = (resp.headers.get("Content-Type") or "").lower()
        if "windows-1251" in ct or "cp1251" in ct or "1251" in ct:
            html = resp.content.decode("windows-1251", errors="replace")
        else:
            raw_head = resp.content[:500].decode("ascii", errors="ignore")
            if "windows-1251" in raw_head.lower() or "cp1251" in raw_head.lower():
                html = resp.content.decode("windows-1251", errors="replace")
            else:
                html = resp.text
        
        log.info("[%s] HTML decoded: %d chars, encoding path: %s",
                 lid, len(html), "cp1251" if "1251" in ct or "1251" in raw_head.lower() else "resp.text")
        
        # Проверка длины
        if not html or len(html) < 500:
            item_result["parse_none_reason"] = f"html_too_short: {len(html or 0)} bytes"
            item_result["elapsed_sec"] = round(time.monotonic() - t0, 2)
            results.append(item_result)
            log.warning("[%s] HTML < 500 bytes → parse returns None", lid)
            continue
        
        # Вызываем parse_page
        observation = collector.parse_page(resp, lid, url, "")
        
        if observation is None:
            item_result["parse_none_reason"] = "parse_page_returned_none (unexpected, html > 500)"
            item_result["elapsed_sec"] = round(time.monotonic() - t0, 2)
            results.append(item_result)
            log.warning("[%s] parse_page returned None despite html > 500", lid)
            continue
        
        # Успех
        item_result["parse_result"] = "OK"
        fields = observation.get("fields", {})
        item_result["fields_keys"] = list(fields.keys())
        item_result["title"] = fields.get("title", {}).get("value", "")
        item_result["price_candidates"] = observation.get("price_candidates", [])
        item_result["media_count"] = len(observation.get("media", {}).get("items", []))
        item_result["availability"] = observation.get("availability", "UNKNOWN")
        
        log.info("[%s] PARSE OK: title=%r, prices=%d, media=%d, fields=%s",
                 lid, item_result["title"][:60] if item_result["title"] else None,
                 len(item_result["price_candidates"]),
                 item_result["media_count"],
                 item_result["fields_keys"])
        
    except Exception as e:
        item_result["parse_error"] = f"parse_exception: {type(e).__name__}: {e}"
        log.exception("[%s] parse_page exception", lid)
    
    item_result["elapsed_sec"] = round(time.monotonic() - t0, 2)
    results.append(item_result)
    
    # Пауза между объявлениями
    if lid != BULLETINS[-1][0]:
        time.sleep(3)

# === ИТОГ ===
print(f"\n{'='*70}")
print("ИТОГ V137")
print(f"{'='*70}")

summary = {
    "version": "v137",
    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    "total": len(BULLETINS),
    "success": sum(1 for r in results if r["parse_result"] == "OK"),
    "failed": sum(1 for r in results if r["parse_result"] != "OK"),
    "results": results,
}

for r in results:
    status = "OK" if r["parse_result"] == "OK" else f"FAIL ({r.get('parse_none_reason') or r.get('parse_error')})"
    print(f"  {r['id']}: {status} | HTTP={r['http_status']} | len={r['content_length']} | "
          f"prices={len(r['price_candidates'])} | media={r['media_count']} | {r['elapsed_sec']}s")
    if r["title"]:
        print(f"    title: {r['title'][:80]}")
    if r["price_candidates"]:
        for p in r["price_candidates"]:
            print(f"    price: {p.get('amount_minor')} kop ({p.get('label')})")

# Сохраняем JSON
out_file = PROJECT / "v137_result.json"
out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nРезультат: {out_file}")
print(f"Лог: {PROJECT / 'test_v137_out.log'}")

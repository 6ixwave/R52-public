"""Живая диагностика сборщиков OZON и FARPOST.
Запускает collect_product на реальных ID, сохраняет сырой HTML,
отчитывается что нашёл парсер и что нет.
"""
import sys, os, json, logging, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_collectors_live")

OUT = Path(__file__).resolve().parent / "diag_live_output"
OUT.mkdir(parents=True, exist_ok=True)

now = datetime.now(timezone(timedelta(hours=10))).isoformat()
report = {"timestamp": now, "tests": []}

# ============================================================
# Тест 1: OZON
# ============================================================
def test_ozon():
    log.info("=== OZON DIAG ===")
    test = {"source": "OZON", "steps": []}
    
    # Известный ID из прошлой диагностики
    listing_id = "286156832"
    url = f"https://www.ozon.ru/product/{listing_id}/"
    
    # 1. Прямой HTTP GET (cffi или requests)
    step = {"name": "http_fetch", "url": url}
    try:
        try:
            from curl_cffi import requests as cffi_req
            resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 30),
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                                         "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"})
            step["client"] = "curl_cffi"
            step["status"] = resp.status_code
            html = resp.text
        except ImportError:
            import requests
            resp = requests.get(url, timeout=(15, 30),
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                                         "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"})
            step["client"] = "requests"
            step["status"] = resp.status_code
            html = resp.text
        
        step["html_len"] = len(html)
        step["first_200"] = html[:200]
        
        # Сохранить сырой HTML
        html_path = OUT / f"ozon_{listing_id}_raw.html"
        html_path.write_text(html, encoding="utf-8")
        step["html_saved"] = str(html_path)
        
        # 2. Проверить ключевые маркеры
        markers = {
            "data-widget": 'data-widget' in html,
            "og:title": 'og:title' in html,
            "JSON-LD": 'application/ld+json' in html,
            "webPrice": 'webPrice' in html,
            "h1_tag": '<h1' in html.lower(),
            "title_tag": '<title' in html.lower(),
            "price_rub": '₽' in html or 'руб' in html.lower(),
            "error_widget": 'data-widget="error"' in html,
            "not_found_text": "Такой страницы не существует" in html,
            "state_var": '__STATE__' in html or 'window.__STATE__' in html,
        }
        step["markers"] = markers
        
        # 3. Запустить parse_page
        from r52.collectors.ozon import OzonCollector
        collector = OzonCollector(data_dir=ROOT / "data", use_proxies=False)
        
        class FakeResp:
            def __init__(self, text, status):
                self.text = text
                self.status_code = status
                self.headers = {"Content-Type": "text/html; charset=utf-8"}
                self.content = text.encode("utf-8")
        
        fake = FakeResp(html, step["status"])
        observation = collector.parse_page(fake, listing_id, url, "")
        
        if observation:
            step["parse_result"] = "SUCCESS"
            step["title"] = observation.get("fields", {}).get("title", {}).get("value", "")
            step["prices"] = observation.get("price_candidates", [])
            step["media_count"] = len(observation.get("media", {}).get("items", []))
            step["availability"] = observation.get("availability", "UNKNOWN")
            # Сохранить observation
            obs_path = OUT / f"ozon_{listing_id}_observation.json"
            obs_path.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
            step["obs_saved"] = str(obs_path)
        else:
            step["parse_result"] = "NONE"
            # Почему None? Проверим условия
            step["none_reason"] = f"html_len={len(html)} (< 500: {len(html) < 500})"
        
        collector.close()
    except Exception as e:
        step["error"] = f"{type(e).__name__}: {e}"
        step["traceback"] = traceback.format_exc()
    
    test["steps"].append(step)
    report["tests"].append(test)
    return test

# ============================================================
# Тест 2: FARPOST
# ============================================================
def test_farpost():
    log.info("=== FARPOST DIAG ===")
    test = {"source": "FARPOST", "steps": []}
    
    listing_id = "287654321"
    url = f"https://www.farpost.ru/vladivostok/{listing_id}.html"
    
    step = {"name": "http_fetch", "url": url}
    try:
        import requests
        resp = requests.get(url, timeout=(10, 30),
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                                     "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                                     "Referer": "https://www.farpost.ru/"})
        step["client"] = "requests"
        step["status"] = resp.status_code
        step["content_type"] = resp.headers.get("Content-Type", "")
        
        # Декодирование как в parse_page
        ct = (resp.headers.get("Content-Type") or "").lower()
        if "windows-1251" in ct or "cp1251" in ct or "1251" in ct:
            html = resp.content.decode("windows-1251", errors="replace")
            step["encoding"] = "windows-1251 (from header)"
        else:
            raw_head = resp.content[:500].decode("ascii", errors="ignore")
            if "windows-1251" in raw_head.lower() or "cp1251" in raw_head.lower():
                html = resp.content.decode("windows-1251", errors="replace")
                step["encoding"] = "windows-1251 (from meta)"
            else:
                html = resp.text
                step["encoding"] = "default (resp.text)"
        
        step["html_len"] = len(html)
        step["first_200"] = html[:200]
        
        html_path = OUT / f"farpost_{listing_id}_raw.html"
        html_path.write_text(html, encoding="utf-8")
        step["html_saved"] = str(html_path)
        
        # Маркеры
        markers = {
            "JSON-LD": 'application/ld+json' in html,
            "og:title": 'og:title' in html,
            "h1_subject": 'class="subject' in html or 'data-field="subject"' in html,
            "price_attr": 'data-bulletin-price' in html,
            "price_class": 'viewbull-summary-price' in html,
            "title_tag": '<title' in html.lower(),
            "price_rub": '₽' in html or 'руб' in html.lower(),
            "gallery": 'gallery' in html.lower(),
            "lightbox": 'lightbox' in html.lower(),
        }
        step["markers"] = markers
        
        # parse_page
        from r52.collectors.farpost import FarpostCollector
        collector = FarpostCollector(data_dir=ROOT / "data", use_proxies=False)
        
        class FakeResp:
            def __init__(self, text, status, headers, content):
                self.text = text
                self.status_code = status
                self.headers = headers
                self.content = content
        
        fake = FakeResp(html, step["status"], dict(resp.headers), resp.content)
        observation = collector.parse_page(fake, listing_id, url, "")
        
        if observation:
            step["parse_result"] = "SUCCESS"
            step["title"] = observation.get("fields", {}).get("title", {}).get("value", "")
            step["prices"] = observation.get("price_candidates", [])
            step["media_count"] = len(observation.get("media", {}).get("items", []))
            step["availability"] = observation.get("availability", "UNKNOWN")
            obs_path = OUT / f"farpost_{listing_id}_observation.json"
            obs_path.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
            step["obs_saved"] = str(obs_path)
        else:
            step["parse_result"] = "NONE"
            step["none_reason"] = f"html_len={len(html)} (< 500: {len(html) < 500})"
        
        collector.close()
    except Exception as e:
        step["error"] = f"{type(e).__name__}: {e}"
        step["traceback"] = traceback.format_exc()
    
    test["steps"].append(step)
    report["tests"].append(test)
    return test

# ============================================================
# Тест 3: Полный collect_product (все стратегии)
# ============================================================
def test_full_collect(collector_cls, source, listing_id, url):
    """Запускает полный collect_product со всеми стратегиями."""
    step = {"name": f"full_collect_{source}", "listing_id": listing_id}
    try:
        collector = collector_cls(data_dir=ROOT / "data", use_proxies=False)
        result = collector.collect_product(listing_id, url, "")
        if result:
            obs = result.get("observation", {})
            step["result"] = "SUCCESS"
            step["status"] = obs.get("status")
            step["evidence"] = obs.get("evidence_level")
            step["title"] = obs.get("fields", {}).get("title", {}).get("value", "")
            step["prices"] = obs.get("price_candidates", [])
            step["media_count"] = len(obs.get("media", {}).get("items", []))
            res_path = OUT / f"{source.lower()}_{listing_id}_full.json"
            res_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            step["saved"] = str(res_path)
        else:
            step["result"] = "NONE"
        collector.close()
    except Exception as e:
        step["error"] = f"{type(e).__name__}: {e}"
        step["traceback"] = traceback.format_exc()
    return step

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    log.info("Запуск живой диагностики. Выход: %s", OUT)
    
    # 1. OZON — прямой fetch + parse
    t1 = test_ozon()
    
    # 2. FARPOST — прямой fetch + parse
    t2 = test_farpost()
    
    # 3. Полный collect_product OZON (Composer → PW → HTTP)
    from r52.collectors.ozon import OzonCollector
    step3 = test_full_collect(OzonCollector, "OZON", "286156832",
                              "https://www.ozon.ru/product/286156832/")
    t1["steps"].append(step3)
    
    # 4. Полный collect_product FARPOST
    from r52.collectors.farpost import FarpostCollector
    step4 = test_full_collect(FarpostCollector, "FARPOST", "287654321",
                              "https://www.farpost.ru/vladivostok/287654321.html")
    t2["steps"].append(step4)
    
    # Сохранить отчёт
    report_path = OUT / "diag_collectors_live_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Отчёт: %s", report_path)
    
    # Краткая сводка
    print("\n" + "=" * 60)
    print("СВОДКА ДИАГНОСТИКИ")
    print("=" * 60)
    for t in report["tests"]:
        print(f"\n[{t['source']}]")
        for s in t["steps"]:
            if "error" in s:
                print(f"  {s['name']}: ERROR — {s['error']}")
            elif s.get("parse_result") or s.get("result"):
                res = s.get("parse_result") or s.get("result")
                title = s.get("title", "?")[:50]
                prices = s.get("prices", [])
                print(f"  {s['name']}: {res} | title={title!r} | prices={len(prices)}")
            else:
                print(f"  {s['name']}: {json.dumps({k:v for k,v in s.items() if k not in ('first_200','traceback')}, ensure_ascii=False)[:200]}")
    print()

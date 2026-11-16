"""
diag_live_v6.py — Ограниченная live-диагностика 6 источников R-52.
Запуск: python diag_live_v6.py
Вывод: diag_live_v6_output.txt (file-primary, не pipe)
Дата: 2026-11-14
"""
import sys, os, time, json, traceback, logging

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_live_v6_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
PER_SOURCE_TIMEOUT = 55  # сек на источник (включая p1+p2)

emit(f"=== R-52 diag_live_v6 — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit(f"R52_HOME: {os.environ['R52_HOME']}")
emit(f"use_proxies: False (direct, no proxy file)")
emit()

# ─── Инициализация сборщиков ───
sources = {}
init_errors = {}

def make_collector(name, cls_path, cls_name):
    """Импортирует и создаёт экземпляр с use_proxies=False."""
    try:
        mod = __import__(cls_path, fromlist=[cls_name])
        cls = getattr(mod, cls_name)
        # Подписи конструктора варьируются; пробуем типовые kwargs
        for kw in [dict(use_proxies=False), dict(source=name, use_proxies=False), dict()]:
            try:
                obj = cls(**kw)
                obj.source = name
                return obj
            except TypeError:
                continue
        obj = cls()
        obj.source = name
        return obj
    except Exception as e:
        init_errors[name] = f"import/init: {type(e).__name__}: {e}"
        return None

emit("── Инициализация ──")
for name, mod_path, cls_name in [
    ("WB", "r52.collectors.wb", "WBCollector"),
    ("OZON", "r52.collectors.ozon", "OzonCollector"),
    ("AVITO", "r52.collectors.avito", "AvitoCollector"),
    ("FARPOST", "r52.collectors.farpost", "FarpostCollector"),
    ("ALIEXPRESS", "r52.collectors.aliexpress", "AliExpressCollector"),
    ("ALIEXPRESS_RU", "r52.collectors.aliexpress", "AliExpressRUCollector"),
]:
    obj = make_collector(name, mod_path, cls_name)
    if obj:
        sources[name] = obj
        emit(f"  {name}: OK ({type(obj).__name__})")
    else:
        emit(f"  {name}: FAIL — {init_errors.get(name, '?')}")
emit()

# ─── Тестовый прогон ───
results = {}

def test_source(name, collector, page):
    """Один запрос: страница page. Возвращает dict-результат."""
    t0 = time.monotonic()
    entry = {
        "source": name, "page": page,
        "status": "UNKNOWN", "http": None,
        "count": 0, "ids": [], "error": "",
        "elapsed": 0.0,
    }
    try:
        items = collector.collect_by_search(QUERY, page=page, page_size=10)
        entry["elapsed"] = round(time.monotonic() - t0, 1)
        if items:
            entry["status"] = "OK"
            entry["count"] = len(items)
            for it in items[:10]:
                obs = it.get("observation", it)
                iid = str(obs.get("id", ""))
                if iid:
                    entry["ids"].append(iid)
        else:
            entry["status"] = "EMPTY"
            entry["error"] = "0 items returned"
    except Exception as e:
        entry["elapsed"] = round(time.monotonic() - t0, 1)
        msg = str(e)
        entry["error"] = f"{type(e).__name__}: {msg[:200]}"
        if "SEARCH_HTTP_" in msg:
            entry["http"] = msg.split("SEARCH_HTTP_")[1].split()[0] if "SEARCH_HTTP_" in msg else None
            entry["status"] = "HTTP_BLOCK"
        elif "SEARCH_PROXY_NOT_READY" in msg:
            entry["status"] = "PROXY_FAIL"
        elif "SEARCH_WAF" in msg or "CHALLENGE" in msg:
            entry["status"] = "WAF"
        elif "SearchError" in type(e).__name__:
            entry["status"] = "SEARCH_ERR"
        else:
            entry["status"] = "EXCEPTION"
    return entry

emit("── Live-тест (p1 + p2) ──")
for name in ["WB", "OZON", "AVITO", "FARPOST", "ALIEXPRESS", "ALIEXPRESS_RU"]:
    col = sources.get(name)
    if not col:
        emit(f"  {name}: SKIP (init failed)")
        results[name] = []
        continue

    emit(f"  {name}: testing...")
    src_results = []
    src_deadline = time.monotonic() + PER_SOURCE_TIMEOUT

    for pg in (1, 2):
        if time.monotonic() > src_deadline:
            emit(f"    p{pg}: TIMEOUT (source deadline {PER_SOURCE_TIMEOUT}s)")
            src_results.append({"source": name, "page": pg, "status": "TIMEOUT", "elapsed": 0, "count": 0, "ids": [], "error": "source deadline"})
            break
        r = test_source(name, col, pg)
        src_results.append(r)
        emit(f"    p{pg}: {r['status']} | {r['count']} items | {r['elapsed']}s | {r['error'][:80]}")
        # Пауза между p1 и p2
        if pg == 1 and r["status"] == "OK":
            time.sleep(3)

    results[name] = src_results
    emit()

# ─── Итоговая таблица ───
emit("── ИТОГ ──")
emit(f"{'Source':<14} {'P1':<10} {'P1#':<5} {'P2':<10} {'P2#':<5} {'P1s':<6} {'P2s':<6}")
emit("-" * 60)
ok_p1 = 0
ok_p2 = 0
for name in ["WB", "OZON", "AVITO", "FARPOST", "ALIEXPRESS", "ALIEXPRESS_RU"]:
    rr = results.get(name, [])
    p1 = rr[0] if len(rr) > 0 else {"status": "SKIP", "count": 0, "elapsed": 0}
    p2 = rr[1] if len(rr) > 1 else {"status": "SKIP", "count": 0, "elapsed": 0}
    if p1["status"] == "OK": ok_p1 += 1
    if p2["status"] == "OK": ok_p2 += 1
    emit(f"{name:<14} {p1['status']:<10} {p1['count']:<5} {p2['status']:<10} {p2['count']:<5} {p1['elapsed']:<6} {p2['elapsed']:<6}")

emit()
emit(f"Live OK p1: {ok_p1}/6")
emit(f"Live OK p2: {ok_p2}/6")

# ─── Детали ID ───
emit()
emit("── ID-детали ──")
for name in ["WB", "OZON", "AVITO", "FARPOST", "ALIEXPRESS", "ALIEXPRESS_RU"]:
    rr = results.get(name, [])
    for r in rr:
        if r["ids"]:
            emit(f"  {name} p{r['page']}: {r['count']} items, first 5 IDs: {r['ids'][:5]}")
        elif r["status"] == "OK":
            emit(f"  {name} p{r['page']}: OK but 0 IDs extracted")

# ─── Повторяемость p1 vs p2 ───
emit()
emit("── Повторяемость p1/p2 ──")
for name in ["WB", "OZON", "AVITO", "FARPOST", "ALIEXPRESS", "ALIEXPRESS_RU"]:
    rr = results.get(name, [])
    if len(rr) >= 2 and rr[0]["ids"] and rr[1]["ids"]:
        overlap = set(rr[0]["ids"]) & set(rr[1]["ids"])
        emit(f"  {name}: p1={len(rr[0]['ids'])} p2={len(rr[1]['ids'])} overlap={len(overlap)}")
        if len(overlap) == len(rr[0]["ids"]) and len(rr[0]["ids"]) > 0:
            emit(f"    WARNING: p2 IDs fully overlap p1 (pagination may be broken)")

emit()
emit("=== DONE ===")
flush()
print(f"\nOutput written to: {OUT}")

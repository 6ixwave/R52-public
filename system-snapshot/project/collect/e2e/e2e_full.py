#!/usr/bin/env python3
"""E2E: 4 JSON-выгрузки (Farpost/WB) + Ozon live → Inbox → test SQLite.
Замер дельты product / observation / price_point по источникам.
"""
import json, sys, time, traceback, os, inspect
from pathlib import Path

BASE = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
sys.path.insert(0, str(BASE))
os.environ["R52_HOME"] = str(BASE / "e2e_test_home")

from r52.config import load_config
from r52.service import App

config = load_config(home=BASE / "e2e_test_home")
app = App(config, install_guard=False)
app.open()

def snap(label):
    with app.db.read() as conn:
        p  = conn.execute("SELECT count(*) FROM product").fetchone()[0]
        o  = conn.execute("SELECT count(*) FROM observation").fetchone()[0]
        pp = conn.execute("SELECT count(*) FROM price_point").fetchone()[0]
        src = conn.execute("""
            SELECT p.source,
                   count(DISTINCT p.id)  AS products,
                   count(DISTINCT o.id)  AS observations,
                   count(DISTINCT pp.id) AS price_points
            FROM product p
            LEFT JOIN observation o  ON o.product_id = p.id
            LEFT JOIN price_point pp ON pp.observation_id = o.id
            GROUP BY p.source ORDER BY p.source
        """).fetchall()
    s = {"products": p, "observations": o, "price_points": pp,
         "sources": [dict(r) for r in src]}
    print(f"\n=== {label} === {json.dumps(s, ensure_ascii=False)}")
    return s

def feed(label, data, fname):
    """stage_bytes → accept, печатает результат. Возвращает dict."""
    item, created = app.inbox.stage_bytes(data, filename=fname, origin="E2E_TEST")
    print(f"  [{label}] staged created={created} status={item['status']} id={item['id']}")
    if item["status"] == "PENDING":
        res = app.inbox.accept(item["id"])
        pv = res.get("preview") or {}
        print(f"    → product={res.get('product_id')} obs_new={res.get('observation_created')} "
              f"title={str(pv.get('title','?'))[:70]} price={pv.get('price_text','?')} "
              f"avail={pv.get('availability','?')}")
        return res
    elif item["status"] == "INVALID":
        print(f"    → INVALID code={item.get('error_code')}")
        return item
    else:
        print(f"    → {item['status']} (auto-accepted?)")
        return item

snap("BEFORE")

# ── Part 1: 4 существующих JSON-выгрузки ──
print("\n──── FEED EXISTING JSON ────")
files = [
    ("FP-1", BASE / "e2e-farpost-v2" / "jarvis-eye-farpost-65c1950c.json"),
    ("FP-2", BASE / "e2e-farpost-v2" / "jarvis-eye-farpost-b9b6aaed.json"),
    ("WB-1", BASE / "e2e-wb-v1" / "jarvis-eye-wb-056e75a5.json"),
    ("WB-2", BASE / "e2e-wb-v1" / "jarvis-eye-wb-d278551a.json"),
]
for label, path in files:
    try:
        feed(label, path.read_bytes(), path.name)
    except Exception as e:
        print(f"    → ERROR {type(e).__name__}: {e}")
        traceback.print_exc()

snap("AFTER_FILES")

# ── Part 2: Ozon live на 3481320800 ──
print("\n──── OZON LIVE 3481320800 ────")
try:
    from r52.collectors import ozon as ozon_mod
    # Инспекция интерфейса
    cls = getattr(ozon_mod, "OzonCollector", None)
    if cls is None:
        # ищем любой класс-наследник
        for name in dir(ozon_mod):
            obj = getattr(ozon_mod, name)
            if isinstance(obj, type) and "ollect" in name:
                cls = obj
                print(f"  found class: {name}")
                break
    if cls:
        print(f"  class={cls.__name__} init={inspect.signature(cls.__init__)}")
        try:
            oc = cls(app)
        except TypeError:
            try:
                oc = cls()
            except TypeError as e2:
                print(f"  init(app) and init() both failed: {e2}")
                oc = None
        if oc:
            coll = getattr(oc, "collect", None) or getattr(oc, "collect_item", None)
            if coll:
                print(f"  collect sig: {inspect.signature(coll)}")
            t0 = time.time()
            # Пробуем разные сигнатуры
            obs = None
            for args in [("3481320800",), ([{"id":"3481320800","url":"https://www.ozon.ru/product/3481320800/"}],)]:
                try:
                    obs = coll(*args)
                    break
                except TypeError:
                    continue
            dt = time.time() - t0
            if obs:
                ti = obs.get("fields",{}).get("title",{}).get("value","?")
                pr = obs.get("price_candidates",[])
                print(f"  collected {dt:.1f}s: {ti[:70]}")
                print(f"  prices: {[(p.get('role'),p.get('amount'),p.get('currency')) for p in pr]}")
                doc = {"format":"JARVIS_BR02_EYE_USER_CAPTURE","capture_action":"EXPLICIT_POPUP_CLICK",
                       "fully_autonomous":False,"collector_source":"OZON","observation":obs}
                feed("OZON", json.dumps(doc, ensure_ascii=False).encode(), "e2e-ozon-3481320800.json")
            else:
                print(f"  collect → None in {dt:.1f}s")
    else:
        print("  no collector class found in ozon module")
except Exception as e:
    print(f"  OZON ERROR: {type(e).__name__}: {e}")
    traceback.print_exc()

snap("AFTER_ALL")

# ── Детализация: что в product ──
print("\n──── PRODUCT DETAIL ────")
with app.db.read() as conn:
    rows = conn.execute("""
        SELECT p.id, p.source, p.listing_id, substr(p.title,1,60) AS title,
               p.last_currency, p.last_regular_minor, p.last_lowest_minor,
               p.last_availability, p.observation_count
        FROM product p ORDER BY p.id DESC LIMIT 20
    """).fetchall()
for r in rows:
    d = dict(r)
    price = f"{d['last_lowest_minor']} {d['last_currency']}" if d["last_lowest_minor"] else "—"
    print(f"  #{d['id']} [{d['source']}] {d['listing_id']} {d['title']} | {price} | avail={d['last_availability']} obs={d['observation_count']}")

# Сохранить
out = {"note": "see stdout", "files_tested": [f[1].name for f in files]}
(BASE / "e2e_full_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
print("\nDONE")
app.stop()

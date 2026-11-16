#!/usr/bin/env python3
"""E2E: Ozon 3481320800 + WB 138278567 через collect_product → Inbox → SQLite."""
import json, sys, time, traceback, os
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
    print(f"=== {label} === products={p} obs={o} pp={pp}")
    return (p, o, pp)

def feed(label, obs, source):
    doc = {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": source,
        "observation": obs,
    }
    data = json.dumps(doc, ensure_ascii=False).encode()
    item, created = app.inbox.stage_bytes(data, filename=f"e2e-{label}.json", origin="E2E_TEST")
    print(f"  [{label}] staged={created} status={item['status']} id={item['id']}")
    if item["status"] == "PENDING":
        res = app.inbox.accept(item["id"])
        pv = res.get("preview") or {}
        print(f"    → product={res.get('product_id')} obs_new={res.get('observation_created')}")
        print(f"      title={str(pv.get('title','?'))[:80]}")
        print(f"      price={pv.get('price_text','?')} avail={pv.get('availability','?')}")
        return res
    elif item["status"] == "INVALID":
        print(f"    → INVALID code={item.get('error_code')}")
    return item

snap("BEFORE")

# ── OZON ──
print("\n──── OZON 3481320800 ────")
try:
    from r52.collectors.ozon import OzonCollector
    oc = OzonCollector(data_dir=None, use_proxies=False)
    url = oc.build_url("3481320800")
    print(f"  url={url}")
    t0 = time.time()
    obs = oc.collect_product("3481320800", url)
    dt = time.time() - t0
    if obs:
        ti = obs.get("fields", {}).get("title", {}).get("value", "?")
        pr = obs.get("price_candidates", [])
        print(f"  {dt:.1f}s title={ti[:80]}")
        print(f"  prices={[(p.get('role'), p.get('amount'), p.get('currency')) for p in pr]}")
        print(f"  status={obs.get('status')} avail={obs.get('availability')}")
        feed("ozon-3481320800", obs, "OZON")
    else:
        print(f"  {dt:.1f}s → None")
except Exception as e:
    print(f"  ERROR {type(e).__name__}: {e}")
    traceback.print_exc()

# ── WB (item с ценой: 138278567) ──
print("\n──── WB 138278567 ────")
try:
    from r52.collectors.wb import WBCollector
    wc = WBCollector(data_dir=None, use_proxies=False)
    url = wc.build_url("138278567")
    print(f"  url={url}")
    t0 = time.time()
    obs = wc.collect_product("138278567", url)
    dt = time.time() - t0
    if obs:
        ti = obs.get("fields", {}).get("title", {}).get("value", "?")
        pr = obs.get("price_candidates", [])
        print(f"  {dt:.1f}s title={ti[:80]}")
        print(f"  prices={[(p.get('role'), p.get('amount'), p.get('currency')) for p in pr]}")
        print(f"  status={obs.get('status')} avail={obs.get('availability')}")
        feed("wb-138278567", obs, "WB")
    else:
        print(f"  {dt:.1f}s → None")
except Exception as e:
    print(f"  ERROR {type(e).__name__}: {e}")
    traceback.print_exc()

# ── WB (второй: 145567890) ──
print("\n──── WB 145567890 ────")
try:
    from r52.collectors.wb import WBCollector
    wc2 = WBCollector(data_dir=None, use_proxies=False)
    url = wc2.build_url("145567890")
    print(f"  url={url}")
    t0 = time.time()
    obs = wc2.collect_product("145567890", url)
    dt = time.time() - t0
    if obs:
        ti = obs.get("fields", {}).get("title", {}).get("value", "?")
        pr = obs.get("price_candidates", [])
        print(f"  {dt:.1f}s title={ti[:80]}")
        print(f"  prices={[(p.get('role'), p.get('amount'), p.get('currency')) for p in pr]}")
        print(f"  status={obs.get('status')} avail={obs.get('availability')}")
        feed("wb-145567890", obs, "WB")
    else:
        print(f"  {dt:.1f}s → None")
except Exception as e:
    print(f"  ERROR {type(e).__name__}: {e}")
    traceback.print_exc()

snap("AFTER")

# Детализация
print("\n──── PRODUCTS ────")
with app.db.read() as conn:
    rows = conn.execute("""
        SELECT p.id, p.source, p.listing_id, substr(p.title,1,50) AS t,
               p.last_currency, p.last_regular_minor, p.last_lowest_minor,
               p.last_availability, p.observation_count
        FROM product p ORDER BY p.id
    """).fetchall()
for r in rows:
    d = dict(r)
    price = f"{d['last_lowest_minor']} {d['last_currency']}" if d["last_lowest_minor"] else "—"
    print(f"  #{d['id']} [{d['source']}] {d['listing_id']} {d['t']} | {price} | {d['last_availability']} obs={d['observation_count']}")

print("\nDONE")
app.stop()

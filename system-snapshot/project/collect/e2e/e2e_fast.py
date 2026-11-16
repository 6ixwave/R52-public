#!/usr/bin/env python3
"""E2E FAST v3: Ozon + WB. Лог в файл, threading-timeout, без signal."""
import json, sys, time, traceback, os, threading
from pathlib import Path

BASE = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
LOG = BASE / "e2e_fast.log"
log_lines = []

def L(msg=""):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    log_lines.append(line)
    print(line, flush=True)

def run_with_timeout(label, fn, secs=90):
    result = [None]
    error = [None]
    def target():
        try:
            result[0] = fn()
        except Exception as e:
            error[0] = e
    t = threading.Thread(target=target, daemon=True)
    t0 = time.time()
    t.start()
    t.join(timeout=secs)
    dt = time.time() - t0
    if t.is_alive():
        L(f"  [{label}] TIMEOUT {dt:.0f}s (killing)")
        return None, dt
    if error[0]:
        L(f"  [{label}] ERROR {dt:.1f}s {type(error[0]).__name__}: {error[0]}")
        L(f"    {traceback.format_exc()}")
        return None, dt
    L(f"  [{label}] OK {dt:.1f}s")
    return result[0], dt

# Redirect stderr to stdout to avoid PowerShell mangling
sys.stderr = sys.stdout

L("=== E2E FAST v3 START ===")
os.environ["R52_HOME"] = str(BASE / "e2e_test_home")
sys.path.insert(0, str(BASE))

from r52.config import load_config
from r52.service import App

config = load_config(home=BASE / "e2e_test_home")
app = App(config, install_guard=False)
app.open()
L("App opened OK")

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
    L(f"  [{label}] staged={created} status={item['status']} id={item['id']}")
    if item["status"] == "PENDING":
        res = app.inbox.accept(item["id"])
        pv = res.get("preview") or {}
        L(f"    product={res.get('product_id')} obs_new={res.get('observation_created')}")
        L(f"    title={str(pv.get('title','?'))[:80]}")
        L(f"    price={pv.get('price_text','?')} avail={pv.get('availability','?')}")
        return res
    elif item["status"] == "INVALID":
        L(f"    INVALID code={item.get('error_code')}")
    return item

# ═══ OZON ═══
L("")
L("━━━ OZON 3481320800 ━━━")
def do_ozon():
    from r52.collectors.ozon import OzonCollector
    oc = OzonCollector(data_dir=None, use_proxies=False)
    # Reset throttle
    if hasattr(oc, '_last_request'):
        oc._last_request = 0
    url = oc.build_url("3481320800")
    L(f"  url={url}")
    obs = oc.collect_product("3481320800", url)
    oc.close()
    return obs

obs, dt = run_with_timeout("OZON", do_ozon, secs=90)
if obs:
    ti = obs.get("fields", {}).get("title", {}).get("value", "?")
    pr = obs.get("price_candidates", [])
    L(f"  title={str(ti)[:80]}")
    L(f"  prices={[(p.get('role'), p.get('amount_minor', p.get('amount')), p.get('currency')) for p in pr]}")
    L(f"  status={obs.get('status')} avail={obs.get('availability')}")
    feed("ozon-3481320800", obs, "OZON")
else:
    L("  → no observation")

# ═══ WB ═══
L("")
L("━━━ WB 138278567 ━━━")
def do_wb():
    from r52.collectors.wb import WBCollector
    wc = WBCollector(data_dir=None, use_proxies=False)
    if hasattr(wc, '_last_request'):
        wc._last_request = 0
    url = wc.build_url("138278567")
    L(f"  url={url}")
    obs = wc.collect_product("138278567", url)
    return obs

obs, dt = run_with_timeout("WB", do_wb, secs=90)
if obs:
    ti = obs.get("fields", {}).get("title", {}).get("value", "?")
    pr = obs.get("price_candidates", [])
    L(f"  title={str(ti)[:80]}")
    L(f"  prices={[(p.get('role'), p.get('amount_minor', p.get('amount')), p.get('currency')) for p in pr]}")
    L(f"  status={obs.get('status')} avail={obs.get('availability')}")
    feed("wb-138278567-v3", obs, "WB")
else:
    L("  → no observation")

# ═══ DB ═══
L("")
L("━━━ DB STATE ━━━")
with app.db.read() as conn:
    rows = conn.execute("""
        SELECT p.id, p.source, p.listing_id, substr(p.title,1,50) AS t,
               p.last_regular_minor, p.last_lowest_minor, p.last_currency,
               p.last_availability, p.observation_count
        FROM product p ORDER BY p.id
    """).fetchall()
for r in rows:
    d = dict(r)
    price = f"{d['last_lowest_minor']} {d['last_currency']}" if d["last_lowest_minor"] else "—"
    L(f"  #{d['id']} [{d['source']}] {d['listing_id']} {d['t']} | {price} | {d['last_availability']} obs={d['observation_count']}")

L("")
L("=== DONE ===")
app.stop()

# Write log
LOG.write_text("\n".join(log_lines), encoding="utf-8")

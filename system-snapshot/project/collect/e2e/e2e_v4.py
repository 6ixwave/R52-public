#!/usr/bin/env python3
"""E2E v4: Ozon (Composer API only) + WB (fixed feed). Быстрый, без Playwright."""
import json, sys, time, traceback, os, threading
from pathlib import Path

BASE = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
LOG = BASE / "e2e_v4_stdout.txt"
log_lines = []

def L(msg=""):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    log_lines.append(line)
    print(line, flush=True)

sys.stderr = sys.stdout

L("=== E2E V4 START ===")
os.environ["R52_HOME"] = str(BASE / "e2e_test_home")
sys.path.insert(0, str(BASE))

from r52.config import load_config
from r52.service import App

config = load_config(home=BASE / "e2e_test_home")
app = App(config, install_guard=False)
app.open()
L("App opened OK")

def feed(label, full_doc, source):
    """full_doc = return of collector.collect_product() — уже JARVIS_BR02_EYE_USER_CAPTURE."""
    # Извлекаем observation из full_doc
    obs = full_doc.get("observation", full_doc)
    # Проверяем что url есть на уровне observation
    obs_url = obs.get("url", "")
    L(f"  [{label}] obs.url={obs_url[:80]}")
    L(f"  [{label}] obs.source={obs.get('source','?')} obs.id={obs.get('id','?')}")
    
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

def show_obs(label, full_doc):
    obs = full_doc.get("observation", full_doc)
    ti = obs.get("fields", {}).get("title", {}).get("value", "?")
    pr = obs.get("price_candidates", [])
    L(f"  [{label}] title={str(ti)[:80]}")
    L(f"  [{label}] prices={[(p.get('role'), p.get('amount_minor'), p.get('currency')) for p in pr]}")
    L(f"  [{label}] status={obs.get('status')} avail={obs.get('availability')}")

# ═══ OZON: Composer API only (no Playwright) ═══
L("")
L("━━━ OZON 3481320800 (Composer API) ━━━")
def do_ozon():
    from r52.collectors.ozon import OzonCollector
    oc = OzonCollector(data_dir=None, use_proxies=False)
    if hasattr(oc, '_last_request'):
        oc._last_request = 0
    url = oc.build_url("3481320800")
    L(f"  url={url}")
    # Только Composer API — без Playwright
    result = oc._try_composer_api("3481320800", url, "")
    oc.close()
    return result

t0 = time.time()
try:
    composer_obs = do_ozon()
    dt = time.time() - t0
    L(f"  Composer API: {dt:.1f}s")
    if composer_obs:
        show_obs("OZON", composer_obs)
        feed("ozon-3481320800-v4", composer_obs, "OZON")
    else:
        L(f"  Composer API: returned None")
except Exception as e:
    dt = time.time() - t0
    L(f"  Composer API ERROR {dt:.1f}s: {type(e).__name__}: {e}")
    L(f"  {traceback.format_exc()}")

# ═══ OZON: direct curl_cffi test (diagnostic) ═══
L("")
L("━━━ OZON direct curl_cffi ━━━")
def do_ozon_direct():
    from curl_cffi import requests as cffi_req
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.ozon.ru/",
    }
    ep = "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/3481320800/"
    L(f"  GET {ep}")
    resp = cffi_req.get(ep, impersonate="chrome", timeout=15, headers=headers)
    L(f"  HTTP {resp.status_code} ({len(resp.content)}B)")
    if resp.status_code == 200:
        data = resp.json()
        # Quick scan for title/price
        def deep_find(d, keys, depth=0):
            if depth > 5:
                return None
            if isinstance(d, dict):
                for k in keys:
                    if k in d and d[k]:
                        return d[k]
                for v in d.values():
                    r = deep_find(v, keys, depth+1)
                    if r:
                        return r
            elif isinstance(d, list):
                for item in d[:10]:
                    r = deep_find(item, keys, depth+1)
                    if r:
                        return r
            return None
        title = deep_find(data, ["title", "name", "seoTitle"])
        L(f"  title={str(title)[:80] if title else 'NOT FOUND'}")
        # Save first 2000 chars for analysis
        snippet = json.dumps(data, ensure_ascii=False)[:2000]
        L(f"  JSON snippet: {snippet}")
        return data
    else:
        L(f"  body[:300]={resp.text[:300]}")
    return None

try:
    do_ozon_direct()
except Exception as e:
    L(f"  direct curl ERROR: {type(e).__name__}: {e}")

# ═══ WB: fixed feed ═══
L("")
L("━━━ WB 138278567 (Утюг BBK) ━━━")
def do_wb():
    from r52.collectors.wb import WBCollector
    wc = WBCollector(data_dir=None, use_proxies=False)
    if hasattr(wc, '_last_request'):
        wc._last_request = 0
    url = wc.build_url("138278567")
    L(f"  url={url}")
    obs = wc.collect_product("138278567", url)
    wc.close()
    return obs

t0 = time.time()
try:
    wb_doc = do_wb()
    dt = time.time() - t0
    L(f"  WB collect: {dt:.1f}s")
    if wb_doc:
        show_obs("WB", wb_doc)
        feed("wb-138278567-v4", wb_doc, "WB")
    else:
        L("  WB: returned None")
except Exception as e:
    dt = time.time() - t0
    L(f"  WB ERROR {dt:.1f}s: {type(e).__name__}: {e}")
    L(f"  {traceback.format_exc()}")

# ═══ DB STATE ═══
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

    pp = conn.execute("""
        SELECT COUNT(*) as n FROM price_point pp JOIN product p2 ON p2.id=pp.product_id WHERE p2.id=?
    """, (d['id'],)).fetchone()
    L(f"    price_points={pp[0]}")

L("")
L("=== DONE ===")
app.stop()
LOG.write_text("\n".join(log_lines), encoding="utf-8")

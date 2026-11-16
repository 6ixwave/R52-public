#!/usr/bin/env python3
"""E2E v5: Ozon (curl_cffi verify=False + requests) + WB (fixed close)."""
import json, sys, time, traceback, os
from pathlib import Path

BASE = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
os.environ["R52_HOME"] = str(BASE / "e2e_test_home")
sys.path.insert(0, str(BASE))

def L(msg=""):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

L("=== E2E V5 START ===")

from r52.config import load_config
from r52.service import App
config = load_config(home=BASE / "e2e_test_home")
app = App(config, install_guard=False)
app.open()
L("App OK")

def feed(label, full_doc, source):
    obs = full_doc.get("observation", full_doc)
    L(f"  [{label}] url={obs.get('url','?')[:80]}")
    L(f"  [{label}] source={obs.get('source','?')} id={obs.get('id','?')}")
    L(f"  [{label}] title={str(obs.get('fields',{}).get('title',{}).get('value','?'))[:60]}")
    L(f"  [{label}] prices={[(p.get('role'),p.get('amount_minor')) for p in obs.get('price_candidates',[])]}")
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
        L(f"    ACCEPTED: product={res.get('product_id')} title={str(pv.get('title','?'))[:60]} price={pv.get('price_text','?')}")
        return res
    elif item["status"] == "INVALID":
        L(f"    INVALID: code={item.get('error_code')}")
    return item

# ═══════════════════════════════════════════════════════
# OZON Strategy A: curl_cffi verify=False
# ═══════════════════════════════════════════════════════
L("")
L("━━━ OZON-A: curl_cffi verify=False ━━━")
t0 = time.time()
ozon_data = None
try:
    from curl_cffi import requests as cffi_req
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.ozon.ru/",
    }
    endpoints = [
        "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/3481320800/",
        "https://www.ozon.ru/api/entrypoint/pdp/3481320800",
    ]
    for ep in endpoints:
        L(f"  GET {ep[:75]}")
        try:
            resp = cffi_req.get(ep, impersonate="chrome", timeout=15, headers=headers, verify=False)
            L(f"    HTTP {resp.status_code} ({len(resp.content)}B) {time.time()-t0:.1f}s")
            if resp.status_code == 200:
                data = resp.json()
                def deep_find(d, keys, depth=0):
                    if depth > 6: return None
                    if isinstance(d, dict):
                        for k in keys:
                            if k in d and d[k]: return d[k]
                        for v in d.values():
                            r = deep_find(v, keys, depth+1)
                            if r: return r
                    elif isinstance(d, list):
                        for item in d[:10]:
                            r = deep_find(item, keys, depth+1)
                            if r: return r
                    return None
                title = deep_find(data, ["title","name","seoTitle"])
                price = deep_find(data, ["price","currentPrice","priceInfo"])
                L(f"    title={str(title)[:60] if title else 'NOT FOUND'}")
                L(f"    price={str(price)[:80] if price else 'NOT FOUND'}")
                (BASE / "ozon_composer_raw.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2)[:50000], encoding="utf-8")
                ozon_data = data
                break
            else:
                L(f"    body[:200]={resp.text[:200]}")
        except Exception as e:
            L(f"    ERR: {type(e).__name__}: {str(e)[:120]}")
    L(f"  OZON-A total: {time.time()-t0:.1f}s → {'GOT DATA' if ozon_data else 'NONE'}")
except Exception as e:
    L(f"  OZON-A FATAL: {type(e).__name__}: {e}")
    L(traceback.format_exc()[:500])

# ═══════════════════════════════════════════════════════
# OZON Strategy B: regular requests (no curl_cffi)
# ═══════════════════════════════════════════════════════
L("")
L("━━━ OZON-B: requests (no cffi) ━━━")
t0 = time.time()
try:
    import requests as req
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    ep = "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/3481320800/"
    L(f"  GET {ep[:75]}")
    resp = req.get(ep, headers=headers, timeout=15)
    L(f"    HTTP {resp.status_code} ({len(resp.content)}B) {time.time()-t0:.1f}s")
    if resp.status_code == 200:
        data = resp.json()
        L(f"    top_keys={list(data.keys())[:8]}")
        (BASE / "ozon_req_raw.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)[:50000], encoding="utf-8")
        if not ozon_data:
            ozon_data = data
    else:
        L(f"    body[:200]={resp.text[:200]}")
except Exception as e:
    L(f"  OZON-B ERR: {type(e).__name__}: {str(e)[:120]}")

# ═══════════════════════════════════════════════════════
# OZON: build observation from composer data + feed
# ═══════════════════════════════════════════════════════
L("")
L("━━━ OZON: build obs + feed ━━━")
if ozon_data:
    t0 = time.time()
    try:
        from r52.collectors.ozon import OzonCollector
        oc = OzonCollector(data_dir=None, use_proxies=False)
        # Reuse collector's parsing on the raw data
        # Build a fake response
        class _R:
            def __init__(s, d): s.text = json.dumps(d, ensure_ascii=False); s.status_code = 200
        # Actually use _try_composer_api logic manually
        fields = {}
        price_candidates = []
        media_items = []
        seen_media = set()
        
        # Title
        for path in [("page","title"),("page","seoTitle"),("product","title"),
                     ("product","name"),("data","title"),("props","pageProps","product","title")]:
            t = oc._deep_get(ozon_data, *path)
            if t:
                fields["title"] = {"status":"KNOWN","value":str(t),"method":"COMPOSER_API",
                                   "evidence":{"method":"COMPOSER_API","source":"OZON"}}
                break
        # Price
        price_obj = None
        for path in [("product","price"),("page","price"),("data","price"),
                     ("product","priceInfo"),("props","pageProps","product","price")]:
            price_obj = oc._deep_get(ozon_data, *path)
            if price_obj: break
        if not price_obj:
            blocks = oc._deep_get(ozon_data, "page", "blocks")
            if isinstance(blocks, list):
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") in ("webPrice","price","pdp-price"):
                        price_obj = b.get("props",{}).get("price") or b.get("data",{}).get("price")
                        if price_obj: break
        if isinstance(price_obj, dict):
            for pk, role in [("price","regular"),("current","regular"),("basic","regular"),
                             ("old","old"),("crossed","old"),("card","card"),
                             ("min","regular"),("max","regular"),("value","regular")]:
                pv = price_obj.get(pk)
                if isinstance(pv, (int,float)) and pv > 0:
                    amount = int(pv) if pv >= 1000 else int(pv * 100)
                    price_candidates.append({"role":role,"amount_minor":amount,"currency":"RUB",
                                             "label":f"composer.{pk}","amount":f"{amount//100}.{amount%100:02d}",
                                             "visible_label":f"composer.{pk}"})
        elif isinstance(price_obj, (int,float)) and price_obj > 0:
            amount = int(price_obj) if price_obj >= 1000 else int(price_obj*100)
            price_candidates.append({"role":"regular","amount_minor":amount,"currency":"RUB",
                                     "label":"composer.price","amount":f"{amount//100}.{amount%100:02d}",
                                     "visible_label":"composer.price"})
        # Media
        for path in [("product","images"),("page","images"),("product","photos"),
                     ("product","gallery"),("data","images")]:
            images = oc._deep_get(ozon_data, *path)
            if isinstance(images, list) and images:
                for img in images[:30]:
                    if isinstance(img, str):
                        oc._add_media(media_items, seen_media, "image", img)
                    elif isinstance(img, dict):
                        u = img.get("url") or img.get("src") or img.get("path")
                        if u: oc._add_media(media_items, seen_media, "image", u)
                break
        # Description
        for path in [("product","description"),("page","description"),("data","description")]:
            desc = oc._deep_get(ozon_data, *path)
            if desc and isinstance(desc, str) and len(desc) > 20:
                fields["description"] = {"status":"KNOWN","value":desc[:3000],
                                         "method":"COMPOSER_API","evidence":{"method":"COMPOSER_API","source":"OZON"}}
                break
        
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        has_title = bool(fields.get("title",{}).get("value",""))
        has_price = len(price_candidates) > 0
        status = "PARTIAL_SUCCESS" if (has_title and has_price) else "DEGRADED"
        
        obs = {
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": "UNKNOWN",
            "category_path": fields.get("category",{}).get("value"),
            "source": "OZON",
            "id": "3481320800",
            "url": "https://www.ozon.ru/product/3481320800/",
            "observed_at": now,
            "status": status,
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
        }
        full_doc = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "OZON",
            "observation": obs,
        }
        L(f"  Built obs: title={str(fields.get('title',{}).get('value','?'))[:60]}")
        L(f"  prices={[(p['role'],p['amount_minor']) for p in price_candidates]}")
        L(f"  media={len(media_items)} items")
        feed("ozon-3481320800-v5", full_doc, "OZON")
        L(f"  OZON feed: {time.time()-t0:.1f}s")
    except Exception as e:
        L(f"  OZON feed ERROR: {type(e).__name__}: {e}")
        L(traceback.format_exc()[:500])
else:
    L("  SKIP: no composer data")

# ═══════════════════════════════════════════════════════
# WB: fixed close
# ═══════════════════════════════════════════════════════
L("")
L("━━━ WB 138278567 (Утюг BBK) ━━━")
t0 = time.time()
try:
    from r52.collectors.wb import WBCollector
    wc = WBCollector(data_dir=None, use_proxies=False)
    wc._last_request = 0
    url = wc.build_url("138278567")
    L(f"  url={url}")
    wb_doc = wc.collect_product("138278567", url)
    wc.session.close()
    dt = time.time() - t0
    L(f"  WB collect: {dt:.1f}s")
    if wb_doc:
        feed("wb-138278567-v5", wb_doc, "WB")
    else:
        L("  WB: returned None")
except Exception as e:
    dt = time.time() - t0
    L(f"  WB ERROR {dt:.1f}s: {type(e).__name__}: {e}")
    L(traceback.format_exc()[:600])

# ═══════════════════════════════════════════════════════
# DB STATE
# ═══════════════════════════════════════════════════════
L("")
L("━━━ DB STATE ━━━")
try:
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
        pp_count = conn.execute("SELECT COUNT(*) FROM price_point").fetchone()[0]
        obs_count = conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
        L(f"  Totals: products={len(rows)} obs={obs_count} price_points={pp_count}")
except Exception as e:
    L(f"  DB ERR: {e}")

L("")
L("=== V5 DONE ===")
app.stop()

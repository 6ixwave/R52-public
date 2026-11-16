"""Fast multi-marketplace probe. Writes diag_v9_out.txt + diag_v9_results.json. Target <65s."""
import json, os, sys, time, logging, re, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
TZ = timezone(timedelta(hours=10))
NOW = lambda: datetime.now(TZ).isoformat()

# Dual logger: console + file
LOGF = BASE / "diag_v9_out.txt"
handler_f = logging.FileHandler(LOGF, mode="w", encoding="utf-8")
handler_c = logging.StreamHandler(sys.stdout)
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
handler_f.setFormatter(fmt)
handler_c.setFormatter(fmt)
log = logging.getLogger("V9")
log.setLevel(logging.INFO)
log.addHandler(handler_f)
log.addHandler(handler_c)

results = {}
t_start = time.monotonic()
log.info("=== DIAG V9 START ===")

# ─── 1. Local DB state ───
def probe_db(label, home_dir):
    dbf = home_dir / "data" / "r52.sqlite3"
    r = {"path": str(dbf), "exists": dbf.exists()}
    if not dbf.exists():
        results[label] = r
        return
    import sqlite3
    conn = sqlite3.connect(str(dbf))
    conn.row_factory = sqlite3.Row
    r["size"] = dbf.stat().st_size
    tables = [x[0] for x in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    r["tables"] = tables
    for t in ("product", "observation", "price_point", "inbox_item"):
        if t in tables:
            c = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
            r[t] = c
            if 0 < c <= 3:
                rows = conn.execute(f"SELECT * FROM [{t}] LIMIT 3").fetchall()
                r[f"{t}_sample"] = [dict(x) for x in rows]
    conn.close()
    results[label] = r
    log.info("DB %s: %s", label, {k: v for k, v in r.items() if k not in ("tables",)})

probe_db("v8_home", BASE / "e2e_v8_home")
probe_db("test_home", BASE / "e2e_test_home")

# ─── 2. Import smoke ───
try:
    os.environ["R52_HOME"] = str(BASE / "e2e_v8_home")
    from r52.config import Config, Paths
    from r52.db import Database, migrate
    from r52.intake.eye import Inbox
    from r52.sources import REGISTRY, collectors_status
    results["imports"] = {"ok": True, "registry": list(REGISTRY.keys())}
    log.info("Imports OK: %s", list(REGISTRY.keys()))
except Exception as e:
    results["imports"] = {"ok": False, "err": str(e)}
    log.error("Imports failed: %s", e)

# ─── 3. WB basket (2 hosts, 8s each) ───
try:
    from curl_cffi import requests as cffi
    nm = 1097975275
    vol, part = nm // 100000, nm // 1000
    cpath = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
    H = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}
    wb_r = {}
    for i in (1, 2):
        t0 = time.monotonic()
        try:
            r = cffi.get(f"https://basket-{i}.wbbasket.ru{cpath}", impersonate="chrome", timeout=8, headers=H)
            wb_r[f"b{i}"] = {"http": r.status_code, "len": len(r.content), "t": round(time.monotonic()-t0, 1)}
            if r.status_code == 200:
                d = r.json()
                wb_r[f"b{i}"].update({"title": d.get("imt_name", "")[:60],
                                       "brand": d.get("selling", {}).get("brand_name", ""),
                                       "opts": len(d.get("options", []))})
                break
        except Exception as e:
            wb_r[f"b{i}"] = {"err": str(e)[:80], "t": round(time.monotonic()-t0, 1)}
    results["wb_basket"] = wb_r
    log.info("WB basket: %s", wb_r)
except Exception as e:
    results["wb_basket"] = {"err": str(e)}
    log.error("WB basket: %s", e)

# ─── 4. WB price v2 ───
try:
    t0 = time.monotonic()
    r = cffi.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}", impersonate="chrome", timeout=10, headers=H)
    pr = {"http": r.status_code, "len": len(r.content), "t": round(time.monotonic()-t0, 1)}
    if r.status_code == 200:
        items = r.json().get("data", {}).get("items", [])
        if items:
            p = items[0].get("price", {})
            pr.update({"basic": p.get("basic"), "product": p.get("product"),
                       "sale": p.get("sale"), "total": p.get("total")})
        else:
            pr["detail"] = "no_items"
    results["wb_price"] = pr
    log.info("WB price: %s", pr)
except Exception as e:
    results["wb_price"] = {"err": str(e)}
    log.error("WB price: %s", e)

# ─── 5. Farpost card ───
try:
    sess = cffi.Session(impersonate="chrome")
    t0 = time.monotonic()
    w = sess.get("https://www.farpost.ru/", timeout=10)
    log.info("FP warm: HTTP=%d t=%.1fs", w.status_code, time.monotonic()-t0)
    time.sleep(1)
    t1 = time.monotonic()
    r = sess.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=15)
    html = r.text
    fp = {"http": r.status_code, "len": len(html), "url": str(r.url)[:80],
          "verify": "/verify" in str(r.url), "t": round(time.monotonic()-t1, 1)}
    if r.status_code == 200 and len(html) > 3000 and not fp["verify"]:
        lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        fp["n_ld"] = len(lds)
        for ld in lds[:3]:
            try:
                o = json.loads(ld.strip())
                fp[f"ld_{o.get('@type','?')}"] = True
                if o.get("@type") == "Product":
                    fp["ld_title"] = o.get("name", "")[:60]
                    off = o.get("offers", {})
                    fp["ld_price"] = off.get("price")
                    fp["ld_currency"] = off.get("priceCurrency")
            except Exception:
                pass
        pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        fp["attr_price"] = int(pm.group(1)) if pm else None
        og = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        fp["og_price"] = og.group(1) if og else None
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        fp["title_tag"] = tm.group(1).strip()[:60] if tm else None
        photos = re.findall(r'"(https?://static\.baza\.farpost\.ru/[^"]+)"', html)
        fp["n_photos"] = len([p for p in photos if not any(s in p for s in ('.svg','.gif','.ico','_thumbnail'))])
    results["farpost"] = fp
    log.info("FP card: %s", fp)
except Exception as e:
    results["farpost"] = {"err": str(e)}
    log.error("FP: %s\n%s", e, traceback.format_exc()[:300])

# ─── 6. Ozon quick (composer + SSR) ───
try:
    lid = "3481320800"
    oz = {}
    # Composer
    t0 = time.monotonic()
    try:
        cu = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/"
        r = cffi.get(cu, impersonate="chrome", timeout=12,
                     headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/"})
        oz["composer"] = {"http": r.status_code, "len": len(r.content), "t": round(time.monotonic()-t0, 1)}
        if r.status_code == 200:
            d = r.json()
            oz["composer"]["keys"] = list(d.keys())[:10] if isinstance(d, dict) else []
    except Exception as e:
        oz["composer"] = {"err": str(e)[:80]}
    # SSR
    t0 = time.monotonic()
    try:
        r = cffi.get(f"https://www.ozon.ru/product/{lid}/", impersonate="chrome", timeout=15,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU",
                              "Referer": "https://www.ozon.ru/"})
        html = r.text
        oz["ssr"] = {"http": r.status_code, "len": len(html), "t": round(time.monotonic()-t0, 1)}
        if r.status_code == 200 and len(html) > 5000:
            lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
            oz["ssr"]["n_ld"] = len(lds)
            og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
            oz["ssr"]["og_price"] = og_p.group(1) if og_p else None
            tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            oz["ssr"]["title"] = tm.group(1).strip()[:60] if tm else None
    except Exception as e:
        oz["ssr"] = {"err": str(e)[:80]}
    results["ozon"] = oz
    log.info("OZON: %s", oz)
except Exception as e:
    results["ozon"] = {"err": str(e)}
    log.error("OZON: %s", e)

# ─── 7. Avito quick (search page) ───
try:
    t0 = time.monotonic()
    r = cffi.get("https://www.avito.ru/vladivostok/smartfony", impersonate="chrome", timeout=15,
                 headers={"Accept": "text/html", "Accept-Language": "ru-RU",
                          "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                          "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                          "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                          "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
                          "Upgrade-Insecure-Requests": "1"})
    html = r.text
    av = {"http": r.status_code, "len": len(html), "t": round(time.monotonic()-t0, 1)}
    if r.status_code == 200:
        av["captcha"] = "captcha" in html.lower() or "antibot" in html.lower()
        prices = re.findall(r'data-price="(\d+)"', html)
        av["n_prices"] = len(prices)
        av["first_prices"] = prices[:5]
        titles = re.findall(r'data-qa="index-item-title"[^>]*>([^<]+)<', html)
        av["n_titles"] = len(titles)
        av["first_titles"] = [t.strip()[:40] for t in titles[:3]]
    results["avito"] = av
    log.info("AVITO: %s", av)
except Exception as e:
    results["avito"] = {"err": str(e)}
    log.error("AVITO: %s", e)

# ─── Finalize ───
results["total_s"] = round(time.monotonic() - t_start, 1)
results["time"] = NOW()
out = BASE / "diag_v9_results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
log.info("=== DIAG V9 DONE t=%.1fs ===", results["total_s"])
log.info("Results → %s", out)

"""
diag_v7f.py — LOCALE FIX + WB import + 6-source scan + pagination overlap.
Запуск: python diag_v7f.py

Цели:
  1. AE_COM: принудительная EN/USD (cookies + _lang + _currency в URL)
  2. AE_RU:  проверка RUB (gatewayAdapt + cookies) — EUR ли всё ещё?
  3. WB:     правильный импорт (wb.WBCollector)
  4. Пагинация AE_RU: 2 страницы, пересечение ID
"""
import sys, os, time, logging

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7f")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v7f_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v7f — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ═══════════════════════════════════════════════════════════
# PART 1: AE_COM with forced EN/USD locale
# ═══════════════════════════════════════════════════════════
emit("── [1] AE_COM forced EN/USD ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    c = AliExpressCollector(use_proxies=False)
    c.source = "ALIEXPRESS"

    # Override _fetch_wholesale_html to add locale params
    from urllib.parse import quote
    def _fetch_com_en(query, page=1):
        url = (f"https://www.aliexpress.com/wholesale?SearchText={quote(query)}"
               f"&page={page}&_lang=en_US&_currency=USD")
        try:
            from curl_cffi import requests as cffi_req
            c._throttle()
            t0 = time.monotonic()
            hdrs = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.aliexpress.com/",
                "Upgrade-Insecure-Requests": "1",
            }
            resp = cffi_req.get(url, impersonate="chrome",
                                proxies=c.pool.get_proxy(),
                                timeout=(15, 30), headers=hdrs,
                                cookies={
                                    "aep_usuc_f": "f_locale=en-US&f_region=US&f_currency=USD&ais=en-US&fre=0&c_b_locale=en_US",
                                    "aep_usuc": "f_region=US&f_locale=en-US&f_currency=USD",
                                })
            lat = time.monotonic() - t0
            c.stats["requests"] += 1
            if resp.status_code == 200 and len(resp.text) > 100000:
                c.stats["success"] += 1
                c.pool.mark_success(lat)
                emit(f"  HTTP 200, {len(resp.text)} bytes, {lat:.1f}s")
                return resp.text
            else:
                emit(f"  HTTP {resp.status_code}, {len(resp.text)} bytes")
        except Exception as e:
            emit(f"  ERR: {e}")
        return None

    html = _fetch_com_en(QUERY, 1)
    if html:
        products = c._parse_wholesale_html(html)
        emit(f"  Parsed: {len(products)} products")

        # Check locale indicators
        titles = [p.get("title", "") for p in products if p.get("title")]
        currencies = set(p.get("currency", "?") for p in products)
        it_markers = sum(1 for t in titles if any(w in t.lower() for w in ("cuffie", "auricolari", "disponib", "magazzino", "esaurito")))
        en_markers = sum(1 for t in titles if any(w in t.lower() for w in ("headphone", "earphone", "wireless", "bluetooth", "audio")))
        ru_markers = sum(1 for t in titles if any(w in t.lower() for w in ("наушник", "гарнитур", "беспроводн")))
        emit(f"  Currencies seen: {currencies}")
        emit(f"  IT markers: {it_markers}/{len(titles)}, EN: {en_markers}, RU: {ru_markers}")

        no_price = sum(1 for p in products if not p.get("price"))
        bad_price = sum(1 for p in products if p.get("price") and float(p["price"]) <= 1.0)
        emit(f"  No-price: {no_price}/{len(products)}, Bad-price: {bad_price}")

        for i, p in enumerate(products[:5]):
            emit(f"  [{i}] id={p.get('item_id','?')}")
            emit(f"      title={str(p.get('title',''))[:70]!r}")
            emit(f"      price={p.get('price','?')} {p.get('currency','?')} orig={p.get('original_price','?')}")
            emit(f"      images={len(p.get('images',[]))}")
    else:
        emit("  FAILED to fetch HTML")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
emit()

# ═══════════════════════════════════════════════════════════
# PART 2: AE_RU — verify RUB (gatewayAdapt + cookies)
# ═══════════════════════════════════════════════════════════
emit("── [2] AE_RU RUB check ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    c = AliExpressRUCollector(use_proxies=False)
    c.source = "ALIEXPRESS_RU"

    html = c._fetch_wholesale_html(QUERY, 1)
    if html:
        products = c._parse_wholesale_html(html)
        emit(f"  Parsed: {len(products)} products")
        currencies = set(p.get("currency", "?") for p in products)
        emit(f"  Currencies: {currencies}")

        # Check if pdp_npi has RUB
        import re as _re
        from html import unescape as _unescape
        from urllib.parse import unquote as _unquote
        rub_count = 0
        eur_count = 0
        for p in products:
            cur = p.get("currency", "")
            if cur == "RUB":
                rub_count += 1
            elif cur == "EUR":
                eur_count += 1
        emit(f"  RUB: {rub_count}, EUR: {eur_count}, Other: {len(products)-rub_count-eur_count}")

        # Raw pdp_npi sample
        card_list_idx = html.find('id="card-list"')
        if card_list_idx >= 0:
            card_area = html[card_list_idx:card_list_idx+200000]
            pdp_matches = _re.findall(r'pdp_npi=([^&]+)', card_area)
            if pdp_matches:
                emit(f"  pdp_npi samples (first 3):")
                for pm in pdp_matches[:3]:
                    decoded = _unquote(_unescape(pm))
                    emit(f"    {decoded[:80]}")
                # Parse currency from first
                parts = decoded.split("!")
                if len(parts) >= 4:
                    emit(f"  → currency field: {parts[1]}, orig={parts[2]}, sale={parts[3]}")

        for i, p in enumerate(products[:5]):
            emit(f"  [{i}] id={p.get('item_id','?')}")
            emit(f"      title={str(p.get('title',''))[:70]!r}")
            emit(f"      price={p.get('price','?')} {p.get('currency','?')}")
    else:
        emit("  FAILED to fetch HTML")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
emit()

# ═══════════════════════════════════════════════════════════
# PART 3: AE_RU pagination overlap (2 pages)
# ═══════════════════════════════════════════════════════════
emit("── [3] AE_RU pagination (p1+p2) ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    c = AliExpressRUCollector(use_proxies=False)
    c.source = "ALIEXPRESS_RU"

    ids_p1, ids_p2 = set(), set()
    for pg in (1, 2):
        t0 = time.monotonic()
        items = c.collect_by_search(QUERY, page=pg, page_size=12)
        el = time.monotonic() - t0
        page_ids = set()
        for it in items:
            obs = it.get("observation", it)
            iid = obs.get("id", "")
            if iid:
                page_ids.add(iid)
        if pg == 1:
            ids_p1 = page_ids
        else:
            ids_p2 = page_ids
        emit(f"  p{pg}: {len(items)} items, {len(page_ids)} unique IDs, {el:.1f}s")

    overlap = ids_p1 & ids_p2
    emit(f"  Overlap: {len(overlap)} IDs")
    if overlap:
        emit(f"  Overlap IDs: {list(overlap)[:5]}")

    # Quality
    all_items = []
    for pg in (1, 2):
        t0 = time.monotonic()
        items = c.collect_by_search(QUERY, page=pg, page_size=12)
        all_items.extend(items)
    total = len(all_items)
    no_id = sum(1 for it in all_items if not it.get("observation", it).get("id"))
    no_title = sum(1 for it in all_items if len(it.get("observation", it).get("fields", {}).get("title", {}).get("value", "")) < 10)
    no_price = sum(1 for it in all_items if not it.get("observation", it).get("price_candidates"))
    emit(f"  Total: {total}, no_id={no_id}, no_title={no_title}, no_price={no_price}")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
emit()

# ═══════════════════════════════════════════════════════════
# PART 4: 6-source quick scan (fixed WB import)
# ═══════════════════════════════════════════════════════════
emit("── [4] 6-SOURCE QUICK SCAN ──")
sources = [
    ("ALIEXPRESS", "ALIEXPRESS"),
    ("ALIEXPRESS_RU", "ALIEXPRESS_RU"),
    ("WB", "WB"),
    ("OZON", "OZON"),
    ("AVITO", "AVITO"),
    ("FARPOST", "FARPOST"),
]
for idx, (src_key, display) in enumerate(sources, 1):
    emit(f"  [{idx}/6] {display}")
    try:
        if src_key == "ALIEXPRESS":
            from r52.collectors.aliexpress import AliExpressCollector
            col = AliExpressCollector(use_proxies=False)
        elif src_key == "ALIEXPRESS_RU":
            from r52.collectors.aliexpress import AliExpressRUCollector
            col = AliExpressRUCollector(use_proxies=False)
        elif src_key == "WB":
            from r52.collectors.wb import WBCollector
            col = WBCollector(use_proxies=False)
        elif src_key == "OZON":
            from r52.collectors.ozon import OzonCollector
            col = OzonCollector(use_proxies=False)
        elif src_key == "AVITO":
            from r52.collectors.avito import AvitoCollector
            col = AvitoCollector(use_proxies=False)
        elif src_key == "FARPOST":
            from r52.collectors.farpost import FarpostCollector
            col = FarpostCollector(use_proxies=False)
        else:
            emit(f"    SKIP (unknown)")
            continue

        col.source = src_key
        t0 = time.monotonic()
        items = col.collect_by_search(QUERY, page=1, page_size=8)
        el = round(time.monotonic() - t0, 1)

        no_price = sum(1 for it in items if not it.get("observation", it).get("price_candidates"))
        sample = ""
        if items:
            obs0 = items[0].get("observation", items[0])
            pc0 = obs0.get("price_candidates", [])
            if pc0:
                sample = f" first_price={pc0[0].get('currency','?')} {pc0[0].get('amount','?')}"
            else:
                sample = " first_price=NONE"
            # Title sample
            t0_val = obs0.get("fields", {}).get("title", {}).get("value", "")
            sample += f" title={t0_val[:50]!r}"

        emit(f"    {len(items)} items {el}s | no_price={no_price}{sample}")
        col.close()
    except Exception as e:
        emit(f"    ERR {type(e).__name__}: {str(e)[:100]}")
    emit()

emit("=== DONE ===")
flush()
log.info("diag_v7f complete")

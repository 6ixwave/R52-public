"""
diag_v7g.py — Focused pipeline diagnostic: why Part 4 gives 0 items.
Tests each stage of collect_by_search independently with timing.

Usage: python -u diag_v7g.py
"""
import sys, os, time, logging, traceback

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7g")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v7g_output.txt")
lines = []

def emit(s=""):
    print(s, flush=True)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v7g — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ═══════════════════════════════════════════════════════════
# SECTION A: COM _fetch_wholesale_html direct
# ═══════════════════════════════════════════════════════════
emit("── [A] COM _fetch_wholesale_html ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    c = AliExpressCollector(use_proxies=False)
    c.source = "ALIEXPRESS"

    t0 = time.monotonic()
    html = c._fetch_wholesale_html(QUERY, 1)
    el = time.monotonic() - t0

    if html:
        emit(f"  OK: {len(html)} bytes, {el:.1f}s")
        # Locale indicators
        import re as _re
        it_words = sum(1 for w in ("cuffie","auricolari","disponib","magazzino","esaurito") if w in html[:50000].lower())
        en_words = sum(1 for w in ("headphone","earphone","wireless","bluetooth") if w in html[:50000].lower())
        ru_words = sum(1 for w in ("наушник","гарнитур","беспроводн") if w in html[:50000].lower())
        emit(f"  Locale: IT={it_words}, EN={en_words}, RU={ru_words}")
        # Currency in pdp_npi
        pdp_matches = _re.findall(r'pdp_npi=([^&]{0,80})', html[:200000])
        if pdp_matches:
            curs = set()
            for pm in pdp_matches[:20]:
                from urllib.parse import unquote
                from html import unescape
                decoded = unquote(unescape(pm))
                parts = decoded.split("!")
                if len(parts) >= 2 and len(parts[1]) == 3:
                    curs.add(parts[1])
            emit(f"  pdp_npi currencies (first 20): {curs}")
        # Parse
        t0 = time.monotonic()
        products = c._parse_wholesale_html(html)
        el2 = time.monotonic() - t0
        emit(f"  Parsed: {len(products)} products in {el2:.2f}s")
        if products:
            p0 = products[0]
            emit(f"  [0] id={p0.get('item_id')} title={str(p0.get('title',''))[:60]!r}")
            emit(f"      price={p0.get('price')} {p0.get('currency')}")
        # Now test full collect_by_search
        t0 = time.monotonic()
        items = c.collect_by_search(QUERY, page=1, page_size=8)
        el3 = time.monotonic() - t0
        emit(f"  collect_by_search: {len(items)} items, {el3:.1f}s")
        if items:
            obs0 = items[0].get("observation", items[0])
            pc0 = obs0.get("price_candidates", [])
            emit(f"  first: id={obs0.get('id')} pc={len(pc0)} title={str(obs0.get('fields',{}).get('title',{}).get('value',''))[:50]!r}")
    else:
        emit(f"  FAILED: {el:.1f}s (no HTML)")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()[-500:]}")
emit()

# ═══════════════════════════════════════════════════════════
# SECTION B: RU _fetch_wholesale_html direct
# ═══════════════════════════════════════════════════════════
emit("── [B] RU _fetch_wholesale_html ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    c = AliExpressRUCollector(use_proxies=False)
    c.source = "ALIEXPRESS_RU"

    t0 = time.monotonic()
    html = c._fetch_wholesale_html(QUERY, 1)
    el = time.monotonic() - t0

    if html:
        emit(f"  OK: {len(html)} bytes, {el:.1f}s")
        import re as _re
        it_words = sum(1 for w in ("cuffie","auricolari","disponib","magazzino","esaurito") if w in html[:50000].lower())
        en_words = sum(1 for w in ("headphone","earphone","wireless","bluetooth") if w in html[:50000].lower())
        ru_words = sum(1 for w in ("наушник","гарнитур","беспроводн") if w in html[:50000].lower())
        emit(f"  Locale: IT={it_words}, EN={en_words}, RU={ru_words}")
        pdp_matches = _re.findall(r'pdp_npi=([^&]{0,80})', html[:200000])
        if pdp_matches:
            curs = set()
            for pm in pdp_matches[:20]:
                from urllib.parse import unquote
                from html import unescape
                decoded = unquote(unescape(pm))
                parts = decoded.split("!")
                if len(parts) >= 2 and len(parts[1]) == 3:
                    curs.add(parts[1])
            emit(f"  pdp_npi currencies (first 20): {curs}")
        t0 = time.monotonic()
        products = c._parse_wholesale_html(html)
        el2 = time.monotonic() - t0
        emit(f"  Parsed: {len(products)} products in {el2:.2f}s")
        if products:
            p0 = products[0]
            emit(f"  [0] id={p0.get('item_id')} title={str(p0.get('title',''))[:60]!r}")
            emit(f"      price={p0.get('price')} {p0.get('currency')}")
        # Full collect_by_search
        t0 = time.monotonic()
        items = c.collect_by_search(QUERY, page=1, page_size=8)
        el3 = time.monotonic() - t0
        emit(f"  collect_by_search: {len(items)} items, {el3:.1f}s")
        if items:
            obs0 = items[0].get("observation", items[0])
            pc0 = obs0.get("price_candidates", [])
            emit(f"  first: id={obs0.get('id')} pc={len(pc0)} title={str(obs0.get('fields',{}).get('title',{}).get('value',''))[:50]!r}")
        else:
            # Check if search API was tried
            emit("  (0 items — check if Phase 2 search API was triggered)")
    else:
        emit(f"  FAILED: {el:.1f}s (no HTML)")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()[-500:]}")
emit()

# ═══════════════════════════════════════════════════════════
# SECTION C: COM with Playwright check (is PW the 47s bottleneck?)
# ═══════════════════════════════════════════════════════════
emit("── [C] COM Playwright state ──")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    c = AliExpressCollector(use_proxies=False)
    c.source = "ALIEXPRESS"
    emit(f"  _pw_failed={c._pw_failed}")
    emit(f"  _page={c._page}")
    emit(f"  _session_warm={c._session_warm}")
    # Test _ensure_browser timing
    t0 = time.monotonic()
    pw_ok = c._ensure_browser()
    el = time.monotonic() - t0
    emit(f"  _ensure_browser: {pw_ok} in {el:.1f}s")
    if pw_ok:
        emit(f"  _page={c._page}")
        emit(f"  _ctx={c._ctx}")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
emit()

# ═══════════════════════════════════════════════════════════
# SECTION D: RU _fetch_search_api (Phase 2 fallback)
# ═══════════════════════════════════════════════════════════
emit("── [D] RU _fetch_search_api ──")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    c = AliExpressRUCollector(use_proxies=False)
    c.source = "ALIEXPRESS_RU"
    t0 = time.monotonic()
    data = c._fetch_search_api(QUERY, 1, 8)
    el = time.monotonic() - t0
    if data:
        emit(f"  OK: {el:.1f}s, keys={list(data.keys())[:5]}")
        # Try parse
        api_products = c._parse_search_api(data)
        emit(f"  Parsed API: {len(api_products)} products")
        if api_products:
            p0 = api_products[0]
            emit(f"  [0] id={p0.get('item_id')} title={str(p0.get('title',''))[:50]!r} price={p0.get('price')}")
    else:
        emit(f"  None returned in {el:.1f}s")
    c.close()
except Exception as e:
    emit(f"  EXCEPTION: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()[-400:]}")
emit()

emit("=== DONE ===")
flush()
log.info("diag_v7g complete")

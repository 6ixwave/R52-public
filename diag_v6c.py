"""
diag_v6c.py — Точечная диагностика: AVITO traceback, OZON HTML, FARPOST pagination.
"""
import sys, os, time, logging, traceback

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6c")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6c_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v6c — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")

# ═══════════════════════════════════════════════════
# 1. AVITO: mobile fallback с полным traceback
# ═══════════════════════════════════════════════════
emit("── AVITO: mobile fallback + traceback ──")
from r52.collectors.avito import AvitoCollector
from r52.collectors.discovery import SearchError

av = AvitoCollector(use_proxies=False)
av.source = "AVITO"

try:
    items = av.collect_by_search(QUERY, page=1, page_size=10)
    emit(f"  Result: {len(items)} items")
except SearchError as se:
    emit(f"  SearchError: {se}")
except Exception as e:
    emit(f"  Exception: {type(e).__name__}: {e}")
    emit("  Traceback:")
    tb = traceback.format_exc()
    for tl in tb.splitlines():
        emit(f"    {tl}")

# Вручную повторить mobile fallback с traceback
emit()
emit("  --- Manual mobile fallback (verbose) ---")
from urllib.parse import quote
import random

try:
    av._warmup()
    time.sleep(2)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.avito.ru/",
    }
    url = f"https://www.avito.ru/rossiya?q={quote(QUERY)}&p=1"
    emit(f"  URL: {url}")
    t0 = time.monotonic()
    if av._cffi:
        resp = av._cffi.get(url, impersonate="chrome", timeout=(15, 30), headers=headers)
    else:
        resp = av.session.get(url, headers=headers, timeout=(10, 30))
    emit(f"  Desktop: HTTP {resp.status_code}, {len(resp.text)} bytes, {time.monotonic()-t0:.1f}s")
    
    if resp.status_code in (403, 429, 439):
        emit(f"  → Trying mobile fallback...")
        m_url = f"https://m.avito.ru/rossiya?q={quote(QUERY)}&p=1"
        m_headers = dict(headers)
        m_headers["User-Agent"] = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
        t0 = time.monotonic()
        try:
            if av._cffi:
                m_resp = av._cffi.get(m_url, impersonate="chrome", timeout=(10, 20), headers=m_headers)
            else:
                m_resp = av.session.get(m_url, headers=m_headers, timeout=(8, 20))
            emit(f"  Mobile: HTTP {m_resp.status_code}, {len(m_resp.text)} bytes, {time.monotonic()-t0:.1f}s")
            if m_resp.status_code == 200:
                emit(f"  Mobile HTML head: {m_resp.text[:200]!r}")
                emit(f"  _is_captcha_page: {av._is_captcha_page(m_resp.text)}")
        except Exception as me:
            emit(f"  Mobile request error: {type(me).__name__}: {me}")
            emit(f"  Traceback: {traceback.format_exc()}")
    else:
        emit(f"  Desktop not blocked (HTTP {resp.status_code}), skipping mobile")
except Exception as e:
    emit(f"  Manual fallback error: {type(e).__name__}: {e}")
    emit(f"  Traceback:")
    for tl in traceback.format_exc().splitlines():
        emit(f"    {tl}")

# ═══════════════════════════════════════════════════
# 2. OZON: HTML-дамп 315KB — что там?
# ═══════════════════════════════════════════════════
emit()
emit("── OZON: HTML analysis ──")
from r52.collectors.ozon import OzonCollector
import re as _re

oz = OzonCollector(use_proxies=False)
oz.source = "OZON"

try:
    oz._warmup()
    time.sleep(2)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.ozon.ru/",
    }
    o_url = f"https://www.ozon.ru/search/?text={quote(QUERY)}&from_global=true&page=1"
    t0 = time.monotonic()
    if oz._cffi:
        o_resp = oz._cffi.get(o_url, impersonate="chrome", timeout=(15, 30), headers=headers, verify=False)
    else:
        o_resp = oz.session.get(o_url, headers=headers, timeout=(10, 30), verify=False)
    el = time.monotonic() - t0
    emit(f"  HTTP {o_resp.status_code}, {len(o_resp.text)} bytes, {el:.1f}s")
    
    html = o_resp.text
    # Что в HTML?
    emit(f"  <title>: {_re.search(r'<title>(.*?)</title>', html, _re.S).group(1)[:100] if _re.search(r'<title>(.*?)</title>', html, _re.S) else 'N/A'}")
    
    # JSON state
    for pat_name, pat in [
        ("__INITIAL_STATE__", r'__INITIAL_STATE__\s*=\s*'),
        ("__NEXT_DATA__", r'__NEXT_DATA__\s*=\s*'),
        ("window.__state", r'window\.__state\s*=\s*'),
        ("data-widget", r'data-widget='),
        ("tileId", r'tileId'),
        ("productId", r'productId'),
        ("/product/", r'/product/'),
        ("captcha", r'captcha'),
        ("antibot", r'antibot'),
        ("challenge", r'challenge'),
    ]:
        hits = len(_re.findall(pat, html, _re.I))
        emit(f"  {pat_name}: {hits} hits")
    
    # product links
    prod_links = _re.findall(r'/product/(\d{6,})/', html)
    emit(f"  /product/ IDs: {len(prod_links)} (first 5: {prod_links[:5]})")
    
    # data-tid
    tids = _re.findall(r'data-tid=["\'](\d+)["\']', html)
    emit(f"  data-tid: {len(tids)} (first 5: {tids[:5]})")
    
    # First 3000 chars
    emit(f"  --- HTML head (first 2000 chars) ---")
    for line in html[:2000].splitlines()[:30]:
        emit(f"    {line[:150]}")
    
except Exception as e:
    emit(f"  OZON error: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()}")

# ═══════════════════════════════════════════════════
# 3. FARPOST: raw p1 vs p2 — отличаются ли?
# ═══════════════════════════════════════════════════
emit()
emit("── FARPOST: p1 vs p2 raw comparison ──")
from r52.collectors.farpost import FarpostCollector

fp = FarpostCollector(use_proxies=False)
fp.source = "FARPOST"

try:
    fp._warmup()
    time.sleep(2)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.farpost.ru/",
    }
    
    raw_pages = {}
    for pg in (1, 2):
        f_url = f"https://www.farpost.ru/dir?query={quote(QUERY)}&page={pg}"
        t0 = time.monotonic()
        if fp._cffi:
            f_resp = fp._cffi.get(f_url, impersonate="chrome", timeout=(15, 30), headers=headers)
        else:
            f_resp = fp.session.get(f_url, headers=headers, timeout=(10, 30))
        el = time.monotonic() - t0
        ct = (f_resp.headers.get("Content-Type") or "").lower()
        if "1251" in ct or "cp1251" in ct:
            f_html = f_resp.content.decode("windows-1251", errors="replace")
        else:
            f_html = f_resp.text
        raw_pages[pg] = f_html
        emit(f"  p{pg}: HTTP {f_resp.status_code}, {len(f_html)} bytes, {el:.1f}s, url={f_resp.url}")
        
        # Page indicators
        page_ind = _re.findall(r'(?:page|страница|стр\.)\s*[:=]?\s*(\d+)', f_html[:5000], _re.I)
        emit(f"    page indicators in head: {page_ind[:5]}")
        # Total results
        total_m = _re.search(r'(\d+)\s*(?:объявлени|результат)', f_html, _re.I)
        emit(f"    total results: {total_m.group(0)[:50] if total_m else 'N/A'}")
        
        if pg == 1:
            time.sleep(18)
    
    # Compare
    if len(raw_pages) == 2:
        h1, h2 = raw_pages[1], raw_pages[2]
        emit(f"  p1 len={len(h1)}, p2 len={len(h2)}, identical={h1 == h2}")
        if h1 != h2:
            # Find first difference
            for i in range(min(len(h1), len(h2))):
                if h1[i] != h2[i]:
                    emit(f"  First diff at char {i}:")
                    emit(f"    p1: ...{h1[max(0,i-50):i+50]}...")
                    emit(f"    p2: ...{h2[max(0,i-50):i+50]}...")
                    break
            # Compare bulletin IDs
            id_pat = _re.compile(r'/([a-z0-9\-]*?(\d{5,}))[a-z0-9\-]*\.html')
            ids1 = [m.group(2) for m in id_pat.finditer(h1)]
            ids2 = [m.group(2) for m in id_pat.finditer(h2)]
            emit(f"  p1 IDs: {len(ids1)} (first 5: {ids1[:5]})")
            emit(f"  p2 IDs: {len(ids2)} (first 5: {ids2[:5]})")
            ov = set(ids1) & set(ids2)
            emit(f"  Overlap: {len(ov)}/{min(len(ids1),len(ids2))}")
        else:
            emit("  WARNING: p1 and p2 HTML are IDENTICAL — page param ignored")
except Exception as e:
    emit(f"  FARPOST error: {type(e).__name__}: {e}")
    emit(f"  {traceback.format_exc()}")

emit()
emit("=== DONE ===")
flush()
print(f"\nOutput: {OUT}")

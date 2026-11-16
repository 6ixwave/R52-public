"""
diag_avito_hydration.py — Deep parse of __staticRouterHydrationData and script[35] JSON.
Then try fetching the final URL /all?q=тест directly.
"""
import re, json, sys, os, time, logging
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("R52_HOME", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test_home"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.avito_hyd")

DEBUG_DIR = Path(__file__).parent
HOME = Path(os.environ["R52_HOME"])
html = (DEBUG_DIR / "avito_real_search.html").read_text(encoding="utf-8")

# ============================================================
# PHASE 1: Parse __staticRouterHydrationData (script [9])
# ============================================================
log.info("=== PHASE 1: __staticRouterHydrationData ===")
m = re.search(
    r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("((?:[^"\\]|\\.)*)"\)',
    html, re.DOTALL
)
if m:
    raw = m.group(1)
    log.info("Raw length: %d", len(raw))
    # Unescape
    try:
        inner = json.loads(f'"{raw}"')
        data = json.loads(inner)
        log.info("Parsed OK. Top keys: %s", list(data.keys()))
        
        # Save full
        (DEBUG_DIR / "avito_hydration_full.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        
        # Explore loaderData
        ld = data.get("loaderData", {})
        log.info("loaderData keys: %s", list(ld.keys()))
        for route_key, route_data in ld.items():
            if route_data is None:
                continue
            log.info("  Route '%s': keys=%s", route_key, list(route_data.keys())[:15] if isinstance(route_data, dict) else type(route_data).__name__)
            if isinstance(route_data, dict):
                for k, v in route_data.items():
                    sv = json.dumps(v, ensure_ascii=False) if not isinstance(v, (int, float, str, bool)) else str(v)
                    log.info("    %s: %s (%d chars)", k, sv[:100], len(sv))
    except (json.JSONDecodeError, TypeError) as e:
        log.error("Parse failed: %s", e)
        # Try manual unescape
        inner = raw.replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')
        try:
            data = json.loads(inner)
            log.info("Manual parse OK. Keys: %s", list(data.keys())[:10])
        except:
            log.error("Manual parse also failed")
            data = None
else:
    log.warning("__staticRouterHydrationData not found with JSON.parse pattern")
    # Try without JSON.parse
    m2 = re.search(r'window\.__staticRouterHydrationData\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m2:
        log.info("Found as raw JSON: %d chars", len(m2.group(1)))
        data = json.loads(m2.group(1))
    else:
        data = None

# ============================================================
# PHASE 2: Parse script[35] (the 12508-char JSON blob)
# ============================================================
log.info("\n=== PHASE 2: Script[35] JSON ===")
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
# Find the one that starts with {"i18n"
for i, s in enumerate(scripts):
    stripped = s.strip()
    if stripped.startswith('{"i18n"') or stripped.startswith('{"loaderData"'):
        log.info("Script[%d]: %d chars", i, len(stripped))
        try:
            blob = json.loads(stripped)
            log.info("Keys: %s", list(blob.keys()))
            (DEBUG_DIR / "avito_script35.json").write_text(
                json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Explore
            ld = blob.get("loaderData", {})
            for rk, rv in ld.items():
                if rv:
                    log.info("  loaderData.%s: %s", rk, json.dumps(rv, ensure_ascii=False)[:200])
        except json.JSONDecodeError as e:
            log.warning("Script[%d] parse: %s", i, e)
        break

# ============================================================
# PHASE 3: Try fetching the FINAL URL /all?q=тест
# ============================================================
log.info("\n=== PHASE 3: Fetch /all?q=тест directly ===")
try:
    from curl_cffi import requests as cffi
    HAS_CFFI = True
except ImportError:
    import requests as cffi
    HAS_CFFI = False

final_url = "https://www.avito.ru/all?q=" + quote("тест") + "&p=1"
log.info("URL: %s", final_url)

headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

try:
    if HAS_CFFI:
        r = cffi.get(final_url, headers=headers, impersonate="chrome", timeout=15)
    else:
        r = cffi.get(final_url, headers=headers, timeout=15)
    log.info("HTTP %s, %d bytes", r.status_code, len(r.content))
    
    if r.status_code == 200:
        body = r.text
        # Save
        out = DEBUG_DIR / "avito_final_page.html"
        out.write_text(body, encoding="utf-8")
        log.info("Saved: %s (%d bytes)", out, len(body))
        
        # Quick scan for prices and cards
        prices = re.findall(r'(\d[\d\s\u00a0]{2,12})\s*(?:₽|руб\b)', body)
        log.info("Price text: %d", len(prices))
        for p in prices[:10]:
            log.info("  %s", p)
        
        # Check for ad cards
        ad_cards = re.findall(r'data-(?:listing|ad|item)-id=["\'](\d+)["\']', body)
        log.info("Ad card IDs: %d", len(ad_cards))
        for a in ad_cards[:10]:
            log.info("  id=%s", a)
        
        # Check for __preloadedState__ with actual data
        ps = re.search(r'window\.__preloadedState__\s*=\s*"((?:[^"\\]|\\.)*)"', body, re.DOTALL)
        if ps:
            log.info("__preloadedState__: %d chars", len(ps.group(1)))
        
        # Check for hydration data
        hd = re.search(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("((?:[^"\\]|\\.)*)"\)', body, re.DOTALL)
        if hd:
            log.info("__staticRouterHydrationData: %d chars", len(hd.group(1)))
            # Parse and look for results
            try:
                inner = json.loads(f'"{hd.group(1)}"')
                hdata = json.loads(inner)
                (DEBUG_DIR / "avito_final_hydration.json").write_text(
                    json.dumps(hdata, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                # Find results
                def find_results(obj, path="", depth=0):
                    if depth > 6: return []
                    hits = []
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            np = f"{path}.{k}" if path else k
                            if k.lower() in ('results', 'items', 'ads', 'listings', 'cards', 'offers'):
                                hits.append((np, type(v).__name__, str(len(v)) if isinstance(v, (list, dict)) else v))
                            if isinstance(v, (dict, list)):
                                hits.extend(find_results(v, np, depth+1))
                    elif isinstance(obj, list):
                        for i, item in enumerate(obj[:3]):
                            hits.extend(find_results(item, f"{path}[{i}]", depth+1))
                    return hits
                hits = find_results(hdata)
                log.info("Result paths: %d", len(hits))
                for h in hits[:15]:
                    log.info("  %s: %s (%s)", h[0], h[1], h[2])
            except Exception as e:
                log.warning("Hydration parse: %s", e)
        
        # Check for redirect again
        if "301" in body[:5000] or "redirected" in body[:5000]:
            log.warning("Possible redirect in final page!")
    elif r.status_code in (301, 302):
        loc = r.headers.get("Location", "?")
        log.info("Redirect to: %s", loc)
    else:
        log.warning("Unexpected status %s", r.status_code)
        body = r.text
        log.info("Body[:300]: %s", body[:300])
except Exception as e:
    log.error("Fetch failed: %s %s", type(e).__name__, e)

# ============================================================
# PHASE 4: Check proxy pool
# ============================================================
log.info("\n=== PHASE 4: Proxy pool ===")
proxy_file = HOME / "collectors" / "proxies.json"
if proxy_file.exists():
    proxies = json.loads(proxy_file.read_text(encoding="utf-8"))
    log.info("Proxies: %s", json.dumps(proxies, ensure_ascii=False)[:300])
else:
    log.info("No proxies.json at %s", proxy_file)
    # Check for env or config
    cfg = HOME / "config.toml"
    if cfg.exists():
        cfg_text = cfg.read_text(encoding="utf-8")
        log.info("config.toml: %s", cfg_text[:500])

log.info("\n=== DONE ===")

#!/usr/bin/env python3
"""diag_v7o v5 — dump ALL punish scripts + double warm-up + longer gaps.
Goal: find where the new x5secdata token is generated in the 84KB page.
"""
import json, re, time, logging, sys, os
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('diag_v7o')

try:
    from curl_cffi import requests as cr
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    import requests as cr
    log.warning("curl_cffi not available")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
BASE_RU = "https://ru.aliexpress.com/wholesale"
SEARCH = "наушники"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def make_session():
    s = cr.Session(impersonate="chrome" if HAS_CURL_CFFI else None)
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })
    return s


def fetch(session, url, label=""):
    t0 = time.time()
    try:
        r = session.get(url, timeout=20)
        dt = time.time() - t0
        log.info(f"  [{label}] HTTP {r.status_code}, {len(r.content)}B, {dt:.1f}s")
        return r
    except Exception as e:
        log.error(f"  [{label}] EXC: {e}")
        return None


def dump_punish_scripts(r_punish, tag="punish"):
    """Dump ALL script blocks from the 84KB punish page to log + file."""
    html = r_punish.text
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    log.info(f"  [{tag}] Total scripts: {len(scripts)}")

    # Save full punish HTML for later analysis
    punish_file = os.path.join(OUT_DIR, f"diag_v7o_{tag}_punish.html")
    try:
        with open(punish_file, 'w', encoding='utf-8') as f:
            f.write(html)
        log.info(f"  [{tag}] Saved punish HTML → {punish_file} ({len(html)}B)")
    except Exception as e:
        log.warning(f"  [{tag}] save punish HTML err: {e}")

    # Dump each script
    script_file = os.path.join(OUT_DIR, f"diag_v7o_{tag}_scripts.txt")
    with open(script_file, 'w', encoding='utf-8') as f:
        for i, sc in enumerate(scripts):
            stripped = sc.strip()
            log.info(f"  [{tag}] Script[{i}] ({len(stripped)}B):")
            # Log first 400 chars of each
            log.info(f"    {stripped[:400]}")
            if len(stripped) > 400:
                log.info(f"    ... (+{len(stripped)-400} more)")
            f.write(f"=== Script {i} ({len(stripped)}B) ===\n{stripped}\n\n")

    # Search for token patterns across ALL scripts
    all_script_text = '\n'.join(scripts)
    patterns = {
        'x5secdata_assign': r'x5secdata\s*[:=]\s*["\']([^"\']{20,})["\']',
        'xg_token': r'\b(xg[a-zA-Z0-9_\-]{30,})\b',
        'document_cookie': r'document\.cookie\s*=\s*["\']([^"\']{20,})["\']',
        'setCookie': r'setCookie\(["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']',
        'localStorage': r'localStorage\.(?:set|get)Item\(["\']([^"\']+)["\']',
        'bx_token': r'__bx__([^"\'&\s]{10,})',
        'token_var': r'var\s+(?:token|tk|secToken|secData)\s*=\s*["\']([^"\']{10,})["\']',
        'window_loc': r'window\.location\.(?:href|replace)\s*=\s*["\']([^"\']{20,})["\']',
    }
    log.info(f"  [{tag}] --- Token pattern search ---")
    for name, pat in patterns.items():
        found = re.findall(pat, all_script_text)
        if found:
            log.info(f"  [{tag}] [{name}]: {len(found)} hits")
            for m in found[:5]:
                if isinstance(m, tuple):
                    log.info(f"    {m}")
                else:
                    log.info(f"    {m[:80]}")
        else:
            log.info(f"  [{tag}] [{name}]: 0")

    # Also search in non-script HTML (inline attributes, meta, etc.)
    non_script = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    for name, pat in [('html_x5', r'x5secdata[=:]["\']?([^"\'&<\s]{20,})'),
                      ('html_xg', r'\b(xg[a-zA-Z0-9_\-]{30,})\b')]:
        found = re.findall(pat, non_script)
        if found:
            log.info(f"  [{tag}] HTML[{name}]: {found[:3]}")

    return scripts


def main():
    log.info("=== diag_v7o v5 — %s ===", time.strftime('%Y-%m-%d %H:%M:%S'))
    s = make_session()

    # Cooldown 30s
    log.info("Cooldown 30s...")
    time.sleep(30)
    log.info("Cooldown done: %s", time.strftime('%H:%M:%S'))

    # [1] Double warm-up (mimic diag_v7j which had multiple prior requests)
    log.info("── [1] Double warm-up ──")
    r_h1 = fetch(s, "https://ru.aliexpress.com/", label="home1")
    if r_h1:
        log.info(f"  home1 Set-Cookie: {r_h1.headers.get('Set-Cookie','')[:200]}")
    time.sleep(3)
    r_h2 = fetch(s, "https://ru.aliexpress.com/", label="home2")
    if r_h2:
        log.info(f"  home2 Set-Cookie: {r_h2.headers.get('Set-Cookie','')[:200]}")
    time.sleep(5)  # Longer gap before search (diag_v7j had 7s)

    # [2] Search → expect TMD
    log.info("── [2] RU search ──")
    search_url = f"{BASE_RU}?SearchText={quote(SEARCH)}&page=1"
    r_search = fetch(s, search_url, label="search")

    tmd_solved = False
    if r_search:
        if len(r_search.content) > 10000:
            log.info(f"  ✓ Direct hit: {len(r_search.content)}B (no TMD!)")
            tmd_solved = True
        else:
            log.info(f"  TMD challenge: {len(r_search.content)}B")
            log.info(f"  Body[:200]: {r_search.text[:200]}")

            # [3] Solve TMD
            log.info("── [3] TMD punish ──")
            x5 = re.search(r'x5secdata=([^"&]+)', r_search.text)
            if x5:
                token = x5.group(1)
                log.info(f"  x5secdata: {token[:50]}... (len={len(token)})")

                # Build punish URL
                punish_m = re.search(r'url\s*=\s*window\.location\.protocol\s*\+\s*"([^"]+)"', r_search.text)
                if punish_m:
                    p = punish_m.group(1)
                    punish_url = ("https:" + p) if p.startswith('//') else p
                else:
                    punish_url = f"https://ru.aliexpress.com//wholesale/_____tmd_____/punish?x5secdata={token}"

                r_punish = fetch(s, punish_url, label="punish")
                if r_punish and len(r_punish.content) > 10000:
                    # DUMP ALL SCRIPTS
                    scripts = dump_punish_scripts(r_punish, tag="ru")

                    # Try to extract token from scripts
                    all_text = r_punish.text
                    new_token = None
                    # Most likely: a long xg... string
                    xg_matches = re.findall(r'\b(xg[a-zA-Z0-9_\-]{40,})\b', all_text)
                    if xg_matches:
                        new_token = max(xg_matches, key=len)
                        log.info(f"  Best xg token: {new_token[:60]}... (len={len(new_token)})")

                    # Also try: the full cookie string
                    dc_matches = re.findall(
                        r'document\.cookie\s*=\s*["\']([^"\']+)["\']', all_text)
                    for dc in dc_matches[:5]:
                        log.info(f"  document.cookie: {dc[:120]}")

                    # Set cookie and refetch
                    if new_token:
                        clean = new_token.split('__bx__')[0] if '__bx__' in new_token else new_token
                        try:
                            s.cookies.set('x5secdata', clean, domain=".aliexpress.com")
                        except Exception:
                            try:
                                s.cookies.set('x5secdata', clean)
                            except Exception:
                                pass
                        log.info(f"  Set x5secdata={clean[:40]}...")

                    time.sleep(3)
                    r_after = fetch(s, search_url, label="refetch")
                    if r_after:
                        if len(r_after.content) > 10000:
                            log.info(f"  ✓ TMD SOLVED → {len(r_after.content)}B")
                            tmd_solved = True
                            r_search = r_after
                        else:
                            log.warning(f"  ✗ Still {len(r_after.content)}B")
                            log.info(f"  Body[:200]: {r_after.text[:200]}")
                            # Second attempt: maybe need the sessionStorage trick
                            # The challenge sets sessionStorage.x5referer then redirects
                            # Try adding Referer header
                            s.headers['Referer'] = search_url
                            time.sleep(2)
                            r_after2 = fetch(s, search_url, label="refetch2")
                            if r_after2:
                                log.info(f"  refetch2: {len(r_after2.content)}B")
                                if len(r_after2.content) > 10000:
                                    tmd_solved = True
                                    r_search = r_after2

    # [4] If we got a full page, quick analysis
    if r_search and len(r_search.content) > 10000:
        log.info("── [4] Page analysis ──")
        html = r_search.text
        lang_m = re.search(r'<html[^>]*lang=["\']?(\w+)', html)
        log.info(f"  lang='{lang_m.group(1) if lang_m else '?'}'")
        t = re.search(r'<title>([^<]+)</title>', html)
        if t:
            log.info(f"  title: {t.group(1)[:120]}")
        # Quick price check
        prices = re.findall(r'(\d{1,4}[.,]\d{2})\s*(?:EUR|€|USD|\$|RUB|₽)', html)
        log.info(f"  Prices found: {len(prices)}, first 5: {prices[:5]}")
        log.info(f"  Domains: it={html.count('it.aliexpress')}, ru={html.count('ru.aliexpress')}")

    # [5] If still stuck, try Playwright-based approach hint
    if not tmd_solved:
        log.info("── [5] TMD still unsolved ──")
        log.info("  Options:")
        log.info("    A) Playwright: execute punish JS in real browser context")
        log.info("    B) Parse 84KB scripts manually (saved to file)")
        log.info("    C) Use longer session warm-up (5+ requests before search)")
        log.info("    D) Try www.aliexpress.com instead of ru.")

    log.info("── Summary ──")
    log.info("  TMD solved: %s", tmd_solved)
    log.info("=== DONE ===")
    log.info("diag_v7o v5 complete")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)

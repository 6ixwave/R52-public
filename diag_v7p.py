#!/usr/bin/env python3
"""diag_v7p — Offline decode of TMD Script 6 + live verify-endpoint test.
Goal: understand how the 79KB obfuscated JS generates the post-challenge token.
"""
import json, re, time, logging, sys, os, codecs
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('diag_v7p')

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
PUNISH_HTML = os.path.join(OUT_DIR, 'diag_v7o_ru_punish.html')


def decode_uint16_array(js_text):
    """Extract and decode Uint16Array([...]) from obfuscated JS to UTF-16LE string."""
    m = re.search(r'new\s+Uint16Array\(\[([\d,]+)\]\)', js_text)
    if not m:
        return None
    nums = [int(x) for x in m.group(1).split(',')]
    raw = bytearray()
    for n in nums:
        raw.append(n & 0xFF)
        raw.append((n >> 8) & 0xFF)
    return raw.decode('utf-16-le', errors='replace')


def main():
    log.info("=== diag_v7p — %s ===", time.strftime('%Y-%m-%d %H:%M:%S'))

    # ── [1] Read saved punish HTML ──
    log.info("── [1] Read punish HTML ──")
    if not os.path.exists(PUNISH_HTML):
        log.error("  Not found: %s", PUNISH_HTML)
        sys.exit(1)
    with open(PUNISH_HTML, 'r', encoding='utf-8') as f:
        html = f.read()
    log.info("  Size: %dB", len(html))

    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    log.info("  Scripts: %d", len(scripts))
    for i, sc in enumerate(scripts):
        log.info("    Script[%d]: %dB", i, len(sc.strip()))

    # ── [2] Decode the largest script (Script 6, ~79KB) ──
    largest_idx = max(range(len(scripts)), key=lambda i: len(scripts[i].strip()))
    largest = scripts[largest_idx].strip()
    log.info("── [2] Largest script: [%d] (%dB) ──", largest_idx, len(largest))

    decoded = decode_uint16_array(largest)
    if decoded:
        log.info("── [3] Decoded JS: %d chars ──", len(decoded))
        decoded_file = os.path.join(OUT_DIR, 'diag_v7p_script6_decoded.js')
        with open(decoded_file, 'w', encoding='utf-8') as f:
            f.write(decoded)
        log.info("  Saved → %s", decoded_file)

        # Pattern search in decoded JS
        patterns = {
            'cookie_set':     r'(?:document|doc)\.cookie\s*=',
            'x5secdata':      r'x5secdata',
            'location':       r'(?:window\.)?location\.(?:href|replace|assign)',
            'fetch_xhr':      r'(?:fetch\s*\(|XMLHttpRequest|\.open\s*\(|\.send\s*\()',
            'verify_path':    r'verify|FORMACTIOIN|_____tmd_____',
            'secd':           r'SECDATA|secd|secData',
            'nc_token':       r'NCTOKENSTR|ncToken|nc_token|ncAppKey',
            'bx':             r'__bx__|bx_|baxia',
            'timer':          r'setInterval|setTimeout',
            'sessionStorage': r'sessionStorage',
            'localStorage':   r'localStorage',
            'form':           r'<form|\.submit\s*\(|action\s*=',
            'callback':       r'callback|onSuccess|onComplete',
            'redirect':       r'redirect|window\.location',
            'json_parse':     r'JSON\.(?:parse|stringify)',
            'atob_btoa':      r'(?:atob|btoa|Buffer|fromCharCode)',
        }
        log.info("  --- Pattern search in decoded JS ---")
        for name, pat in patterns.items():
            hits = re.findall(pat, decoded)
            log.info("  [%s]: %d hits", name, len(hits))
            for h in hits[:5]:
                log.info("    %s", h[:120])

        # Show head and tail
        log.info("  --- First 600 chars ---")
        log.info("  %s", decoded[:600])
        log.info("  --- Last 600 chars ---")
        log.info("  %s", decoded[-600:])

        # Look for string literals > 15 chars (often URLs, keys, cookie names)
        str_literals = re.findall(r'["\']([^"\'\\]{15,80})["\']', decoded)
        interesting = [s for s in str_literals if not s.startswith(('http', 'data:')) or 'aliexpress' in s or 'tmd' in s or 'cookie' in s.lower() or 'token' in s.lower() or 'verify' in s.lower()]
        log.info("  --- Interesting string literals (%d) ---", len(interesting))
        for s in interesting[:20]:
            log.info("    '%s'", s)
    else:
        log.warning("  Uint16Array not found — trying alt patterns")
        log.info("  Script head[:400]: %s", largest[:400])
        # Maybe it's base64 or hex
        b64_m = re.search(r'["\']([A-Za-z0-9+/=]{200,})["\']', largest)
        if b64_m:
            log.info("  Found base64-like blob: %dB", len(b64_m.group(1)))

    # ── [4] Analyze Script 7 (window._config_) ──
    log.info("── [4] Script 7 (window._config_) ──")
    config_script = ''
    for i, sc in enumerate(scripts):
        if 'window._config_' in sc:
            config_script = sc.strip()
            log.info("  Found in Script[%d]", i)
            break
    if not config_script:
        log.warning("  window._config_ not found in any script")

    secd_m = re.search(r'"SECDATA"\s*:\s*"([^"]+)"', config_script)
    nc_m = re.search(r'"NCTOKENSTR"\s*:\s*"([^"]+)"', config_script)
    form_m = re.search(r'"FORMACTIOIN"\s*:\s*"([^"]+)"', config_script)
    ncapp_m = re.search(r'"NCAPPKEY"\s*:\s*"([^"]+)"', config_script)
    host_m = re.search(r'"HOST"\s*:\s*"([^"]+)"', config_script)
    path_m = re.search(r'"PATH"\s*:\s*"([^"]+)"', config_script)
    qrcode_m = re.search(r'"qrcode"\s*:\s*"([^"]+)"', config_script)

    if secd_m:
        secd = secd_m.group(1)
        log.info("  SECDATA: %s... (len=%d)", secd[:80], len(secd))
        bx_m = re.search(r'__bx__(.+)$', secd)
        if bx_m:
            log.info("  __bx__ suffix: %s", bx_m.group(1))
    if nc_m:
        log.info("  NCTOKENSTR: %s", nc_m.group(1))
    if form_m:
        log.info("  FORMACTIOIN: %s", form_m.group(1))
    if ncapp_m:
        log.info("  NCAPPKEY: %s", ncapp_m.group(1))
    if host_m:
        log.info("  HOST: %s", host_m.group(1))
    if path_m:
        log.info("  PATH: %s", path_m.group(1))
    if qrcode_m:
        log.info("  qrcode: %s", qrcode_m.group(1))

    # ── [5] Non-script HTML: forms, inputs, meta ──
    log.info("── [5] Non-script HTML ──")
    non_script = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    forms = re.findall(r'<form[^>]*>.*?</form>', non_script, re.DOTALL)
    log.info("  Forms: %d", len(forms))
    for i, form in enumerate(forms[:3]):
        log.info("  Form[%d]: %s", i, form[:400])
    inputs = re.findall(r'<input[^>]+>', non_script)
    log.info("  Inputs: %d", len(inputs))
    for inp in inputs[:10]:
        log.info("    %s", inp[:200])
    metas = re.findall(r'<meta[^>]+>', non_script)
    log.info("  Meta: %d", len(metas))
    for m in metas[:10]:
        log.info("    %s", m[:180])

    # ── [6] Live: POST to verify endpoint ──
    log.info("── [6] Live verify test ──")
    try:
        from curl_cffi import requests as cr
        HAS_CURL_CFFI = True
    except ImportError:
        import requests as cr
        HAS_CURL_CFFI = False
    log.info("  Backend: %s", 'curl_cffi' if HAS_CURL_CFFI else 'requests')

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    s = cr.Session(impersonate="chrome" if HAS_CURL_CFFI else None)
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    })

    log.info("  Cooldown 15s...")
    time.sleep(15)

    r_home = s.get("https://ru.aliexpress.com/", timeout=20)
    log.info("  Home: HTTP %d, %dB", r_home.status_code, len(r_home.content))
    time.sleep(3)

    search_url = f"https://ru.aliexpress.com/wholesale?SearchText={quote('наушники')}&page=1"
    r_search = s.get(search_url, timeout=20)
    log.info("  Search: HTTP %d, %dB", r_search.status_code, len(r_search.content))

    if len(r_search.content) < 10000:
        x5_m = re.search(r'x5secdata=([^"&]+)', r_search.text)
        if x5_m:
            token = x5_m.group(1)
            log.info("  TMD token: %s... (len=%d)", token[:60], len(token))

            punish_url = f"https://ru.aliexpress.com//wholesale/_____tmd_____/punish?x5secdata={token}"
            r_punish = s.get(punish_url, timeout=20)
            log.info("  Punish: HTTP %d, %dB", r_punish.status_code, len(r_punish.content))

            if len(r_punish.content) > 10000:
                p_secd_m = re.search(r'"SECDATA"\s*:\s*"([^"]+)"', r_punish.text)
                p_nc_m = re.search(r'"NCTOKENSTR"\s*:\s*"([^"]+)"', r_punish.text)
                if p_secd_m:
                    p_secd = p_secd_m.group(1)
                    log.info("  Live SECDATA: %s... (len=%d)", p_secd[:60], len(p_secd))

                verify_url = "https://ru.aliexpress.com/wholesale/_____tmd_____/verify/"
                log.info("  POST → %s", verify_url)

                payloads = [
                    ("json-secd", {"secd": p_secd_m.group(1) if p_secd_m else ""}),
                    ("json-full", {
                        "secd": p_secd_m.group(1) if p_secd_m else "",
                        "ncToken": p_nc_m.group(1) if p_nc_m else "",
                        "host": "ru.aliexpress.com:443",
                        "path": "/wholesale",
                    }),
                    ("form-secd", None),  # form-encoded below
                ]
                for name, payload in payloads:
                    try:
                        if name == "form-secd":
                            r_v = s.post(verify_url,
                                         data={"secd": p_secd_m.group(1) if p_secd_m else ""},
                                         headers={"Referer": punish_url,
                                                  "Content-Type": "application/x-www-form-urlencoded"},
                                         timeout=15)
                        else:
                            r_v = s.post(verify_url, json=payload,
                                         headers={"Referer": punish_url,
                                                  "Origin": "https://ru.aliexpress.com"},
                                         timeout=15)
                        log.info("  Verify[%s]: HTTP %d, %dB", name, r_v.status_code, len(r_v.content))
                        log.info("    Body[:300]: %s", r_v.text[:300])
                        sc = r_v.headers.get('Set-Cookie', '')
                        if sc:
                            log.info("    Set-Cookie: %s", sc[:200])
                    except Exception as e:
                        log.warning("  Verify[%s] err: %s", name, e)

                # Also try GET with secd as query param
                try:
                    verify_get = f"{verify_url}?secd={quote(p_secd_m.group(1) if p_secd_m else '')}"
                    r_vg = s.get(verify_get, headers={"Referer": punish_url}, timeout=15)
                    log.info("  Verify[GET]: HTTP %d, %dB", r_vg.status_code, len(r_vg.content))
                    log.info("    Body[:200]: %s", r_vg.text[:200])
                except Exception as e:
                    log.warning("  Verify[GET] err: %s", e)

    log.info("── Summary ──")
    log.info("=== DONE ===")
    log.info("diag_v7p complete")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        log.exception("FATAL: %s", e)
        sys.exit(1)

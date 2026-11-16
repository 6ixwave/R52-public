"""
diag_farpost_v3.py — Чистая диагностика AltCHA Farpost.
Стратегии:
  A: Fetch challenge URL с Accept: application/json
  B: Извлечь challenge из HTML-страницы (script/JSON)
  C: Использовать b-параметр как challenge, решить PoW, POST
  D: f=1 fallback
"""
import sys, os, re, json, time, hashlib, logging
from html import unescape
from urllib.parse import quote, unquote, parse_qs, urlparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("diag.fp3")

from r52.collectors.farpost import FarpostCollector

QUERY = "тест"
PAGE = 1
collector = FarpostCollector(data_dir=None, use_proxies=False)
collector._warmup()
time.sleep(2)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.farpost.ru/",
}

def get_html(resp):
    ct = (resp.headers.get("Content-Type") or "").lower()
    if "1251" in ct or "cp1251" in ct:
        return resp.content.decode("windows-1251", errors="replace")
    return resp.text

def solve_pow(challenge, difficulty, max_iter=20_000_000):
    """Find nonce where SHA-256(challenge+nonce) has >= difficulty leading zero bits."""
    t0 = time.time()
    for i in range(max_iter):
        nonce = str(i)
        h = hashlib.sha256((challenge + nonce).encode("utf-8")).digest()
        # Count leading zero bits
        zero_bits = 0
        for byte in h:
            if byte == 0:
                zero_bits += 8
            else:
                zero_bits += 8 - byte.bit_length()
                break
        if zero_bits >= difficulty:
            return nonce, zero_bits, time.time() - t0
    return None, 0, time.time() - t0

def solve_pow_colon(challenge, difficulty, max_iter=20_000_000):
    """SHA-256(challenge:nonce) variant."""
    t0 = time.time()
    for i in range(max_iter):
        nonce = str(i)
        h = hashlib.sha256(f"{challenge}:{nonce}".encode("utf-8")).digest()
        zero_bits = 0
        for byte in h:
            if byte == 0:
                zero_bits += 8
            else:
                zero_bits += 8 - byte.bit_length()
                break
        if zero_bits >= difficulty:
            return nonce, zero_bits, time.time() - t0
    return None, 0, time.time() - t0

# ============================================================
# STEP 1: Get the verify page
# ============================================================
print("=" * 60)
print("[1] Fetching verify page")
target = f"https://www.farpost.ru/dir?query={quote(QUERY)}&page={PAGE}"
if collector._cffi:
    resp = collector._cffi.get(target, impersonate="chrome", timeout=(15, 30), headers=HEADERS)
else:
    resp = collector.session.get(target, headers=HEADERS, timeout=(10, 30))

html = get_html(resp)
print(f"    Status: {resp.status_code}, Size: {len(resp.content)}B")
print(f"    Final URL: {resp.url}")

# ============================================================
# STEP 2: Extract all AltCHA/form params
# ============================================================
print(f"\n[2] Extracting params...")

# AltCHA challenge attribute (HTML-decode &amp; → &)
altcha_m = re.search(r'<altcha-widget[^>]*challenge="([^"]+)"', html)
challenge_attr = unescape(altcha_m.group(1)) if altcha_m else None
print(f"    challenge attr (decoded): {challenge_attr}")

# Form
form_m = re.search(r'<form[^>]*id="grecap-form"[^>]*action="([^"]*)"[^>]*>(.*?)</form>', html, re.DOTALL)
form_action = unescape(form_m.group(1)) if form_m else ""
form_body = form_m.group(2) if form_m else ""
print(f"    form action (decoded): {form_action}")

s_val = re.search(r'name="s"\s+value="([^"]*)"', form_body)
t_val = re.search(r'name="t"\s+value="([^"]*)"', form_body)
grecap_init = re.search(r'name="[REDACTED_AUTH_SECRET]"\s+value="([^"]*)"', form_body)
s = s_val.group(1) if s_val else ""
t = t_val.group(1) if t_val else ""
grecap_init_val = grecap_init.group(1) if grecap_init else ""
print(f"    s: {s}")
print(f"    t: {t}")
print(f"    grecap-init: {grecap_init_val}")
print(f"    s == grecap-init: {s == grecap_init_val}")

# Build base URL for relative paths
base_url = str(resp.url).split("?")[0]
print(f"    Base URL: {base_url}")

# ============================================================
# STEP 3: Try fetching the challenge endpoint
# ============================================================
print(f"\n[3] Fetching challenge endpoint...")

# Strategy A: Accept: application/json
json_headers = dict(HEADERS)
json_headers["Accept"] = "application/json"

challenge_url = base_url + challenge_attr if challenge_attr and challenge_attr.startswith("?") else challenge_attr
print(f"    A: {challenge_url}")
if collector._cffi:
    ch_resp = collector._cffi.get(challenge_url, impersonate="chrome", timeout=(10, 20), headers=json_headers)
else:
    ch_resp = collector.session.get(challenge_url, headers=json_headers, timeout=(10, 20))

ch_text = ch_resp.text
ch_ct = (ch_resp.headers.get("Content-Type") or "").lower()
print(f"    A: Status={ch_resp.status_code}, CT={ch_ct}, Size={len(ch_resp.content)}B")

ch_data = None
if "json" in ch_ct:
    try:
        ch_data = json.loads(ch_text)
        print(f"    A: JSON: {json.dumps(ch_data, ensure_ascii=False)[:300]}")
    except:
        pass
else:
    # Look for embedded JSON in HTML
    json_embed = re.search(r'<script[^>]*>\s*(\{[^<]*"challenge"[^<]*\})\s*</script>', ch_text, re.DOTALL)
    if json_embed:
        try:
            ch_data = json.loads(json_embed.group(1))
            print(f"    A: Embedded JSON: {ch_data}")
        except:
            pass
    # Look for data-challenge or similar
    dc = re.search(r'data-challenge=["\']([^"\']+)["\']', ch_text)
    if dc:
        print(f"    A: data-challenge: {dc.group(1)}")
    # Check if it's the same HTML page (redirect loop)
    if "altcha-widget" in ch_text and "grecap-form" in ch_text:
        print(f"    A: Got HTML verify page again (not JSON) — trying f=1")

# Strategy B: f=1 (simpler mode)
print(f"\n    B: Trying f=1...")
f1_url = f"{base_url}/verify?r=1&u={quote(QUERY, safe='')}&f=1"
# Actually build from the challenge attr but replace f=3 with f=1
if challenge_attr:
    f1_attr = re.sub(r'f=\d+', 'f=1', challenge_attr)
    f1_url = base_url + f1_attr
print(f"    B: {f1_url}")
if collector._cffi:
    f1_resp = collector._cffi.get(f1_url, impersonate="chrome", timeout=(10, 20), headers=json_headers)
else:
    f1_resp = collector.session.get(f1_url, headers=json_headers, timeout=(10, 20))

f1_text = f1_resp.text
f1_ct = (f1_resp.headers.get("Content-Type") or "").lower()
print(f"    B: Status={f1_resp.status_code}, CT={f1_ct}, Size={len(f1_resp.content)}B")
print(f"    B: First 300: {f1_text[:300]}")

if "json" in f1_ct:
    try:
        ch_data = json.loads(f1_text)
        print(f"    B: JSON: {json.dumps(ch_data, ensure_ascii=False)[:300]}")
    except:
        pass

# Strategy C: f=2
print(f"\n    C: Trying f=2...")
if challenge_attr:
    f2_attr = re.sub(r'f=\d+', 'f=2', challenge_attr)
    f2_url = base_url + f2_attr
    if collector._cffi:
        f2_resp = collector._cffi.get(f2_url, impersonate="chrome", timeout=(10, 20), headers=json_headers)
    else:
        f2_resp = collector.session.get(f2_url, headers=json_headers, timeout=(10, 20))
    f2_text = f2_resp.text
    f2_ct = (f2_resp.headers.get("Content-Type") or "").lower()
    print(f"    C: Status={f2_resp.status_code}, CT={f2_ct}, Size={len(f2_resp.content)}B")
    print(f"    C: First 300: {f2_text[:300]}")
    if "json" in f2_ct:
        try:
            ch_data = json.loads(f2_text)
            print(f"    C: JSON: {json.dumps(ch_data, ensure_ascii=False)[:300]}")
        except:
            pass

# ============================================================
# STEP 4: Determine challenge & solve
# ============================================================
print(f"\n[4] Solving proof-of-work...")

# If we got proper JSON with challenge
if ch_data and "challenge" in ch_data:
    challenge = ch_data["challenge"]
    difficulty = ch_data.get("difficulty", 20)
    algo = ch_data.get("algorithm", "sha256")
    print(f"    From JSON: challenge={challenge!r}, difficulty={difficulty}, algo={algo}")
else:
    # Use 'b' param (timestamp) as challenge — most likely for AltCHA
    # The 'b' value is in the challenge URL
    b_m = re.search(r'[&?]b=(\d+)', challenge_attr or "")
    challenge = b_m.group(1) if b_m else t
    difficulty = 20
    print(f"    Using 'b' as challenge: {challenge}, difficulty={difficulty}")
    # Also try 's' (the SHA-256 hash) as challenge
    print(f"    Alt: 's' as challenge: {s[:32]}...")

# Solve
nonce, bits, elapsed = solve_pow(challenge, difficulty)
print(f"    solve(challenge+nonce): nonce={nonce}, bits={bits}, time={elapsed:.2f}s")

if nonce is None:
    nonce, bits, elapsed = solve_pow_colon(challenge, difficulty)
    print(f"    solve(challenge:nonce): nonce={nonce}, bits={bits}, time={elapsed:.2f}s")

# Also solve with 's' as challenge (in case 'b' is wrong)
nonce_s, bits_s, elapsed_s = solve_pow(s, difficulty)
print(f"    solve(s+nonce): nonce={nonce_s}, bits={bits_s}, time={elapsed_s:.2f}s")

# ============================================================
# STEP 5: POST the solution
# ============================================================
print(f"\n[5] POSTing solution...")

# The form posts to: base_url + form_action
# form_action = "?r=1&u=/dir?query=тест&page=1&f=3"
post_url = base_url + form_action
print(f"    POST URL: {post_url}")

# Try multiple solution formats
solutions = []
if nonce:
    solutions.append(("b+nonce", f"{challenge}:{nonce}"))
    solutions.append(("b:nonce", f"{challenge}:{nonce}"))
    solutions.append(("nonce_only", nonce))
if nonce_s:
    solutions.append(("s+nonce", f"{s}:{nonce_s}"))
    solutions.append(("s:nonce", f"{s}:{nonce_s}"))

# Also: maybe the solution is just the hash itself
h_sol = hashlib.sha256((challenge + (nonce or "0")).encode()).hexdigest()
solutions.append(("hash", h_sol))

card_pat = re.compile(
    r'<a[^>]*href=["\'](?:https?://www\.farpost\.ru)?/[^"\']*/([a-z0-9\-]*?(\d{5,}))[a-z0-9\-]*\.html["\'][^>]*>',
    re.I
)

success = False
for label, sol in solutions:
    print(f"\n    --- Solution [{label}]: {sol[:60]}{'...' if len(sol)>60 else ''}")
    post_data = {
        "s": s,
        "t": t,
        "[REDACTED_AUTH_SECRET]": sol,
    }
    post_headers = dict(HEADERS)
    post_headers["Content-Type"] = "application/x-www-form-urlencoded"
    post_headers["Referer"] = base_url + "/verify"
    
    if collector._cffi:
        post_resp = collector._cffi.post(post_url, data=post_data, impersonate="chrome",
                                         timeout=(15, 30), headers=post_headers)
    else:
        post_resp = collector.session.post(post_url, data=post_data, headers=post_headers, timeout=(10, 30))
    
    post_html = get_html(post_resp)
    cards = list(card_pat.finditer(post_html))
    still_captcha = "grecap-form" in post_html or "altcha-widget" in post_html
    title_m = re.search(r'<title>([^<]+)</title>', post_html, re.I)
    title = title_m.group(1).strip() if title_m else "?"
    
    print(f"        Status={post_resp.status_code}, Cards={len(cards)}, CAPTCHA={still_captcha}")
    print(f"        Title: {title[:60]}")
    print(f"        Final URL: {post_resp.url}")
    
    if cards:
        print(f"        SUCCESS! {len(cards)} cards found!")
        out = os.path.join(os.path.dirname(__file__), f"farpost_v3_success_{label}.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(post_html)
        print(f"        Saved: {out}")
        success = True
        break
    elif not still_captcha and len(post_html) > 5000:
        print(f"        No CAPTCHA but no cards — checking content...")
        print(f"        Snippet: {post_html[1000:2000]}")
        out = os.path.join(os.path.dirname(__file__), f"farpost_v3_nocap_{label}.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(post_html)
        print(f"        Saved: {out}")
    
    time.sleep(2)

# ============================================================
# STEP 6: If all failed, try direct re-fetch (cookie might be set)
# ============================================================
if not success:
    print(f"\n[6] Re-fetching target (cookies may have been set)...")
    time.sleep(3)
    if collector._cffi:
        rf_resp = collector._cffi.get(target, impersonate="chrome", timeout=(15, 30), headers=HEADERS)
    else:
        rf_resp = collector.session.get(target, headers=HEADERS, timeout=(10, 30))
    rf_html = get_html(rf_resp)
    rf_cards = list(card_pat.finditer(rf_html))
    rf_captcha = "grecap-form" in rf_html or "altcha-widget" in rf_html
    rf_title = re.search(r'<title>([^<]+)</title>', rf_html, re.I)
    print(f"    Status={rf_resp.status_code}, Cards={len(rf_cards)}, CAPTCHA={rf_captcha}")
    print(f"    Title: {rf_title.group(1).strip()[:60] if rf_title else '?'}")
    print(f"    Final URL: {rf_resp.url}")
    if rf_cards:
        print(f"    SUCCESS on re-fetch!")
        out = os.path.join(os.path.dirname(__file__), "farpost_v3_refetch.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(rf_html)
    else:
        out = os.path.join(os.path.dirname(__file__), "farpost_v3_refetch.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(rf_html)
        print(f"    Saved: {out}")

# ============================================================
# STEP 7: Show the full verify page JS for manual analysis
# ============================================================
print(f"\n[7] Verify page JavaScript (for AltCHA logic):")
# Find all script blocks
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
for i, s in enumerate(scripts):
    if 'altcha' in s.lower() or 'challenge' in s.lower() or 'domReady' in s or 'grecap' in s:
        print(f"    --- Script {i} ({len(s)} chars) ---")
        print(f"    {s[:800]}")
        print(f"    ...")

# Also show the altcha-widget element and surrounding context
altcha_ctx = re.search(r'.{200}<altcha-widget.{300}', html, re.DOTALL)
if altcha_ctx:
    print(f"\n    AltCHA context:\n    {altcha_ctx.group(0)}")

print(f"\n=== DONE ===")

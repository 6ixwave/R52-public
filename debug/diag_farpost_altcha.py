"""
diag_farpost_altcha.py — Диагностика и решение AltCHA-верификации Farpost.
AltCHA = proof-of-work: SHA-256(challenge + nonce) → N leading zero bits.
"""
import sys, os, re, json, time, hashlib, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("diag.fp-altcha")

from r52.collectors.farpost import FarpostCollector
from urllib.parse import quote, unquote, parse_qs, urlparse

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

# Step 1: Fetch the verify page (which redirects to /verify)
target_url = f"https://www.farpost.ru/dir?query={quote(QUERY)}&page={PAGE}"
print(f"[1] Fetching: {target_url}")
if collector._cffi:
    resp = collector._cffi.get(target_url, impersonate="chrome", timeout=(15, 30), headers=HEADERS)
else:
    resp = collector.session.get(target_url, headers=HEADERS, timeout=(10, 30))

ct = (resp.headers.get("Content-Type") or "").lower()
if "1251" in ct or "cp1251" in ct:
    html = resp.content.decode("windows-1251", errors="replace")
else:
    html = resp.text

print(f"    Status: {resp.status_code}, Final URL: {resp.url}")
print(f"    Size: {len(resp.content)}B")

# Step 2: Extract AltCHA parameters
print(f"\n[2] Extracting AltCHA parameters...")

# The altcha-widget challenge attribute
altcha_m = re.search(r'<altcha-widget[^>]*challenge="([^"]+)"', html)
if not altcha_m:
    print("    NO altcha-widget found!")
    # Try to find any challenge indicator
    challenge_m = re.search(r'challenge["\s:=]+([^"\'<>\s]+)', html)
    if challenge_m:
        print(f"    Found challenge attr: {challenge_m.group(1)}")
    sys.exit(1)

challenge_url = unquote(altcha_m.group(1))
print(f"    Challenge URL: {challenge_url}")

# Extract form params
form_m = re.search(r'<form[^>]*id="grecap-form"[^>]*action="([^"]*)"[^>]*>(.*?)</form>', html, re.DOTALL)
if form_m:
    form_action = unquote(form_m.group(1))
    form_body = form_m.group(2)
    print(f"    Form action: {form_action}")
    
    # Extract hidden inputs
    s_val = re.search(r'name="s"\s+value="([^"]*)"', form_body)
    t_val = re.search(r'name="t"\s+value="([^"]*)"', form_body)
    grecap_val = re.search(r'name="g-recaptcha-response"\s+value="([^"]*)"', form_body)
    print(f"    s: {s_val.group(1) if s_val else 'N/A'}")
    print(f"    t: {t_val.group(1) if t_val else 'N/A'}")
    print(f"    g-recaptcha-response: {grecap_val.group(1) if grecap_val else 'N/A'}")

# Step 3: Fetch the AltCHA challenge (it's a URL that returns JSON with challenge params)
print(f"\n[3] Fetching AltCHA challenge...")
# The challenge URL is relative to the current page
if challenge_url.startswith("?"):
    # Relative to /verify path
    base = str(resp.url).split("?")[0]
    full_challenge_url = base + challenge_url
else:
    full_challenge_url = challenge_url

print(f"    Full challenge URL: {full_challenge_url}")

# Parse query params from challenge URL
parsed = urlparse(full_challenge_url)
params = parse_qs(parsed.query)
print(f"    Params: {json.dumps({k: v[0] for k, v in params.items()}, ensure_ascii=False)}")

# AltCHA challenge: GET the challenge URL → returns JSON {challenge, difficulty, ...}
if collector._cffi:
    ch_resp = collector._cffi.get(full_challenge_url, impersonate="chrome", timeout=(10, 20), headers=HEADERS)
else:
    ch_resp = collector.session.get(full_challenge_url, headers=HEADERS, timeout=(10, 20))

print(f"    Challenge response: {ch_resp.status_code}, {len(ch_resp.content)}B")
ch_text = ch_resp.text
print(f"    Body: {ch_text[:500]}")

# Try to parse as JSON
try:
    ch_data = json.loads(ch_text)
    print(f"    JSON: {json.dumps(ch_data, ensure_ascii=False)[:300]}")
except:
    ch_data = None
    print("    NOT JSON — checking HTML...")
    # Maybe it's an HTML page with the challenge embedded
    ch_json_m = re.search(r'(\{[^}]*"challenge"[^}]*\})', ch_text)
    if ch_json_m:
        ch_data = json.loads(ch_json_m.group(1))
        print(f"    Embedded JSON: {ch_data}")

# Step 4: Solve proof-of-work
print(f"\n[4] Solving proof-of-work...")
if ch_data and "challenge" in ch_data:
    challenge = ch_data["challenge"]
    difficulty = ch_data.get("difficulty", 20)  # default 20 bits
    print(f"    Challenge: {challenge}")
    print(f"    Difficulty: {difficulty} bits")
    
    # AltCHA: find nonce such that SHA-256(challenge + nonce) has `difficulty` leading zero bits
    # The "nonce" is typically a hex string
    t0 = time.time()
    found = False
    for i in range(10_000_000):
        nonce = str(i)
        h = hashlib.sha256((challenge + nonce).encode()).digest()
        # Check leading zero bits
        bits = 0
        for byte in h:
            if byte == 0:
                bits += 8
            else:
                bits += 8 - byte.bit_length()
                break
        if bits >= difficulty:
            print(f"    SOLVED: nonce={nonce} (i={i}), bits={bits}, time={time.time()-t0:.2f}s")
            found = True
            break
    if not found:
        print(f"    NOT SOLVED in 10M iterations ({time.time()-t0:.1f}s)")
        # Maybe it's a different scheme: SHA-256(challenge + ":" + nonce)
        for i in range(10_000_000):
            nonce = str(i)
            h = hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()
            bits = 0
            for byte in h:
                if byte == 0:
                    bits += 8
                else:
                    bits += 8 - byte.bit_length()
                    break
            if bits >= difficulty:
                print(f"    SOLVED (colon): nonce={nonce} (i={i}), bits={bits}, time={time.time()-t0:.2f}s")
                found = True
                break
        if not found:
            print("    Trying hex nonce...")
            for i in range(10_000_000):
                nonce = f"{i:08x}"
                h = hashlib.sha256((challenge + nonce).encode()).digest()
                bits = 0
                for byte in h:
                    if byte == 0:
                        bits += 8
                    else:
                        bits += 8 - byte.bit_length()
                        break
                if bits >= difficulty:
                    print(f"    SOLVED (hex): nonce={nonce} (i={i}), bits={bits}, time={time.time()-t0:.2f}s")
                    found = True
                    break
elif ch_data:
    print(f"    Challenge data (no 'challenge' key): {ch_data}")
else:
    print("    No challenge data — trying generic approach")
    # Maybe the challenge IS the URL params
    challenge = params.get("b", [str(int(time.time()*1000))])[0]
    print(f"    Using 'b' param as challenge: {challenge}")
    difficulty = 20
    t0 = time.time()
    for i in range(5_000_000):
        nonce = str(i)
        h = hashlib.sha256((challenge + nonce).encode()).digest()
        bits = 0
        for byte in h:
            if byte == 0:
                bits += 8
            else:
                bits += 8 - byte.bit_length()
                break
        if bits >= difficulty:
            print(f"    SOLVED: nonce={nonce}, bits={bits}, time={time.time()-t0:.2f}s")
            break

# Step 5: POST the solution back
print(f"\n[5] POSTing solution...")
# The form posts to the action URL with the solution
# AltCHA response format: the widget fills g-recaptcha-response with the solution
# Solution format: challenge:nonce or just the hash

# Determine solution format from the page JS
js_m = re.search(r'domReady\(function\(\)\s*{(.*?)\})\s*\)', html, re.DOTALL)
if js_m:
    print(f"    JS logic: {js_m.group(1)[:400]}")

# POST: form action with s, t, g-recaptcha-response (solution)
if form_m:
    post_url = str(resp.url).split("?")[0] + form_m.group(1)
    print(f"    POST URL: {post_url}")
    
    post_data = {}
    if s_val:
        post_data["s"] = s_val.group(1)
    if t_val:
        post_data["t"] = t_val.group(1)
    # Solution: typically "challenge:nonce" or the full proof
    if ch_data and "challenge" in ch_data:
        post_data["g-recaptcha-response"] = f"{ch_data['challenge']}:{nonce}"
    else:
        post_data["g-recaptcha-response"] = f"{challenge}:{nonce}"
    
    print(f"    POST data: {post_data}")
    
    if collector._cffi:
        post_resp = collector._cffi.post(post_url, data=post_data, impersonate="chrome",
                                         timeout=(15, 30), headers=HEADERS)
    else:
        post_resp = collector.session.post(post_url, data=post_data, headers=HEADERS, timeout=(10, 30))
    
    print(f"    POST response: {post_resp.status_code}, {len(post_resp.content)}B")
    print(f"    Final URL: {post_resp.url}")
    
    ct2 = (post_resp.headers.get("Content-Type") or "").lower()
    if "1251" in ct2 or "cp1251" in ct2:
        post_html = post_resp.content.decode("windows-1251", errors="replace")
    else:
        post_html = post_resp.text
    
    # Check if we got results
    card_pat = re.compile(
        r'<a[^>]*href=["\'](?:https?://www\.farpost\.ru)?/[^"\']*/([a-z0-9\-]*?(\d{5,}))[a-z0-9\-]*\.html["\'][^>]*>',
        re.I
    )
    cards = list(card_pat.finditer(post_html))
    still_captcha = "grecap-form" in post_html or "altcha-widget" in post_html
    title_m2 = re.search(r'<title>([^<]+)</title>', post_html, re.I)
    title2 = title_m2.group(1).strip() if title_m2 else "?"
    
    print(f"    Title: {title2[:80]}")
    print(f"    Cards: {len(cards)}")
    print(f"    Still CAPTCHA: {still_captcha}")
    
    if cards:
        print(f"\n    SUCCESS! Got {len(cards)} cards. Saving...")
        out = os.path.join(os.path.dirname(__file__), "farpost_after_altcha.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(post_html)
        print(f"    Saved: {out}")
        
        # Quick parse
        for i, m in enumerate(cards[:5]):
            fid = m.group(2)
            a_close = post_html.find('</a>', m.end())
            a_text = re.sub(r'<[^>]+>', '', post_html[m.end():a_close]).strip() if a_close > 0 else "?"
            print(f"      [{i+1}] ID={fid} title={a_text[:60]}")

# Step 6: Also try the f=1 fallback (simpler verification?)
print(f"\n[6] Trying f=1 fallback...")
fallback_url = f"https://www.farpost.ru/verify?r=1&u=%2Fdir%3Fquery%3D{quote(QUERY)}%26page%3D1&f=1"
if collector._cffi:
    fb_resp = collector._cffi.get(fallback_url, impersonate="chrome", timeout=(15, 30), headers=HEADERS)
else:
    fb_resp = collector.session.get(fallback_url, headers=HEADERS, timeout=(10, 30))

ct3 = (fb_resp.headers.get("Content-Type") or "").lower()
if "1251" in ct3 or "cp1251" in ct3:
    fb_html = fb_resp.content.decode("windows-1251", errors="replace")
else:
    fb_html = fb_resp.text

fb_title = re.search(r'<title>([^<]+)</title>', fb_html, re.I)
fb_cards = len(list(card_pat.finditer(fb_html)))
fb_captcha = "grecap-form" in fb_html or "altcha-widget" in fb_html
print(f"    Status: {fb_resp.status_code}, Size: {len(fb_resp.content)}B")
print(f"    Title: {fb_title.group(1).strip()[:60] if fb_title else '?'}")
print(f"    Cards: {fb_cards}, CAPTCHA: {fb_captcha}")
print(f"    Final URL: {fb_resp.url}")

# Save
out_fb = os.path.join(os.path.dirname(__file__), "farpost_f1_fallback.html")
with open(out_fb, "w", encoding="utf-8") as f:
    f.write(fb_html)
print(f"    Saved: {out_fb}")

# Show relevant section
if fb_captcha:
    # Find the form
    fb_form = re.search(r'<form[^>]*>(.*?)</form>', fb_html, re.DOTALL)
    if fb_form:
        print(f"    Form: {fb_form.group(0)[:500]}")
else:
    print(f"    HTML snippet: {fb_html[2000:4000]}")

print(f"\n=== DONE ===")

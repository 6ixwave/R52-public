"""Diagnostic: trace normalize_eye step-by-step on AliExpress RU JSON."""
import json, sys, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from r52.domain.normalize import (
    normalize_eye, card_identity, canonical_source, _split,
    HOSTS, ID_PATTERNS, clean,
)
from r52.domain.media import normalize_media
from r52.errors import AppError

json_path = ROOT / "collect" / "output" / "ALIEXPRESS_RU" / "jarvis-eye-aliexpress_ru-fad5944c.json"
print(f"Loading: {json_path}")
doc = json.loads(json_path.read_text(encoding="utf-8-sig"))
obs = doc["observation"]

print(f"\n=== Raw observation keys: {list(obs.keys())}")
print(f"source (raw): {obs.get('source')!r}")
print(f"id (raw): {obs.get('id')!r}")
print(f"url (raw): {obs.get('url')!r}")

# Step 1: canonical_source
source = canonical_source(obs.get("source"))
print(f"\n=== canonical_source: {source!r}")
print(f"source in HOSTS: {source in HOSTS}")
if source in HOSTS:
    print(f"HOSTS['{source}'] = {HOSTS[source]}")

# Step 2: clean (media removed)
cleaned = clean({k: v for k, v in obs.items() if k != "media"})
print(f"\n=== After clean:")
print(f"  source: {cleaned.get('source')!r}")
print(f"  id: {cleaned.get('id')!r}")
print(f"  url: {cleaned.get('url')!r}")

# Step 3: listing_id
listing_id = str(cleaned.get("id") or cleaned.get("listing_id") or "").strip()
print(f"\n=== listing_id: {listing_id!r}")

# Step 4: card_identity trace
url = cleaned.get("url")
print(f"\n=== card_identity({source!r}, {listing_id!r}, {url!r})")
split = _split(url)
print(f"  _split -> {split}")
if split:
    parts, port = split
    host = (parts.hostname or "").lower()
    print(f"  scheme: {parts.scheme!r}")
    print(f"  hostname: {host!r}")
    print(f"  port: {port!r}")
    print(f"  username: {parts.username!r}")
    print(f"  password: {parts.password!r}")
    print(f"  path: {parts.path!r}")
    print(f"  host in HOSTS['{source}']: {host in HOSTS.get(source, set())}")
    pat = ID_PATTERNS[source]
    print(f"  ID pattern: {pat.pattern!r}")
    match = pat.search(parts.path)
    print(f"  match: {match}")
    if match:
        groups = match.groups()
        print(f"  groups: {groups}")
        found = next((g for g in groups if g), None)
        print(f"  found: {found!r}")
        print(f"  str(listing_id)==found: {str(listing_id) == found}")
else:
    print("  _split returned None -> PUBLIC_SOURCE_URL_REQUIRED (case 1)")

# Step 5: full normalize_eye
print(f"\n=== Full normalize_eye call:")
try:
    normalized, cleaned_obs = normalize_eye(obs)
    print(f"  SUCCESS")
    print(f"  source={normalized['source']}, listing_id={normalized['listing_id']}")
    print(f"  url={normalized['url']}")
    print(f"  title={normalized['title'][:80]!r}")
    print(f"  prices={len(normalized['prices'])}")
    print(f"  availability={normalized['availability']}")
    print(f"  media items={len((normalized.get('media') or {}).get('items', []))}")
except AppError as e:
    print(f"  AppError: code={e.code}, status={e.status}, detail={e.detail}")
    traceback.print_exc()
except Exception as e:
    print(f"  {type(e).__name__}: {e}")
    traceback.print_exc()

print("\n=== Done.")

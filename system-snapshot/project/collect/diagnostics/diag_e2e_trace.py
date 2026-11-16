"""Точечная диагностика E2E Farpost: каждый шаг с traceback."""
import sys, os, time, json, traceback, logging
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    stream=sys.stdout)

print("=" * 70)
print("STEP 1: Импорт FarpostCollector")
print("=" * 70)
try:
    from r52.collectors.farpost import FarpostCollector
    print("  OK: FarpostCollector импортирован")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 2: Импорт normalize_eye")
print("=" * 70)
try:
    from r52.domain.normalize import normalize_eye
    print("  OK: normalize_eye импортирован")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 3: Импорт normalize_media (внутренний)")
print("=" * 70)
try:
    from r52.domain.media import normalize_media
    print("  OK: normalize_media импортирован")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 4: Создание FarpostCollector(use_proxies=False)")
print("=" * 70)
try:
    collector = FarpostCollector(use_proxies=False)
    print(f"  OK: stats={collector.stats}")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 5: Warm-up (GET farpost.ru)")
print("=" * 70)
try:
    t0 = time.monotonic()
    collector._warmup()
    print(f"  OK: warmed_up={collector._warmed_up}, {time.monotonic()-t0:.1f}s")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 6: Fetch одного URL")
print("=" * 70)
URL = "https://www.farpost.ru/vladivostok/zapchasti/datchik-kisloroda-lynxauto-art-dm-2350-g16879823067.html"
LID = "16879823067"
try:
    t0 = time.monotonic()
    resp = collector.fetch(URL)
    elapsed = time.monotonic() - t0
    if resp is None:
        print(f"  FETCH FAILED ({elapsed:.1f}s), stats={collector.stats}")
        sys.exit(1)
    print(f"  OK: HTTP {resp.status_code}, {len(resp.text)} bytes, {elapsed:.1f}s")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 7: parse_page")
print("=" * 70)
try:
    obs = collector.parse_page(resp, LID, URL, "")
    if obs is None:
        print("  PARSE returned None")
        sys.exit(1)
    fields_keys = list(obs.get("fields", {}).keys())
    prices = obs.get("price_candidates", [])
    media = obs.get("media", {}).get("items", [])
    print(f"  OK: fields={fields_keys}")
    print(f"      prices={len(prices)}, media={len(media)}, avail={obs.get('availability')}")
    for p in prices:
        print(f"      price: role={p.get('role')} amount_minor={p.get('amount_minor')} cur={p.get('currency')} label={p.get('label')}")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 8: _ensure_evidence / _ensure_price_amounts / _ensure_availability")
print("=" * 70)
try:
    collector._ensure_evidence(obs)
    collector._ensure_price_amounts(obs)
    collector._ensure_availability_in_fields(obs)
    print("  OK: post-processing done")
    # Проверяем, что amount добавлен
    for p in obs.get("price_candidates", []):
        print(f"      price after ensure: amount={p.get('amount')} amount_minor={p.get('amount_minor')} visible_label={p.get('visible_label')}")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 9: Обёртка в intake-формат")
print("=" * 70)
try:
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=10))).isoformat()
    wrapped = {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": "FARPOST",
        "observation": {
            **obs,
            "source": "FARPOST",
            "id": str(LID),
            "url": URL,
            "observed_at": now,
            "status": "PARTIAL_SUCCESS",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
        },
    }
    print(f"  OK: wrapped, observed_at={now}")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 10: normalize_eye")
print("=" * 70)
try:
    t0 = time.monotonic()
    normalized, cleaned = normalize_eye(wrapped["observation"])
    elapsed = time.monotonic() - t0
    print(f"  OK: {elapsed:.2f}s")
    print(f"      title={normalized['title'][:80]!r}")
    print(f"      listing_id={normalized['listing_id']}")
    print(f"      url={normalized['url']}")
    print(f"      prices={len(normalized['prices'])}")
    for p in normalized['prices']:
        print(f"        role={p['role']} amount_minor={p['amount_minor']} cur={p['currency']}")
    print(f"      availability={normalized['availability']}")
    media_n = normalized.get('media') or {}
    print(f"      media items={len(media_n.get('items', []))}")
    print(f"      brand={normalized.get('brand')}")
    print(f"      model={normalized.get('model')}")
    print(f"      category_path={normalized.get('category_path')}")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("STEP 11: Запись JSON в test-intake-e2e/")
print("=" * 70)
try:
    OUT = Path(__file__).parent / "test-intake-e2e"
    OUT.mkdir(exist_ok=True)
    fname = f"jarvis-eye-farpost-{LID}.json"
    (OUT / fname).write_text(json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  OK: {fname} ({(OUT/fname).stat().st_size} bytes)")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("ALL STEPS PASSED")
print("=" * 70)
sys.exit(0)

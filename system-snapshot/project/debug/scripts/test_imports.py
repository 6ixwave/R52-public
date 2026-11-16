"""Проверка импортов и реестров сборщиков."""
import sys
sys.path.insert(0, ".")

print("=== Import test: r52.collectors ===")
try:
    from r52.collectors import (
        BaseCollector, ProxyPool,
        WBCollector, OzonCollector, AvitoCollector,
        FarpostCollector, AliExpressCollector, AliExpressRUCollector,
        run_collector_subprocess, COLLECTOR_REGISTRY,
    )
    print("  collectors __init__: OK")
    print(f"  Registry keys: {list(COLLECTOR_REGISTRY.keys())}")
    for k, (cls, title) in COLLECTOR_REGISTRY.items():
        print(f"    {k}: {cls.__name__} / {title}")
except Exception as e:
    print(f"  collectors __init__: FAIL - {e}")
    import traceback; traceback.print_exc()

print("\n=== Import test: r52.sources ===")
try:
    from r52.sources import SOURCE_KEYS, collector_for, collectors_status, run_source
    print(f"  sources.py: OK, keys={SOURCE_KEYS}")
    status = collectors_status()
    for s in status:
        print(f"    {s['source']}: {s['collector']} ({s['title']})")
    # Check if ALIEXPRESS_RU is registered
    cls_ru = collector_for("ALIEXPRESS_RU")
    print(f"  ALIEXPRESS_RU in sources: {'YES' if cls_ru else 'NO'}")
except Exception as e:
    print(f"  sources.py: FAIL - {e}")
    import traceback; traceback.print_exc()

print("\n=== Import test: runner mapping ===")
try:
    from r52.collectors.runner import _get_collector_class
    for src in ["WB", "OZON", "AVITO", "FARPOST", "ALIEXPRESS", "ALIEXPRESS_RU"]:
        cls = _get_collector_class(src)
        print(f"  runner {src}: {cls.__name__}")
except Exception as e:
    print(f"  runner mapping: FAIL - {e}")
    import traceback; traceback.print_exc()

print("\n=== Import test: intake ===")
try:
    from r52.intake.eye import Eye
    print("  intake.eye: OK")
except Exception as e:
    print(f"  intake.eye: FAIL - {e}")

try:
    from r52.intake.folder import FolderWatcher
    print("  intake.folder: OK")
except Exception as e:
    print(f"  intake.folder: FAIL - {e}")

print("\n=== Import test: core modules ===")
try:
    from r52.config import Config
    print("  config: OK")
except Exception as e:
    print(f"  config: FAIL - {e}")

try:
    from r52.service import Service
    print("  service: OK")
except Exception as e:
    print(f"  service: FAIL - {e}")

print("\n=== Done ===")

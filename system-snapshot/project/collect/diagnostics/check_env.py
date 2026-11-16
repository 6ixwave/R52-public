"""Быстрая проверка окружения Python."""
import sys
import platform

print(f"Python: {sys.version}")
print(f"Platform: {platform.platform()}")
print(f"Executable: {sys.executable}")

# Основные модули
mods = ["requests", "urllib3", "json", "re", "hashlib", "base64",
        "concurrent.futures", "sqlite3", "subprocess", "pathlib"]
for m in mods:
    try:
        mod = __import__(m)
        ver = getattr(mod, "__version__", "")
        print(f"  [OK] {m} {ver}")
    except ImportError as e:
        print(f"  [MISS] {m}: {e}")

# curl_cffi
try:
    from curl_cffi import requests as cffi_req
    print(f"  [OK] curl_cffi {cffi_req.__version__ if hasattr(cffi_req,'__version__') else 'installed'}")
    # Проверка impersonate
    s = cffi_req.Session(impersonate="chrome")
    print(f"  [OK] curl_cffi Session(impersonate='chrome') created")
    s.close()
except Exception as e:
    print(f"  [ERR] curl_cffi: {e}")

# bs4 / lxml
for m in ["bs4", "lxml", "html5lib"]:
    try:
        __import__(m)
        print(f"  [OK] {m}")
    except ImportError:
        print(f"  [MISS] {m}")

print("\nDone.")

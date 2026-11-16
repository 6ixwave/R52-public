"""R-52: локальный помощник для наблюдения за товарами, ценами и задачами."""

import sys as _sys

if _sys.version_info < (3, 11):
    raise SystemExit(f"R-52 требует Python 3.11 или новее, сейчас {_sys.version.split()[0]}")

__version__ = "2.12.0"
SERVICE_ID = "r52-core"

"""Диагностика: что реально в рендеренном HTML WB search от PwFetcher.
Сохраняет HTML и прогоняет набор паттернов, чтобы найти, где лежат ID товаров."""
import sys, time, re, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")

import r52.collectors.wb as wbmod

WbCollector = None
for n in dir(wbmod):
    if n.endswith("Collector") and n != "BaseCollector":
        WbCollector = getattr(wbmod, n)
        break
if WbCollector is None:
    print("WbCollector class not found"); sys.exit(1)

home = Path(r"C:\Users\aleks\AppData\Local\R52")
out = home / "logs" / "wb_pw_diag.html"

col = WbCollector(data_dir=home, use_proxies=True)
col._warmup()
pw = col._get_pw()
if not pw:
    print("PW is None (proxy/browser fail)"); sys.exit(1)

url = "https://www.wildberries.ru/catalog/0/ru/?search=видеокарты&page=1"
t0 = time.monotonic()
res = pw.get(url, extra_wait_ms=12000)
elapsed = time.monotonic() - t0

html = res.html if (res and hasattr(res, "html")) else ""
status = getattr(res, "status", None) if res else None
print(f"STATUS={status} BYTES={len(html)} TIME={elapsed:.1f}s")

out.write_text(html, encoding="utf-8")
print(f"SAVED {out}")

pats = {
    "detail_aspx":      r'/catalog/(\d+)/detail\.aspx',
    "catalog_id":       r'/catalog/(\d{6,})/',
    "data_nm":          r'data-(?:nm-?id|id|product-?id|nm)=["\']?(\d{6,})',
    "aria_label":       r'aria-label=["\']([^"\']{10,200})["\']',
    "next_data":        r'__NEXT_DATA__',
    "initial_state":    r'__INITIAL_STATE__',
    "window_state":     r'window\.__state',
    "json_nm_id":       r'"nm_id"\s*:\s*(\d+)',
    "json_id":          r'"id"\s*:\s*(\d{6,})',
    "json_product":     r'"product"\s*:\s*\{',
    "img_wb":           r'(?:src|data-src)=["\'][^"\']*(?:wb\.ru|wildberries\.ru)/[^"\']+\.(?:jpg|jpeg|png|webp)',
}
for name, pat in pats.items():
    ms = re.findall(pat, html, re.I)
    uniq = list(dict.fromkeys(ms))
    sample = [str(x)[:60] for x in uniq[:5]]
    print(f"PAT {name:16s} total={len(ms):5d} uniq={len(uniq):5d} sample={sample}")

print("\n--- HEAD 2000 ---")
print(html[:2000])
print("\n--- TAIL 500 ---")
print(html[-500:])

try:
    col.close()
except Exception:
    pass
print("DONE")

"""Инспектор dump-HTML: какие реально товарные ссылки/JSON есть у WB и OZON."""
import re
import pathlib

BASE = pathlib.Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")


def show(label, ms, n=4, width=150):
    print(f"  {label}: {len(ms)}")
    for x in ms[:n]:
        if isinstance(x, re.Match):
            x = x.group(0)
        print("    ", repr(str(x))[:width])


def inspect(name):
    p = BASE / name
    if not p.exists():
        print("===", name, "MISSING")
        return
    html = p.read_text(encoding="utf-8", errors="replace")
    print("===", name, len(html), "B", flush=True)

    if name.startswith("_diag_wb"):
        show("href detail.aspx", re.findall(r'href=["\'][^"\']*detail\.aspx[^"\']*["\']', html, re.I))
        show("catalog/num", re.findall(r'/catalog/\d{5,}/detail\.aspx', html))
        show("nm= param", re.findall(r'nm=\d+', html)[:8])
        show("data-nm", re.findall(r'data-nm=["\']\d+["\']', html))
        show("wbbasket img", re.findall(r'https://[^"\']*wbbasket\.ru/[^"\']{0,60}', html)[:4])
        for m in re.finditer(r'window\.__[A-Z_]+', html):
            show("window.__ state", [m.group(0)], 6)
        for key in ("__NEXT_DATA__", "__INITIAL_STATE__", "state={", "props="):
            i = html.find(key)
            print(f"  find {key!r}: idx={i}")
            if i >= 0:
                print("     ctx:", repr(html[i:i+120]))
    else:
        show("/product/", re.findall(r'/product/\d{6,}', html))
        show("data-tid", re.findall(r'data-tid=["\']\d+["\']', html))
        show("tileId", re.findall(r'"tileId"\s*:\s*"?(\d+)', html))
        show("productId", re.findall(r'"productId"\s*:\s*"?(\d+)', html))
        show("itemId", re.findall(r'"itemId"\s*:\s*"?(\d+)', html))
        show("sku", re.findall(r'"sku"\s*:\s*"?(\d+)', html))
        show("data-widget", re.findall(r'data-widget=["\'][^"\']+["\']', html)[:6])
        show("href product", re.findall(r'href=["\'][^"\']*product[^"\']*["\']', html, re.I)[:4])
        for key in ("__NEXT_DATA__", "__INITIAL_STATE__", "window.__STATE", "data-widget", "tile", "ProductCard"):
            i = html.find(key)
            print(f"  find {key!r}: idx={i}")
            if i >= 0:
                print("     ctx:", repr(html[max(0, i-40):i+140]))
    print(flush=True)


for f in ["_diag_wb_p1.html", "_diag_ozon_p2.html", "_diag_ozon_p1.html"]:
    inspect(f)
print("done", flush=True)

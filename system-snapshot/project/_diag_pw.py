"""Direct Playwright (без прокси): dump маркеров + сохранение HTML.
Проверяет: (1) OZON search direct → tiles? (2) WB search direct → формат ссылок."""
import time
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
OUT = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")


def markers(html: str) -> dict:
    low = html[:15000].lower()
    return {
        "len": len(html),
        "product_links": len(re.findall(r"/product/\d{6,}", html)),
        "detail_aspx": len(re.findall(r"/catalog/\d+/detail\.aspx", html)),
        "data_tid": len(re.findall(r"data-tid", html)),
        "nm_param": len(re.findall(r"nm=\d+", html)),
        "next_data": "__NEXT_DATA__" in html,
        "init_state": "__INITIAL_STATE__" in html,
        "waf": sum(1 for w in ("captcha", "antibot", "challenge", "fab_chlg", "abt-", "puzzle") if w in low),
    }


def probe(pw, tag, url, wait_ms=9000):
    t0 = time.monotonic()
    try:
        with pw.new_context(user_agent=UA, locale="ru-RU",
                            viewport={"width": 1366, "height": 900}) as ctx:
            page = ctx.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=35000)
            time.sleep(wait_ms / 1000.0)
            # лёгкая прокрутка, чтобы дорендерить ленивые плитки
            try:
                page.mouse.wheel(0, 2000)
                time.sleep(1.5)
            except Exception:
                pass
            html = page.content()
            status = resp.status if resp else 0
            m = markers(html)
            print(f"{tag} -> HTTP {status} {m} ({time.monotonic()-t0:.1f}s)", flush=True)
            (OUT / f"_diag_{tag}.html").write_text(html, encoding="utf-8")
            return status, html
    except Exception as e:
        print(f"{tag} -> ERR {type(e).__name__}: {str(e)[:140]} ({time.monotonic()-t0:.1f}s)", flush=True)
        return None, ""


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=False,
                           args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
    print("browser started headless=False direct", flush=True)
    probe(b, "ozon_p1", "https://www.ozon.ru/search/?text=%D0%B2%D0%B8%D0%B4%D0%B5%D0%BE%D0%BA%D0%B0%D1%80%D1%82%D1%8B&from_global=true&page=1")
    probe(b, "ozon_p2", "https://www.ozon.ru/search/?text=%D0%B2%D0%B8%D0%B4%D0%B5%D0%BE%D0%BA%D0%B0%D1%80%D1%82%D1%8B&from_global=true&page=2")
    probe(b, "wb_p1", "https://www.wildberries.ru/catalog/0/ru/?search=%D0%B2%D0%B8%D0%B4%D0%B5%D0%BE%D0%BA%D0%B0%D1%80%D1%82%D1%8B&page=1")
    b.close()
print("done", flush=True)

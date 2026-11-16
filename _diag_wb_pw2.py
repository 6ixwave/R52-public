"""Диагностика WB search: Playwright напрямую.
1) Перехват XHR (search API, card.wb.ru)
2) Ожидание DOM-селекторов плиток
3) ДUMP DOM на разных этапах
4) Попробовать page.evaluate() для извлечения из React state
"""
import sys, time, re, json, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")

from playwright.sync_api import sync_playwright

home = Path(r"C:\Users\aleks\AppData\Local\R52")
out_dir = home / "logs"
out_dir.mkdir(parents=True, exist_ok=True)

# Прокси из пула
proxy_file = home / "data" / "proxies.txt"
proxies = []
if proxy_file.exists():
    proxies = [l.strip() for l in proxy_file.read_text().splitlines() if l.strip()]
proxy = proxies[0] if proxies else None
print(f"PROXY: {proxy}")

url = "https://www.wildberries.ru/catalog/0/ru/?search=видеокарты&page=1"

xhr_captured = []

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        proxy={"server": proxy} if proxy else None,
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="ru-RU",
        viewport={"width": 1920, "height": 1080},
    )
    page = ctx.new_page()

    # Перехват XHR
    def on_response(resp):
        try:
            u = resp.url
            if any(k in u for k in ["search", "card.wb.ru", "catalog.wb.ru", "splitter.wb.ru", "exactmatch"]):
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    body = resp.text()
                    xhr_captured.append({"url": u[:200], "status": resp.status, "len": len(body), "body_head": body[:500]})
                    print(f"XHR CAPTURED: {resp.status} {u[:120]} ({len(body)}B)")
        except Exception:
            pass

    page.on("response", on_response)

    print(f"\n--- NAVIGATE {url} ---")
    t0 = time.monotonic()
    resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    print(f"goto: status={resp.status if resp else '?'} time={time.monotonic()-t0:.1f}s")

    # Этап 1: сразу после DCL
    html1 = page.content()
    (out_dir / "wb_diag_stage1_dcl.html").write_text(html1, encoding="utf-8")
    print(f"STAGE1 DCL: {len(html1)}B")

    # Этап 2: networkidle
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception as e:
        print(f"networkidle timeout: {e}")
    html2 = page.content()
    (out_dir / "wb_diag_stage2_idle.html").write_text(html2, encoding="utf-8")
    print(f"STAGE2 IDLE: {len(html2)}B")

    # Этап 3: подождать 15с额外
    page.wait_for_timeout(15000)
    html3 = page.content()
    (out_dir / "wb_diag_stage3_15s.html").write_text(html3, encoding="utf-8")
    print(f"STAGE3 +15s: {len(html3)}B")

    # DOM-селекторы: попробуем разные
    selectors = [
        "a[href*='/catalog/']",
        "a[href*='detail.aspx']",
        "[data-testid*='product']",
        "[data-testid*='tile']",
        "[class*='product']",
        "[class*='tile']",
        "[class*='catalog']",
        "a[href*='/catalog/'] span",
        "[class*='c-main__product']",
        "div[class*='product-card']",
        "[data-nm-id]",
        "[data-id]",
    ]
    print("\n--- DOM SELECTORS (stage3) ---")
    for sel in selectors:
        try:
            els = page.query_selector_all(sel)
            if els:
                print(f"  {sel}: {len(els)} elements")
                if len(els) <= 5:
                    for el in els[:3]:
                        txt = (el.inner_text() or "")[:80]
                        href = el.get_attribute("href") or ""
                        print(f"    href={href[:80]} text={txt!r}")
        except Exception as e:
            print(f"  {sel}: ERR {e}")

    # Попробовать извлечь из React state
    print("\n--- REACT STATE ---")
    try:
        state = page.evaluate("""() => {
            // Try various global state containers
            const results = {};
            if (window.__NEXT_DATA__) results.next_data = JSON.stringify(window.__NEXT_DATA__).slice(0, 2000);
            if (window.__INITIAL_STATE__) results.initial_state = JSON.stringify(window.__INITIAL_STATE__).slice(0, 2000);
            if (window.__state) results.state = JSON.stringify(window.__state).slice(0, 2000);
            if (window.__APP_DATA__) results.app_data = JSON.stringify(window.__APP_DATA__).slice(0, 2000);
            // Try React fiber on root
            const root = document.getElementById('root') || document.getElementById('app') || document.querySelector('[data-reactroot]');
            if (root) {
                const key = Object.keys(root).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'));
                if (key) {
                    results.fiber_key = key;
                    results.fiber_head = JSON.stringify(root[key]).slice(0, 1000);
                }
            }
            results.title = document.title;
            results.body_len = document.body.innerHTML.length;
            results.a_count = document.querySelectorAll('a').length;
            results.img_count = document.querySelectorAll('img').length;
            return results;
        }""")
        for k, v in (state or {}).items():
            print(f"  {k}: {str(v)[:300]}")
    except Exception as e:
        print(f"  evaluate error: {e}")

    # Скролл и повторный сбор
    print("\n--- SCROLL ---")
    for i in range(3):
        page.evaluate(f"window.scrollBy(0, {800 * (i+1)})")
        page.wait_for_timeout(3000)
    html4 = page.content()
    (out_dir / "wb_diag_stage4_scroll.html").write_text(html4, encoding="utf-8")
    print(f"STAGE4 SCROLL: {len(html4)}B")

    # Fallback: извлечь все /catalog/\d+/ из финального HTML
    final_html = html4
    pats = {
        "detail_aspx": r'/catalog/(\d+)/detail\.aspx',
        "catalog_id": r'/catalog/(\d{6,})/',
        "data_nm": r'data-(?:nm-?id|id|product-?id|nm)=["\']?(\d{6,})',
        "json_nm_id": r'"nm_id"\s*:\s*(\d+)',
        "json_id": r'"id"\s*:\s*(\d{6,})',
        "img_wb": r'(?:src|data-src)=["\'][^"\']*(?:wb\.ru|wildberries\.ru)/[^"\']+\.(?:jpg|jpeg|png|webp)',
        "aria_label": r'aria-label=["\']([^"\']{10,200})["\']',
    }
    print("\n--- FINAL PATTERNS (stage4) ---")
    for name, pat in pats.items():
        ms = re.findall(pat, final_html, re.I)
        uniq = list(dict.fromkeys(ms))
        sample = [str(x)[:60] for x in uniq[:5]]
        print(f"PAT {name:16s} total={len(ms):5d} uniq={len(uniq):5d} sample={sample}")

    # XHR summary
    print(f"\n--- XHR CAPTURED: {len(xhr_captured)} ---")
    for x in xhr_captured[:10]:
        print(f"  {x['status']} {x['len']}B {x['url']}")
        print(f"    body: {x['body_head'][:200]}")

    (out_dir / "wb_diag_xhr.json").write_text(json.dumps(xhr_captured, ensure_ascii=False, indent=2), encoding="utf-8")

    browser.close()

print(f"\nDONE total_time={time.monotonic()-t0:.1f}s")

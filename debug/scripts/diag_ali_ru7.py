"""Диагностика AliExpress.ru click-captcha v7: сохранить картинки + улучшить сравнение."""
import json, logging, os, sys, time, io
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag_ali_ru7")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from r52.collectors.aliexpress import AliExpressCollector
from PIL import Image, ImageChops
import numpy as np

PID = "1005013496905897"
OUT_DIR = os.path.join(BASE, "diag_ali_ru7_imgs")
OUT_JSON = os.path.join(BASE, "diag_ali_ru7_result.json")

os.makedirs(OUT_DIR, exist_ok=True)


def save_png(data: bytes, path: str):
    with open(path, "wb") as f:
        f.write(data)


def compare_v2(q_bytes: bytes, grid_bytes: list) -> list:
    """Улучшенное сравнение: crop-center + multi-method."""
    q_img = Image.open(io.BytesIO(q_bytes)).convert("RGB")
    log.info("Question size: %dx%d", q_img.width, q_img.height)

    # Crop center to square (use min dimension)
    sq = min(q_img.width, q_img.height)
    left = (q_img.width - sq) // 2
    top = (q_img.height - sq) // 2
    q_sq = q_img.crop((left, top, left + sq, top + sq))

    # Resize to 64x64 for robustness
    target = (64, 64)
    q_small = q_sq.resize(target, Image.LANCZOS)
    q_gray = q_small.convert("L")
    q_arr = np.array(q_gray, dtype=np.float64)

    scores = []
    for i, gb in enumerate(grid_bytes):
        if not gb:
            scores.append((i, 1.0))
            continue
        g_img = Image.open(io.BytesIO(gb)).convert("RGB")
        g_small = g_img.resize(target, Image.LANCZOS)
        g_gray = g_small.convert("L")
        g_arr = np.array(g_gray, dtype=np.float64)

        # Method 1: Normalized cross-correlation (NCC)
        q_norm = (q_arr - q_arr.mean()) / (q_arr.std() + 1e-8)
        g_norm = (g_arr - g_arr.mean()) / (g_arr.std() + 1e-8)
        ncc = float(np.mean(q_norm * g_norm))
        ncc_score = 1.0 - max(0, min(1, ncc))  # higher = more similar

        # Method 2: Color histogram intersection
        q_hist = np.array(q_small.getdata(), dtype=np.float64)
        g_hist = np.array(g_small.getdata(), dtype=np.float64)
        # Binned histogram (32 bins per channel)
        q_h = np.histogram(q_hist, bins=32, range=(0, 256))[0].astype(float)
        g_h = np.histogram(g_hist, bins=32, range=(0, 256))[0].astype(float)
        q_h /= (q_h.sum() + 1e-8)
        g_h /= (g_h.sum() + 1e-8)
        hist_intersect = float(np.minimum(q_h, g_h).sum())

        # Method 3: Mean absolute difference (MAD)
        mad = float(np.mean(np.abs(q_arr - g_arr))) / 255.0

        # Combined score (lower = more similar)
        combined = 0.4 * ncc_score + 0.3 * (1.0 - hist_intersect) + 0.3 * mad
        scores.append((i, combined))
        log.info("  grid[%d]: ncc=%.3f hist=%.3f mad=%.3f combined=%.3f", i, ncc_score, hist_intersect, mad, combined)

    scores.sort(key=lambda x: x[1])
    log.info("Sorted: %s", [(i, round(s, 3)) for i, s in scores])

    # Pick top cells: those with combined < 0.35, or top-3 if none
    selected = [idx for idx, s in scores if s < 0.35]
    if not selected:
        selected = [scores[0][0], scores[1][0], scores[2][0]]
    if len(selected) > 4:
        selected = [idx for idx, _ in scores[:4]]
    return selected


def main():
    result = {"pid": PID, "ts": datetime.now(timezone.utc).isoformat(), "phase": "start", "log": []}

    def logline(msg):
        result["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        log.info(msg)

    logline("Starting v7 diagnostic...")
    col = AliExpressCollector(domain="ru")

    try:
        ok = col._ensure_browser()
        if not ok:
            result["phase"] = "browser_failed"
            return
        logline("Browser OK")

        col._warm_session()
        logline(f"Warm: {len(col._ctx.cookies())} cookies")

        url = col.build_url(PID)
        logline(f"URL: {url}")

        col._mtop_bodies = []
        resp = col._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        logline(f"HTTP {resp.status if resp else 0}")

        # Wait for captcha
        cap_found = False
        for tick in range(15):
            col._page.wait_for_timeout(1000)
            try:
                st = col._page.evaluate("""
                    () => {
                        const p = document.getElementById('baxia-punish');
                        if (p) {
                            const cls = p.className || '';
                            return {cap: cls.includes('captchaclick') ? 'click' : cls.includes('drag') ? 'drag' : 'other', body: document.body.innerHTML.length};
                        }
                        const h1 = document.querySelector('h1');
                        return {cap: 'none', h1: h1 ? h1.textContent.trim().substring(0,100) : null};
                    }
                """)
                logline(f"tick={tick}: {st}")
                if st.get("cap") == "click":
                    cap_found = True
                    break
                if st.get("cap") == "none" and st.get("h1"):
                    break
            except Exception:
                continue

        if not cap_found:
            result["phase"] = "no_captcha"
            logline("No click-captcha found, page may have loaded directly")
            # Try extraction anyway
            col._page.wait_for_timeout(3000)

        if cap_found:
            logline("Captcha found, extracting images...")
            col._page.wait_for_selector('#click-question-canvas', timeout=10000)
            col._page.wait_for_timeout(2000)

            # Save question
            q_el = col._page.query_selector('#click-question-canvas')
            q_bytes = q_el.screenshot()
            save_png(q_bytes, os.path.join(OUT_DIR, "question.png"))
            logline(f"Question saved: {len(q_bytes)} bytes")

            # Save grid cells
            grid_bytes = []
            for i in range(9):
                g_el = col._page.query_selector(f'#click-grid-{i}')
                if g_el:
                    gb = g_el.screenshot()
                    save_png(gb, os.path.join(OUT_DIR, f"grid_{i}.png"))
                    grid_bytes.append(gb)
                else:
                    grid_bytes.append(None)
                    logline(f"grid_{i}: NOT FOUND")
            logline(f"Grid saved: {sum(1 for g in grid_bytes if g)}/9 cells")

            # Compare
            logline("Running comparison v2...")
            selected = compare_v2(q_bytes, grid_bytes)
            logline(f"Selected: {selected}")

            # Click selected
            for idx in selected:
                g_el = col._page.query_selector(f'#click-grid-{idx}')
                if g_el:
                    box = g_el.bounding_box()
                    if box:
                        col._page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                    else:
                        g_el.click()
                    col._page.wait_for_timeout(300)

            col._page.wait_for_timeout(500)
            submit_el = col._page.query_selector('#click-submit')
            if submit_el:
                submit_el.click()
                logline("Submitted")

            # Wait result
            solved = False
            for i in range(15):
                col._page.wait_for_timeout(1000)
                try:
                    st = col._page.evaluate("""
                        () => {
                            const p = document.getElementById('baxia-punish');
                            if (p) {
                                const msg = document.querySelector('.error-msg');
                                return {onCap: true, msg: msg ? msg.textContent.trim() : ''};
                            }
                            const h1 = document.querySelector('h1');
                            const prices = [];
                            document.querySelectorAll('[data-pl="price"],[class*="pdp-price"],span[class*="price"]').forEach(el => {
                                const t = (el.textContent||'').trim();
                                if (t && /\\d/.test(t) && t.length < 50) prices.push(t);
                            });
                            return {onCap: false, h1: h1?h1.textContent.trim().substring(0,200):null, prices:[...new Set(prices)].slice(0,8)};
                        }
                    """)
                    logline(f"result tick={i}: {st}")
                    if not st.get("onCap") and (st.get("prices") or (st.get("h1") and len(st["h1"]) > 15)):
                        solved = True
                        break
                    if st.get("onCap") and "Неправильный" in st.get("msg", ""):
                        break
                except Exception:
                    continue

            result["solved"] = solved
            result["selected"] = selected

            # If solved, extract data
            if solved:
                col._page.wait_for_timeout(3000)
                dom = col._page.evaluate("""
                    () => {
                        const r = {};
                        const h1 = document.querySelector('h1');
                        r.title = h1 ? h1.textContent.trim() : null;
                        r.prices = [];
                        document.querySelectorAll('[data-pl="price"],[class*="price--current"],[class*="Price--current"],span[class*="price"]').forEach(el => {
                            const t = (el.textContent||'').trim();
                            if (t && /\\d/.test(t) && t.length < 60) r.prices.push(t);
                        });
                        r.prices = [...new Set(r.prices)].slice(0,10);
                        r.meta = {};
                        document.querySelectorAll('meta[property]').forEach(m => {
                            const p = m.getAttribute('property')||'';
                            if (p.includes('og:')) r.meta[p] = (m.getAttribute('content')||'').substring(0,200);
                        });
                        return r;
                    }
                """)
                result["dom"] = dom
                logline(f"DOM: title={dom.get('title','')[:60]} prices={dom.get('prices',[])}")

        result["phase"] = "done"

    except Exception as e:
        import traceback
        result["phase"] = f"error: {e}"
        result["traceback"] = traceback.format_exc()
        logline(f"ERROR: {e}")
    finally:
        col.close()

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logline(f"Saved: {OUT_JSON}")


if __name__ == "__main__":
    main()

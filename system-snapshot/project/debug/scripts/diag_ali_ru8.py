"""Диагностика AliExpress.ru click-captcha v8: object-crop + multi-method + robust submit."""
import json, logging, os, sys, time, io
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag_ali_ru8")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from r52.collectors.aliexpress import AliExpressCollector
from PIL import Image, ImageChops, ImageFilter
import numpy as np

PID = "1005013496905897"
OUT_DIR = os.path.join(BASE, "diag_ali_ru8_imgs")
OUT_JSON = os.path.join(BASE, "diag_ali_ru8_result.json")
os.makedirs(OUT_DIR, exist_ok=True)


def find_object_bbox(img: Image.Image, threshold: int = 40) -> tuple:
    """Найти bounding box объекта (не-фоновых пикселей) на изображении."""
    arr = np.array(img.convert("L"), dtype=np.float64)
    # Фон — самые светлые пиксели (>255-threshold)
    mask = arr < (255 - threshold)
    if not mask.any():
        # Если всё "фон" — берём центральную треть
        h, w = arr.shape
        return (w // 3, h // 3, 2 * w // 3, 2 * h // 3)
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    # Небольшой padding
    pad = 2
    rmin = max(0, rmin - pad)
    rmax = min(arr.shape[0] - 1, rmax + pad)
    cmin = max(0, cmin - pad)
    cmax = min(arr.shape[1] - 1, cmax + pad)
    return (cmin, rmin, cmax, rmax)


def crop_to_square(img: Image.Image, bbox: tuple) -> Image.Image:
    """Crop по bbox и pad до квадрата."""
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    cropped = img.crop((x1, y1, x2, y2))
    sq = max(w, h)
    canvas = Image.new("RGB", (sq, sq), (255, 255, 255))
    ox = (sq - w) // 2
    oy = (sq - h) // 2
    canvas.paste(cropped, (ox, oy))
    return canvas


def compare_v3(q_bytes: bytes, grid_bytes: list) -> tuple:
    """Улучшенное сравнение: object-crop + multi-method.
    Возвращает (selected_list, scores_dict)."""
    q_img = Image.open(io.BytesIO(q_bytes)).convert("RGB")
    log.info("Question: %dx%d", q_img.width, q_img.height)

    # 1. Найти объект в вопросе
    q_bbox = find_object_bbox(q_img, threshold=50)
    log.info("Question object bbox: %s (w=%d h=%d)", q_bbox, q_bbox[2]-q_bbox[0], q_bbox[3]-q_bbox[1])

    # 2. Crop + square
    q_obj = crop_to_square(q_img, q_bbox)
    q_obj.save(os.path.join(OUT_DIR, "q_object.png"))

    # 3. Resize к 96x96
    target = (96, 96)
    q_small = q_obj.resize(target, Image.LANCZOS)
    q_gray = np.array(q_small.convert("L"), dtype=np.float64)

    scores = {}
    for i, gb in enumerate(grid_bytes):
        if not gb:
            scores[i] = 1.0
            continue
        g_img = Image.open(io.BytesIO(gb)).convert("RGB")
        g_small = g_img.resize(target, Image.LANCZOS)
        g_gray = np.array(g_small.convert("L"), dtype=np.float64)

        # Method A: NCC (normalized cross-correlation)
        qn = (q_gray - q_gray.mean()) / (q_gray.std() + 1e-8)
        gn = (g_gray - g_gray.mean()) / (g_gray.std() + 1e-8)
        ncc = float(np.mean(qn * gn))
        ncc_score = 1.0 - max(0.0, min(1.0, ncc))

        # Method B: Edge similarity (Sobel-like via simple gradient)
        q_edges = np.abs(np.diff(q_gray, axis=1)).mean() + np.abs(np.diff(q_gray, axis=0)).mean()
        g_edges = np.abs(np.diff(g_gray, axis=1)).mean() + np.abs(np.diff(g_gray, axis=0)).mean()
        edge_diff = abs(q_edges - g_edges) / (max(q_edges, g_edges) + 1e-8)

        # Method C: Color histogram (32 bins, RGB)
        q_rgb = np.array(q_small, dtype=np.float64).reshape(-1, 3)
        g_rgb = np.array(g_small, dtype=np.float64).reshape(-1, 3)
        q_h = np.zeros(96)
        g_h = np.zeros(96)
        for c in range(3):
            h, _ = np.histogram(q_rgb[:, c], bins=32, range=(0, 256))
            q_h[c*32:(c+1)*32] = h
            h, _ = np.histogram(g_rgb[:, c], bins=32, range=(0, 256))
            g_h[c*32:(c+1)*32] = h
        q_h /= (q_h.sum() + 1e-8)
        g_h /= (g_h.sum() + 1e-8)
        hist_inter = float(np.minimum(q_h, g_h).sum())

        # Method D: MAD (mean absolute difference)
        mad = float(np.mean(np.abs(q_gray - g_gray))) / 255.0

        # Combined: lower = more similar
        combined = 0.35 * ncc_score + 0.2 * edge_diff + 0.25 * (1.0 - hist_inter) + 0.2 * mad
        scores[i] = combined
        log.info("  grid[%d]: ncc=%.3f edge=%.3f hist=%.3f mad=%.3f => %.3f",
                 i, ncc_score, edge_diff, hist_inter, mad, combined)

    # Sort
    sorted_scores = sorted(scores.items(), key=lambda x: x[1])
    log.info("Sorted: %s", [(i, round(s, 4)) for i, s in sorted_scores])

    # Selection strategy:
    # - If best is clearly better than worst (ratio < 0.6), take top-1 or top-2
    # - Otherwise take top-3
    best_s = sorted_scores[0][1]
    worst_s = sorted_scores[-1][1]
    ratio = best_s / (worst_s + 1e-8)

    if ratio < 0.55:
        selected = [sorted_scores[0][0]]
        log.info("Clear winner (ratio=%.2f), selecting 1: %s", ratio, selected)
    elif ratio < 0.75:
        selected = [sorted_scores[0][0], sorted_scores[1][0]]
        log.info("Moderate confidence (ratio=%.2f), selecting 2: %s", ratio, selected)
    else:
        selected = [sorted_scores[0][0], sorted_scores[1][0], sorted_scores[2][0]]
        log.info("Ambiguous (ratio=%.2f), selecting 3: %s", ratio, selected)

    return selected, scores


def main():
    result = {"pid": PID, "ts": datetime.now(timezone.utc).isoformat(), "phase": "start", "log": []}

    def logline(msg):
        result["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        log.info(msg)

    logline("Starting v8 diagnostic...")
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

        # Wait for captcha or direct load
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
            logline("No click-captcha, page loaded directly")
            col._page.wait_for_timeout(3000)

        if cap_found:
            logline("Captcha found, extracting...")
            col._page.wait_for_selector('#click-question-canvas', timeout=10000)
            col._page.wait_for_timeout(2000)

            # Save question
            q_el = col._page.query_selector('#click-question-canvas')
            q_bytes = q_el.screenshot()
            with open(os.path.join(OUT_DIR, "question.png"), "wb") as f:
                f.write(q_bytes)
            logline(f"Question: {len(q_bytes)}b")

            # Save grid
            grid_bytes = []
            for i in range(9):
                g_el = col._page.query_selector(f'#click-grid-{i}')
                if g_el:
                    gb = g_el.screenshot()
                    with open(os.path.join(OUT_DIR, f"grid_{i}.png"), "wb") as f:
                        f.write(gb)
                    grid_bytes.append(gb)
                else:
                    grid_bytes.append(None)
            logline(f"Grid: {sum(1 for g in grid_bytes if g)}/9")

            # Compare
            logline("Compare v3...")
            selected, scores = compare_v3(q_bytes, grid_bytes)
            logline(f"Selected: {selected}")
            result["selected"] = selected
            result["scores"] = {str(k): round(v, 4) for k, v in scores.items()}

            # Click selected cells (with slight randomization for human-likeness)
            import random
            for idx in selected:
                g_el = col._page.query_selector(f'#click-grid-{idx}')
                if g_el:
                    box = g_el.bounding_box()
                    if box:
                        # Click slightly off-center for realism
                        cx = box['x'] + box['width'] / 2 + random.uniform(-5, 5)
                        cy = box['y'] + box['height'] / 2 + random.uniform(-5, 5)
                        col._page.mouse.click(cx, cy)
                    else:
                        g_el.click()
                    col._page.wait_for_timeout(400)

            # Submit with proper wait
            col._page.wait_for_timeout(800)
            submit_el = col._page.query_selector('#click-submit')
            if submit_el:
                # Check if disabled
                disabled = submit_el.evaluate("el => el.disabled || el.classList.contains('disabled')")
                logline(f"Submit disabled={disabled}")
                if not disabled:
                    submit_el.click()
                    logline("Submitted")
                else:
                    # Force click via JS
                    submit_el.evaluate("el => el.click()")
                    logline("Submitted via JS")
            else:
                logline("Submit button NOT FOUND")
                # Try alternative selectors
                for sel in ['button[type="submit"]', '.click-submit', '#submit-btn']:
                    alt = col._page.query_selector(sel)
                    if alt:
                        alt.click()
                        logline(f"Submitted via alt: {sel}")
                        break

            # Wait for result (up to 20s)
            solved = False
            cap_gone = False
            for i in range(20):
                col._page.wait_for_timeout(1000)
                try:
                    st = col._page.evaluate("""
                        () => {
                            const p = document.getElementById('baxia-punish');
                            if (p) {
                                const msg = document.querySelector('.error-msg, .click-captcha-result .result-msg, [class*="error"], [class*="result"]');
                                const msgText = msg ? msg.textContent.trim() : '';
                                const visible = p.offsetParent !== null;
                                return {onCap: true, visible: visible, msg: msgText.substring(0,100)};
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
                    logline(f"result[{i}]: {st}")
                    if not st.get("onCap"):
                        cap_gone = True
                        if st.get("prices") or (st.get("h1") and len(st["h1"]) > 15):
                            solved = True
                            break
                    if st.get("onCap") and st.get("msg") and ("Неправильный" in st["msg"] or "error" in st["msg"].lower()):
                        logline("Wrong selection detected")
                        break
                    if st.get("onCap") and not st.get("visible", True) and i > 3:
                        cap_gone = True
                        break
                except Exception as e:
                    logline(f"result[{i}] eval error: {e}")
                    continue

            result["solved"] = solved
            result["cap_gone"] = cap_gone

            # If solved or cap gone, extract
            if solved or cap_gone:
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
                        r.jsonld = [];
                        document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                            try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
                        });
                        r.bodyLen = document.body.innerHTML.length;
                        return r;
                    }
                """)
                result["dom"] = dom
                logline(f"DOM: title={dom.get('title','')[:60]} prices={dom.get('prices',[])} jsonld={len(dom.get('jsonld',[]))}")

                # Save HTML for analysis
                html = col._page.content()
                with open(os.path.join(OUT_DIR, "page.html"), "w", encoding="utf-8") as f:
                    f.write(html)
                logline(f"HTML: {len(html)}b")

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

"""V12: Live click-captcha — исправленный цикл попыток + детекция смены картинок.
Стратегия: top-1 → top-2 → top-3 → все 9 (brute-force check)."""
import json, logging, os, sys, time, io, random, hashlib
from datetime import datetime, timezone
from PIL import Image, ImageFilter
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag_ali_ru12")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from r52.collectors.aliexpress import AliExpressCollector

PID = "1005013496905897"
OUT_DIR = os.path.join(BASE, "diag_ali_ru12_imgs")
OUT_JSON = os.path.join(BASE, "diag_ali_ru12_result.json")
os.makedirs(OUT_DIR, exist_ok=True)


# ─── V10 comparison (same as before, fixed) ───

def mask_object(img, border_px=8):
    arr = np.array(img, dtype=np.float64)
    h, w, _ = arr.shape
    corners = np.concatenate([
        arr[:border_px, :border_px].reshape(-1, 3),
        arr[:border_px, -border_px:].reshape(-1, 3),
        arr[-border_px:, :border_px].reshape(-1, 3),
        arr[-border_px:, -border_px:].reshape(-1, 3),
    ])
    bg = np.median(corners, axis=0)
    dist = np.sqrt(((arr - bg) ** 2).sum(axis=2))
    threshold = max(20.0, np.percentile(dist, 70))
    mask = dist > threshold
    return mask, bg, threshold


def extract_obj_crop(img, mask, pad=3):
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return img
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    rmin = max(0, rmin - pad)
    rmax = min(img.height - 1, rmax + pad)
    cmin = max(0, cmin - pad)
    cmax = min(img.width - 1, cmax + pad)
    return img.crop((cmin, rmin, cmax + 1, rmax + 1))


def shape_descriptor(gray):
    h, w = gray.shape
    g = gray.astype(np.float64) / 255.0
    binary = (g > g.mean()).astype(np.float64)
    fill = binary.sum() / (h * w + 1e-8)
    aspect = w / (h + 1e-8)
    cy, cx = h / 2.0, w / 2.0
    ys, xs = np.mgrid[0:h, 0:w]
    angles = np.arctan2(ys - cy, xs - cx)
    sectors = ((angles + np.pi) / (2 * np.pi) * 8).astype(int) % 8
    radial = np.zeros(8)
    for s in range(8):
        radial[s] = binary[sectors == s].sum() / (binary.sum() + 1e-8)
    gx = np.abs(np.diff(g, axis=1)).mean()
    gy = np.abs(np.diff(g, axis=0)).mean()
    edge = (gx + gy) / 2
    return np.array([fill, aspect, edge] + list(radial))


def hu_moments(gray):
    g = gray.astype(np.float64) / 255.0
    thresh = g.mean()
    binary = (g > thresh).astype(np.float64)
    h, w = binary.shape
    cy, cx = h / 2.0, w / 2.0
    moments = np.zeros(7)
    for j in range(4):
        for i in range(4 - j):
            if i + j == 0:
                continue
            mu = np.sum(binary * ((np.arange(w) - cx) ** j) * ((np.arange(h) - cy) ** i))
            mu /= (h * w)
            idx = min(i * 3 + j, 6)
            moments[idx] = mu
    m11 = abs(moments[0] + 1e-10)
    for k in range(7):
        moments[k] = abs(moments[k]) / (m11 ** (k + 1) + 1e-10)
    return moments


def compare_v10(q_bytes, grid_bytes):
    """V10: fine sliding + object mask + shape. Returns (final_scores, grid_info)."""
    q_img = Image.open(io.BytesIO(q_bytes)).convert("RGB")
    w, h = q_img.size
    sq = min(w, h)
    step = 8
    n_windows = (w - sq) // step + 1
    windows = list(range(0, w - sq + 1, step))
    if windows and windows[-1] != w - sq:
        windows.append(w - sq)
    log.info("Q: %dx%d, %d windows", w, h, len(windows))

    grid_info = []
    for i, gb in enumerate(grid_bytes):
        if not gb:
            grid_info.append(None)
            continue
        g_img = Image.open(io.BytesIO(gb)).convert("RGB")
        mask, bg, thr = mask_object(g_img)
        obj_crop = extract_obj_crop(g_img, mask)
        obj_small = obj_crop.resize((64, 64), Image.LANCZOS)
        obj_gray = np.array(obj_small.convert("L"), dtype=np.float64)
        desc = shape_descriptor(obj_gray)
        hu = hu_moments(obj_gray)
        grid_info.append({"crop": obj_crop, "small": obj_small, "gray": obj_gray, "desc": desc, "hu": hu})
        obj_crop.save(os.path.join(OUT_DIR, f"g{i}_obj.png"))

    scores = {i: [] for i in range(9)}
    for wi, wx in enumerate(windows):
        w_crop = q_img.crop((wx, 0, wx + sq, h))
        w_small = w_crop.resize((64, 64), Image.LANCZOS)
        w_gray = np.array(w_small.convert("L"), dtype=np.float64)
        w_desc = shape_descriptor(w_gray)
        w_hu = hu_moments(w_gray)

        for i, gi in enumerate(grid_info):
            if gi is None:
                scores[i].append(1.0)
                continue
            g_arr = gi["gray"]
            qn = (w_gray - w_gray.mean()) / (w_gray.std() + 1e-8)
            gn = (g_arr - g_arr.mean()) / (g_arr.std() + 1e-8)
            ncc = float(np.mean(qn * gn))
            mad = float(np.mean(np.abs(w_gray - g_arr))) / 255.0
            desc_dist = float(np.linalg.norm(w_desc - gi["desc"]) / (np.linalg.norm(w_desc) + np.linalg.norm(gi["desc"]) + 1e-8))
            hu_dist = float(np.linalg.norm(w_hu - gi["hu"]) / (np.linalg.norm(w_hu) + np.linalg.norm(gi["hu"]) + 1e-8))
            score = 0.30 * (1.0 - max(0, min(1, ncc))) + 0.25 * mad + 0.25 * desc_dist + 0.20 * hu_dist
            scores[i].append(score)

    min_scores = {i: min(v) for i, v in scores.items()}
    mean_scores = {i: float(np.mean(v)) for i, v in scores.items()}
    final = {i: 0.6 * min_scores[i] + 0.4 * mean_scores[i] for i in range(9)}
    return final, grid_info


def hash_images(grid_bytes):
    """Fingerprint the grid to detect if images changed after submit."""
    h = hashlib.md5()
    for gb in grid_bytes:
        if gb:
            h.update(gb)
    return h.hexdigest()


def extract_captcha_images(page, out_dir, prefix=""):
    """Extract question + 9 grid images. Returns (q_bytes, grid_bytes)."""
    page.wait_for_selector('#click-question-canvas', timeout=10000)
    page.wait_for_timeout(1500)
    q_el = page.query_selector('#click-question-canvas')
    q_bytes = q_el.screenshot() if q_el else b''
    if q_bytes:
        with open(os.path.join(out_dir, f"{prefix}question.png"), "wb") as f:
            f.write(q_bytes)
    grid_bytes = []
    for i in range(9):
        g_el = page.query_selector(f'#click-grid-{i}')
        if g_el:
            gb = g_el.screenshot()
            with open(os.path.join(out_dir, f"{prefix}grid_{i}.png"), "wb") as f:
                f.write(gb)
            grid_bytes.append(gb)
        else:
            grid_bytes.append(None)
    return q_bytes, grid_bytes


def check_page_state(page):
    """Check if we're still on CAPTCHA or got through. Returns dict."""
    return page.evaluate("""
        () => {
            const p = document.getElementById('baxia-punish');
            if (p) {
                const cls = p.className || '';
                const isClick = cls.includes('captchaclick');
                const msg = document.querySelector('.error-msg, .click-captcha-result .result-msg, [class*="error"], [class*="result"]');
                const msgText = msg ? msg.textContent.trim() : '';
                return {onCap: true, isClick: isClick, msg: msgText.substring(0,120), body: document.body.innerHTML.length};
            }
            const h1 = document.querySelector('h1');
            const prices = [];
            document.querySelectorAll('[data-pl="price"],[class*="pdp-price"],span[class*="price"]').forEach(el => {
                const t = (el.textContent||'').trim();
                if (t && /\\d/.test(t) && t.length < 50) prices.push(t);
            });
            return {onCap: false, h1: h1?h1.textContent.trim().substring(0,200):null, prices:[...new Set(prices)].slice(0,8), body: document.body.innerHTML.length};
        }
    """)


def click_cells(page, indices):
    """Click specified grid cells with human-like jitter."""
    for idx in indices:
        g_el = page.query_selector(f'#click-grid-{idx}')
        if g_el:
            box = g_el.bounding_box()
            if box:
                cx = box['x'] + box['width'] / 2 + random.uniform(-4, 4)
                cy = box['y'] + box['height'] / 2 + random.uniform(-4, 4)
                page.mouse.click(cx, cy)
            else:
                g_el.click()
        page.wait_for_timeout(random.randint(300, 600))


def submit_captcha(page):
    """Click submit button. Returns True if found and clicked."""
    page.wait_for_timeout(500)
    submit_el = page.query_selector('#click-submit')
    if submit_el:
        submit_el.click()
        return True
    # Try alternative selectors
    for sel in ['button[type="submit"]', '.click-captcha-submit', '[class*="submit"]']:
        el = page.query_selector(sel)
        if el:
            el.click()
            return True
    return False


def wait_for_result(page, timeout_ticks=15, tick_ms=1000):
    """Wait for CAPTCHA result. Returns (status, state_dict).
    status: 'success' | 'wrong' | 'timeout' | 'new_captcha'
    """
    prev_body = None
    for i in range(timeout_ticks):
        page.wait_for_timeout(tick_ms)
        try:
            st = check_page_state(page)
        except Exception:
            continue
        if not st.get("onCap"):
            if st.get("prices") or (st.get("h1") and len(st["h1"]) > 15):
                return "success", st
            # Might be intermediate page
            if st.get("body", 0) < 50000:
                return "success", st
            continue
        # Still on CAPTCHA
        msg = st.get("msg", "")
        if "неправильн" in msg.lower() or "wrong" in msg.lower() or "error" in msg.lower():
            return "wrong", st
        # Detect body size change (new captcha loaded)
        if prev_body is not None and abs(st.get("body", 0) - prev_body) > 5000:
            return "new_captcha", st
        prev_body = st.get("body", 0)
    return "timeout", st


def main():
    result = {"pid": PID, "ts": datetime.now(timezone.utc).isoformat(), "phase": "start", "log": [], "attempts": []}

    def logline(msg):
        result["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        log.info(msg)

    logline("V12 live test starting...")
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
        resp = col._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        logline(f"HTTP {resp.status if resp else 0}")

        # Wait for captcha or direct load
        cap_found = False
        direct_loaded = False
        for tick in range(15):
            col._page.wait_for_timeout(1000)
            try:
                st = check_page_state(col._page)
                logline(f"tick={tick}: {st}")
                if st.get("onCap") and st.get("isClick"):
                    cap_found = True
                    break
                if not st.get("onCap") and (st.get("h1") or st.get("prices")):
                    direct_loaded = True
                    break
            except Exception:
                continue

        if direct_loaded:
            logline("Direct load! No captcha.")
            result["phase"] = "direct_load"
            result["solved"] = True
            return

        if not cap_found:
            result["phase"] = "no_captcha_detected"
            return

        # Extract initial images
        logline("Captcha found, extracting images...")
        q_bytes, grid_bytes = extract_captcha_images(col._page, OUT_DIR, "")
        initial_hash = hash_images(grid_bytes)
        logline(f"Images: Q={len(q_bytes)}b, Grid={sum(1 for g in grid_bytes if g)}/9, hash={initial_hash[:8]}")

        # Compute scores
        logline("Running V10 comparison...")
        final_scores, grid_info = compare_v10(q_bytes, grid_bytes)
        sorted_f = sorted(final_scores.items(), key=lambda x: x[1])
        logline(f"Scores: {[(i, round(s, 3)) for i, s in sorted_f]}")
        result["scores"] = {str(k): round(v, 4) for k, v in final_scores.items()}

        # Build attempt strategy: top-1, top-2, top-3, all-9
        top = [x[0] for x in sorted_f]
        attempts = [
            [top[0]],
            [top[0], top[1]],
            [top[0], top[1], top[2]],
            list(range(9)),  # brute force
        ]

        solved = False
        for attempt_idx, selected in enumerate(attempts):
            logline(f"=== Attempt {attempt_idx + 1}: clicking {selected} ===")
            result["attempts"].append({"idx": attempt_idx, "cells": selected, "status": "pending"})

            # Click
            click_cells(col._page, selected)

            # Submit
            submitted = submit_captcha(col._page)
            logline(f"  Submitted: {submitted}")
            if not submitted:
                logline("  Submit button not found, trying Enter key...")
                col._page.keyboard.press("Enter")
                col._page.wait_for_timeout(1000)

            # Wait for result
            status, st = wait_for_result(col._page, timeout_ticks=14, tick_ms=1000)
            logline(f"  Status: {status}, state: {st}")
            result["attempts"][attempt_idx]["status"] = status

            if status == "success":
                solved = True
                result["solved_attempt"] = attempt_idx + 1
                result["solved_cells"] = selected
                logline(f"  *** SOLVED on attempt {attempt_idx + 1}! ***")
                break

            if status in ("wrong", "new_captcha"):
                logline(f"  Wrong/new captcha, refreshing...")
                # Try refresh button
                refresh_el = col._page.query_selector('#click-refresh')
                if refresh_el:
                    refresh_el.click()
                else:
                    # Try clicking the question image to refresh
                    q_el = col._page.query_selector('#click-question-canvas')
                    if q_el:
                        q_el.click()
                col._page.wait_for_timeout(3000)
                # Re-extract
                q_bytes, grid_bytes = extract_captcha_images(col._page, OUT_DIR, f"a{attempt_idx + 1}_")
                new_hash = hash_images(grid_bytes)
                logline(f"  New hash: {new_hash[:8]} (changed: {new_hash != initial_hash})")
                # Re-compute
                final_scores, grid_info = compare_v10(q_bytes, grid_bytes)
                sorted_f = sorted(final_scores.items(), key=lambda x: x[1])
                logline(f"  New scores: {[(i, round(s, 3)) for i, s in sorted_f[:5]]}")
                top = [x[0] for x in sorted_f]
                # Update remaining attempts
                for j in range(attempt_idx + 1, len(attempts)):
                    if j == 0:
                        attempts[j] = [top[0]]
                    elif j == 1:
                        attempts[j] = [top[0], top[1]]
                    elif j == 2:
                        attempts[j] = [top[0], top[1], top[2]]
                    else:
                        attempts[j] = list(range(9))
                initial_hash = new_hash

            elif status == "timeout":
                logline("  Timeout — trying next attempt (maybe need more clicks?)")
                # Check if images changed (silent refresh)
                q_bytes2, grid_bytes2 = extract_captcha_images(col._page, OUT_DIR, f"t{attempt_idx}_")
                new_hash = hash_images(grid_bytes2)
                if new_hash != initial_hash:
                    logline("  Images changed during timeout!")
                    initial_hash = new_hash
                    final_scores, grid_info = compare_v10(q_bytes2, grid_bytes2)
                    sorted_f = sorted(final_scores.items(), key=lambda x: x[1])
                    top = [x[0] for x in sorted_f]
                    for j in range(attempt_idx + 1, len(attempts)):
                        if j == 0:
                            attempts[j] = [top[0]]
                        elif j == 1:
                            attempts[j] = [top[0], top[1]]
                        elif j == 2:
                            attempts[j] = [top[0], top[1], top[2]]
                        else:
                            attempts[j] = list(range(9))

        result["solved"] = solved

        # If solved, extract full data
        if solved:
            col._page.wait_for_timeout(4000)
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
                    r.images = [];
                    document.querySelectorAll('img').forEach(img => {
                        const src = img.src || img.getAttribute('data-src') || '';
                        if (src && (src.includes('alicdn')||src.includes('aliexpress-media'))
                            && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
                            && !src.includes('.svg') && !src.includes('.gif')) {
                            r.images.push(src);
                        }
                    });
                    r.images = [...new Set(r.images)].slice(0,15);
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
            logline(f"DOM: title={str(dom.get('title',''))[:80]}")
            logline(f"  prices={dom.get('prices',[])}")
            logline(f"  images={len(dom.get('images',[]))} jsonld={len(dom.get('jsonld',[]))}")

            html = col._page.content()
            with open(os.path.join(OUT_DIR, "page.html"), "w", encoding="utf-8") as f:
                f.write(html)
            logline(f"HTML saved: {len(html)}b")

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
    logline(f"PHASE={result['phase']} solved={result.get('solved')}")


if __name__ == "__main__":
    main()

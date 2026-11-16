"""V10: Тонкий слайд + маска фона + shape descriptors (Hu moments)."""
import json, logging, os, sys, io
from PIL import Image, ImageFilter
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag_ali_ru10")

BASE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(BASE, "diag_ali_ru8_imgs")
OUT_DIR = os.path.join(BASE, "diag_ali_ru10_out")
os.makedirs(OUT_DIR, exist_ok=True)


def load_q():
    return Image.open(os.path.join(IMG_DIR, "question.png")).convert("RGB")


def load_grid():
    cells = []
    for i in range(9):
        p = os.path.join(IMG_DIR, f"grid_{i}.png")
        cells.append(Image.open(p).convert("RGB") if os.path.exists(p) else None)
    return cells


def mask_object(img: Image.Image, border_px: int = 8) -> np.ndarray:
    """Маска объекта: пиксели, далёкие от медианного цвета по краям."""
    arr = np.array(img, dtype=np.float64)
    h, w, _ = arr.shape
    # Corner regions as background reference
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


def extract_obj_crop(img: Image.Image, mask: np.ndarray, pad: int = 3) -> Image.Image:
    """Crop по bounding box маски + padding."""
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


def hu_moments(gray: np.ndarray, size: int = 64) -> np.ndarray:
    """Упрощённые инварианты формы (7 Hu moments) через центральные моменты."""
    # Normalize to [0,1]
    g = gray.astype(np.float64) / 255.0
    # Binarize at midpoint
    thresh = g.mean()
    binary = (g > thresh).astype(np.float64)
    
    h, w = binary.shape
    cy, cx = h / 2.0, w / 2.0
    # Central moments up to order 3
    moments = np.zeros(7)
    for j in range(4):
        for i in range(4 - j):
            if i + j == 0:
                continue
            mu = np.sum(binary * ((np.arange(w) - cx) ** j) * ((np.arange(h) - cy) ** i))
            # Normalize by image area
            mu /= (h * w)
            # Store: index = i*3 + j (simplified)
            idx = min(i * 3 + j, 6)
            moments[idx] = mu
    # Normalize (rotation/scale invariance)
    m11 = abs(moments[0] + 1e-10)
    for k in range(7):
        moments[k] = abs(moments[k]) / (m11 ** (k + 1) + 1e-10)
    return moments


def shape_descriptor(gray: np.ndarray) -> np.ndarray:
    """Combined shape descriptor: aspect ratio, fill ratio, radial profile."""
    h, w = gray.shape
    g = gray.astype(np.float64) / 255.0
    binary = (g > g.mean()).astype(np.float64)
    
    fill = binary.sum() / (h * w + 1e-8)
    aspect = w / (h + 1e-8)
    
    # Radial profile (8 sectors)
    cy, cx = h / 2.0, w / 2.0
    ys, xs = np.mgrid[0:h, 0:w]
    angles = np.arctan2(ys - cy, xs - cx)
    sectors = ((angles + np.pi) / (2 * np.pi) * 8).astype(int) % 8
    radial = np.zeros(8)
    for s in range(8):
        radial[s] = binary[sectors == s].sum() / (binary.sum() + 1e-8)
    
    # Edge density (gradient magnitude)
    gx = np.abs(np.diff(g, axis=1)).mean()
    gy = np.abs(np.diff(g, axis=0)).mean()
    edge = (gx + gy) / 2
    
    return np.array([fill, aspect, edge] + list(radial))


def compare_v4(q_img: Image.Image, grid_cells: list) -> dict:
    """Multi-window + object mask + shape descriptors."""
    w, h = q_img.size
    sq = min(w, h)  # 49
    step = 10
    n_windows = (w - sq) // step + 1
    windows = [i * step for i in range(n_windows)]
    windows.append(w - sq)
    log.info("Q: %dx%d, %d windows (step=%d)", w, h, len(windows), step)

    # Pre-compute grid descriptors
    grid_info = []
    for i, gc in enumerate(grid_cells):
        if gc is None:
            grid_info.append(None)
            continue
        mask, bg, thr = mask_object(gc)
        obj_crop = extract_obj_crop(gc, mask)
        obj_small = obj_crop.resize((64, 64), Image.LANCZOS)
        obj_gray = np.array(obj_small.convert("L"))
        desc = shape_descriptor(obj_gray)
        hu = hu_moments(obj_gray)
        grid_info.append({"mask": mask, "crop": obj_crop, "desc": desc, "hu": hu, "bg": bg})
        # Save for debug
        obj_crop.save(os.path.join(OUT_DIR, f"g{i}_obj.png"))
        log.info("  grid[%d]: obj=%dx%d fill=%.2f bg=%s", i, obj_crop.width, obj_crop.height,
                 mask.mean(), bg.astype(int))

    # For each window, compare with each grid cell
    scores = {i: [] for i in range(9)}
    for wi, wx in enumerate(windows):
        w_crop = q_img.crop((wx, 0, wx + sq, h))
        w_small = w_crop.resize((64, 64), Image.LANCZOS)
        w_gray = np.array(w_small.convert("L"), dtype=np.float64)
        w_desc = shape_descriptor(w_gray)
        w_hu = hu_moments(w_gray)
        
        if wi == 0:
            w_crop.save(os.path.join(OUT_DIR, "q_win0.png"))
        if wi == len(windows) // 2:
            w_crop.save(os.path.join(OUT_DIR, "q_winmid.png"))
        if wi == len(windows) - 1:
            w_crop.save(os.path.join(OUT_DIR, "q_winlast.png"))

        for i, gi in enumerate(grid_info):
            if gi is None:
                scores[i].append(1.0)
                continue
            # NCC on grayscale
            qn = (w_gray - w_gray.mean()) / (w_gray.std() + 1e-8)
            gn = (np.array(gi["crop"].resize((64,64), Image.LANCZOS).convert("L"), dtype=np.float64) - 0).clip(0, 255)
            gn = (gn - gn.mean()) / (gn.std() + 1e-8)
            ncc = float(np.mean(qn * gn))
            
            # MAD
            g_arr = np.array(gi["crop"].resize((64,64), Image.LANCZOS).convert("L"), dtype=np.float64)
            mad = float(np.mean(np.abs(w_gray - g_arr))) / 255.0
            
            # Shape descriptor distance
            desc_dist = float(np.linalg.norm(w_desc - gi["desc"]) / (np.linalg.norm(w_desc) + np.linalg.norm(gi["desc"]) + 1e-8))
            
            # Hu moment distance
            hu_dist = float(np.linalg.norm(w_hu - gi["hu"]) / (np.linalg.norm(w_hu) + np.linalg.norm(gi["hu"]) + 1e-8))
            
            # Combined (lower = better match)
            score = 0.30 * (1.0 - max(0, min(1, ncc))) + 0.25 * mad + 0.25 * desc_dist + 0.20 * hu_dist
            scores[i].append(score)

    # For each cell: MIN score across windows (best alignment)
    min_scores = {i: min(v) for i, v in scores.items()}
    # Also: MEAN score (consistency)
    mean_scores = {i: np.mean(v) for i, v in scores.items()}
    
    # Final: 0.6*min + 0.4*mean (reward both good match AND consistency)
    final = {i: 0.6 * min_scores[i] + 0.4 * mean_scores[i] for i in range(9)}
    
    sorted_final = sorted(final.items(), key=lambda x: x[1])
    log.info("\n=== FINAL SCORES (0.6*min + 0.4*mean) ===")
    for i, s in sorted_final:
        log.info("  grid[%d]: min=%.4f mean=%.4f final=%.4f", i, min_scores[i], mean_scores[i], s)
    
    return final, min_scores, mean_scores, windows


def main():
    log.info("=== V10: Fine sliding + object mask + shape ===")
    q_img = load_q()
    grid = load_grid()
    
    final, mins, means, windows = compare_v4(q_img, grid)
    
    # Selection
    sorted_f = sorted(final.items(), key=lambda x: x[1])
    best = sorted_f[0][1]
    worst = sorted_f[-1][1]
    ratio = best / (worst + 1e-8)
    
    if ratio < 0.6:
        selected = [sorted_f[0][0], sorted_f[1][0]]
        log.info("Clear (ratio=%.2f): selecting 2 -> %s", ratio, selected)
    elif ratio < 0.8:
        selected = [sorted_f[0][0], sorted_f[1][0], sorted_f[2][0]]
        log.info("Moderate (ratio=%.2f): selecting 3 -> %s", ratio, selected)
    else:
        selected = [i for i, _ in sorted_f[:4]]
        log.info("Ambiguous (ratio=%.2f): selecting 4 -> %s", ratio, selected)
    
    log.info("SELECTED: %s", selected)
    
    report = {
        "windows": len(windows),
        "final": {str(k): round(v, 4) for k, v in final.items()},
        "min": {str(k): round(v, 4) for k, v in mins.items()},
        "mean": {str(k): round(v, 4) for k, v in means.items()},
        "ratio": round(ratio, 4),
        "selected": selected,
        "sorted": [(i, round(s, 4)) for i, s in sorted_f],
    }
    out = os.path.join(BASE, "diag_ali_ru10_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info("Report: %s", out)
    log.info("=== DONE ===")


if __name__ == "__main__":
    main()

"""V9: Анализ сохранённых картинок капчи (без браузера).
Цель: найти лучший способ сопоставления question-strip → grid-cells."""
import json, logging, os, sys, io
from PIL import Image, ImageChops, ImageFilter, ImageEnhance
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag_ali_ru9")

BASE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(BASE, "diag_ali_ru8_imgs")
OUT_DIR = os.path.join(BASE, "diag_ali_ru9_out")
os.makedirs(OUT_DIR, exist_ok=True)


def load_question():
    p = os.path.join(IMG_DIR, "question.png")
    img = Image.open(p).convert("RGB")
    log.info("Question: %dx%d", img.width, img.height)
    return img


def load_grid():
    cells = []
    for i in range(9):
        p = os.path.join(IMG_DIR, f"grid_{i}.png")
        if os.path.exists(p):
            cells.append(Image.open(p).convert("RGB"))
        else:
            cells.append(None)
    log.info("Grid: %s", [c.size if c else None for c in cells])
    return cells


def extract_object_v1(q_img: Image.Image) -> Image.Image:
    """Метод 1: центральный квадрат (min_dim x min_dim)."""
    w, h = q_img.size
    sq = min(w, h)
    left = (w - sq) // 2
    top = (h - sq) // 2
    crop = q_img.crop((left, top, left + sq, top + sq))
    crop.save(os.path.join(OUT_DIR, "obj_v1_center.png"))
    log.info("V1 center crop: %s", crop.size)
    return crop


def extract_object_v2(q_img: Image.Image) -> Image.Image:
    """Метод 2: adaptive threshold — найти самый "плотный" по краям участок."""
    w, h = q_img.size
    gray = np.array(q_img.convert("L"), dtype=np.float64)
    
    # Edge density per column (Sobel-like: abs diff of adjacent columns)
    col_edges = np.abs(np.diff(gray, axis=1)).mean(axis=0)
    # Smooth with window
    win = max(1, w // 10)
    kernel = np.ones(win) / win
    col_edges_smooth = np.convolve(col_edges, kernel, mode='same')
    
    # Find the column with highest edge density (object center)
    center_col = int(np.argmax(col_edges_smooth))
    log.info("V2: edge peak at col %d (val=%.1f), strip width=%d", center_col, col_edges_smooth[center_col], w)
    
    sq = min(w, h)
    left = max(0, min(center_col - sq // 2, w - sq))
    top = (h - sq) // 2
    crop = q_img.crop((left, top, left + sq, top + sq))
    crop.save(os.path.join(OUT_DIR, "obj_v2_edgepeak.png"))
    log.info("V2 edge-peak crop: %s at x=%d", crop.size, left)
    return crop


def extract_object_v3(q_img: Image.Image) -> Image.Image:
    """Метод 3: color saturation — объект обычно ярче/насыщеннее фона."""
    w, h = q_img.size
    arr = np.array(q_img, dtype=np.float64)
    # Saturation: max(R,G,B) - min(R,G,B)
    sat = arr.max(axis=2) - arr.min(axis=2)
    # Column-wise mean saturation
    col_sat = sat.mean(axis=0)
    win = max(1, w // 10)
    kernel = np.ones(win) / win
    col_sat_smooth = np.convolve(col_sat, kernel, mode='same')
    
    center_col = int(np.argmax(col_sat_smooth))
    log.info("V3: saturation peak at col %d (val=%.1f)", center_col, col_sat_smooth[center_col])
    
    sq = min(w, h)
    left = max(0, min(center_col - sq // 2, w - sq))
    top = (h - sq) // 2
    crop = q_img.crop((left, top, left + sq, top + sq))
    crop.save(os.path.join(OUT_DIR, "obj_v3_saturation.png"))
    return crop


def extract_object_v4(q_img: Image.Image) -> Image.Image:
    """Метод 4: median background subtraction.
    Фон = медианный цвет. Объект = пиксели, далёкие от фона."""
    w, h = q_img.size
    arr = np.array(q_img, dtype=np.float64)
    # Median color (background)
    bg = np.median(arr.reshape(-1, 3), axis=0)
    log.info("V4: bg color = %s", bg.astype(int))
    
    # Distance from bg per pixel
    dist = np.sqrt(((arr - bg) ** 2).sum(axis=2))
    # Column-wise mean distance
    col_dist = dist.mean(axis=0)
    win = max(1, w // 10)
    kernel = np.ones(win) / win
    col_dist_smooth = np.convolve(col_dist, kernel, mode='same')
    
    center_col = int(np.argmax(col_dist_smooth))
    log.info("V4: distance peak at col %d (val=%.1f)", center_col, col_dist_smooth[center_col])
    
    sq = min(w, h)
    left = max(0, min(center_col - sq // 2, w - sq))
    top = (h - sq) // 2
    crop = q_img.crop((left, top, left + sq, top + sq))
    crop.save(os.path.join(OUT_DIR, "obj_v4_bgdist.png"))
    return crop


def compare_crops(q_crops: list, grid_cells: list) -> dict:
    """Сравнить несколько вариантов кропа вопроса с grid-клетками.
    Возвращает {crop_name: {grid_idx: score}}."""
    target = (96, 96)
    results = {}
    
    for name, q_crop in q_crops:
        q_small = q_crop.resize(target, Image.LANCZOS)
        q_gray = np.array(q_small.convert("L"), dtype=np.float64)
        q_rgb = np.array(q_small, dtype=np.float64)
        
        scores = {}
        for i, g_cell in enumerate(grid_cells):
            if g_cell is None:
                scores[i] = 1.0
                continue
            g_small = g_cell.resize(target, Image.LANCZOS)
            g_gray = np.array(g_small.convert("L"), dtype=np.float64)
            g_rgb = np.array(g_small, dtype=np.float64)
            
            # NCC
            qn = (q_gray - q_gray.mean()) / (q_gray.std() + 1e-8)
            gn = (g_gray - g_gray.mean()) / (g_gray.std() + 1e-8)
            ncc = float(np.mean(qn * gn))
            
            # MAD (grayscale)
            mad = float(np.mean(np.abs(q_gray - g_gray))) / 255.0
            
            # RGB histogram intersection (32 bins per channel)
            q_h = np.zeros(96, dtype=float)
            g_h = np.zeros(96, dtype=float)
            for c in range(3):
                hq, _ = np.histogram(q_rgb[:, c], bins=32, range=(0, 256))
                hg, _ = np.histogram(g_rgb[:, c], bins=32, range=(0, 256))
                q_h[c*32:(c+1)*32] = hq
                g_h[c*32:(c+1)*32] = hg
            q_h /= (q_h.sum() + 1e-8)
            g_h /= (g_h.sum() + 1e-8)
            hist = float(np.minimum(q_h, g_h).sum())
            
            # Edge density similarity
            q_e = np.abs(np.diff(q_gray, axis=1)).mean() + np.abs(np.diff(q_gray, axis=0)).mean()
            g_e = np.abs(np.diff(g_gray, axis=1)).mean() + np.abs(np.diff(g_gray, axis=0)).mean()
            edge_sim = 1.0 - abs(q_e - g_e) / (max(q_e, g_e) + 1e-8)
            
            # Combined (lower = more similar)
            combined = 0.30 * (1.0 - max(0, min(1, ncc))) + 0.25 * mad + 0.25 * (1.0 - hist) + 0.20 * (1.0 - edge_sim)
            scores[i] = combined
        
        results[name] = scores
        sorted_s = sorted(scores.items(), key=lambda x: x[1])
        log.info("  %s: %s", name, [(i, round(s, 3)) for i, s in sorted_s[:5]])
    
    return results


def main():
    log.info("=== V9: Offline captcha image analysis ===")
    
    q_img = load_question()
    grid = load_grid()
    
    # Extract object using 4 methods
    crops = [
        ("v1_center", extract_object_v1(q_img)),
        ("v2_edgepeak", extract_object_v2(q_img)),
        ("v3_saturation", extract_object_v3(q_img)),
        ("v4_bgdist", extract_object_v4(q_img)),
    ]
    
    # Compare all
    log.info("Comparing 4 crop methods vs 9 grid cells...")
    results = compare_crops(crops, grid)
    
    # Aggregate: average rank across methods
    all_scores = {}
    for i in range(9):
        all_scores[i] = []
    for name, scores in results.items():
        for i, s in scores.items():
            all_scores[i].append(s)
    
    avg_scores = {i: np.mean(v) for i, v in all_scores.items()}
    sorted_avg = sorted(avg_scores.items(), key=lambda x: x[1])
    log.info("\n=== AGGREGATE (avg across 4 methods) ===")
    for i, s in sorted_avg:
        log.info("  grid[%d]: avg=%.4f  (per-method: %s)", i, s, 
                 [round(results[m][i], 3) for m in results])
    
    # Save report
    report = {
        "question_size": list(q_img.size),
        "grid_sizes": [list(c.size) if c else None for c in grid],
        "methods": {name: {str(k): round(v, 4) for k, v in scores.items()} for name, scores in results.items()},
        "aggregate": {str(k): round(v, 4) for k, v in sorted_avg},
        "top3": [i for i, _ in sorted_avg[:3]],
        "top5": [i for i, _ in sorted_avg[:5]],
    }
    out_path = os.path.join(BASE, "diag_ali_ru9_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info("Report: %s", out_path)
    
    # Save a composite image: question strip + 4 crops + best 3 grid cells
    best3 = [i for i, _ in sorted_avg[:3]]
    log.info("Best 3 cells: %s", best3)
    
    # Also: try "multi-window" approach — slide a 49x49 window across the strip
    log.info("\n=== Multi-window approach ===")
    w, h = q_img.size
    sq = min(w, h)  # 49
    n_windows = 5
    window_positions = [int(w * i / (n_windows - 1)) - sq // 2 for i in range(n_windows)]
    window_positions = [max(0, min(p, w - sq)) for p in window_positions]
    log.info("Windows at x: %s", window_positions)
    
    window_scores = {i: [] for i in range(9)}
    for wi, wx in enumerate(window_positions):
        w_crop = q_img.crop((wx, 0, wx + sq, h))
        w_small = w_crop.resize((96, 96), Image.LANCZOS)
        w_gray = np.array(w_small.convert("L"), dtype=np.float64)
        for i, g_cell in enumerate(grid):
            if g_cell is None:
                window_scores[i].append(1.0)
                continue
            g_small = g_cell.resize((96, 96), Image.LANCZOS)
            g_gray = np.array(g_small.convert("L"), dtype=np.float64)
            # NCC
            qn = (w_gray - w_gray.mean()) / (w_gray.std() + 1e-8)
            gn = (g_gray - g_gray.mean()) / (g_gray.std() + 1e-8)
            ncc = float(np.mean(qn * gn))
            # MAD
            mad = float(np.mean(np.abs(w_gray - g_gray))) / 255.0
            score = 0.5 * (1.0 - max(0, min(1, ncc))) + 0.5 * mad
            window_scores[i].append(score)
    
    # For each grid cell, take the MIN score across windows (best match)
    min_window_scores = {i: min(v) for i, v in window_scores.items()}
    sorted_mw = sorted(min_window_scores.items(), key=lambda x: x[1])
    log.info("Multi-window (min across 5 windows):")
    for i, s in sorted_mw:
        log.info("  grid[%d]: min=%.4f  windows=%s", i, s, [round(x, 3) for x in window_scores[i]])
    
    report["multi_window"] = {str(k): round(v, 4) for k, v in sorted_mw}
    report["multi_window_top3"] = [i for i, _ in sorted_mw[:3]]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    log.info("\n=== DONE ===")
    log.info("Best 3 (aggregate): %s", [i for i, _ in sorted_avg[:3]])
    log.info("Best 3 (multi-window): %s", [i for i, _ in sorted_mw[:3]])


if __name__ == "__main__":
    main()

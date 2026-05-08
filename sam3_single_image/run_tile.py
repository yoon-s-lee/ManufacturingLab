"""Run SAM 3 on overlapping tiles of a SEM image and stitch the results.

Why tile? SAM 3's input is fixed at 1008x1008. A 30-pixel-wide crack in a
640x960 image is a tiny fraction of the field of view after letterboxing —
right at the edge of what SAM's attention can latch onto. Tiling into smaller
sub-images and resizing each up to 1008 effectively gives every pixel ~3x more
attention tokens, which is the regime SAM was trained on.

All tunable parameters live in the CONFIG block below. Edit them in place,
then run:

    python sam3_single_image/run_tile.py

Outputs (under sam3_single_image/output/<image-stem>/tiled/):
    overlay.png        - input image with all stitched masks overlaid
    tile_grid.png      - diagnostic showing tile boundaries on the image
    masks.npz          - stacked binary masks, uint8 (N, H, W) at full image res
    scores.txt         - one confidence score per surviving mask
    boxes.txt          - one xyxy bounding box per mask, in full-image coords
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw
from sam3.model.sam3_image_processor import Sam3Processor
from sam3.model_builder import build_sam3_image_model

# ---------------------------------------------------------------------------
# CONFIG -- edit these to tune SAM 3 + tiling.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CONFIG = {
    # --- I/O ----------------------------------------------------------------
    "image_path": ROOT / "UW_SEM_Images_batch_1_no_infobar" / "8_1_1_lowmass_2.png",
    "output_dir": Path(__file__).parent / "output",  # "tiled" subdir auto-appended

    # --- Tiling -------------------------------------------------------------
    # Tile size in pixels (square). Smaller tiles = more zoom on each crack
    # but more SAM calls and more risk of losing context. 320 is a good start
    # for 640x960 SEMs; try 240 for finer cracks or 480 for fewer tiles.
    "tile_size": 320,

    # Pixel overlap between adjacent tiles. Cracks landing on a tile seam
    # would otherwise get cut in half — overlap ensures each crack is fully
    # contained in at least one tile. Should be >= longest expected crack
    # half-length (~half tile_size is generous).
    "tile_overlap": 96,

    # --- SAM 3 prompt -------------------------------------------------------
    "prompt": "crack",

    # --- SAM 3 inference knobs ---------------------------------------------
    "confidence_threshold": 0.35,
    "top_k_per_tile": 0,           # 0 = no per-tile cap; rely on threshold

    # SAM 3 checkpoint is locked to 1008. Don't change.
    "resolution": 1008,
    "device": "cpu",

    # --- Stitching ----------------------------------------------------------
    # After tiles run, the same crack may be detected by 2-4 overlapping
    # tiles. Greedy NMS by mask IoU keeps the highest-scoring detection per
    # cluster. Lower threshold = more aggressive deduplication.
    "nms_iou_threshold": 0.4,

    # Drop full-image masks smaller than this (pixels). Useful to filter
    # corner artifacts that pop up at tile edges.
    "min_mask_area": 20,

    # --- Overlay rendering --------------------------------------------------
    "overlay_alpha": 0.5,
    "color_seed": 0,
    "draw_boxes": True,

    # --- Tile grid diagnostic ----------------------------------------------
    "grid_color": (60, 220, 90),
}
# ---------------------------------------------------------------------------


def tile_origins_1d(image_size: int, tile: int, overlap: int) -> list[int]:
    """Origins along one axis. Last tile is clamped to img-tile so it fits."""
    if image_size <= tile:
        return [0]
    stride = max(1, tile - overlap)
    origins = list(range(0, image_size - tile + 1, stride))
    if origins[-1] != image_size - tile:
        origins.append(image_size - tile)
    return origins


def tile_origins(h: int, w: int, tile: int, overlap: int) -> list[tuple[int, int]]:
    ys = tile_origins_1d(h, tile, overlap)
    xs = tile_origins_1d(w, tile, overlap)
    return [(y, x) for y in ys for x in xs]


def boxes_overlap(b1: np.ndarray, b2: np.ndarray) -> bool:
    return (
        min(b1[2], b2[2]) > max(b1[0], b2[0])
        and min(b1[3], b2[3]) > max(b1[1], b2[1])
    )


def mask_nms(
    masks: np.ndarray, boxes: np.ndarray, scores: np.ndarray, iou_threshold: float,
) -> list[int]:
    """Greedy mask-IoU NMS. Returns indices to keep, in score-desc order."""
    if len(masks) == 0:
        return []
    order = np.argsort(scores)[::-1]
    areas = masks.reshape(len(masks), -1).sum(axis=1)
    keep: list[int] = []
    suppressed = np.zeros(len(masks), dtype=bool)
    for i in order:
        if suppressed[i]:
            continue
        keep.append(int(i))
        bi = boxes[i]
        for j in order:
            if j == i or suppressed[j]:
                continue
            if not boxes_overlap(bi, boxes[j]):
                continue
            inter = int(np.logical_and(masks[i], masks[j]).sum())
            if inter == 0:
                continue
            union = int(areas[i] + areas[j] - inter)
            if union > 0 and inter / union > iou_threshold:
                suppressed[j] = True
    return keep


def colorize_overlay(
    gray: np.ndarray, masks: np.ndarray, boxes: np.ndarray | None,
    alpha: float, seed: int, draw_boxes: bool,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rgb = np.stack([gray, gray, gray], axis=-1).astype(np.float32)
    colors = []
    for m in masks:
        color = rng.integers(64, 255, size=3).astype(np.float32)
        colors.append(color)
        sel = m.astype(bool)
        rgb[sel] = (1 - alpha) * rgb[sel] + alpha * color
    rgb = rgb.clip(0, 255).astype(np.uint8)

    if draw_boxes and boxes is not None:
        h, w = gray.shape
        for color, box in zip(colors, boxes, strict=True):
            x0, y0, x1, y1 = [int(round(v)) for v in box]
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w - 1, x1), min(h - 1, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            c = color.astype(np.uint8)
            rgb[y0, x0:x1 + 1] = c
            rgb[y1, x0:x1 + 1] = c
            rgb[y0:y1 + 1, x0] = c
            rgb[y0:y1 + 1, x1] = c
    return rgb


def render_tile_grid(
    gray: np.ndarray, origins: list[tuple[int, int]], tile: int,
    color: tuple[int, int, int],
) -> np.ndarray:
    img = Image.fromarray(np.stack([gray, gray, gray], axis=-1).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    h, w = gray.shape
    for i, (y, x) in enumerate(origins):
        x1 = min(w - 1, x + tile - 1)
        y1 = min(h - 1, y + tile - 1)
        draw.rectangle([x, y, x1, y1], outline=color, width=1)
        draw.text((x + 2, y + 2), str(i), fill=color)
    return np.asarray(img)


def clear_outputs(out_dir: Path) -> None:
    for filename in (
        "overlay.png", "tile_grid.png", "masks.npz", "scores.txt", "boxes.txt",
    ):
        path = out_dir / filename
        if path.exists():
            path.unlink()


def run_one_tile(
    processor: Sam3Processor, tile_pil: Image.Image, prompt: str,
    threshold: float, top_k: int | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    effective_threshold = -1.0 if top_k else threshold
    state = processor.set_image(tile_pil)
    processor.set_confidence_threshold(effective_threshold, state=state)
    out = processor.set_text_prompt(prompt=prompt, state=state)
    masks = out["masks"]
    boxes = out["boxes"]
    scores = out["scores"]

    if top_k:
        scores_t = scores if torch.is_tensor(scores) else torch.as_tensor(scores)
        order = torch.argsort(scores_t, descending=True)[:top_k]
        masks = masks[order]
        boxes = boxes[order]
        scores = scores_t[order]

    masks_np = masks.detach().cpu().numpy() if torch.is_tensor(masks) else np.asarray(masks)
    if masks_np.ndim == 4:
        masks_np = masks_np[:, 0]
    masks_np = (masks_np > 0).astype(np.uint8)
    boxes_np = boxes.detach().cpu().numpy() if torch.is_tensor(boxes) else np.asarray(boxes)
    scores_np = scores.detach().cpu().numpy() if torch.is_tensor(scores) else np.asarray(scores)
    return masks_np, boxes_np, scores_np


def main() -> int:
    cfg = CONFIG
    image_path: Path = cfg["image_path"]
    out_dir: Path = cfg["output_dir"] / image_path.stem / "tiled"
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_outputs(out_dir)

    pil_full = Image.open(image_path).convert("RGB")
    gray = np.asarray(Image.open(image_path).convert("L"))
    H, W = gray.shape
    print(f"image:  {image_path}  shape=({H}, {W})")
    print(f"output: {out_dir}")
    print(f"prompt: {cfg['prompt']!r}  threshold: {cfg['confidence_threshold']}  "
          f"top_k_per_tile: {cfg['top_k_per_tile']}  device: {cfg['device']}")

    origins = tile_origins(H, W, cfg["tile_size"], cfg["tile_overlap"])
    print(f"tiling: {len(origins)} tiles of {cfg['tile_size']}x{cfg['tile_size']}, "
          f"overlap={cfg['tile_overlap']}")

    Image.fromarray(render_tile_grid(
        gray, origins, cfg["tile_size"], cfg["grid_color"],
    )).save(out_dir / "tile_grid.png")

    print("loading SAM 3...")
    t0 = time.time()
    model = build_sam3_image_model().to(cfg["device"]).eval()
    processor = Sam3Processor(
        model,
        resolution=cfg["resolution"],
        device=cfg["device"],
        confidence_threshold=cfg["confidence_threshold"],
    )
    print(f"  done in {time.time() - t0:.1f}s")

    tile_size = cfg["tile_size"]
    top_k = cfg["top_k_per_tile"] or None

    all_masks: list[np.ndarray] = []
    all_boxes: list[np.ndarray] = []
    all_scores: list[float] = []

    t_total = time.time()
    for i, (y, x) in enumerate(origins, 1):
        t0 = time.time()
        crop = pil_full.crop((x, y, x + tile_size, y + tile_size))
        m_t, b_t, s_t = run_one_tile(
            processor, crop, cfg["prompt"],
            cfg["confidence_threshold"], top_k,
        )
        n_tile = len(m_t)

        for k in range(n_tile):
            full_m = np.zeros((H, W), dtype=np.uint8)
            full_m[y:y + tile_size, x:x + tile_size] = m_t[k]
            if int(full_m.sum()) < cfg["min_mask_area"]:
                continue
            full_b = np.array([
                b_t[k][0] + x, b_t[k][1] + y,
                b_t[k][2] + x, b_t[k][3] + y,
            ], dtype=np.float32)
            all_masks.append(full_m)
            all_boxes.append(full_b)
            all_scores.append(float(s_t[k]))

        print(f"[{i:>2d}/{len(origins)}] tile y={y:>3d} x={x:>3d}: "
              f"{n_tile} mask(s) ({time.time() - t0:.1f}s)")

    print(f"\nall tiles done in {time.time() - t_total:.1f}s; "
          f"{len(all_masks)} raw mask(s) before NMS")

    if not all_masks:
        Image.fromarray(gray).save(out_dir / "overlay.png")
        (out_dir / "scores.txt").write_text("")
        (out_dir / "boxes.txt").write_text("")
        print("no masks above threshold; wrote plain image to overlay.png")
        return 0

    masks_arr = np.stack(all_masks, axis=0)
    boxes_arr = np.stack(all_boxes, axis=0)
    scores_arr = np.asarray(all_scores, dtype=np.float32)

    print(f"running mask-IoU NMS at iou_threshold={cfg['nms_iou_threshold']}...")
    t0 = time.time()
    keep = mask_nms(masks_arr, boxes_arr, scores_arr, cfg["nms_iou_threshold"])
    print(f"  kept {len(keep)} of {len(masks_arr)} ({time.time() - t0:.1f}s)")

    masks_arr = masks_arr[keep]
    boxes_arr = boxes_arr[keep]
    scores_arr = scores_arr[keep]

    np.savez_compressed(out_dir / "masks.npz", masks=masks_arr)
    (out_dir / "scores.txt").write_text("\n".join(f"{s:.4f}" for s in scores_arr) + "\n")
    (out_dir / "boxes.txt").write_text(
        "\n".join(" ".join(f"{v:.2f}" for v in b) for b in boxes_arr) + "\n"
    )

    overlay = colorize_overlay(
        gray, masks_arr, boxes_arr,
        alpha=cfg["overlay_alpha"], seed=cfg["color_seed"],
        draw_boxes=cfg["draw_boxes"],
    )
    overlay_path = out_dir / "overlay.png"
    Image.fromarray(overlay).save(overlay_path)
    print(f"\nopen these to review:")
    print(f"  stitched overlay: {overlay_path}")
    print(f"  tile grid:        {out_dir / 'tile_grid.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

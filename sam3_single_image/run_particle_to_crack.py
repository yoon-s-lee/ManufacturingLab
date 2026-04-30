"""Two-stage SAM 3: find particles, then look for cracks inside each particle.

Stage 1 — full image, prompt="particle", keep top-K by score. These tend to
work well on SEM and give us a set of bounding boxes that bracket the regions
where cracks can plausibly exist.

Stage 2 — for each particle bbox, crop the image and re-run SAM with
prompt="crack" at a higher confidence threshold. This focuses SAM's attention
on a small region that we already believe contains material.

Why a custom crop, not the raw particle bbox?
    SAM 3's image processor resizes its input to a fixed 1008x1008 internally.
    Handing it a tiny 60x80 particle bbox would upsample ~13x with bilinear
    interpolation, smearing the 1-3px crack signal into a smooth blur. To
    avoid that we (a) expand the bbox by `crop_padding_px` on each side so
    cracks at the particle edge are included, (b) pad up to `min_crop_size`
    so the upsample factor is bounded (~4x at 240px), and (c) optionally pad
    to a square so SAM's internal letterboxing doesn't distort aspect ratio.

All tunable parameters live in the CONFIG block below. Edit them in place,
then run:

    python sam3_single_image/run_particle_to_crack.py

Outputs (under sam3_single_image/output/<image-stem>/particle_crack/):
    particle_overlay.png  - Stage 1 particles, color-overlaid (sanity check)
    particle_boxes.txt    - one xyxy bounding box per particle
    crop_grid.png         - diagnostic showing the expanded crop regions
    overlay.png           - Stage 2 crack masks, color-overlaid
    masks.npz             - stacked binary crack masks at full image res
    scores.txt            - one confidence score per crack mask
    boxes.txt             - one xyxy bbox per crack mask, full-image coords
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
# CONFIG -- edit these to tune the two-stage pipeline.
# ---------------------------------------------------------------------------
CONFIG = {
    # --- I/O ----------------------------------------------------------------
    "image_path": Path(
        "/Users/ariuseich/Code/CathodeCrackDetectionCode/"
        "UW_SEM_Images_batch_1_no_infobar/8_1_1_lowmass_2.png"
    ),
    "output_dir": Path(__file__).parent / "output",  # "particle_crack" subdir auto-appended

    # --- Stage 1: particles ------------------------------------------------
    "particle_prompt": "particle",
    "particle_top_k": 35,         # keep top-K particle masks by score
    "particle_threshold": 0.0,    # only used if particle_top_k == 0

    # --- Stage 2: cracks inside each particle ------------------------------
    "crack_prompt": "crack",
    "crack_threshold": 0.35,
    "crack_top_k_per_crop": 0,    # 0 = no per-crop cap; rely on threshold

    # --- Crop sizing (anti-upsample-blowup) --------------------------------
    # Pixels added on every side of the raw particle bbox before cropping.
    # Catches cracks that extend slightly past the particle boundary.
    "crop_padding_px": 16,

    # Minimum crop dimension on the short axis. With min_crop_size=240 a tiny
    # particle gets upsampled at most ~4x to reach SAM's 1008 input, instead
    # of >10x. Raise to be more conservative; lower to zoom harder.
    "min_crop_size": 240,

    # If True, pad the crop to a square so SAM's internal letterboxing does
    # not distort aspect ratio inside the model.
    "make_crops_square": True,

    # --- SAM 3 model -------------------------------------------------------
    "resolution": 1008,           # locked at 1008; do not change
    "device": "cpu",

    # --- Stitching ---------------------------------------------------------
    # Particle crops overlap, so the same crack may be detected from 2+ crops.
    # Greedy mask-IoU NMS keeps the highest-scoring detection per cluster.
    "nms_iou_threshold": 0.4,
    "min_mask_area": 20,

    # --- Rendering ---------------------------------------------------------
    "overlay_alpha": 0.5,
    "color_seed": 0,
    "draw_boxes": True,
    "particle_box_color": (240, 200, 60),  # yellow on particle_overlay.png
    "crop_box_color": (60, 220, 90),       # green on crop_grid.png
}
# ---------------------------------------------------------------------------


def expand_crop_box(
    box: np.ndarray, padding_px: int, min_size: int, make_square: bool,
    img_w: int, img_h: int,
) -> tuple[int, int, int, int]:
    """Pad a particle bbox to a sane crop region, clamped inside the image.

    Order of operations: pad on every side, enforce minimum size, square it
    up, then *shift* (not squash) to fit inside the image — that way the
    output aspect ratio is preserved.
    """
    x0, y0, x1, y1 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    w = (x1 - x0) + 2 * padding_px
    h = (y1 - y0) + 2 * padding_px
    w = max(w, float(min_size))
    h = max(h, float(min_size))
    if make_square:
        s = max(w, h)
        w = h = s
    nx0 = int(round(cx - w / 2))
    ny0 = int(round(cy - h / 2))
    nx1 = nx0 + int(round(w))
    ny1 = ny0 + int(round(h))
    if nx0 < 0:
        nx1 -= nx0
        nx0 = 0
    if ny0 < 0:
        ny1 -= ny0
        ny0 = 0
    if nx1 > img_w:
        nx0 -= (nx1 - img_w)
        nx1 = img_w
    if ny1 > img_h:
        ny0 -= (ny1 - img_h)
        ny1 = img_h
    nx0 = max(0, nx0)
    ny0 = max(0, ny0)
    nx1 = min(img_w, nx1)
    ny1 = min(img_h, ny1)
    return nx0, ny0, nx1, ny1


def boxes_overlap(b1: np.ndarray, b2: np.ndarray) -> bool:
    return (
        min(b1[2], b2[2]) > max(b1[0], b2[0])
        and min(b1[3], b2[3]) > max(b1[1], b2[1])
    )


def mask_nms(
    masks: np.ndarray, boxes: np.ndarray, scores: np.ndarray, iou_threshold: float,
) -> list[int]:
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


def render_box_grid(
    gray: np.ndarray, boxes: list[tuple[int, int, int, int]],
    color: tuple[int, int, int],
) -> np.ndarray:
    img = Image.fromarray(np.stack([gray, gray, gray], axis=-1).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=color, width=1)
        draw.text((x0 + 2, y0 + 2), str(i), fill=color)
    return np.asarray(img)


def clear_outputs(out_dir: Path) -> None:
    for filename in (
        "particle_overlay.png", "particle_boxes.txt",
        "crop_grid.png", "overlay.png",
        "masks.npz", "scores.txt", "boxes.txt",
    ):
        path = out_dir / filename
        if path.exists():
            path.unlink()


def run_sam(
    processor: Sam3Processor, pil: Image.Image, prompt: str,
    threshold: float, top_k: int | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    effective_threshold = -1.0 if top_k else threshold
    state = processor.set_image(pil)
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
    out_dir: Path = cfg["output_dir"] / image_path.stem / "particle_crack"
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_outputs(out_dir)

    pil_full = Image.open(image_path).convert("RGB")
    gray = np.asarray(Image.open(image_path).convert("L"))
    H, W = gray.shape
    print(f"image:  {image_path}  shape=({H}, {W})")
    print(f"output: {out_dir}")
    print(f"stage 1: prompt={cfg['particle_prompt']!r} top_k={cfg['particle_top_k']}")
    print(f"stage 2: prompt={cfg['crack_prompt']!r} threshold={cfg['crack_threshold']}")

    print("\nloading SAM 3...")
    t0 = time.time()
    model = build_sam3_image_model().to(cfg["device"]).eval()
    processor = Sam3Processor(
        model,
        resolution=cfg["resolution"],
        device=cfg["device"],
        confidence_threshold=cfg["particle_threshold"],
    )
    print(f"  done in {time.time() - t0:.1f}s")

    # ------------------------------------------------------------------
    # Stage 1: particle detection on the full image.
    # ------------------------------------------------------------------
    print("\n[stage 1] running SAM with particle prompt...")
    t0 = time.time()
    p_top_k = cfg["particle_top_k"] or None
    p_masks, p_boxes, p_scores = run_sam(
        processor, pil_full, cfg["particle_prompt"],
        cfg["particle_threshold"], p_top_k,
    )
    print(f"  found {len(p_masks)} particle(s) in {time.time() - t0:.1f}s")

    if len(p_masks) == 0:
        print("no particles found — nothing to do for stage 2.")
        return 0

    Image.fromarray(colorize_overlay(
        gray, p_masks, p_boxes,
        alpha=cfg["overlay_alpha"], seed=cfg["color_seed"],
        draw_boxes=cfg["draw_boxes"],
    )).save(out_dir / "particle_overlay.png")
    (out_dir / "particle_boxes.txt").write_text(
        "\n".join(" ".join(f"{v:.2f}" for v in b) for b in p_boxes) + "\n"
    )

    # ------------------------------------------------------------------
    # Expand each particle bbox into a sensible crop region.
    # ------------------------------------------------------------------
    crop_boxes: list[tuple[int, int, int, int]] = []
    upsample_factors: list[float] = []
    for b in p_boxes:
        cx0, cy0, cx1, cy1 = expand_crop_box(
            b,
            padding_px=cfg["crop_padding_px"],
            min_size=cfg["min_crop_size"],
            make_square=cfg["make_crops_square"],
            img_w=W, img_h=H,
        )
        crop_boxes.append((cx0, cy0, cx1, cy1))
        short_side = min(cx1 - cx0, cy1 - cy0)
        if short_side > 0:
            upsample_factors.append(cfg["resolution"] / short_side)

    Image.fromarray(render_box_grid(
        gray, crop_boxes, cfg["crop_box_color"],
    )).save(out_dir / "crop_grid.png")

    if upsample_factors:
        print(f"\ncrop sizes (short axis): "
              f"min={min(c[3]-c[1] for c in crop_boxes):d}  "
              f"max={max(c[3]-c[1] for c in crop_boxes):d}")
        print(f"  upsample to {cfg['resolution']}: "
              f"min={min(upsample_factors):.2f}x  max={max(upsample_factors):.2f}x")

    # ------------------------------------------------------------------
    # Stage 2: crack detection inside each particle crop.
    # ------------------------------------------------------------------
    print(f"\n[stage 2] running SAM with crack prompt on {len(crop_boxes)} crop(s)...")
    all_masks: list[np.ndarray] = []
    all_boxes: list[np.ndarray] = []
    all_scores: list[float] = []

    c_top_k = cfg["crack_top_k_per_crop"] or None
    t_total = time.time()
    for i, (cx0, cy0, cx1, cy1) in enumerate(crop_boxes, 1):
        t0 = time.time()
        crop = pil_full.crop((cx0, cy0, cx1, cy1))
        m_t, b_t, s_t = run_sam(
            processor, crop, cfg["crack_prompt"],
            cfg["crack_threshold"], c_top_k,
        )
        n_crop = len(m_t)
        for k in range(n_crop):
            full_m = np.zeros((H, W), dtype=np.uint8)
            full_m[cy0:cy1, cx0:cx1] = m_t[k]
            if int(full_m.sum()) < cfg["min_mask_area"]:
                continue
            full_b = np.array([
                b_t[k][0] + cx0, b_t[k][1] + cy0,
                b_t[k][2] + cx0, b_t[k][3] + cy0,
            ], dtype=np.float32)
            all_masks.append(full_m)
            all_boxes.append(full_b)
            all_scores.append(float(s_t[k]))

        print(f"[{i:>3d}/{len(crop_boxes)}] crop {(cx1-cx0)}x{(cy1-cy0)} "
              f"@({cx0},{cy0}): {n_crop} crack mask(s) ({time.time() - t0:.1f}s)")

    print(f"\nall crops done in {time.time() - t_total:.1f}s; "
          f"{len(all_masks)} raw crack mask(s) before NMS")

    if not all_masks:
        Image.fromarray(gray).save(out_dir / "overlay.png")
        (out_dir / "scores.txt").write_text("")
        (out_dir / "boxes.txt").write_text("")
        print("no cracks above threshold; wrote plain image to overlay.png")
        return 0

    masks_arr = np.stack(all_masks, axis=0)
    boxes_arr = np.stack(all_boxes, axis=0)
    scores_arr = np.asarray(all_scores, dtype=np.float32)

    print(f"\nrunning mask-IoU NMS at iou_threshold={cfg['nms_iou_threshold']}...")
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
    print(f"  particles (stage 1): {out_dir / 'particle_overlay.png'}")
    print(f"  crop regions:        {out_dir / 'crop_grid.png'}")
    print(f"  cracks (stage 2):    {overlay_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

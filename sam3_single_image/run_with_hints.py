"""Run SAM 3 with manual positive/negative box hints (same-image prompting).

This is the *cheap diagnostic* before investing in cross-image visual exemplar
support. You hand-mark boxes around real cracks (positive) and around things
that fool SAM into thinking they're cracks (negative — grain boundaries, scratch
marks, particle edges). SAM combines those hints with the optional text prompt
and re-runs inference. The hints apply only to this image; they do not transfer.

The point of running this:
    If giving SAM 3-6 hand-placed hints noticeably moves the result toward your
    real cracks, then SAM's visual prompt path *can* latch onto crack features —
    and it's worth the effort to wire up cross-image exemplar support so the
    hints generalize. If hints make no useful difference, the model just isn't
    seeing crack signal regardless of how you prompt it, and that effort would
    be wasted.

Workflow:
    1. Open the image in any viewer and note pixel coords for ~3 cracks and
       ~3 lookalikes (most viewers show cursor x,y in the status bar).
    2. Fill in POSITIVE_BOXES and NEGATIVE_BOXES below as [x0, y0, x1, y1].
    3. python sam3_single_image/run_with_hints.py
    4. Compare overlay.png against the no-hints baseline (output/<stem>/overlay.png).

Outputs (under sam3_single_image/output/<image-stem>/with_hints/):
    hints_overlay.png  - SEM with positive hints in green, negative in red
                         (open this FIRST to verify your boxes are where you
                         think they are before paying for the SAM run)
    overlay.png        - SAM masks + hint boxes drawn on top
    masks.npz          - stacked binary masks at full image res
    scores.txt         - one confidence score per mask
    boxes.txt          - one xyxy bounding box per mask (full-image coords)
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
# CONFIG -- edit these.
# ---------------------------------------------------------------------------
CONFIG = {
    # --- I/O ----------------------------------------------------------------
    "image_path": Path(
        "/Users/ariuseich/Code/CathodeCrackDetectionCode/"
        "UW_SEM_Images_batch_1_no_infobar/8_1_1_lowmass_2.png"
    ),
    "output_dir": Path(__file__).parent / "output",  # "with_hints" subdir auto-appended

    # --- Hints (PIXEL coords, [x0, y0, x1, y1]) ----------------------------
    # Positive hints: boxes around regions you KNOW are cracks. Tight is better
    # than loose — try to bracket just the crack itself, not a wide region.
    "positive_boxes": [
        [782, 275, 812, 405],
        [360, 120, 470, 215],
        [729, 56,  753, 74 ],
        [681, 400, 706, 520],
    ],

    # Negative hints: boxes around things that FOOL SAM (grain boundaries,
    # bright surface scratches, particle edges that look line-like). These
    # tell the model "this signature isn't a crack."
    "negative_boxes": [
        [232, 345, 267, 412],
        [150, 487, 225, 522],
        [470, 494, 505, 594],
    ],

    # --- Optional text prompt ----------------------------------------------
    # Set to None to rely entirely on geometric hints. Set to a string to
    # combine text + geometric (text gives a coarse concept, hints refine it).
    "text_prompt": "crack",

    # --- SAM 3 inference knobs ---------------------------------------------
    "confidence_threshold": 0.20,
    "top_k": 30,                    # 0 = no cap; rely on threshold
    "resolution": 1008,            # locked at 1008; do not change
    "device": "cpu",

    # --- Overlay rendering --------------------------------------------------
    "overlay_alpha": 0.5,
    "color_seed": 0,
    "draw_mask_boxes": True,        # bbox outline of each output mask
    "positive_hint_color": (60, 220, 90),    # bright green
    "negative_hint_color": (220, 60, 60),    # red
    "hint_line_width": 2,
}
# ---------------------------------------------------------------------------


def pixel_box_to_normalized_cxcywh(
    box: list[float], img_w: int, img_h: int,
) -> list[float]:
    """Convert [x0, y0, x1, y1] pixel coords to [cx, cy, w, h] normalized to [0, 1]."""
    x0, y0, x1, y1 = box
    cx = ((x0 + x1) / 2.0) / img_w
    cy = ((y0 + y1) / 2.0) / img_h
    w = (x1 - x0) / img_w
    h = (y1 - y0) / img_h
    return [cx, cy, w, h]


def colorize_mask_overlay(
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


def draw_hint_boxes(
    rgb: np.ndarray,
    positive_boxes: list[list[float]],
    negative_boxes: list[list[float]],
    pos_color: tuple[int, int, int],
    neg_color: tuple[int, int, int],
    line_width: int,
) -> np.ndarray:
    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img)
    for i, b in enumerate(positive_boxes):
        x0, y0, x1, y1 = [int(round(v)) for v in b]
        draw.rectangle([x0, y0, x1, y1], outline=pos_color, width=line_width)
        draw.text((x0 + 2, y0 + 2), f"+{i}", fill=pos_color)
    for i, b in enumerate(negative_boxes):
        x0, y0, x1, y1 = [int(round(v)) for v in b]
        draw.rectangle([x0, y0, x1, y1], outline=neg_color, width=line_width)
        draw.text((x0 + 2, y0 + 2), f"-{i}", fill=neg_color)
    return np.asarray(img)


def clear_outputs(out_dir: Path) -> None:
    for filename in (
        "hints_overlay.png", "overlay.png",
        "masks.npz", "scores.txt", "boxes.txt",
    ):
        path = out_dir / filename
        if path.exists():
            path.unlink()


def main() -> int:
    cfg = CONFIG
    image_path: Path = cfg["image_path"]
    out_dir: Path = cfg["output_dir"] / image_path.stem / "with_hints"
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_outputs(out_dir)

    pil_full = Image.open(image_path).convert("RGB")
    gray = np.asarray(Image.open(image_path).convert("L"))
    H, W = gray.shape
    print(f"image:  {image_path}  shape=({H}, {W})")
    print(f"output: {out_dir}")

    pos_boxes_px = cfg["positive_boxes"]
    neg_boxes_px = cfg["negative_boxes"]
    text_prompt = cfg["text_prompt"]

    if not pos_boxes_px and not neg_boxes_px:
        print("\nERROR: both POSITIVE_BOXES and NEGATIVE_BOXES are empty.")
        print("Add at least one hint box to CONFIG. If you want pure text prompting,")
        print("use run.py instead.")
        return 1

    print(f"hints: {len(pos_boxes_px)} positive, {len(neg_boxes_px)} negative")
    print(f"text prompt: {text_prompt!r}")

    # Always write hints_overlay first, even before SAM runs, so the user can
    # sanity-check box positions without paying the SAM cost twice.
    hint_only = draw_hint_boxes(
        np.stack([gray, gray, gray], axis=-1).astype(np.uint8),
        pos_boxes_px, neg_boxes_px,
        cfg["positive_hint_color"], cfg["negative_hint_color"],
        cfg["hint_line_width"],
    )
    Image.fromarray(hint_only).save(out_dir / "hints_overlay.png")
    print(f"hint placement preview: {out_dir / 'hints_overlay.png'}")

    print("\nloading SAM 3...")
    t0 = time.time()
    model = build_sam3_image_model().to(cfg["device"]).eval()
    processor = Sam3Processor(
        model,
        resolution=cfg["resolution"],
        device=cfg["device"],
        confidence_threshold=cfg["confidence_threshold"],
    )
    print(f"  done in {time.time() - t0:.1f}s")

    print("\nrunning inference...")
    t0 = time.time()
    top_k = cfg["top_k"] or None
    effective_threshold = -1.0 if top_k else cfg["confidence_threshold"]

    state = processor.set_image(pil_full)
    processor.set_confidence_threshold(effective_threshold, state=state)

    out = None
    if text_prompt is not None:
        out = processor.set_text_prompt(prompt=text_prompt, state=state)

    for b in pos_boxes_px:
        norm = pixel_box_to_normalized_cxcywh(b, W, H)
        out = processor.add_geometric_prompt(box=norm, label=True, state=state)
    for b in neg_boxes_px:
        norm = pixel_box_to_normalized_cxcywh(b, W, H)
        out = processor.add_geometric_prompt(box=norm, label=False, state=state)
    print(f"  done in {time.time() - t0:.1f}s")

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

    n = len(masks_np)
    print(f"kept {n} mask(s)")

    if n == 0:
        Image.fromarray(hint_only).save(out_dir / "overlay.png")
        (out_dir / "scores.txt").write_text("")
        (out_dir / "boxes.txt").write_text("")
        print("no masks above threshold; overlay shows only the hint boxes.")
        return 0

    np.savez_compressed(out_dir / "masks.npz", masks=masks_np)
    (out_dir / "scores.txt").write_text("\n".join(f"{s:.4f}" for s in scores_np) + "\n")
    (out_dir / "boxes.txt").write_text(
        "\n".join(" ".join(f"{v:.2f}" for v in b) for b in boxes_np) + "\n"
    )

    overlay = colorize_mask_overlay(
        gray, masks_np, boxes_np,
        alpha=cfg["overlay_alpha"], seed=cfg["color_seed"],
        draw_boxes=cfg["draw_mask_boxes"],
    )
    overlay = draw_hint_boxes(
        overlay, pos_boxes_px, neg_boxes_px,
        cfg["positive_hint_color"], cfg["negative_hint_color"],
        cfg["hint_line_width"],
    )
    overlay_path = out_dir / "overlay.png"
    Image.fromarray(overlay).save(overlay_path)

    print(f"\nopen these to compare against the no-hints baseline:")
    print(f"  with hints: {overlay_path}")
    print(f"  baseline:   {cfg['output_dir'] / image_path.stem / 'overlay.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

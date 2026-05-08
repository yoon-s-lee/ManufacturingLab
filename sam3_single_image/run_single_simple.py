"""Run SAM 3 on a single SEM image and save raw masks + shape features.

All SAM-side tunable parameters live in the CONFIG block below. Edit them in
place, then run:

    python sam3_single_image/run_single_simple.py

Outputs (under sam3_single_image/output/<image-stem>/single_simple/):
    overlay.png         - input image with ALL SAM masks overlaid (raw)
    masks.npz           - stacked binary masks, uint8 (N, H, W)
    scores.txt          - one confidence score per mask
    boxes.txt           - one xyxy bounding box per mask
    shape_features.csv  - per-mask geometric stats
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from sam3.model.sam3_image_processor import Sam3Processor
from sam3.model_builder import build_sam3_image_model
from skimage.measure import label, regionprops
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""

# ---------------------------------------------------------------------------
# CONFIG -- edit these to tune SAM 3.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CONFIG = {
    # --- I/O ----------------------------------------------------------------
    "image_path": ROOT / "UW_SEM_Images_batch_1_no_infobar" / "8_1_1_lowmass_2.png",
    "output_dir": Path(__file__).parent / "output",  # "single_simple" subdir auto-appended

    # Input resolution at which the image is resized before feeding to the
    # model. NOTE: the SAM 3 checkpoint is trained at 1008 only — the model's
    # internal RoPE and position buffers are fixed to that grid. Do not change
    # this value; it will raise an assertion error at any other resolution.
    "resolution": 1008,

    # --- SAM 3 prompt -------------------------------------------------------
    # Free-text concept prompt. Try: "crack", "particle", "void", "grain",
    # "dark linear crack", "fissure", "fracture line", "thin dark line".
    "prompt": "crack",

    # --- SAM 3 inference knobs ---------------------------------------------
    # Confidence threshold for keeping a mask. SAM 3 scores on grayscale SEM
    # tend to be low (~0.05); lower this if you get zero masks. Set to a
    # negative value (e.g. -1.0) to disable threshold filtering and rely on
    # top_k instead.
    "confidence_threshold": 0.1,

    # Keep only the top-K highest-scoring masks. Set to None (or 0) to keep
    # all masks above confidence_threshold.
    "top_k": 10,

    # Torch device. "cpu" is the only fully working path on the patched-macos
    # fork; "mps" hits unimplemented ops, "cuda" needs an NVIDIA GPU.
    "device": "cpu",

    # --- Overlay rendering --------------------------------------------------
    # Blend factor for mask color over the grayscale image (0..1).
    "overlay_alpha": 0.5,

    # Seed for the random per-mask colors (so reruns look identical).
    "color_seed": 0,

    # If True, also draw the bounding box outline of each mask on the overlay.
    "draw_boxes": True,
}
# ---------------------------------------------------------------------------


def colorize_overlay(
    gray: np.ndarray,
    masks: np.ndarray,
    boxes: np.ndarray | None,
    alpha: float,
    seed: int,
    draw_boxes: bool,
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


SHAPE_FEATURE_FIELDS = (
    "idx",
    "score",
    "area",
    "eccentricity",
    "aspect_ratio",
    "solidity",
    "extent",
)


def compute_shape_features(masks: np.ndarray, scores: np.ndarray) -> list[dict]:
    """Per-mask geometric stats from the largest connected component."""
    rows = []
    for i, (m, s) in enumerate(zip(masks, scores, strict=True)):
        lbl = label(m.astype(np.uint8), connectivity=2)
        if lbl.max() == 0:
            rows.append({
                "idx": i, "score": float(s), "area": 0,
                "eccentricity": 0.0, "aspect_ratio": 0.0,
                "solidity": 0.0, "extent": 0.0,
            })
            continue
        props = regionprops(lbl)
        p = max(props, key=lambda r: r.area)
        minor = p.axis_minor_length
        major = p.axis_major_length
        aspect = (major / minor) if minor > 1e-6 else float("inf")
        rows.append({
            "idx": i,
            "score": float(s),
            "area": int(p.area),
            "eccentricity": float(p.eccentricity),
            "aspect_ratio": float(aspect),
            "solidity": float(p.solidity),
            "extent": float(p.extent),
        })
    return rows


def write_shape_features_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHAPE_FEATURE_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: (f"{r[k]:.4f}" if isinstance(r[k], float) else r[k])
                             for k in SHAPE_FEATURE_FIELDS})


def clear_outputs(out_dir: Path) -> None:
    for filename in (
        "overlay.png", "masks.npz", "scores.txt", "boxes.txt", "shape_features.csv",
    ):
        path = out_dir / filename
        if path.exists():
            path.unlink()


def main() -> int:
    cfg = CONFIG
    image_path: Path = cfg["image_path"]
    out_dir: Path = cfg["output_dir"] / image_path.stem / "single_simple"
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_outputs(out_dir)

    print(f"image:  {image_path}")
    print(f"output: {out_dir}")
    print(
        f"prompt: {cfg['prompt']!r}  threshold: {cfg['confidence_threshold']}  "
        f"top_k: {cfg['top_k']}  device: {cfg['device']}"
    )

    pil = Image.open(image_path).convert("RGB")
    gray = np.asarray(Image.open(image_path).convert("L"))
    print(f"loaded image: shape={gray.shape} dtype={gray.dtype}")

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

    top_k = cfg["top_k"] or None
    effective_threshold = -1.0 if top_k else cfg["confidence_threshold"]

    print("running inference...")
    t0 = time.time()
    state = processor.set_image(pil)
    processor.set_confidence_threshold(effective_threshold, state=state)
    out = processor.set_text_prompt(prompt=cfg["prompt"], state=state)
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
    if masks_np.ndim == 4:  # (N, 1, H, W) -> (N, H, W)
        masks_np = masks_np[:, 0]
    masks_np = (masks_np > 0).astype(np.uint8)

    boxes_np = boxes.detach().cpu().numpy() if torch.is_tensor(boxes) else np.asarray(boxes)
    scores_np = scores.detach().cpu().numpy() if torch.is_tensor(scores) else np.asarray(scores)

    n = len(masks_np)
    print(f"kept {n} mask(s)")

    if n == 0:
        Image.fromarray(gray).save(out_dir / "overlay.png")
        (out_dir / "scores.txt").write_text("")
        (out_dir / "boxes.txt").write_text("")
        print(f"no masks above threshold; wrote plain image to {out_dir / 'overlay.png'}")
        return 0

    np.savez_compressed(out_dir / "masks.npz", masks=masks_np)
    (out_dir / "scores.txt").write_text("\n".join(f"{s:.4f}" for s in scores_np) + "\n")
    (out_dir / "boxes.txt").write_text(
        "\n".join(" ".join(f"{v:.2f}" for v in b) for b in boxes_np) + "\n"
    )

    print("computing shape features...")
    t0 = time.time()
    feature_rows = compute_shape_features(masks_np, scores_np)
    write_shape_features_csv(out_dir / "shape_features.csv", feature_rows)
    print(f"  done in {time.time() - t0:.2f}s")

    overlay = colorize_overlay(
        gray, masks_np, boxes_np,
        alpha=cfg["overlay_alpha"],
        seed=cfg["color_seed"],
        draw_boxes=cfg["draw_boxes"],
    )
    overlay_path = out_dir / "overlay.png"
    Image.fromarray(overlay).save(overlay_path)
    print(f"\nraw overlay (all SAM masks):\n  {overlay_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

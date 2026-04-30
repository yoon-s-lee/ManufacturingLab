"""Batch-segment SEM images with SAM 3 using a generic concept prompt.

Dumps, per input image:
  <out>/<stem>/masks.npz         - stacked binary masks (uint8, N x H x W)
  <out>/<stem>/scores.txt        - confidence score per mask, one per line
  <out>/<stem>/boxes.txt         - xyxy bounding boxes, one per line
  <out>/<stem>/segment_<i>.png   - each mask cropped to its bbox, background blacked
  <out>/<stem>/overlay.png       - all masks color-overlaid on the image

Usage:
    python scripts/sam3_batch_segment.py UW_SEM_Images_batch_1 UW_SEM_Images_batch_1_segments
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from sam3.model.sam3_image_processor import Sam3Processor
from sam3.model_builder import build_sam3_image_model

from cathode_cracks.io import load_sem

_RNG = np.random.default_rng(0)
_IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def clear_generated_outputs(out_dir: Path) -> None:
    for filename in ("masks.npz", "scores.txt", "boxes.txt", "overlay.png"):
        path = out_dir / filename
        if path.exists():
            path.unlink()
    for path in out_dir.glob("segment_*.png"):
        path.unlink()


def colorize_overlay(gray: np.ndarray, masks: np.ndarray, alpha: float = 0.5) -> np.ndarray:
    rgb = np.stack([gray, gray, gray], axis=-1).astype(np.float32)
    for m in masks:
        color = _RNG.integers(64, 255, size=3).astype(np.float32)
        sel = m.astype(bool)
        rgb[sel] = (1 - alpha) * rgb[sel] + alpha * color
    return rgb.clip(0, 255).astype(np.uint8)


def save_segment(gray: np.ndarray, mask: np.ndarray, box: np.ndarray, path: Path) -> None:
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(gray.shape[1], x1), min(gray.shape[0], y1)
    if x1 <= x0 or y1 <= y0:
        return
    crop = gray[y0:y1, x0:x1]
    m = mask[y0:y1, x0:x1].astype(bool)
    out = np.where(m, crop, 0).astype(np.uint8)
    Image.fromarray(out).save(path)


def process_image(
    processor: Sam3Processor,
    image_path: Path,
    out_dir: Path,
    prompt: str,
    threshold: float,
    top_k: int | None,
) -> int:
    gray = load_sem(image_path)
    pil = Image.fromarray(gray).convert("RGB")

    # SAM 3 presence scores on grayscale SEM are low (~0.05); surface everything,
    # then keep top_k by score if requested.
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
    if masks_np.ndim == 4:  # (N, 1, H, W) -> (N, H, W)
        masks_np = masks_np[:, 0]
    masks_np = (masks_np > 0).astype(np.uint8)

    boxes_np = boxes.detach().cpu().numpy() if torch.is_tensor(boxes) else np.asarray(boxes)
    scores_np = scores.detach().cpu().numpy() if torch.is_tensor(scores) else np.asarray(scores)

    out_dir.mkdir(parents=True, exist_ok=True)
    clear_generated_outputs(out_dir)
    n = len(masks_np)
    if n == 0:
        (out_dir / "scores.txt").write_text("")
        (out_dir / "boxes.txt").write_text("")
        Image.fromarray(gray).save(out_dir / "overlay.png")
        return 0

    np.savez_compressed(out_dir / "masks.npz", masks=masks_np)
    (out_dir / "scores.txt").write_text("\n".join(f"{s:.4f}" for s in scores_np) + "\n")
    (out_dir / "boxes.txt").write_text(
        "\n".join(" ".join(f"{v:.2f}" for v in b) for b in boxes_np) + "\n"
    )

    for i, (m, b) in enumerate(zip(masks_np, boxes_np, strict=True)):
        save_segment(gray, m, b, out_dir / f"segment_{i:03d}.png")

    overlay = colorize_overlay(gray, masks_np)
    Image.fromarray(overlay).save(out_dir / "overlay.png")
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir", type=Path)
    ap.add_argument("output_dir", type=Path)
    ap.add_argument("--prompt", default="particle")
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument(
        "--top-k",
        type=int,
        default=50,
        help="Keep top-K masks by score (overrides --threshold). Set to 0 to use threshold.",
    )
    ap.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    args = ap.parse_args()

    images = sorted(p for p in args.input_dir.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES)
    if not images:
        print(f"no images found in {args.input_dir}")
        return 1

    print(f"device: {args.device}  prompt: {args.prompt!r}  threshold: {args.threshold}")
    print("loading SAM 3...")
    t0 = time.time()
    model = build_sam3_image_model().to(args.device).eval()
    processor = Sam3Processor(model, device=args.device, confidence_threshold=args.threshold)
    print(f"  done in {time.time() - t0:.1f}s")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for i, img_path in enumerate(images, 1):
        t0 = time.time()
        n = process_image(
            processor,
            img_path,
            args.output_dir / img_path.stem,
            args.prompt,
            args.threshold,
            args.top_k or None,
        )
        print(f"[{i}/{len(images)}] {img_path.name}: {n} segments ({time.time() - t0:.1f}s)")

    print(f"\nresults written to {args.output_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

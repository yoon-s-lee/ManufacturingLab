"""Run MobileSAM's automatic mask generator on a directory of SEM images.

MobileSAM (unlike SAM 3) doesn't take a text prompt. We let the automatic mask
generator sample a dense point grid and produce every segment it can find. The
intent is a "natural" segmentation so the user can visually check whether the
model latches onto crack structure.

Outputs per image:
  <out>/<stem>/overlay.png        color-per-mask overlay on grayscale
  <out>/<stem>/masks.npz          stacked binary masks (uint8, N x H x W)
  <out>/<stem>/info.txt           N, elapsed s, image shape

Usage:
    python mobilesam/run_mobilesam.py \\
        UW_SEM_Images_batch_1 mobilesam/output
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

# Make `cathode_cracks.io.load_sem` importable when run directly.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
from mobile_sam import SamAutomaticMaskGenerator, sam_model_registry  # noqa: E402

from cathode_cracks.io import load_sem, strip_info_bar  # noqa: E402

_WEIGHTS = Path(__file__).resolve().parent / "mobile_sam.pt"
_RNG = np.random.default_rng(0)
_IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    # MPS currently errors inside MobileSAM's AMG (float64 point tensors), so
    # skip it even when available and fall through to CPU.
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def colorize_overlay(gray: np.ndarray, masks: list[np.ndarray], alpha: float = 0.5) -> np.ndarray:
    rgb = np.stack([gray, gray, gray], axis=-1).astype(np.float32)
    # Draw largest first so small masks (often more interesting) stay on top.
    order = sorted(range(len(masks)), key=lambda i: -int(masks[i].sum()))
    for i in order:
        m = masks[i].astype(bool)
        color = _RNG.integers(64, 255, size=3).astype(np.float32)
        rgb[m] = (1 - alpha) * rgb[m] + alpha * color
    return rgb.clip(0, 255).astype(np.uint8)


def process_image(
    generator: SamAutomaticMaskGenerator,
    image_path: Path,
    out_dir: Path,
) -> tuple[int, float]:
    gray = load_sem(image_path)
    sample, _info = strip_info_bar(gray)
    rgb = np.stack([sample, sample, sample], axis=-1)

    t0 = time.time()
    anns = generator.generate(rgb)
    elapsed = time.time() - t0

    masks = [a["segmentation"].astype(np.uint8) for a in anns]
    out_dir.mkdir(parents=True, exist_ok=True)
    masks_path = out_dir / "masks.npz"
    if masks_path.exists():
        masks_path.unlink()

    overlay = colorize_overlay(sample, masks) if masks else np.stack([sample] * 3, axis=-1)
    Image.fromarray(overlay).save(out_dir / "overlay.png")

    if masks:
        np.savez_compressed(out_dir / "masks.npz", masks=np.stack(masks))

    (out_dir / "info.txt").write_text(
        f"n_masks={len(masks)}\nelapsed_s={elapsed:.2f}\nshape={sample.shape}\n"
    )
    return len(masks), elapsed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir", type=Path)
    ap.add_argument("output_dir", type=Path)
    ap.add_argument("--weights", type=Path, default=_WEIGHTS)
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"])
    ap.add_argument(
        "--points-per-side",
        type=int,
        default=32,
        help="Grid density. Higher = more masks, slower.",
    )
    ap.add_argument("--pred-iou-thresh", type=float, default=0.86)
    ap.add_argument("--stability-score-thresh", type=float, default=0.92)
    ap.add_argument("--min-mask-region-area", type=int, default=50)
    args = ap.parse_args()

    device = pick_device(args.device)
    images = sorted(
        p
        for p in args.input_dir.iterdir()
        if p.suffix.lower() in _IMAGE_SUFFIXES
    )
    if not images:
        print(f"no images found in {args.input_dir}")
        return 1

    print(f"device: {device}  weights: {args.weights}")
    if not args.weights.exists():
        print(f"missing weights at {args.weights}")
        return 1

    print("loading MobileSAM...")
    t0 = time.time()
    sam = sam_model_registry["vit_t"](checkpoint=str(args.weights))
    sam.to(device=device).eval()
    generator = SamAutomaticMaskGenerator(
        sam,
        points_per_side=args.points_per_side,
        pred_iou_thresh=args.pred_iou_thresh,
        stability_score_thresh=args.stability_score_thresh,
        min_mask_region_area=args.min_mask_region_area,
    )
    print(f"  done in {time.time() - t0:.1f}s")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for i, img_path in enumerate(images, 1):
        n, elapsed = process_image(generator, img_path, args.output_dir / img_path.stem)
        print(f"[{i}/{len(images)}] {img_path.name}: {n} masks ({elapsed:.1f}s)")

    print(f"\nresults written to {args.output_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

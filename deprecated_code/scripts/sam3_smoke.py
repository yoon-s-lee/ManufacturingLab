"""Smoke test: load one SEM image and run SAM 3 with a text prompt on MPS.

Usage:
    python scripts/sam3_smoke.py data/raw/8_1_1_lowmass_1.tif [--prompt "crack"]

Expects `pip install -e ".[annotate]"` has been run and `huggingface-cli login`
has been completed with access to facebook/sam3 granted.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from sam3.model.sam3_image_processor import Sam3Processor
from sam3.model_builder import build_sam3_image_model

from cathode_cracks.io import load_sem


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--prompt", default="crack")
    ap.add_argument(
        "--device",
        default="cpu",
        choices=["cpu", "mps", "cuda"],
        help="cpu is the only fully working path on this fork; mps hits unimplemented ops.",
    )
    args = ap.parse_args()
    device = args.device
    print(f"device: {device}")

    arr = load_sem(args.image)
    pil = Image.fromarray(arr).convert("RGB")
    print(f"image: {args.image.name} shape={arr.shape} dtype={arr.dtype}")

    t0 = time.time()
    model = build_sam3_image_model().to(device).eval()
    processor = Sam3Processor(model, device=device)
    print(f"model loaded in {time.time() - t0:.1f}s")

    t0 = time.time()
    state = processor.set_image(pil)
    out = processor.set_text_prompt(state=state, prompt=args.prompt)
    masks, boxes, scores = out["masks"], out["boxes"], out["scores"]
    print(f"inference: {time.time() - t0:.2f}s")

    masks_np = masks.detach().cpu().numpy() if torch.is_tensor(masks) else np.asarray(masks)
    print(f"masks: shape={masks_np.shape} instances={len(masks_np)}")
    print(f"boxes: {len(boxes)}  scores: {scores}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

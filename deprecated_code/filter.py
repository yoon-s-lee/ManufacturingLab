"""Shape-based post-filter for SAM 3 masks — keeps crack-shaped regions only.

Reads the cached outputs of run.py (masks.npz + shape_features.csv) and writes
two new overlays. SAM 3 is NOT re-run, so each iteration is sub-second.

Workflow:
    1. python sam3_single_image/run.py        (slow, ~5s — runs SAM 3)
    2. edit SHAPE_FILTER below
    3. python sam3_single_image/filter.py     (fast — re-renders overlays)

Outputs (under sam3_single_image/output/<image-stem>/):
    overlay_filtered.png  - kept masks only (your candidate cracks)
    overlay_debug.png     - kept = green, dropped = dim red, with reason text

Tuning tip: open shape_features.csv in a spreadsheet, sort by eccentricity
descending. The top of the list is what looks the most line-like to the
geometry. Pick thresholds where the obvious particles drop off.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# CONFIG -- edit these to tune the shape filter.
# Set any threshold to None to disable that single criterion (useful for
# diagnosing which one is killing a real crack).
# ---------------------------------------------------------------------------
CONFIG = {
    "image_path": Path(
        "/Users/ariuseich/Code/CathodeCrackDetectionCode/"
        "UW_SEM_Images_batch_1_no_infobar/8_1_1_lowmass_2.png"
    ),
    "output_dir": Path(__file__).parent / "output",
}

SHAPE_FILTER = {
    # Pixel-area bounds. Drops single-pixel dust and image-spanning fills.
    "min_area": 30,
    "max_area": 50000,

    # Eccentricity of best-fit ellipse: 0 = circle, 1 = line.
    # Cracks ≈ 0.97+; particles ≈ 0.6–0.85.
    "min_eccentricity": 0.90,

    # Ratio of major to minor axis. Cracks > 5; particles ~ 1–2.
    "min_aspect_ratio": 4.0,

    # area / convex_hull_area. Wiggly cracks: low (~0.3–0.6); solid blobs ≈ 1.0.
    "max_solidity": 0.85,

    # area / bbox_area. Thin diagonals fill little of their bbox.
    "max_extent": 0.5,
}

# Render options.
KEPT_COLOR = (60, 220, 90)        # bright green
DROPPED_COLOR = (220, 60, 60)     # red
OVERLAY_ALPHA = 0.55
DEBUG_DROPPED_ALPHA = 0.25         # dim dropped masks in debug overlay
DRAW_BOX_LABELS = True             # write drop-reason text on debug overlay
# ---------------------------------------------------------------------------


def load_features(csv_path: Path) -> list[dict]:
    rows = []
    with csv_path.open() as f:
        for r in csv.DictReader(f):
            rows.append({
                "idx": int(r["idx"]),
                "score": float(r["score"]),
                "area": int(r["area"]),
                "eccentricity": float(r["eccentricity"]),
                "aspect_ratio": float(r["aspect_ratio"]),
                "solidity": float(r["solidity"]),
                "extent": float(r["extent"]),
            })
    return rows


def drop_reason(feat: dict, cfg: dict) -> str | None:
    """Return None if mask is kept, else a short string explaining the drop."""
    if cfg["min_area"] is not None and feat["area"] < cfg["min_area"]:
        return f"area<{cfg['min_area']}"
    if cfg["max_area"] is not None and feat["area"] > cfg["max_area"]:
        return f"area>{cfg['max_area']}"
    if cfg["min_eccentricity"] is not None and feat["eccentricity"] < cfg["min_eccentricity"]:
        return f"ecc<{cfg['min_eccentricity']}"
    if cfg["min_aspect_ratio"] is not None and feat["aspect_ratio"] < cfg["min_aspect_ratio"]:
        return f"ar<{cfg['min_aspect_ratio']}"
    if cfg["max_solidity"] is not None and feat["solidity"] > cfg["max_solidity"]:
        return f"sol>{cfg['max_solidity']}"
    if cfg["max_extent"] is not None and feat["extent"] > cfg["max_extent"]:
        return f"ext>{cfg['max_extent']}"
    return None


def render_overlay(
    gray: np.ndarray,
    masks: np.ndarray,
    color: tuple[int, int, int],
    alpha: float,
) -> np.ndarray:
    rgb = np.stack([gray, gray, gray], axis=-1).astype(np.float32)
    color_arr = np.asarray(color, dtype=np.float32)
    for m in masks:
        sel = m.astype(bool)
        rgb[sel] = (1 - alpha) * rgb[sel] + alpha * color_arr
    return rgb.clip(0, 255).astype(np.uint8)


def render_debug_overlay(
    gray: np.ndarray,
    masks: np.ndarray,
    reasons: list[str | None],
) -> np.ndarray:
    rgb = np.stack([gray, gray, gray], axis=-1).astype(np.float32)
    kept_arr = np.asarray(KEPT_COLOR, dtype=np.float32)
    dropped_arr = np.asarray(DROPPED_COLOR, dtype=np.float32)
    for m, reason in zip(masks, reasons, strict=True):
        sel = m.astype(bool)
        if reason is None:
            rgb[sel] = (1 - OVERLAY_ALPHA) * rgb[sel] + OVERLAY_ALPHA * kept_arr
        else:
            rgb[sel] = (1 - DEBUG_DROPPED_ALPHA) * rgb[sel] + DEBUG_DROPPED_ALPHA * dropped_arr
    out = rgb.clip(0, 255).astype(np.uint8)

    if DRAW_BOX_LABELS:
        img = Image.fromarray(out)
        draw = ImageDraw.Draw(img)
        for m, reason in zip(masks, reasons, strict=True):
            if reason is None:
                continue
            ys, xs = np.where(m)
            if len(xs) == 0:
                continue
            cx, cy = int(xs.mean()), int(ys.mean())
            draw.text((cx, cy), reason, fill=DROPPED_COLOR)
        out = np.asarray(img)
    return out


def main() -> int:
    image_path: Path = CONFIG["image_path"]
    out_dir: Path = CONFIG["output_dir"] / image_path.stem

    masks_path = out_dir / "masks.npz"
    features_path = out_dir / "shape_features.csv"
    if not masks_path.exists() or not features_path.exists():
        print(f"missing cached outputs in {out_dir}.\n"
              f"run `python sam3_single_image/run.py` first.")
        return 1

    masks = np.load(masks_path)["masks"]  # (N, H, W) uint8
    features = load_features(features_path)
    gray = np.asarray(Image.open(image_path).convert("L"))
    if gray.shape != masks.shape[1:]:
        print(f"WARNING: image shape {gray.shape} != mask shape {masks.shape[1:]}; "
              f"did you change image_path since the last run?")

    print(f"loaded {len(masks)} mask(s) from {masks_path}")
    print(f"shape filter: {SHAPE_FILTER}")

    reasons = [drop_reason(f, SHAPE_FILTER) for f in features]
    kept_idx = [i for i, r in enumerate(reasons) if r is None]
    dropped_idx = [i for i, r in enumerate(reasons) if r is not None]

    print(f"\nkept:    {len(kept_idx):3d}")
    print(f"dropped: {len(dropped_idx):3d}")

    # Print per-reason breakdown.
    from collections import Counter
    drop_counts = Counter(r for r in reasons if r is not None)
    if drop_counts:
        print("drop breakdown:")
        for reason, count in drop_counts.most_common():
            print(f"  {reason:>20s}  {count}")

    # Filtered overlay (kept only).
    if kept_idx:
        kept_overlay = render_overlay(gray, masks[kept_idx], KEPT_COLOR, OVERLAY_ALPHA)
    else:
        kept_overlay = np.stack([gray, gray, gray], axis=-1)
    Image.fromarray(kept_overlay).save(out_dir / "overlay_filtered.png")

    # Debug overlay (kept + dropped + reason text).
    debug_overlay = render_debug_overlay(gray, masks, reasons)
    Image.fromarray(debug_overlay).save(out_dir / "overlay_debug.png")

    print(f"\nopen these to view results:")
    print(f"  candidates: {out_dir / 'overlay_filtered.png'}")
    print(f"  debug view: {out_dir / 'overlay_debug.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

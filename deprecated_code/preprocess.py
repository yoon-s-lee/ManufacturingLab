"""Classical ridge-detection pre-pass to find candidate crack locations.

This script runs entirely in classical CV — no SAM, no torch. It uses a
vesselness/ridge filter (Frangi / Sato / Meijering) to score each pixel by how
much it looks like a thin dark line, then turns connected ridge regions into
candidate bounding boxes. Those boxes are *intended* for use as SAM 3 geometric
prompts in a later stage, but this script only writes them to disk — nothing
downstream touches them yet.

Workflow:
    1. edit CONFIG below (especially `sigmas` and `response_threshold`)
    2. python sam3_single_image/preprocess.py
    3. open the diagnostic PNGs and decide whether the candidates look real

Outputs (under sam3_single_image/output/<image-stem>/preprocess/):
    ridge_response.png      - raw vesselness heatmap as grayscale (sanity check)
    ridge_overlay.png       - vesselness glow on top of the SEM image
    ridge_binary.png        - thresholded ridge mask, black/white
    candidates_overlay.png  - SEM with bboxes + index labels around each survivor
    ridge_boxes.txt         - one `x0 y0 x1 y1` per line (for future SAM stage)
    ridge_features.csv      - per-candidate stats: length, aspect, mean response
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from skimage import exposure, filters, img_as_float
from skimage.measure import label, regionprops

# ---------------------------------------------------------------------------
# CONFIG -- edit these to tune ridge detection.
# ---------------------------------------------------------------------------
CONFIG = {
    "image_path": Path(
        "/Users/ariuseich/Code/CathodeCrackDetectionCode/"
        "UW_SEM_Images_batch_1_no_infobar/8_1_1_lowmass_2.png"
    ),
    "output_dir": Path(__file__).parent / "output",

    # --- Pre-enhancement (CLAHE) -------------------------------------------
    # CLAHE = Contrast-Limited Adaptive Histogram Equalization. Boosts local
    # contrast so weak cracks are easier for the ridge filter to lock onto.
    # Set to False to skip and feed the raw image directly to Frangi.
    "use_clahe": True,
    "clahe_clip_limit": 0.02,    # higher = more aggressive contrast boost (0.001..0.05 useful)
    "clahe_kernel_size": None,    # None lets skimage pick (~1/8 of image dim)

    # --- Ridge filter -------------------------------------------------------
    # "frangi" is the canonical vesselness; "sato" emphasizes tubularity at
    # small scales; "meijering" is good for thin neurites/cracks. Try all three.
    "ridge_filter": "frangi",     # "frangi" | "sato" | "meijering"

    # Sigmas correspond to line *half-widths* in pixels. Cracks here look ~1-3
    # pixels wide at native 640x960; bump the upper end if you have wider cracks.
    "sigmas": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],

    # True: detect dark ridges on bright background (cracks). False: bright on dark.
    "black_ridges": True,

    # --- Binarize -----------------------------------------------------------
    # Threshold on the vesselness map (range [0, 1]). Lower = more candidates.
    # If you set this too high you'll lose faint cracks; too low and grain
    # boundaries / noise survive.
    "response_threshold": 0.05,

    # --- Component shape filtering -----------------------------------------
    # Reject candidates that are too short or not elongated enough.
    "min_length": 20,             # major axis length in pixels
    "min_aspect_ratio": 3.0,      # major / minor of best-fit ellipse
    "max_components": 50,         # cap, ranked by mean vesselness response

    # --- Bbox export --------------------------------------------------------
    # Pixels added on every side of each surviving component's bbox before
    # writing to ridge_boxes.txt. SAM tends to need a little context around
    # the prompt box.
    "bbox_padding": 4,

    # --- Visualization knobs (don't affect the saved boxes) ----------------
    "overlay_color": (255, 60, 200),  # magenta glow on ridge_overlay.png
    "overlay_alpha_max": 0.75,         # peak blend at vesselness = 1.0
    "box_color": (60, 220, 90),        # green outlines on candidates_overlay.png
    "box_label_color": (255, 255, 0),  # yellow index labels
}
# ---------------------------------------------------------------------------


def run_ridge_filter(image: np.ndarray, name: str, sigmas, black_ridges: bool) -> np.ndarray:
    fn = {
        "frangi": filters.frangi,
        "sato": filters.sato,
        "meijering": filters.meijering,
    }[name]
    response = fn(image, sigmas=sigmas, black_ridges=black_ridges)
    if response.max() > 0:
        response = response / response.max()
    return response.astype(np.float32)


def save_response_png(response: np.ndarray, path: Path) -> None:
    Image.fromarray((response * 255).clip(0, 255).astype(np.uint8)).save(path)


def save_overlay(
    gray: np.ndarray, response: np.ndarray, color: tuple[int, int, int],
    alpha_max: float, path: Path,
) -> None:
    rgb = np.stack([gray, gray, gray], axis=-1).astype(np.float32)
    color_arr = np.asarray(color, dtype=np.float32)
    a = (response * alpha_max).clip(0, 1)[..., None]
    blended = (1 - a) * rgb + a * color_arr
    Image.fromarray(blended.clip(0, 255).astype(np.uint8)).save(path)


def find_candidates(
    response: np.ndarray, threshold: float,
    min_length: float, min_aspect_ratio: float, max_components: int,
) -> tuple[list[dict], np.ndarray]:
    binary = response > threshold
    lbl = label(binary, connectivity=2)
    if lbl.max() == 0:
        return []
    rows = []
    for p in regionprops(lbl, intensity_image=response):
        minor = p.axis_minor_length
        major = p.axis_major_length
        aspect = (major / minor) if minor > 1e-6 else float("inf")
        if major < min_length:
            continue
        if aspect < min_aspect_ratio:
            continue
        y0, x0, y1, x1 = p.bbox
        rows.append({
            "label": int(p.label),
            "area": int(p.area),
            "length": float(major),
            "width": float(minor),
            "aspect_ratio": float(aspect),
            "mean_response": float(p.intensity_mean),
            "x0": int(x0), "y0": int(y0), "x1": int(x1), "y1": int(y1),
        })
    rows.sort(key=lambda r: r["mean_response"], reverse=True)
    if max_components and len(rows) > max_components:
        rows = rows[:max_components]
    for i, r in enumerate(rows):
        r["idx"] = i
    return rows, binary


def pad_box(x0: int, y0: int, x1: int, y1: int, pad: int, w: int, h: int) -> tuple[int, int, int, int]:
    return (
        max(0, x0 - pad),
        max(0, y0 - pad),
        min(w, x1 + pad),
        min(h, y1 + pad),
    )


def save_candidates_overlay(
    gray: np.ndarray, candidates: list[dict], pad: int,
    box_color: tuple[int, int, int], label_color: tuple[int, int, int],
    path: Path,
) -> None:
    h, w = gray.shape
    img = Image.fromarray(np.stack([gray, gray, gray], axis=-1).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    for c in candidates:
        x0, y0, x1, y1 = pad_box(c["x0"], c["y0"], c["x1"], c["y1"], pad, w, h)
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=box_color, width=1)
        draw.text((x0 + 1, max(0, y0 - 10)), str(c["idx"]), fill=label_color)
    img.save(path)


def write_boxes_txt(path: Path, candidates: list[dict], pad: int, w: int, h: int) -> None:
    lines = []
    for c in candidates:
        x0, y0, x1, y1 = pad_box(c["x0"], c["y0"], c["x1"], c["y1"], pad, w, h)
        lines.append(f"{x0} {y0} {x1} {y1}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def write_features_csv(path: Path, candidates: list[dict], pad: int, w: int, h: int) -> None:
    fields = (
        "idx", "x0", "y0", "x1", "y1",
        "length", "width", "aspect_ratio", "mean_response", "area",
    )
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for c in candidates:
            x0, y0, x1, y1 = pad_box(c["x0"], c["y0"], c["x1"], c["y1"], pad, w, h)
            writer.writerow({
                "idx": c["idx"],
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "length": f"{c['length']:.2f}",
                "width": f"{c['width']:.2f}",
                "aspect_ratio": f"{c['aspect_ratio']:.2f}",
                "mean_response": f"{c['mean_response']:.4f}",
                "area": c["area"],
            })


def clear_outputs(out_dir: Path) -> None:
    for filename in (
        "ridge_response.png", "ridge_overlay.png", "ridge_binary.png",
        "candidates_overlay.png", "ridge_boxes.txt", "ridge_features.csv",
    ):
        path = out_dir / filename
        if path.exists():
            path.unlink()


def main() -> int:
    cfg = CONFIG
    image_path: Path = cfg["image_path"]
    out_dir: Path = cfg["output_dir"] / image_path.stem / "preprocess"
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_outputs(out_dir)

    print(f"image:  {image_path}")
    print(f"output: {out_dir}")
    print(f"filter: {cfg['ridge_filter']}  sigmas: {cfg['sigmas']}  "
          f"black_ridges: {cfg['black_ridges']}  threshold: {cfg['response_threshold']}")

    gray_u8 = np.asarray(Image.open(image_path).convert("L"))
    h, w = gray_u8.shape
    print(f"loaded image: shape={gray_u8.shape}")

    img = img_as_float(gray_u8)
    if cfg["use_clahe"]:
        img = exposure.equalize_adapthist(
            img,
            clip_limit=cfg["clahe_clip_limit"],
            kernel_size=cfg["clahe_kernel_size"],
        )
        print(f"applied CLAHE: clip_limit={cfg['clahe_clip_limit']}")

    response = run_ridge_filter(
        img, cfg["ridge_filter"], cfg["sigmas"], cfg["black_ridges"],
    )
    n_above = int((response > cfg["response_threshold"]).sum())
    print(f"vesselness: max={response.max():.3f}  pixels>thr={n_above}  "
          f"({100 * n_above / response.size:.2f}% of image)")

    save_response_png(response, out_dir / "ridge_response.png")
    save_overlay(
        gray_u8, response, cfg["overlay_color"],
        cfg["overlay_alpha_max"], out_dir / "ridge_overlay.png",
    )

    candidates, binary = find_candidates(
        response,
        threshold=cfg["response_threshold"],
        min_length=cfg["min_length"],
        min_aspect_ratio=cfg["min_aspect_ratio"],
        max_components=cfg["max_components"],
    )
    Image.fromarray((binary * 255).astype(np.uint8)).save(out_dir / "ridge_binary.png")

    print(f"\ncandidates surviving shape filter: {len(candidates)}")
    if candidates:
        print(f"  top 5 by mean response:")
        print(f"  {'idx':>3} {'length':>7} {'width':>6} {'aspect':>7} {'response':>9}")
        for c in candidates[:5]:
            print(f"  {c['idx']:>3d} {c['length']:>7.1f} {c['width']:>6.2f} "
                  f"{c['aspect_ratio']:>7.2f} {c['mean_response']:>9.4f}")

    save_candidates_overlay(
        gray_u8, candidates, cfg["bbox_padding"],
        cfg["box_color"], cfg["box_label_color"],
        out_dir / "candidates_overlay.png",
    )
    write_boxes_txt(out_dir / "ridge_boxes.txt", candidates, cfg["bbox_padding"], w, h)
    write_features_csv(out_dir / "ridge_features.csv", candidates, cfg["bbox_padding"], w, h)

    print(f"\nopen these to review:")
    print(f"  vesselness map: {out_dir / 'ridge_overlay.png'}")
    print(f"  binary mask:    {out_dir / 'ridge_binary.png'}")
    print(f"  candidates:     {out_dir / 'candidates_overlay.png'}")
    print(f"\nIf the candidates look like real cracks, the bboxes are saved")
    print(f"at {out_dir / 'ridge_boxes.txt'} ready to feed into a future SAM stage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

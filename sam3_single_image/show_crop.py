"""Clip and save a crop from an image given a pixel [x0, y0, x1, y1] box.

Use this to visually verify hint box coordinates before filling them into
run_with_hints.py. Boxes are in the same pixel xyxy format that
run_with_hints.py uses, so you can copy a box you scouted here straight into
its positive_boxes / negative_boxes lists.

(SAM 3's geometric prompt API actually takes normalized [cx, cy, w, h], but
both run_with_hints.py and this script convert from pixel xyxy at the
boundary — there's no performance difference, and pixels are what cursor
status bars in image viewers report.)

Usage (edit CONFIG below, then):

    python sam3_single_image/show_crop.py

A `<true|false>_` prefix is added to every filename based on is_crack, so
positive and negative examples never collide and you can spot-check at a
glance which kind a saved hint is.

Outputs (under sam3_single_image/output/<image-stem>/saved_hints/):
    save_as_long_term_hint=True:
        <true|false>_crop_<x0>_<y0>_<x1>_<y1>.png      - the cropped region
        <true|false>_context_<x0>_<y0>_<x1>_<y1>.png   - full image w/ box
        Filenames embed the pixel xyxy so repeated runs accumulate — use this
        once you've found a box worth keeping.
    save_as_long_term_hint=False:
        <true|false>_crop_scratch.png      - overwritten every run
        <true|false>_context_scratch.png   - overwritten every run
        Use this while iterating: leave the files open in a viewer and re-run
        with new box values; the viewer auto-refreshes. The is_crack flag
        gives you separate scratch pairs for positive vs negative examples.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# CONFIG -- edit these.
# ---------------------------------------------------------------------------
x_0 = 470
y_0 = 494

ROOT = Path(__file__).resolve().parent.parent


CONFIG = {
    "image_path": ROOT / "UW_SEM_Images_batch_1_no_infobar" / "8_1_1_lowmass_2.png",

    # Pixel [x0, y0, x1, y1] — same format as run_with_hints.py's
    # positive_boxes / negative_boxes. Edit this to the box you want to inspect.

    "box": [x_0, y_0, x_0+35, y_0+100],

    # True  -> this box is a positive crack example (saves filename starts "true_").
    #          Goes into run_with_hints.py's positive_boxes.
    # False -> this box is a negative example: a non-crack lookalike like a
    #          grain boundary or scratch (filename starts "false_"). Goes into
    #          run_with_hints.py's negative_boxes.
    "is_crack": False,

    # True  -> filenames embed the pixel xyxy; runs accumulate. Use when you've
    #          locked in a box worth keeping.
    # False -> overwrite a fixed pair (crop_scratch.png / context_scratch.png).
    #          Use while iterating — leave the two files open in a viewer and
    #          they auto-refresh on each rerun.
    "save_as_long_term_hint": False,

    # Where to write the two output images. "saved_hints" subdir auto-appended
    # under <output_dir>/<image-stem>/, matching the layout of the other scripts.
    "output_dir": Path(__file__).parent / "output",

    # Draw a border on the context image to show the box outline.
    "box_color": (60, 220, 90),
    "box_line_width": 2,
}
# ---------------------------------------------------------------------------


def clamp_pixel_xyxy(
    box: list[float], img_w: int, img_h: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(img_w - 1, x1)
    y1 = min(img_h - 1, y1)
    return x0, y0, x1, y1


def main() -> int:
    cfg = CONFIG
    image_path: Path = cfg["image_path"]
    box: list[float] = cfg["box"]
    out_dir: Path = cfg["output_dir"] / image_path.stem / "saved_hints"
    out_dir.mkdir(parents=True, exist_ok=True)

    pil = Image.open(image_path).convert("RGB")
    W, H = pil.size
    print(f"image:  {image_path}  size=({W}w x {H}h)")
    print(f"output: {out_dir}")

    x0, y0, x1, y1 = clamp_pixel_xyxy(box, W, H)
    print(f"box (pixel xyxy): [{x0}, {y0}, {x1}, {y1}]")
    print(f"crop size: {x1 - x0}w x {y1 - y0}h px")

    if x1 <= x0 or y1 <= y0:
        print("ERROR: box is zero-area after clamping — check your x0/y0/x1/y1 values.")
        return 1

    label = "true" if cfg["is_crack"] else "false"
    if cfg["save_as_long_term_hint"]:
        suffix = f"{x0}_{y0}_{x1}_{y1}"
        crop_name = f"{label}_crop_{suffix}.png"
        ctx_name = f"{label}_context_{suffix}.png"
    else:
        crop_name = f"{label}_crop_scratch.png"
        ctx_name = f"{label}_context_scratch.png"

    # Crop
    crop = pil.crop((x0, y0, x1, y1))
    crop_path = out_dir / crop_name
    crop.save(crop_path)
    print(f"\ncrop saved:    {crop_path}")

    # Context: full image with box drawn
    ctx = pil.copy()
    draw = ImageDraw.Draw(ctx)
    draw.rectangle(
        [x0, y0, x1, y1],
        outline=cfg["box_color"],
        width=cfg["box_line_width"],
    )
    ctx_path = out_dir / ctx_name
    ctx.save(ctx_path)
    print(f"context saved: {ctx_path}")

    # Also print the pixel xyxy ready to paste into run_with_hints.py
    target = "positive_boxes" if cfg["is_crack"] else "negative_boxes"
    print(f"\nTo use as a hint in run_with_hints.py:")
    print(f'    "{target}": [ [{x0}, {y0}, {x1}, {y1}], ],')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

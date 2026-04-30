"""Command-line utilities for SEM crack-detection preprocessing."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from cathode_cracks.io import load_sem, strip_info_bar

_IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def _iter_images(paths: list[Path]) -> list[Path]:
    images: list[Path] = []
    for path in paths:
        if path.is_dir():
            images.extend(p for p in sorted(path.iterdir()) if p.suffix.lower() in _IMAGE_SUFFIXES)
        elif path.suffix.lower() in _IMAGE_SUFFIXES:
            images.append(path)
    return images


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load SEM images, remove the burned-in info bar, and write PNGs."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Image files or directories to process.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("processed"))
    parser.add_argument(
        "--keep-info-bar",
        action="store_true",
        help="Load and normalize without cropping.",
    )
    args = parser.parse_args(argv)

    images = _iter_images(args.inputs)
    if not images:
        print("no supported images found")
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for image_path in images:
        img = load_sem(image_path)
        sample, strip = (img, None) if args.keep_info_bar else strip_info_bar(img)
        out_path = args.output_dir / f"{image_path.stem}.png"
        Image.fromarray(sample).save(out_path)
        strip_msg = "none" if strip is None else f"{strip.shape[0]} rows"
        print(f"{image_path.name}: wrote {out_path} info_bar={strip_msg}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

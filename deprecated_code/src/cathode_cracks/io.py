"""Image loading and simple layout utilities for SEM TIFFs.

Batch-1 "TIFFs" are actually raw JPEG/JFIF streams that were given a .tif
extension — the file command reports them as `JPEG image data, JFIF standard`
and the first four bytes are `FF D8 FF E0`. Real TIFFs (including JPEG-in-TIFF
emitted by some SEM vendor tools) have magic `II*\\x00` or `MM\\x00*`. We
dispatch on the leading bytes so both variants load transparently.

Most SEM vendors also burn a metadata strip onto the bottom of the image
(scale bar, kV, WD, detector). For detection we want the strip split off; for
later metric work we may want to parse it. Keep the two concerns separate.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

_TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
_JPEG_MAGIC = (b"\xff\xd8\xff",)


def _read_raw(path: Path) -> np.ndarray:
    with path.open("rb") as fh:
        head = fh.read(4)
    if head.startswith(_TIFF_MAGIC):
        return tifffile.imread(str(path))
    if head.startswith(_JPEG_MAGIC):
        with Image.open(path) as im:
            return np.asarray(im)
    # Last resort: let PIL try — it also handles PNG, BMP, etc.
    with Image.open(path) as im:
        return np.asarray(im)


def _to_grayscale(img: np.ndarray) -> np.ndarray:
    """Collapse image pages/channels to a single 2-D luminance image."""
    while img.ndim > 2:
        if img.ndim == 3 and img.shape[-1] in (1, 3, 4):
            if img.shape[-1] == 1:
                img = img[..., 0]
            else:
                source_dtype = img.dtype
                rgb = img[..., :3].astype(np.float32, copy=False)
                img = np.tensordot(rgb, np.array([0.299, 0.587, 0.114]), axes=([-1], [0]))
                if source_dtype == np.uint8:
                    img = np.rint(img).clip(0, 255).astype(np.uint8)
        else:
            img = img[0]
    if img.ndim != 2:
        raise ValueError(f"expected at least a 2-D image, got shape {img.shape}")
    return img


def load_sem(path: str | Path) -> np.ndarray:
    """Load a SEM image as a 2-D uint8 array.

    Dispatches on magic bytes: real TIFFs go through tifffile+imagecodecs;
    JPEG-disguised-as-TIFF (batch-1) and other common formats go through PIL.
    If the file has multiple pages or channels, the first page's luminance is
    returned.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    img = _read_raw(path)
    img = _to_grayscale(img)

    if img.dtype != np.uint8:
        # Rescale any higher-bit-depth image to uint8 preserving dynamic range.
        lo, hi = np.percentile(img, [0, 100])
        img = ((img - lo) / (hi - lo) * 255.0).clip(0, 255) if hi > lo else np.zeros_like(img)
        img = img.astype(np.uint8)

    return img


def strip_info_bar(img: np.ndarray, min_rows: int = 8) -> tuple[np.ndarray, np.ndarray | None]:
    """Split the image into (sample_region, info_strip).

    Batch-1 SEM info bars are black-background bands with white text and
    spacer rows. The old variance-MAD heuristic missed most real images
    because the final text rows are not a contiguous bottom-anchored outlier
    run. Instead, find the topmost dark, nearly flat row in the bottom half.

    Returns (sample_region, info_strip). If no strip is detected, info_strip
    is None and the original image is returned unchanged.
    """
    if img.ndim != 2:
        raise ValueError(f"expected 2-D image, got shape {img.shape}")

    h = img.shape[0]
    row_std = img.std(axis=1)
    row_mean = img.mean(axis=1)
    dark_flat = (row_std < 10.0) & (row_mean < 80.0)
    dark_flat[: h // 2] = False

    candidates = np.flatnonzero(dark_flat)
    if len(candidates) == 0 or h - candidates[0] < min_rows:
        return img, None

    split = int(candidates[0])
    return img[:split], img[split:]

"""Stage-1 smoke test: load a real SEM TIFF and exercise the I/O layout split."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cathode_cracks import __version__
from cathode_cracks.io import load_sem, strip_info_bar

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "UW_SEM_Images_batch_1" / "8_1_1_lowmass_1.tif"


def test_version_exposed() -> None:
    assert __version__


@pytest.mark.skipif(not SAMPLE.exists(), reason=f"sample image not found at {SAMPLE}")
def test_load_sem_returns_uint8_2d() -> None:
    img = load_sem(SAMPLE)
    assert isinstance(img, np.ndarray)
    assert img.ndim == 2
    assert img.dtype == np.uint8
    # Sanity check against the known batch-1 geometry (960x683).
    assert img.shape == (683, 960)


@pytest.mark.skipif(not SAMPLE.exists(), reason=f"sample image not found at {SAMPLE}")
def test_strip_info_bar_shapes_are_consistent() -> None:
    img = load_sem(SAMPLE)
    sample, strip = strip_info_bar(img)
    # The sample region must still be 2-D and no taller than the input.
    assert sample.ndim == 2
    assert sample.shape[0] <= img.shape[0]
    # If a strip was found, sample+strip rows equal the original row count.
    if strip is not None:
        assert strip.ndim == 2
        assert sample.shape[0] + strip.shape[0] == img.shape[0]
        assert sample.shape[1] == strip.shape[1] == img.shape[1]


@pytest.mark.skipif(not SAMPLE.exists(), reason=f"sample image not found at {SAMPLE}")
def test_strip_info_bar_finds_known_batch_1_bar() -> None:
    img = load_sem(SAMPLE)
    sample, strip = strip_info_bar(img)
    assert sample.shape == (640, 960)
    assert strip is not None
    assert strip.shape == (43, 960)


def test_load_sem_collapses_color_page_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "stack.tif"
    path.write_bytes(b"stub")
    stack = np.zeros((2, 3, 4, 3), dtype=np.uint8)
    stack[0, :, :, 0] = 10
    stack[0, :, :, 1] = 20
    stack[0, :, :, 2] = 30

    monkeypatch.setattr("cathode_cracks.io._read_raw", lambda _path: stack)

    img = load_sem(path)
    assert img.shape == (3, 4)
    assert img.dtype == np.uint8
    assert np.all(img == 18)

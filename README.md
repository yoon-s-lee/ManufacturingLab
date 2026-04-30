# Cathode Crack Detection

Crack detection in SEM images of NMC 622 cathodes. UW Capstone project.

The active code lives in [sam3_single_image/](sam3_single_image/) — a small set of standalone scripts that run Meta's SAM 3 against single SEM tiles in different prompting modes. Earlier experiments (a `cathode_cracks` Python package, a Label Studio annotation stack, MobileSAM trials, and a multi-image batch pipeline) are preserved in [deprecated_code/](deprecated_code/) for reference; nothing in the active path depends on them.

## Layout

```text
sam3_single_image/                  # active SAM 3 experiments (5 standalone entrypoints)
UW_SEM_Images_batch_1_no_infobar/   # SEM input images consumed by the scripts above
deprecated_code/                    # earlier experiments — kept for reference, not on any active path
README.md
.gitignore
```

## Active scripts

Each script is a self-contained entrypoint. Configure inputs by editing the `CONFIG` block at the top of the file, then run with `python sam3_single_image/<name>.py`.

| Script | What it does |
|---|---|
| `run_single_simple.py` | Baseline — runs SAM 3 once on a full SEM tile with a text prompt and dumps masks + per-mask shape features. |
| `run_tile.py` | Tiles the image into overlapping crops, runs SAM 3 on each, stitches masks back to full resolution. Helps when cracks are tiny relative to SAM's 1008×1008 input. |
| `run_particle_to_crack.py` | Two-stage: detect particles first, then re-run SAM inside each particle bbox looking for cracks. Bounds the upsample factor SAM has to work with. |
| `run_with_hints.py` | Same-image prompting with hand-placed positive/negative box hints. Diagnostic for whether SAM's visual prompt path can latch onto cracks at all. |
| `show_crop.py` | Helper for `run_with_hints.py` — clips and saves a crop given an xyxy box, so you can sanity-check candidate hint coordinates before pasting them in. |

Outputs land under `sam3_single_image/output/<image-stem>/<mode>/` and are gitignored.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install numpy torch torchvision Pillow scikit-image
pip install "sam3 @ git+https://github.com/hiroalchem/sam3.git@patched-macos"
```

The scripts also need a Hugging Face account with access to `facebook/sam3` granted; run `huggingface-cli login` once before the first inference.

## About `deprecated_code/`

This folder holds work that's been superseded but is kept intact so it can be revived if needed:

- `src/cathode_cracks/` + `pyproject.toml` + `requirements.txt` — earlier installable package with a `detect-cracks` CLI, ONNX Runtime + PySide6 GUI scaffolding, and a tiered dependency model (`[dev]` / `[train]` / `[annotate]` extras).
- `tests/`, `scripts/` — pytest suite and batch/smoke scripts that import the package above.
- `mobilesam/` — MobileSAM exploration with its own checkpoint and runner.
- `sem-crack-labeling/` — self-hosted Label Studio project for producing brush-mask annotations. See [its README](deprecated_code/sem-crack-labeling/README.md) for the Docker setup.
- `UW_SEM_Images_batch_1/`, `UW_SEM_Images_batch_1_segments/` — original raw SEM batch (with infobars) and the segmentation outputs from `scripts/sam3_batch_segment.py`. Both are gitignored due to size.
- `filter.py`, `preprocess.py`, `preprocess/`, `overlay_*.png` — earlier shelved-from-`sam3_single_image/` scripts that referenced a `run.py` no longer present.
- `configs/`, `data/`, `models/`, `notebooks/`, `packaging/` — empty scaffolding from the package layout.

To revive any of this, move the relevant subtree back to the repo root. The package is editable-installable via `pip install -e ./deprecated_code` once moved (or in place, if you re-point the `setuptools.packages.find` `where` field).

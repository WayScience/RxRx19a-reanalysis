"""
Human-in-the-loop crop spot-check notebook (plan.md section 14, section 16
"Random visual samples look centered and masked correctly", and section 27's
pilot exit criterion "Random crop and segmentation QC looks correct").

Writes a `jupytext` "py:light" notebook script that uses `cytodataframe`
to display a small, seeded-random sample of per-cell crops directly inside
a pandas-like table -- open it in Jupyter and look. Nothing here runs
automatically as part of validation; it is a convenience output for a human
to spot-check segmentation and masking, generated fresh for every run
alongside `qc/images/` (plan.md section 16: "store a small set of QC
images... do not store huge diagnostic image collections" -- this writes
only ``n_samples`` crop images, not the whole run's crops).

The crop JPEGs already have background pixels zeroed outside the
CellProfiler cell mask (see :mod:`rerx.crops`), so the materialized DNA
channel crop alone shows whether segmentation is centered and masked
correctly -- no separate mask overlay is needed.
"""

from pathlib import Path

DEFAULT_N_SAMPLES = 12

_TEMPLATE = '''# %% [markdown]
# # Crop spot check -- {run_dir}
#
# Human visual QC (plan.md section 16 / 27): a small, seeded-random sample
# of per-cell crops from this run, rendered with `cytodataframe` so each
# row shows its DNA (nuclei) channel crop next to its metadata. Crop
# background pixels outside the CellProfiler cell mask are already zeroed
# (see `rerx.crops.crop_and_mask_channel`), so this single channel is
# enough to see whether segmentation looks centered and masked correctly.
#
# Re-run `rerx.qc_notebook.build_crop_review_notebook` to regenerate this
# file with a fresh sample (or a different `n_samples`/`seed`).

# %%
from pathlib import Path

import pandas as pd
from cytodataframe import CytoDataFrame
from PIL import Image

from rerx.crops import decode_jpeg

pd.set_option("display.notebook_repr_html", True)

run_dir = Path(r"{run_dir}")
n_samples = {n_samples}
seed = {seed}

# %% [markdown]
# ## Load crops and take a random sample

# %%
crop_shards = sorted((run_dir / "crops" / "cells").glob("*.parquet"))
if not crop_shards:
    raise FileNotFoundError(f"no crop shards under {{run_dir / 'crops' / 'cells'}}")
crops = pd.concat((pd.read_parquet(p) for p in crop_shards), ignore_index=True)
len_crops = len(crops)

sample_n = min(n_samples, len_crops)
sample = crops.sample(n=sample_n, random_state=seed).reset_index(drop=True)
len_sample = len(sample)
print(
    f"{{len_crops}} total crops across {{len(crop_shards)}} shard(s); "
    f"showing {{len_sample}}"
)

# %% [markdown]
# ## Materialize the DNA channel crop as TIFFs cytodataframe can display

# %%
image_dir = run_dir / "qc" / "images" / "crop_tiffs"
image_dir.mkdir(parents=True, exist_ok=True)

meta_rows = []
for _, row in sample.iterrows():
    arr = decode_jpeg(row["crop_w1_jpeg"])
    tiff_name = f"{{row['Metadata_cell_id']}}_w1.tiff"
    Image.fromarray(arr).save(image_dir / tiff_name)
    meta_rows.append(
        {{
            "Metadata_cell_id": row["Metadata_cell_id"],
            "Metadata_well": row["Metadata_well"],
            "Metadata_site": row["Metadata_site"],
            "Image_FileName_DNA": tiff_name,
            "Image_PathName_DNA": str(image_dir),
        }}
    )
meta = pd.DataFrame(meta_rows)

# %% [markdown]
# ## Display -- each row's DNA channel crop, masked to the segmented cell

# %%
cdf = CytoDataFrame(
    meta,
    display_options={{"render_whole_image": True, "width": 150, "height": 150}},
)
cdf
'''


def notebook_source(
    run_dir: Path, n_samples: int = DEFAULT_N_SAMPLES, seed: int = 0
) -> str:
    """
    Render the jupytext "py:light" crop spot-check notebook source.

    Parameters
    ----------
    run_dir : Path
        Run directory containing ``crops/cells/*.parquet``. Embedded as an
        absolute path so the generated notebook works regardless of where
        it's opened from.
    n_samples : int
        Number of crops to randomly sample and display.
    seed : int
        Random seed for the sample, so re-running without changing inputs
        reproduces the same spot check.

    Returns
    -------
    str
        jupytext "py:light" source text (``# %%`` cell markers), runnable
        directly as a plain Python script or paired into a ``.ipynb`` via
        ``jupytext --to notebook``.
    """
    return _TEMPLATE.format(
        run_dir=Path(run_dir).resolve(), n_samples=n_samples, seed=seed
    )


def build_crop_review_notebook(
    run_dir: Path,
    out_path: Path | None = None,
    n_samples: int = DEFAULT_N_SAMPLES,
    seed: int = 0,
) -> Path:
    """
    Write the crop spot-check notebook script for a run.

    Parameters
    ----------
    run_dir : Path
        Run directory; must already have at least one
        ``crops/cells/*.parquet`` shard (see :mod:`rerx.crops`).
    out_path : Path | None
        Where to write the notebook script. Defaults to
        ``run_dir / "qc" / "images" / "crop_spot_check.py"`` (plan.md
        section 16's ``qc/images/`` QC output location).
    n_samples : int
        Number of crops the generated notebook will sample and display.
    seed : int
        Random seed embedded in the generated notebook's sample.

    Returns
    -------
    Path
        Where the notebook script was written.

    Raises
    ------
    FileNotFoundError
        If ``run_dir`` has no crop shards yet (crops is an earlier
        pipeline stage; this is an output of running the pipeline, not a
        standalone tool with no data to show).
    """
    run_dir = Path(run_dir)
    crop_shards = sorted((run_dir / "crops" / "cells").glob("*.parquet"))
    if not crop_shards:
        raise FileNotFoundError(
            f"no crop shards under {run_dir / 'crops' / 'cells'}; "
            "run the crops pipeline stage first"
        )
    dest = out_path or (run_dir / "qc" / "images" / "crop_spot_check.py")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(notebook_source(run_dir=run_dir, n_samples=n_samples, seed=seed))
    return dest

#!/usr/bin/env python
"""
Build a cytodataframe notebook comparing nuclei segmentation methods on
the same real pilot sites: Cellpose (independent reference) plus every
CellProfiler nuclei-thresholding variant exercised in plan.md's
segmentation review (Otsu, Robust Background, Minimum Cross-Entropy,
Adaptive).

One-off diagnostic tool (same spirit as segmentation_cellpose_check.py),
not part of the production pipeline. Renders each (well, method) pair as
a DNA-channel image with that method's mask boundaries drawn on top, then
emits a jupytext "py:light" notebook with one CytoDataFrame row PER SITE
and one image column PER METHOD -- so every method's segmentation for a
site is visible side by side in a single row, not spread across several
scrolled rows.

Usage (from repo root, after collecting DNA PNGs + mask TIFFs -- see
scripts/segmentation_cellpose_check.py for Cellpose, Alpine/apptainer for
CellProfiler variants):

    uv run --with cytodataframe --with scikit-image --with tifffile \\
        python scripts/segmentation_cytodataframe_compare.py \\
        --dna-images-dir /tmp/segcmp/images_w1 \\
        --mask "cellpose=/tmp/segcmp/cellpose_masks:{well}_cellpose_mask.tiff" \\
        --mask "otsu=/tmp/segcmp/cp_output/otsu:{well}_s1_w1_MaskNuclei.tiff" \\
        --wells AA02 AA08 AA09 AA16 E08 E16 \\
        --out-dir /tmp/segcmp/review

cytodataframe quirk (confirmed against 0.4.0's source): image columns are
only detected by VALUE, scanning for filenames ending in ``.tif``/
``.tiff`` -- a ``.png`` filename in the column is silently never treated
as an image reference at all. Overlays are rendered as TIFF for this
reason. The FileName/PathName column pair only needs "FileName"/
"PathName" as a substring (not an exact "Image_FileName_X" prefix), so
one such pair per method name works for the wide layout below.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# plan.md's segmentation review: AA08/E08 were the two measurably dimmer
# wells where Otsu badly over-segmented; the rest matched Cellpose within
# +/-25%. Carried here only to label the comparison, not to compute it.
DIM_WELLS = {"AA08", "E08"}
CONDITIONS = {
    "AA02": "Active SARS-CoV-2",
    "AA09": "Active SARS-CoV-2",
    "AA08": "UV Inactivated SARS-CoV-2",
    "AA16": "UV Inactivated SARS-CoV-2",
    "E08": "Mock",
    "E16": "Mock",
}

_TEMPLATE = '''# %% [markdown]
# # Segmentation method comparison -- {wells_label}
#
# DNA (w1) images with each method's mask boundaries, one row per site,
# one image column per method: {methods_label}.
#
# Regenerate via `scripts/segmentation_cytodataframe_compare.py`.

# %%
import re

import pandas as pd
from cytodataframe import CytoDataFrame
from IPython.display import HTML, display

meta = pd.read_json(r"{meta_json_path}")

# %%
IMAGE_DISPLAY_PX = 420

cdf = CytoDataFrame(
    meta,
    display_options={{
        "render_whole_image": True,
        "width": IMAGE_DISPLAY_PX,
        "height": IMAGE_DISPLAY_PX,
    }},
)
html = cdf._repr_html_(debug=True)
static_style = (
    f"width:{{IMAGE_DISPLAY_PX}}px;height:{{IMAGE_DISPLAY_PX}}px;"
    f"min-width:{{IMAGE_DISPLAY_PX}}px;max-width:none;object-fit:fill;"
)
html = re.sub(
    r'(<img[^>]*style=")[^"]*(")',
    rf"\g<1>{{static_style}}\g<2>",
    html,
)
display(HTML(html))
'''


def _load_gray(path: Path) -> "np.ndarray":
    import numpy as np
    from PIL import Image

    return np.array(Image.open(path))


def _load_mask(path: Path) -> "np.ndarray":
    import tifffile

    return tifffile.imread(path)


def _rescale_for_display(img: "np.ndarray") -> "np.ndarray":
    """Contrast-stretch to the 1st/99th percentile for visibility only --
    display, not measurement (dim wells are otherwise too dark to judge
    the boundary overlay by eye)."""
    import numpy as np

    lo, hi = np.percentile(img, (1, 99))
    if hi <= lo:
        return img
    stretched = np.clip((img.astype(np.float64) - lo) / (hi - lo), 0, 1)
    return (stretched * 255).astype(np.uint8)


def render_overlay(dna_path: Path, mask_path: Path, out_path: Path) -> int:
    """Render DNA + mask-boundary overlay TIFF; return the object count.

    Saved as TIFF, not PNG: cytodataframe's ``find_image_columns`` only
    recognizes values ending in ``.tif``/``.tiff`` as image filenames
    (checked against cytodataframe 0.4.0's source directly) -- a PNG
    filename in this column is silently never detected as an image
    reference at all, so the notebook renders plain text instead of
    thumbnails.
    """
    import numpy as np
    from PIL import Image
    from skimage.segmentation import mark_boundaries

    dna = _rescale_for_display(_load_gray(dna_path))
    mask = _load_mask(mask_path)
    n_objects = int(len(np.unique(mask)) - (1 if 0 in mask else 0))

    gray_rgb = np.stack([dna, dna, dna], axis=-1) / 255.0
    # Neon green boundary (#39FF14-style electric green, not pastel):
    # maximally saturated so it pops against both the bright nucleus
    # cores and the dim background in low-contrast wells.
    overlay = mark_boundaries(gray_rgb, mask, color=(0.22, 1.0, 0.08), mode="thick")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((overlay * 255).astype(np.uint8)).save(out_path, format="TIFF")
    return n_objects


@dataclass(frozen=True)
class MaskSource:
    name: str
    directory: Path
    filename_pattern: str  # e.g. "{well}_s1_w1_MaskNuclei.tiff"

    @classmethod
    def parse(cls, text: str) -> "MaskSource":
        name, rest = text.split("=", 1)
        directory, filename_pattern = rest.rsplit(":", 1)
        return cls(name=name, directory=Path(directory), filename_pattern=filename_pattern)


def build_comparison(
    dna_images_dir: Path,
    masks: list[MaskSource],
    wells: list[str],
    out_dir: Path,
    dna_filename_pattern: str = "HRCE-1_25_{well}_1_w1.png",
) -> Path:
    """Render every (well, method) overlay and write the WIDE metadata
    JSON the generated notebook reads: one row per well, with one
    ``Image_FileName_<label>``/``Image_PathName_<label>`` column pair
    per method so every method's mask for that site renders in the same
    row.

    Column order within each row is deliberate, not insertion-order
    incidental: all ``Image_FileName_*`` (the actual rendered
    thumbnails) sit adjacent to each other right after the metadata
    columns, so scanning across a row is an unbroken strip of images --
    not image/count/path/image/count/path, which breaks up the visual
    comparison the whole point of this notebook is to support. The
    per-method counts and the ``Image_PathName_*`` columns cytodataframe
    needs internally (but a human never needs to look at) are grouped
    separately, after every image column. Each label is prefixed with
    its 1-based position (``1_cellpose``, ``2_cp_robustbackground``,
    ...) so the column header itself states both the method name and
    its left-to-right order, and column order stays stable regardless
    of how ``--mask`` arguments are supplied.

    Returns the metadata JSON path.
    """
    out_dir = Path(out_dir)
    image_dir = out_dir / "overlays"
    labels = [f"{i + 1}_{source.name}" for i, source in enumerate(masks)]

    rows = []
    for well in wells:
        dna_path = dna_images_dir / dna_filename_pattern.format(well=well)
        if not dna_path.is_file():
            raise FileNotFoundError(f"missing DNA image: {dna_path}")

        image_cols: dict[str, str] = {}
        count_cols: dict[str, int] = {}
        path_cols: dict[str, str] = {}
        for source, label in zip(masks, labels):
            mask_path = source.directory / source.filename_pattern.format(well=well)
            if not mask_path.is_file():
                print(f"skip {well}/{source.name}: no mask at {mask_path}")
                continue
            out_name = f"{well}_{source.name}.tiff"
            n_objects = render_overlay(dna_path, mask_path, image_dir / out_name)
            image_cols[f"Image_FileName_{label}"] = out_name
            count_cols[f"Metadata_n_nuclei_{label}"] = n_objects
            path_cols[f"Image_PathName_{label}"] = str(image_dir)
            print(f"{well}/{source.name}: {n_objects} nuclei -> {out_name}")

        row = {
            "Metadata_well": well,
            "Metadata_condition": CONDITIONS.get(well, "unknown"),
            "Metadata_dim_well": well in DIM_WELLS,
            **image_cols,
            **count_cols,
            **path_cols,
        }
        rows.append(row)

    meta_path = out_dir / "comparison_meta.json"
    meta_path.write_text(json.dumps(rows, indent=2))
    return meta_path


def build_notebook(
    meta_json_path: Path, wells: list[str], methods: list[str], out_path: Path
) -> Path:
    source = _TEMPLATE.format(
        wells_label=", ".join(wells),
        methods_label=", ".join(methods),
        meta_json_path=Path(meta_json_path).resolve(),
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(source)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dna-images-dir", required=True, type=Path)
    parser.add_argument(
        "--mask",
        dest="masks",
        action="append",
        required=True,
        help='NAME=DIR:FILENAME_PATTERN, e.g. "cellpose=/tmp/m:{well}_cellpose_mask.tiff"',
    )
    parser.add_argument("--wells", nargs="+", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--dna-filename-pattern", default="HRCE-1_25_{well}_1_w1.png"
    )
    parser.add_argument("--out-notebook", type=Path, default=None)
    args = parser.parse_args()

    masks = [MaskSource.parse(m) for m in args.masks]
    meta_path = build_comparison(
        dna_images_dir=args.dna_images_dir,
        masks=masks,
        wells=args.wells,
        out_dir=args.out_dir,
        dna_filename_pattern=args.dna_filename_pattern,
    )
    notebook_path = args.out_notebook or (args.out_dir / "segmentation_compare.py")
    build_notebook(meta_path, args.wells, [m.name for m in masks], notebook_path)
    print(f"\nwrote {meta_path}")
    print(f"wrote {notebook_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""
Cellpose-vs-CellProfiler nuclei segmentation check.

A reusable, repeatable way to sanity-check CellProfiler's nuclei
segmentation (``pipelines/rxrx19a.cppipe``'s ``IdentifyPrimaryObjects``
step) against Cellpose's generic "nuclei" model as an independent
reference, on a small sample of real pilot sites. This is a one-off
diagnostic tool, not part of the production pipeline -- Cellpose is
never a pipeline dependency and is pulled in on demand via
``uv run --with cellpose --with scikit-image --with tifffile``.

For the full per-dataset pre-step procedure this script is one part of
(sample selection, threshold-variant pipeline generation, count/contrast
comparison), see ``docs/src/segmentation-config-check.md`` and
``src/rerx/segmentation_check.py``.

Workflow (three steps, run independently so each can be rerun alone):

1. Download the w1 (Hoechst/nuclei) channel PNGs for a chosen set of
   sites from the public RxRx19a bucket::

       uv run python scripts/segmentation_cellpose_check.py download-images \\
           --sites HRCE-1:25:AA02:1 HRCE-1:25:AA02:2 \\
           --out-dir /tmp/segcmp/images_w1

2. Run Cellpose's nuclei model on those images (needs the extra deps,
   CPU is fine for a small sample but slow -- budget a few minutes per
   1024x1024 image)::

       uv run --with cellpose --with scikit-image --with tifffile \\
           python scripts/segmentation_cellpose_check.py run-cellpose \\
           --images-dir /tmp/segcmp/images_w1 \\
           --out-dir /tmp/segcmp/cellpose_masks

   Separately, get the matching CellProfiler ``*_MaskNuclei.tiff`` files
   for the same sites by running the pinned pipeline (apptainer, see
   :func:`rerx.cellprofiler.cellprofiler_command`) -- this needs Alpine
   or another machine with the ``.sif``, so it stays outside this script.

3. Compare the two sets of masks: object counts, pixel-level overlap
   (IoU, matched at >= 0.5 IoU), and area distributions, pooled and per
   site::

       uv run --with scikit-image --with tifffile \\
           python scripts/segmentation_cellpose_check.py compare \\
           --cellprofiler-dir /tmp/segcmp/cp_output \\
           --cellpose-dir /tmp/segcmp/cellpose_masks \\
           --out-json /tmp/segcmp/comparison_summary.json

CellProfiler mask files are expected named ``<well>_s<site>_w1_MaskNuclei.tiff``
(the pipeline's own ``SaveImages`` naming). Cellpose mask files are
named ``<experiment>_<plate>_<well>_<site>_cellpose_mask.tiff`` (this
script's own naming from ``run-cellpose``).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

IMAGES_URL = "https://storage.googleapis.com/rxrx/RxRx19a/images"
PX_SIZE_UM = 0.65
IOU_MATCH_THRESHOLD = 0.5


@dataclass(frozen=True)
class SiteRef:
    experiment: str
    plate: str
    well: str
    site: int

    @classmethod
    def parse(cls, text: str) -> "SiteRef":
        experiment, plate, well, site = text.split(":")
        return cls(experiment, plate, well, int(site))

    @property
    def stem(self) -> str:
        return f"{self.experiment}_{self.plate}_{self.well}_{self.site}"


def cmd_download_images(args: argparse.Namespace) -> None:
    import requests

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sites = [SiteRef.parse(s) for s in args.sites]
    for site in sites:
        url = (
            f"{IMAGES_URL}/{site.experiment}/Plate{site.plate}/"
            f"{site.well}_s{site.site}_w1.png"
        )
        dest = out_dir / f"{site.stem}_w1.png"
        if dest.exists():
            continue
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        print(f"downloaded {dest.name} ({len(resp.content)} bytes)")


def cmd_run_cellpose(args: argparse.Namespace) -> None:
    import numpy as np
    import tifffile
    from cellpose import models
    from PIL import Image

    images_dir = Path(args.images_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model = models.CellposeModel(gpu=False, model_type="nuclei")
    image_paths = sorted(images_dir.glob("*_w1.png"))
    print(f"found {len(image_paths)} images")
    for path in image_paths:
        stem = path.stem.removesuffix("_w1")
        img = np.array(Image.open(path))
        masks, _flows, _styles = model.eval(img, diameter=None, channels=[0, 0])
        tifffile.imwrite(out_dir / f"{stem}_cellpose_mask.tiff", masks.astype(np.int32))
        n_nuclei = int(masks.max())
        print(f"{stem}: {n_nuclei} nuclei detected")


def _match_masks(
    cp_mask: "np.ndarray",
    cpo_mask: "np.ndarray",
    iou_thresh: float = IOU_MATCH_THRESHOLD,
) -> list[float]:
    """Greedy best-IoU match for each CellProfiler object against Cellpose."""
    import numpy as np

    _ = iou_thresh  # thresholding happens at the call site
    cp_labels = [v for v in np.unique(cp_mask) if v != 0]
    ious = []
    for cl in cp_labels:
        cp_region = cp_mask == cl
        overlapping = np.unique(cpo_mask[cp_region])
        overlapping = overlapping[overlapping != 0]
        best_iou = 0.0
        for ol in overlapping:
            cpo_region = cpo_mask == ol
            intersection = np.logical_and(cp_region, cpo_region).sum()
            union = np.logical_or(cp_region, cpo_region).sum()
            iou = intersection / union if union else 0.0
            best_iou = max(best_iou, iou)
        ious.append(best_iou)
    return ious


def _area_stats(values: list[float]) -> dict[str, float] | None:
    import numpy as np

    if not values:
        return None
    arr = np.array(values)
    return {
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "p05": float(np.percentile(arr, 5)),
        "p95": float(np.percentile(arr, 95)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def cmd_compare(args: argparse.Namespace) -> None:
    import numpy as np
    import tifffile
    from skimage.measure import regionprops_table

    cp_dir = Path(args.cellprofiler_dir)
    cpo_dir = Path(args.cellpose_dir)

    cp_files = sorted(cp_dir.glob("*_MaskNuclei.tiff"))
    per_site = []
    cp_areas_all: list[float] = []
    cpo_areas_all: list[float] = []

    for cp_path in cp_files:
        # <well>_s<site>_w1_MaskNuclei.tiff
        name = cp_path.name.removesuffix("_w1_MaskNuclei.tiff")
        well, site_part = name.rsplit("_s", 1)
        site = int(site_part)
        cpo_candidates = list(cpo_dir.glob(f"*_{well}_{site}_cellpose_mask.tiff"))
        if not cpo_candidates:
            print(f"skip {well}_s{site}: no matching cellpose mask")
            continue
        cp_mask = tifffile.imread(cp_path)
        cpo_mask = tifffile.imread(cpo_candidates[0])

        cp_fg = cp_mask > 0
        cpo_fg = cpo_mask > 0
        union_px = np.logical_or(cp_fg, cpo_fg).sum()
        pixel_iou = (
            float(np.logical_and(cp_fg, cpo_fg).sum() / union_px) if union_px else 0.0
        )

        ious = _match_masks(cp_mask, cpo_mask)
        matched = [i for i in ious if i >= IOU_MATCH_THRESHOLD]

        cp_areas = regionprops_table(cp_mask, properties=("area",))["area"] * (
            PX_SIZE_UM**2
        )
        cpo_areas = regionprops_table(cpo_mask, properties=("area",))["area"] * (
            PX_SIZE_UM**2
        )
        cp_areas_all.extend(cp_areas.tolist())
        cpo_areas_all.extend(cpo_areas.tolist())

        cp_median = float(np.median(cp_areas)) if len(cp_areas) else None
        cpo_median = float(np.median(cpo_areas)) if len(cpo_areas) else None
        per_site.append(
            {
                "well": well,
                "site": site,
                "n_cellprofiler": len(cp_areas),
                "n_cellpose": len(cpo_areas),
                "pixel_iou_any_overlap": pixel_iou,
                "n_matched_iou50": len(matched),
                "match_rate_cp_side": (len(matched) / len(ious)) if ious else 0.0,
                "cp_median_area_um2": cp_median,
                "cellpose_median_area_um2": cpo_median,
            }
        )
        print(
            f"{well}_s{site}: CP={len(cp_areas)} Cellpose={len(cpo_areas)} "
            f"pixel_iou={pixel_iou:.3f} matched50={len(matched)}/{len(ious)}"
        )

    summary = {
        "n_sites": len(per_site),
        "per_site": per_site,
        "pooled": {
            "cp_n_total": len(cp_areas_all),
            "cellpose_n_total": len(cpo_areas_all),
            "cp_area_um2": _area_stats(cp_areas_all),
            "cellpose_area_um2": _area_stats(cpo_areas_all),
        },
    }
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {out_json}")
    print(json.dumps(summary["pooled"], indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_dl = sub.add_parser("download-images", help="Download w1 PNGs for given sites")
    p_dl.add_argument(
        "--sites",
        nargs="+",
        required=True,
        help="Sites as experiment:plate:well:site, e.g. HRCE-1:25:AA02:1",
    )
    p_dl.add_argument("--out-dir", required=True)
    p_dl.set_defaults(func=cmd_download_images)

    p_cp = sub.add_parser("run-cellpose", help="Run Cellpose's nuclei model")
    p_cp.add_argument("--images-dir", required=True)
    p_cp.add_argument("--out-dir", required=True)
    p_cp.set_defaults(func=cmd_run_cellpose)

    p_cmp = sub.add_parser("compare", help="Compare CellProfiler vs Cellpose masks")
    p_cmp.add_argument("--cellprofiler-dir", required=True)
    p_cmp.add_argument("--cellpose-dir", required=True)
    p_cmp.add_argument("--out-json", required=True)
    p_cmp.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

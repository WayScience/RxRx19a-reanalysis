#!/usr/bin/env python
"""
Spike 002, step 1: build matched lossless/JPEG-95 crop shards on Alpine.

Re-crops a sample of cells from the staged source PNGs using the EXACT
production crop path (rerx.crops.crop_and_mask_channel -- same masking,
same boundary padding), then writes two matched crop shards for the
same cells:

- lossless: the source pixels, PNG-encoded (decoded losslessly at
  embed time -- this is plan.md's "lossless in-memory crops" side)
- jpeg95: those same pixels re-encoded as JPEG quality 95 (the
  production encoding; also verifies our re-crop reproduces the stored
  production crops before trusting the comparison)

Sample size: N_CELLS cells spread across the first crop shard's sites
(one whole shard covers 24 sites; we sample from a few sites to keep the
embed step quick on CPU).

Usage (host venv, on an Alpine compute node):
    python prepare_lossless_jpeg_crops.py <shard_id>
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

sys.path.insert(0, "/scratch/alpine/dabu57888@xsede.org/rerx/ReRx/src")

from rerx.crops import crop_and_mask_channel, decode_jpeg

RUN_DIR = Path("/pl/active/koala/ReRx/runs/pilot-dev")
SCRATCH_SHARD_ROOT = Path(
    "/scratch/alpine/dabu57888@xsede.org/rerx/pilot-dev"
)
OUT_DIR = RUN_DIR / "baseline" / "spike002_lossless_vs_jpeg"
N_SITES = 8
SHARD_ID = sys.argv[1] if len(sys.argv) > 1 else "HRCE-1-Plate25-0000"
# Re-crop sanity threshold: mean abs pixel difference above this between
# our re-crop and the stored production crop means the re-crop path is
# wrong (real JPEG-95 differences average well below 1).
GROSS_MISMATCH_MEAN_ABS_DIFF = 10

# Site id parts: HRCE-1_25_<well>_<site> splits into 5 parts.
SITE_ID_PARTS = 5


def encode_png(arr: np.ndarray) -> bytes:
    import io

    buf = io.BytesIO()
    Image.fromarray(arr, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def encode_jpeg(arr: np.ndarray, quality: int = 95) -> bytes:
    import io

    buf = io.BytesIO()
    Image.fromarray(arr, mode="L").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shard_dir = SCRATCH_SHARD_ROOT / SHARD_ID
    images_dir = shard_dir / "images" / "HRCE-1" / "Plate25"
    output_dir = shard_dir / "output"

    crops = pd.read_parquet(RUN_DIR / "crops" / "cells" / f"{SHARD_ID}.parquet")
    sites = sorted(crops["Metadata_site_id"].unique())[:N_SITES]
    sample = crops[crops["Metadata_site_id"].isin(sites)].reset_index(drop=True)
    print(f"re-cropping {len(sample)} cells across sites {sites}")

    lossless_rows, jpeg_rows = [], []
    mismatched_pixels = 0
    for site_id, site_rows in sample.groupby("Metadata_site_id", sort=True):
        # Metadata_site_id is HRCE-1_25_<well>_<site>; the staged files
        # are named <well>_s<site>_w<channel>.png.
        parts = str(site_id).split("_")
        well, site = parts[-2], parts[-1]
        mask = np.array(
            Image.open(next(output_dir.glob(f"{well}_s{site}_w1_MaskCells.tiff")))
        )
        channel_imgs = {
            ch: np.array(Image.open(images_dir / f"{well}_s{site}_w{ch}.png"))
            for ch in (1, 2, 3, 4, 5)
        }
        for _, cell in site_rows.iterrows():
            lr, jr = {}, {}
            for col in (
                "Metadata_cell_id",
                "Metadata_site_id",
                "Metadata_object_number",
                "Metadata_center_x",
                "Metadata_center_y",
                "crop_width",
                "crop_height",
            ):
                lr[col] = jr[col] = cell[col]
            for ch in (1, 2, 3, 4, 5):
                pixels = crop_and_mask_channel(
                    channel_imgs[ch],
                    mask,
                    int(cell["Metadata_object_number"]),
                    float(cell["Metadata_center_x"]),
                    float(cell["Metadata_center_y"]),
                    crop_size=int(cell["crop_width"]),
                )
                lr[f"crop_w{ch}_png"] = encode_png(pixels)
                jr[f"crop_w{ch}_jpeg"] = encode_jpeg(pixels)
                # sanity: our re-crop must reproduce the stored production
                # JPEG decode up to JPEG being lossy -- count only gross
                # mismatches (>10 mean abs diff).
                stored = decode_jpeg(cell[f"crop_w{ch}_jpeg"])
                diff = np.abs(stored.astype(int) - pixels.astype(int)).mean()
                if diff > GROSS_MISMATCH_MEAN_ABS_DIFF:
                    mismatched_pixels += 1
            lossless_rows.append(lr)
            jpeg_rows.append(jr)

    print(f"gross re-crop mismatches vs stored crops: {mismatched_pixels}")
    lossless_df = pd.DataFrame(lossless_rows)
    jpeg_df = pd.DataFrame(jpeg_rows)
    lossless_path = OUT_DIR / "crops_lossless.parquet"
    jpeg_path = OUT_DIR / "crops_jpeg95.parquet"
    lossless_df.to_parquet(lossless_path, index=False)
    jpeg_df.to_parquet(jpeg_path, index=False)
    print(f"wrote {lossless_path} ({len(lossless_df)} cells)")
    print(f"wrote {jpeg_path} ({len(jpeg_df)} cells)")


if __name__ == "__main__":
    main()

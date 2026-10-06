#!/usr/bin/env python
"""
Spike 002, step 2: embed lossless vs JPEG-95 crops and compare.

Runs INSIDE containers/morphem.def (torch/transformers live there).
Reads the two matched crop shards built by prepare_lossless_jpeg_crops.py,
decodes each shard's 5-channel images (PNG for lossless, JPEG for q95),
embeds both through the baked-in MorphEm model, and compares the two
embedding matrices cell-by-cell and feature-by-feature.

Usage (inside the container):
    python embed_and_compare.py
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import rerx_morphem as morphem
from PIL import Image

SPIKE_DIR = Path("/rerx/spike002")
RUN_BASELINE = Path("/pl/active/koala/ReRx/runs/pilot-dev/baseline")
SPIKE_OUT = RUN_BASELINE / "spike002_lossless_vs_jpeg"
RESULTS_JSON = SPIKE_DIR / "results.json"


def decode_channels(crops: pd.DataFrame, cols: list[str]) -> "np.ndarray":
    """Decode one shard's 5 channel-image columns into (N, 5, H, W)."""
    n = len(crops)
    h = int(crops["crop_height"].iloc[0])
    w = int(crops["crop_width"].iloc[0])
    out = np.zeros((n, 5, h, w), dtype=np.float32)
    for i, row in enumerate(crops[cols].itertuples(index=False)):
        for c, data in enumerate(row):
            out[i, c] = np.array(Image.open(io.BytesIO(data)).convert("L"))
    return out


def embed(tensor: "np.ndarray") -> np.ndarray:
    """Embed a decoded (N, 5, H, W) tensor with MorphEm (Bag of Channels)."""
    import torch

    model = morphem.load_model()
    device = morphem._select_device()
    # embed_crops_batched takes a torch tensor and handles batching,
    # resize to 224, and the model-card transforms.
    t = torch.from_numpy(tensor)
    elapsed, features = morphem.embed_crops_batched(
        model, t, device, batch_size=morphem.MORPHEM_DEFAULT_BATCH_SIZE
    )
    print(f"embedded {tensor.shape[0]} cells in {elapsed:.1f}s on {device}")
    return features


def main() -> None:
    lossless = pd.read_parquet(SPIKE_OUT / "crops_lossless.parquet")
    jpeg = pd.read_parquet(SPIKE_OUT / "crops_jpeg95.parquet")
    assert len(lossless) == len(jpeg)
    assert (lossless["Metadata_cell_id"] == jpeg["Metadata_cell_id"]).all()
    n = len(lossless)
    print(f"comparing {n} matched cells")

    png_cols = [f"crop_w{i}_png" for i in (1, 2, 3, 4, 5)]
    jpeg_cols = [f"crop_w{i}_jpeg" for i in (1, 2, 3, 4, 5)]

    emb_lossless = embed(decode_channels(lossless, png_cols))
    emb_jpeg = embed(decode_channels(jpeg, jpeg_cols))

    assert emb_lossless.shape == emb_jpeg.shape
    diff = np.abs(emb_lossless - emb_jpeg)
    scale = np.std(emb_lossless, axis=0)
    scale[scale == 0] = 1.0
    rel_diff = diff / scale

    # Per-feature correlation across cells (all 1,920 dims pooled).
    def col_corr(a: "np.ndarray", b: "np.ndarray") -> float:
        ca, cb = a - a.mean(0), b - b.mean(0)
        num = (ca * cb).sum(0)
        den = np.sqrt((ca**2).sum(0) * (cb**2).sum(0))
        den[den == 0] = 1.0
        return float(np.mean(num / den))

    # The pipeline's conclusions operate on SITE-LEVEL MEDIANS (buscar,
    # Recursion correlations -- see rerx.finalize / morphem.py
    # site_medians), so "acceptable for this analysis" must be judged
    # there too, not only per-cell.
    sites = lossless["Metadata_site_id"].to_numpy()
    uniq_sites = pd.unique(sites)
    med_a = np.stack([np.median(emb_lossless[sites == s], axis=0) for s in uniq_sites])
    med_b = np.stack([np.median(emb_jpeg[sites == s], axis=0) for s in uniq_sites])
    med_diff = np.abs(med_a - med_b)
    med_scale = np.std(med_a, axis=0)
    med_scale[med_scale == 0] = 1.0

    # Spearman between the two encodings' site-distance matrices -- the
    # exact statistic the phenotypic report uses to compare profilers.
    # Hand-rolled (rank + Pearson): the morphem container ships no scipy.
    def _ranks(v: "np.ndarray") -> "np.ndarray":
        order = np.argsort(v)
        ranks = np.empty(len(v), dtype=np.float64)
        ranks[order] = np.arange(len(v), dtype=np.float64)
        return ranks

    def _spearman(x: "np.ndarray", y: "np.ndarray") -> float:
        rx, ry = _ranks(x), _ranks(y)
        rx, ry = rx - rx.mean(), ry - ry.mean()
        den = np.sqrt((rx**2).sum() * (ry**2).sum())
        return float((rx * ry).sum() / den) if den else 1.0

    dist_a = np.linalg.norm(med_a[:, None] - med_a[None, :], axis=-1)
    dist_b = np.linalg.norm(med_b[:, None] - med_b[None, :], axis=-1)
    triu = np.triu_indices(len(uniq_sites), k=1)
    rho = _spearman(dist_a[triu], dist_b[triu])

    results = {
        "n_cells": int(n),
        "n_sites": len(uniq_sites),
        "n_features": int(emb_lossless.shape[1]),
        "max_abs_diff": float(diff.max()),
        "mean_abs_diff": float(diff.mean()),
        "p99_abs_diff": float(np.percentile(diff, 99)),
        "mean_rel_diff_vs_feature_std": float(rel_diff.mean()),
        "max_rel_diff_vs_feature_std": float(rel_diff.max()),
        "mean_perfeature_pearson_across_cells": col_corr(emb_lossless, emb_jpeg),
        # site-level (the analysis granularity)
        "site_median_mean_rel_diff_vs_feature_std": float(
            (med_diff / med_scale).mean()
        ),
        "site_distance_matrix_spearman": float(rho),
    }
    RESULTS_JSON.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

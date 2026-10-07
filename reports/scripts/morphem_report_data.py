"""Compute MorphEm report data from the pilot-morphem run.

Writes reports/data/morphem_report.json with:
- cell-level QC (counts, NaNs, duplicate cell IDs)
- site-level median embeddings + PCA coords
- Spearman site-distance and kNN-overlap vs CellProfiler and DL embeddings

Usage:

    python morphem_report_data.py <run-dir> [output.json]

<run-dir> is the durable run directory (for example
/pl/active/koala/ReRx/runs/pilot-dev) holding profiles/ underneath.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_RUN_DIR_ARG = 1
_OUT_ARG = 2

RUN = (
    Path(sys.argv[_RUN_DIR_ARG])
    if len(sys.argv) > _RUN_DIR_ARG
    else Path("runs/pilot-dev")
)
OUT = (
    Path(sys.argv[_OUT_ARG])
    if len(sys.argv) > _OUT_ARG
    else Path("morphem_report.json")
)


def site_medians(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    feats = df[feature_cols]
    grouped = feats.groupby(df["Metadata_site_id"]).median()
    return pd.DataFrame(grouped)


def pca2(mat: np.ndarray, seed: int = 19) -> np.ndarray:
    x = mat - mat.mean(axis=0)
    # SVD on the transposed covariance-free matrix
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    return x @ vt[:2].T


def main() -> None:
    fs = pd.read_parquet(
        RUN
        / "profiles/morphem/feature_selected/"
        / "experiment=HRCE-1/plate=25/profiles.parquet"
    )
    feature_cols = [c for c in fs.columns if c.startswith("Morphem_")]

    qc = {
        "n_cells": len(fs),
        "n_raw_features": 1920,
        "n_selected_features": len(feature_cols),
        "nan_cells": int(fs[feature_cols].isna().any(axis=1).sum()),
        "duplicate_cell_ids": int(fs["Metadata_cell_id"].duplicated().sum()),
    }

    # Site medians and PCA
    site_key = "Metadata_site_id" if "Metadata_site_id" in fs.columns else None
    if site_key is None:
        # construct from experiment/plate/well/site
        exp = fs["Metadata_Experiment"].astype(str)
        pl = fs["Metadata_Plate"].astype(str)
        well = fs["Metadata_Well"].astype(str)
        site = fs["Metadata_Site"].astype(str)
        fs = fs.assign(Metadata_site_id=exp + "_" + pl + "_" + well + "_" + site)
        site_key = "Metadata_site_id"

    med = site_medians(fs, feature_cols)
    coords = pca2(med.values)

    # per-site control / well / perturbation from the first cell of each site
    meta_cols = [
        "Metadata_rxrx_control_type",
        "Metadata_Well",
        "Metadata_perturbation",
    ]
    firsts = fs.groupby(site_key)[meta_cols].first()
    pca_points = [
        {
            "x": round(float(coords[i, 0]), 3),
            "y": round(float(coords[i, 1]), 3),
            "control": str(firsts.iloc[i]["Metadata_rxrx_control_type"]),
            "well": str(firsts.iloc[i]["Metadata_Well"]),
            "perturbation": str(firsts.iloc[i]["Metadata_perturbation"]),
            "site_id": str(med.index[i]),
        }
        for i in range(len(med))
    ]

    # Comparison vs CellProfiler (already site-median) vs DL embeddings
    cp = pd.read_parquet(
        RUN
        / "profiles/cellprofiler/feature_selected/"
        / "experiment=HRCE-1/plate=25/profiles.parquet"
    )
    cp_site = (
        cp[[c for c in cp.columns if c.startswith(("Cells_", "Cytoplasm_", "Nuclei_"))]]
        .groupby(
            cp["Metadata_Experiment"].astype(str)
            + "_"
            + cp["Metadata_Plate"].astype(str)
            + "_"
            + cp["Metadata_Well"].astype(str)
            + "_"
            + cp["Metadata_Site"].astype(str)
        )
        .median()
    )
    cp_site = cp_site.loc[cp_site.index.isin(med.index)]

    def spearman_site_distance(a: pd.DataFrame, b: pd.DataFrame) -> float:
        common = a.index.intersection(b.index)

        def pdist(m: pd.DataFrame) -> np.ndarray:
            m = m.loc[common].values
            m = m - m.mean(axis=0)
            n = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-12)
            d = 1 - n @ n.T
            iu = np.triu_indices(len(common), k=1)
            return d[iu]

        da, db = pdist(a), pdist(b)
        from scipy.stats import spearmanr

        return float(spearmanr(da, db).statistic)

    def knn_overlap(a: pd.DataFrame, b: pd.DataFrame, k: int = 10) -> float:
        common = a.index.intersection(b.index)

        def neighbors(m: pd.DataFrame) -> np.ndarray:
            mat: np.ndarray = m.loc[common].values
            mat = mat - mat.mean(axis=0)
            mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
            d = mat @ mat.T
            np.fill_diagonal(d, -np.inf)
            return np.argsort(-d, axis=1)[:, :k]

        na, nb = neighbors(a), neighbors(b)
        overlaps = [len(set(na[i]) & set(nb[i])) / k for i in range(len(common))]
        return float(np.mean(overlaps))

    result = {
        **qc,
        "pca_points": pca_points,
        "spearman_vs_cellprofiler": round(spearman_site_distance(med, cp_site), 3),
        "knn_overlap_vs_cellprofiler_k10": round(knn_overlap(med, cp_site), 3),
    }

    OUT.write_text(json.dumps(result))
    print(json.dumps({k: v for k, v in result.items() if k != "pca_points"}, indent=2))


if __name__ == "__main__":
    main()

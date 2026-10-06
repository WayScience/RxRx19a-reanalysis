"""
Compute UMAP coordinates for the pilot report plots.

Replaces the earlier PCA 2-D coordinates in the report JSONs. Runs on
the local copies of the pilot data in reports/data/ (full CellProfiler
feature-selected table, full Recursion site embeddings, MorphEm
feature-selected sample -- 3,333 of 33,330 cells, all 168 sites).

Usage (repo root):

    uv run --frozen --with umap-learn python reports/scripts/umap_report_data.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import umap

HERE = Path(__file__).resolve().parents[1] / "data"
OUT = HERE / "umap_report.json"

CP_PREFIXES = ("Cells_", "Cytoplasm_", "Nuclei_")
META_COLS = [
    "Metadata_Well",
    "Metadata_perturbation",
    "Metadata_rxrx_control_type",
]
SEED = 19


def standardize(x: np.ndarray) -> np.ndarray:
    mu = x.mean(axis=0)
    sd = x.std(axis=0)
    sd[sd == 0] = 1.0
    return (x - mu) / sd


def umap2(mat: np.ndarray, label: str) -> np.ndarray:
    reducer = umap.UMAP(n_components=2, n_neighbors=15, min_dist=0.1, random_state=SEED)
    coords = reducer.fit_transform(standardize(mat))
    print(f"{label}: {mat.shape[0]} rows x {mat.shape[1]} features -> umap")
    return coords


def med_by_site(df: pd.DataFrame, feats: list[str], keys: pd.Series) -> pd.DataFrame:
    x = df[feats].fillna(0.0).copy()
    x["_s"] = keys.values
    return x.groupby("_s").median()


def main() -> None:
    cp = pd.read_parquet(
        HERE / "feature_selected/experiment=HRCE-1/plate=25/profiles.parquet"
    )
    emb = pd.read_parquet(HERE / "recursion_site_embeddings.parquet")
    morphem = pd.read_parquet(HERE / "morphem_feature_selected_sample.parquet")

    cpfeat = [c for c in cp.columns if c.startswith(CP_PREFIXES)]
    embfeat = [c for c in emb.columns if c.startswith("feature_")]
    morphemfeat = [c for c in morphem.columns if c.startswith("Morphem_")]

    # Site keys in the site_id format the reports already use:
    # "HRCE-1_25_<well>_<site>"
    cp_site = (
        cp["Metadata_Experiment"].astype(str)
        + "_"
        + cp["Metadata_Plate"].astype(str)
        + "_"
        + cp["Metadata_Well"].astype(str)
        + "_"
        + cp["Metadata_Site"].astype(str)
    )

    cp_med = med_by_site(cp, cpfeat, cp_site)
    morphem_med = med_by_site(morphem, morphemfeat, morphem["Metadata_site_id"])
    emb = emb.set_index("site_id")

    # CellProfiler: per-cell metadata for the first cell of each site
    # (well / perturbation / control are constant within a site).
    site_meta = cp.assign(_s=cp_site.values).groupby("_s").first()[META_COLS]

    payload: dict = {}

    coords = umap2(cp_med.values, "cellprofiler")
    payload["cellprofiler_umap"] = [
        {
            "x": round(float(coords[i, 0]), 3),
            "y": round(float(coords[i, 1]), 3),
            "control": row["Metadata_rxrx_control_type"],
            "well": row["Metadata_Well"],
            "perturbation": row["Metadata_perturbation"],
            "site_id": s,
        }
        for i, (s, row) in enumerate(site_meta.iterrows())
    ]

    common = sorted(set(emb.index) & set(cp_med.index))
    coords = umap2(emb.loc[common, embfeat].values, "recursion")
    payload["recursion_umap"] = [
        {
            "x": round(float(coords[i, 0]), 3),
            "y": round(float(coords[i, 1]), 3),
            "control": site_meta.loc[s, "Metadata_rxrx_control_type"],
            "well": site_meta.loc[s, "Metadata_Well"],
            "perturbation": site_meta.loc[s, "Metadata_perturbation"],
            "site_id": s,
        }
        for i, s in enumerate(common)
    ]

    available = [c for c in META_COLS if c in morphem.columns]
    morphem_meta = (
        morphem.groupby("Metadata_site_id").first()[available]
        if available
        else morphem.groupby("Metadata_site_id").first()
    )
    coords = umap2(morphem_med.values, "morphem")
    payload["morphem_umap"] = []
    for i, s in enumerate(morphem_med.index):
        row = morphem_meta.loc[s]
        ctrl = (
            row["Metadata_rxrx_control_type"]
            if "Metadata_rxrx_control_type" in morphem.columns
            else ""
        )
        well = row["Metadata_Well"] if "Metadata_Well" in morphem.columns else ""
        pert = row.get("Metadata_perturbation", "")
        payload["morphem_umap"].append(
            {
                "x": round(float(coords[i, 0]), 3),
                "y": round(float(coords[i, 1]), 3),
                "control": str(ctrl),
                "well": str(well),
                "perturbation": str(pert),
                "site_id": str(s),
            }
        )

    OUT.write_text(json.dumps(payload, indent=1))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

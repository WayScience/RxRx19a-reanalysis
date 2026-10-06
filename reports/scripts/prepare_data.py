"""
Pilot report data prep: builds compact JSON payloads for the two HTML
reports (phenotypic/embeddings comparison, buscar reversal scoring).

Reads the pilot run's already-downloaded local copies under
reports/data/ (synced from Alpine) and writes reports/data/*.json.
Not part of the shipped rerx package -- a one-off report-building
script, run locally with `uv run python reports/scripts/prepare_data.py`.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.decomposition import PCA
from sklearn.metrics import pairwise_distances

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

MORPH_PREFIXES = ("Cells_", "Cytoplasm_", "Nuclei_")
CONTROL_COLORS = {
    "mock": "#2f9e6e",
    "uv": "#c78a1e",
    "active_untreated": "#c74444",
    "treated": "#6d7bd6",
}


def load_cp_profiles() -> pd.DataFrame:
    return pd.read_parquet(
        DATA_DIR
        / "feature_selected"
        / "experiment=HRCE-1"
        / "plate=25"
        / "profiles.parquet"
    )


def load_normalized_profiles() -> pd.DataFrame:
    return pd.read_parquet(
        DATA_DIR / "normalized" / "experiment=HRCE-1" / "plate=25" / "profiles.parquet"
    )


def load_embeddings() -> pd.DataFrame:
    return pd.read_parquet(DATA_DIR / "recursion_site_embeddings.parquet")


def site_level_cp(df: pd.DataFrame) -> pd.DataFrame:
    """Median-aggregate feature-selected cell profiles to one row per site."""
    feat_cols = [c for c in df.columns if c.startswith(MORPH_PREFIXES)]
    site_id = (
        df["Metadata_Experiment"].astype(str)
        + "_"
        + df["Metadata_Plate"].astype(str)
        + "_"
        + df["Metadata_Well"].astype(str)
        + "_"
        + df["Metadata_Site"].astype(str)
    )
    out = df[feat_cols].copy()
    out["site_id"] = site_id
    out["Metadata_Well"] = df["Metadata_Well"]
    out["Metadata_rxrx_control_type"] = df["Metadata_rxrx_control_type"]
    out["Metadata_perturbation"] = df["Metadata_perturbation"]
    group_cols = [
        "site_id",
        "Metadata_Well",
        "Metadata_rxrx_control_type",
        "Metadata_perturbation",
    ]
    return out.groupby(group_cols, as_index=False)[feat_cols].median()


def standardize(features: np.ndarray) -> np.ndarray:
    centered = features - features.mean(axis=0)
    std = features.std(axis=0)
    std[std == 0] = 1.0
    return centered / std


def pca_2d(features: np.ndarray, seed: int = 0) -> np.ndarray:
    # Standardize first: PCA is scale-sensitive, and Pycytominer's
    # feature-selected columns are not all on the same scale (raw
    # texture features can run into the hundreds). The randomized SVD
    # solver also overflows on the raw scale for this many rows/cols,
    # so use the exact solver on standardized data.
    scaled = standardize(features)
    pca = PCA(n_components=2, random_state=seed, svd_solver="full")
    return pca.fit_transform(scaled)


def build_phenotypic_payload() -> dict:
    cp = load_cp_profiles()
    emb = load_embeddings()

    cp_site = site_level_cp(cp)
    feat_cols = [c for c in cp_site.columns if c.startswith(MORPH_PREFIXES)]

    # Match sites present in both representations.
    matched = cp_site.merge(emb, on="site_id", how="inner", suffixes=("", "_emb"))
    emb_cols = [c for c in emb.columns if c.startswith("feature_")]

    cp_coords = pca_2d(matched[feat_cols].fillna(0.0).to_numpy())
    emb_coords = pca_2d(matched[emb_cols].fillna(0.0).to_numpy())

    # Pairwise site-distance geometry comparison (plan.md section 17).
    cp_dist = pairwise_distances(standardize(matched[feat_cols].fillna(0.0).to_numpy()))
    emb_dist = pairwise_distances(standardize(matched[emb_cols].fillna(0.0).to_numpy()))
    iu = np.triu_indices_from(cp_dist, k=1)
    rho, _p = spearmanr(cp_dist[iu], emb_dist[iu])

    # k-NN overlap at k=10.
    k = 10

    def knn_sets(dist: np.ndarray, k: int) -> list[set]:
        return [set(np.argsort(row)[1 : k + 1]) for row in dist]

    cp_knn = knn_sets(cp_dist, k)
    emb_knn = knn_sets(emb_dist, k)
    overlaps = [len(a & b) / k for a, b in zip(cp_knn, emb_knn, strict=True)]
    mean_knn_overlap = float(np.mean(overlaps))

    def points(coords: np.ndarray) -> list[dict]:
        return [
            {
                "x": round(float(coords[i, 0]), 3),
                "y": round(float(coords[i, 1]), 3),
                "control": row["Metadata_rxrx_control_type"],
                "well": row["Metadata_Well"],
                "perturbation": row["Metadata_perturbation"],
                "site_id": row["site_id"],
            }
            for i, (_, row) in enumerate(matched.iterrows())
        ]

    control_counts = matched["Metadata_rxrx_control_type"].value_counts().to_dict()

    return {
        "n_sites_matched": len(matched),
        "n_cp_features": len(feat_cols),
        "n_embedding_dims": len(emb_cols),
        "control_counts": control_counts,
        "spearman_rho": round(float(rho), 3),
        "mean_knn_overlap_k10": round(mean_knn_overlap, 3),
        "cellprofiler_pca": points(cp_coords),
        "recursion_pca": points(emb_coords),
        "colors": CONTROL_COLORS,
    }


def build_buscar_payload() -> dict:
    import polars as pl

    from rerx.buscar import (
        BuscarConfig,
        build_buscar_signatures,
        calculate_scores_summary,
    )
    from rerx.validate import check_control_separation

    normalized = load_normalized_profiles()
    feat_cols = [c for c in normalized.columns if c.startswith(MORPH_PREFIXES)]

    # Single-cell QC gate result (same call the pipeline itself makes).
    sc_gate = check_control_separation(normalized)

    # Well-level aggregate (median per well x perturbation) -- the
    # replicate unit buscar is designed to score at.
    agg = (
        normalized.groupby(["Metadata_Well", "Metadata_perturbation"])[feat_cols]
        .median()
        .reset_index()
    )
    agg_gate = check_control_separation(
        agg.rename(
            columns={"Metadata_perturbation": "Metadata_rxrx_control_type"}
        ).assign(
            Metadata_rxrx_control_type=lambda d: d["Metadata_rxrx_control_type"].where(
                d["Metadata_rxrx_control_type"].isin(["mock", "active_untreated"]),
                other="treated",
            )
        )
    )

    pl_agg = pl.from_pandas(agg)
    cfg = BuscarConfig()
    on_sig, off_sig, ambiguous = build_buscar_signatures(pl_agg, cfg)
    scores = calculate_scores_summary(pl_agg, on_sig, off_sig, cfg).to_pandas()

    scores = scores[scores["perturbation"] != cfg.target_state].copy()
    scores["compound"] = scores["perturbation"].str.split("__").str[0]
    scores["concentration"] = pd.to_numeric(
        scores["perturbation"].str.split("__").str[1], errors="coerce"
    )
    is_control = scores["compound"].isin(["active_untreated", "uv"])
    scores["group"] = np.where(is_control, scores["compound"], scores["compound"])

    cell_counts = normalized.groupby("Metadata_perturbation").size().to_dict()
    well_counts = agg.groupby("Metadata_perturbation").size().to_dict()

    rows = []
    for _, r in scores.iterrows():
        rows.append(
            {
                "perturbation": r["perturbation"],
                "compound": r["compound"],
                "concentration": (
                    None if pd.isna(r["concentration"]) else float(r["concentration"])
                ),
                "on_score": round(float(r["on_buscar_scores"]), 4),
                "off_score": round(float(r["off_buscar_scores"]), 4),
                "is_reference": bool(r["is_reference_distance"]),
                "cell_count": int(cell_counts.get(r["perturbation"], 0)),
                "well_count": int(well_counts.get(r["perturbation"], 0)),
            }
        )

    return {
        "target_state": cfg.target_state,
        "ref_state": cfg.ref_state,
        "on_signature_count": len(on_sig),
        "off_signature_count": len(off_sig),
        "ambiguous_count": len(ambiguous),
        "total_features": len(feat_cols),
        "single_cell_gate": {
            "skipped": sc_gate.skipped,
            "passed": sc_gate.passed,
            "median_abs_effect_size": (
                None
                if sc_gate.median_abs_effect_size is None
                else round(sc_gate.median_abs_effect_size, 3)
            ),
            "checked_features": sc_gate.checked_features,
        },
        "well_level_gate": {
            "skipped": agg_gate.skipped,
            "passed": agg_gate.passed,
            "median_abs_effect_size": (
                None
                if agg_gate.median_abs_effect_size is None
                else round(agg_gate.median_abs_effect_size, 3)
            ),
            "checked_features": agg_gate.checked_features,
        },
        "scores": rows,
    }


def main() -> None:
    phenotypic = build_phenotypic_payload()

    # Merge the side payloads that phenotypic_overview.html renders but
    # prepare_data.py itself does not compute: UMAP coordinates
    # (umap_report_data.py) and the MorphEm section (morphem_report_data.py
    # plus the MorphEm UMAP points). Without this merge, re-running
    # embed_data.py on a freshly generated phenotypic payload silently
    # drops those sections from the committed report.
    umap = json.loads((DATA_DIR / "umap_report.json").read_text())
    morphem = json.loads((DATA_DIR / "morphem_report.json").read_text())
    phenotypic["cellprofiler_umap"] = umap["cellprofiler_umap"]
    phenotypic["recursion_umap"] = umap["recursion_umap"]
    morphem = dict(morphem)
    morphem["umap_points"] = umap["morphem_umap"]
    phenotypic["morphem"] = morphem

    (DATA_DIR / "phenotypic_report_data.json").write_text(
        json.dumps(phenotypic, indent=None)
    )
    print(f"phenotypic payload: {len(phenotypic['cellprofiler_pca'])} sites")

    buscar = build_buscar_payload()
    (DATA_DIR / "buscar_report_data.json").write_text(json.dumps(buscar, indent=None))
    print(f"buscar payload: {len(buscar['scores'])} perturbations")


if __name__ == "__main__":
    main()

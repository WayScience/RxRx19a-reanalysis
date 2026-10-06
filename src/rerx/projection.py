"""
Recursion-style on/off-perturbation projection scoring for ReRx.

Implements the small-molecule screening post-processing described in
Cuccarese et al. 2020 (biorxiv 2020.08.02.233064, "Functional immune
mapping with deep-learning enabled phenomics applied to
immunomodulatory and COVID-19 drug discovery"):

1. Compute the vector pointing between the barycenters of the
   untreated (mock) and perturbed (active, untreated-with-virus)
   control populations.
2. Decompose each embedding into the signed scalar projection onto
   that vector (the on-perturbation score) and the scalar rejection
   (the off-perturbation score).
3. Normalize so the mean on-perturbation score is 0 for the untreated
   condition and 1 for the perturbed condition.
4. Assess separation of the untreated and perturbed controls along the
   on-perturbation axis with a Z-factor.

This is an alternative to buscar scoring (rerx.buscar), following the
dataset's published analysis method.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Control perturbation labels, matching rerx.buscar defaults.
UNTREATED_CONTROL = "mock"
PERTURBED_CONTROL = "active_untreated"

# Column holding each row's perturbation identifier.
PERTURBATION_COL = "Metadata_perturbation"


def _feature_matrix(profiles: pd.DataFrame, feature_cols: list[str]) -> np.ndarray:
    """Feature matrix with imputed NaNs (column means), float64."""
    mat = profiles[feature_cols].to_numpy(dtype=np.float64, copy=True)
    col_means = np.nanmean(mat, axis=0)
    nan_rows, nan_cols = np.where(np.isnan(mat))
    mat[nan_rows, nan_cols] = np.take(col_means, nan_cols)
    return mat


def control_separation_zfactor(
    on_scores_control: np.ndarray,
    on_scores_perturbed: np.ndarray,
) -> float:
    """
    Z-factor separating two control populations on the on-perturbation axis.

    Parameters
    ----------
    on_scores_control : np.ndarray
        On-perturbation scores of untreated (mock) rows.
    on_scores_perturbed : np.ndarray
        On-perturbation scores of perturbed control rows.

    Returns
    -------
    float
        Z-factor (Z' = 1 - 3 * (sd1 + sd2) / |mean1 - mean2|).
        Higher is better; <= 0 means the controls do not separate.
    """
    mean_c = float(np.mean(on_scores_control))
    mean_p = float(np.mean(on_scores_perturbed))
    sd_c = float(np.std(on_scores_control, ddof=1))
    sd_p = float(np.std(on_scores_perturbed, ddof=1))
    gap = abs(mean_p - mean_c)
    if gap == 0:
        return 0.0
    return 1.0 - 3.0 * (sd_c + sd_p) / gap


def projection_scores(
    profiles: pd.DataFrame,
    feature_cols: list[str] | None = None,
    untreated_control: str = UNTREATED_CONTROL,
    perturbed_control: str = PERTURBED_CONTROL,
    perturbation_col: str = PERTURBATION_COL,
) -> pd.DataFrame:
    """
    Score each perturbation with the Recursion on/off-projection method.

    Parameters
    ----------
    profiles : pd.DataFrame
        Annotated single-cell (or well-level) profiles with
        ``perturbation_col`` and morphology feature columns.
    feature_cols : list[str] | None
        Feature columns to use. ``None`` selects all non-metadata
        (non-``Metadata_``) columns.
    untreated_control : str
        Untreated (healthy) control label (mock).
    perturbed_control : str
        Perturbed (disease) control label (active_untreated).
    perturbation_col : str
        Metadata column holding perturbation identifiers.

    Returns
    -------
    pd.DataFrame
        One row per perturbation, sorted by name:
        ``perturbation``, ``cell_count``,
        ``mean_on_perturbation_score``, ``mean_off_perturbation_score``.

    Raises
    ------
    ValueError
        If either control population is missing.
    """
    perturbations = profiles[perturbation_col]
    control_rows = profiles.loc[profiles[perturbation_col] == untreated_control]
    perturbed_rows = profiles.loc[profiles[perturbation_col] == perturbed_control]
    if control_rows.empty:
        raise ValueError(f"no rows for untreated control {untreated_control!r}")
    if perturbed_rows.empty:
        raise ValueError(f"no rows for perturbed control {perturbed_control!r}")

    if feature_cols is None:
        feature_cols = [
            c
            for c in profiles.columns
            if not c.startswith("Metadata_")
            and pd.api.types.is_numeric_dtype(profiles[c])
        ]

    feats = _feature_matrix(profiles, feature_cols)
    control_centre = _feature_matrix(control_rows, feature_cols).mean(axis=0)
    perturbed_centre = _feature_matrix(perturbed_rows, feature_cols).mean(axis=0)

    # Vector from untreated to perturbed barycenter; its length sets
    # the on-score scale so controls land on 0 and 1.
    axis_vec = perturbed_centre - control_centre
    axis_len = float(np.linalg.norm(axis_vec))
    if axis_len == 0:
        raise ValueError("untreated and perturbed barycenters coincide")

    # Signed projection and rejection of every row onto the axis.
    # Apple Accelerate BLAS (macOS 26 + numpy) emits spurious
    # "divide by zero/overflow/invalid value in matmul" RuntimeWarnings
    # for this GEMV on ordinary data (verified: results match a manual
    # row-by-row dot product exactly; float32 is silent). Silence only
    # those three signals, scoped to this computation, so real
    # numerical problems elsewhere still surface.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        row_norms = np.linalg.norm(feats - control_centre, axis=1)
        on_from_control = (feats - control_centre) @ axis_vec / axis_len
    if not (np.isfinite(row_norms).all() and np.isfinite(on_from_control).all()):
        raise ValueError(
            "projection produced non-finite scores; check the feature "
            "matrix for inf/nan values"
        )
    off = np.sqrt(
        np.clip(
            row_norms**2 - on_from_control**2,
            0.0,
            None,
        )
    )
    # Normalize on-scores: 0 at the untreated barycenter, 1 at the
    # perturbed barycenter (paper normalization).
    on_normalized = on_from_control / axis_len

    scored = pd.DataFrame(
        {
            "perturbation": perturbations.to_numpy(),
            "on_perturbation": on_normalized,
            "off_perturbation": off,
        }
    )
    summary = (
        scored.groupby("perturbation")
        .agg(
            cell_count=("on_perturbation", "size"),
            mean_on_perturbation_score=("on_perturbation", "mean"),
            mean_off_perturbation_score=("off_perturbation", "mean"),
        )
        .reset_index()
        .sort_values("perturbation")
        .reset_index(drop=True)
    )
    return summary

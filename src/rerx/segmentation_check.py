"""
Segmentation pre-step checks: dataset-agnostic logic for picking a
representative sample of sites and comparing two sets of object counts.

This module holds the pure, testable pieces of the Cellpose-vs-
CellProfiler threshold-strategy check described in plan.md's pilot
segmentation review. The reusable end-to-end recipe (download images,
run Cellpose, run CellProfiler threshold variants, compare) lives in
``scripts/segmentation_cellpose_check.py`` and
``docs/segmentation-config-check.md``; this module is the part of it
that is safe and fast to unit test without a real CellProfiler/Cellpose
run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ContrastStats:
    """
    Intensity summary for one nuclei-channel image.

    Attributes
    ----------
    mean, std : float
        Pixel intensity mean/standard deviation.
    p99 : float
        99th percentile intensity (robust to a few hot pixels).
    max_intensity : float
        Peak pixel intensity.
    """

    mean: float
    std: float
    p99: float
    max_intensity: float

    def is_dim(self, max_intensity_threshold: float) -> bool:
        """
        Whether this field is dim enough to risk threshold-driven
        over-segmentation (plan.md: fields with low peak intensity were
        the ones CellProfiler's Otsu threshold over-segmented).
        """
        return self.max_intensity < max_intensity_threshold


def image_contrast_stats(image: np.ndarray) -> ContrastStats:
    """
    Compute intensity summary stats for one image array.

    Parameters
    ----------
    image : np.ndarray
        2D grayscale image (any numeric dtype).

    Returns
    -------
    ContrastStats
    """
    flat = np.asarray(image).ravel()
    return ContrastStats(
        mean=float(np.mean(flat)),
        std=float(np.std(flat)),
        p99=float(np.percentile(flat, 99)),
        max_intensity=float(np.max(flat)),
    )


def pick_sample_sites(
    metadata: pd.DataFrame,
    wells_per_condition: int = 2,
    seed: int = 0,
) -> pd.DataFrame:
    """
    Pick a small, representative sample of sites for a segmentation
    pre-check: a fixed number of wells per disease condition, all sites
    from each chosen well.

    Deterministic for a fixed ``seed``. Every disease condition present
    in ``metadata`` must have at least ``wells_per_condition`` distinct
    wells, or this raises (a dataset too small to sample this way should
    fail loudly, not silently return a smaller sample).

    Parameters
    ----------
    metadata : pd.DataFrame
        Rows with at least ``well``, ``site``, ``disease_condition``
        columns (e.g. one plate's worth of parsed RxRx19a metadata).
    wells_per_condition : int
        Number of distinct wells to sample per disease condition.
    seed : int
        Random seed for well selection (reproducible across reruns).

    Returns
    -------
    pd.DataFrame
        All rows (all sites) for the selected wells.

    Raises
    ------
    ValueError
        If any disease condition has fewer than ``wells_per_condition``
        distinct wells.
    """
    rng = random.Random(seed)
    chosen_wells: list[str] = []
    for condition, group in metadata.groupby("disease_condition"):
        wells = sorted(group["well"].unique())
        if len(wells) < wells_per_condition:
            raise ValueError(
                f"disease_condition {condition!r} has only {len(wells)} "
                f"distinct wells, need {wells_per_condition}"
            )
        chosen_wells.extend(rng.sample(wells, wells_per_condition))
    return metadata[metadata["well"].isin(chosen_wells)].reset_index(drop=True)


@dataclass(frozen=True)
class CountComparison:
    """
    Per-site and aggregate comparison between two object-count sources.

    Attributes
    ----------
    per_site : dict[str, float]
        Absolute percent error per site key:
        ``abs(candidate - reference) / reference * 100``.
    mean_abs_pct_error : float
        Mean of ``per_site`` across all sites.
    """

    per_site: dict[str, float]
    mean_abs_pct_error: float


def compare_counts(
    reference: dict[str, int], candidate: dict[str, int]
) -> CountComparison:
    """
    Compare two site-keyed object-count dictionaries (e.g. Cellpose's
    reference count vs. a CellProfiler threshold variant's count).

    Parameters
    ----------
    reference : dict[str, int]
        Ground-truth-ish counts (e.g. from Cellpose), keyed by a site
        identifier such as ``"{well}_{site}"``.
    candidate : dict[str, int]
        Counts to evaluate, keyed the same way.

    Returns
    -------
    CountComparison

    Raises
    ------
    ValueError
        If ``reference`` and ``candidate`` don't have the same set of
        site keys (comparing mismatched sites would silently understate
        error).
    """
    if set(reference) != set(candidate):
        raise ValueError(
            "reference and candidate must have the same site keys; "
            f"only in reference: {set(reference) - set(candidate)}, "
            f"only in candidate: {set(candidate) - set(reference)}"
        )
    per_site = {
        key: abs(candidate[key] - reference[key]) / reference[key] * 100
        for key in reference
    }
    mean_abs_pct_error = sum(per_site.values()) / len(per_site)
    return CountComparison(per_site=per_site, mean_abs_pct_error=mean_abs_pct_error)


def build_threshold_variant(cppipe_text: str, setting: str, value: str) -> str:
    """
    Build a one-setting-changed variant of a ``.cppipe`` pipeline file's
    text, for a threshold-strategy sweep (plan.md's segmentation review
    built four such variants -- baseline/RobustBackground/correction-
    factor/Adaptive -- by hand; this is the same operation, formalized).

    Only the *first* line matching ``"    {setting}:"`` is changed, so
    this must be called against a pipeline where that setting appears
    once per module of interest (e.g. run it against a pipeline that
    already has the desired module isolated, or be aware it changes the
    first occurrence top-to-bottom).

    Parameters
    ----------
    cppipe_text : str
        Full text of a ``.cppipe`` pipeline file.
    setting : str
        Exact CellProfiler setting name, e.g. ``"Thresholding method"``.
    value : str
        Exact CellProfiler setting value, e.g. ``"Robust Background"``.
        CellProfiler's setting values are exact strings (e.g. the method
        is ``"Robust Background"`` *with* a space -- ``"RobustBackground"``
        crashes with "Invalid thresholding settings").

    Returns
    -------
    str
        The modified pipeline text, same line count as the input.

    Raises
    ------
    ValueError
        If ``setting`` does not appear in ``cppipe_text`` at all.
    """
    prefix = f"    {setting}:"
    lines = cppipe_text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{prefix}{value}"
            break
    else:
        raise ValueError(f"setting {setting!r} not found in pipeline text")
    result = "\n".join(lines)
    if cppipe_text.endswith("\n"):
        result += "\n"
    return result


def flag_dim_wells(
    quality: pd.DataFrame,
    metric_col: str = "max_intensity",
    well_col: str = "well",
    n_mad: float = 3.0,
) -> list[str]:
    """
    Flag wells whose per-image quality metric is a plate-wide outlier on
    the dim side, from CellProfiler's own ``MeasureImageQuality`` output
    (e.g. ``Image_ImageQuality_MaxIntensity_DNA`` loaded from a shard's
    SQLite) -- the production-safe replacement for the one-off Cellpose
    cross-check (plan.md segmentation review found Plate 25's AA08/E08
    wells this way, by hand, via a side Cellpose run; this is the same
    signal, read directly off a column CellProfiler already writes on
    every production run at negligible extra cost).

    A well is flagged if its mean metric across sites falls more than
    ``n_mad`` median absolute deviations below the plate's median
    well-mean. MAD (not mean/std) is used because the dim wells
    themselves must not be allowed to drag the baseline down -- a
    standard-deviation-based cutoff shifts every time a new dim well
    joins the sample, a median-based one does not.

    Parameters
    ----------
    quality : pd.DataFrame
        Per-image quality rows for one plate, with at least ``well_col``
        and ``metric_col`` columns (e.g. one row per site).
    metric_col : str
        Column to flag on, e.g. ``"max_intensity"``. Lower values are
        treated as worse (matches ``MaxIntensity``/``MeanIntensity``;
        for a metric where higher is worse, negate it before calling).
    well_col : str
        Column identifying the well.
    n_mad : float
        Flag threshold in median absolute deviations below the plate
        median. 3.0 is a conventional "likely outlier" cutoff.

    Returns
    -------
    list[str]
        Flagged well identifiers, in the order they first appear in
        ``quality`` (empty if none are flagged, including when every
        well is identical).

    Raises
    ------
    ValueError
        If ``metric_col`` is not a column of ``quality``.

    Notes
    -----
    A median/MAD statistic needs several wells to be meaningful -- a
    real pilot plate with 5+ wells per condition resolves a single dim
    well cleanly (confirmed against Plate 25, 1, and 13's real data),
    but a 2-3 well smoke test may not flag a known-dim well because the
    median itself gets pulled toward it. This is an inherent limit of
    small-sample outlier detection, not a bug.
    """
    if metric_col not in quality.columns:
        raise ValueError(f"quality is missing column {metric_col!r}")
    well_means = quality.groupby(well_col, sort=False)[metric_col].mean()
    median = well_means.median()
    mad = (well_means - median).abs().median()
    if mad == 0:
        return []
    threshold = median - n_mad * mad
    return [well for well in well_means.index if well_means[well] < threshold]

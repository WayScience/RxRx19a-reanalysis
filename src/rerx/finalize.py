"""
Per-plate finalize batch: annotate, normalize, select, QC gate, buscar.

Implements the compute-scaling half of plan.md sections 18-19: a full
RxRx19a run spans many plates, and each plate is its own biological batch
(its own control population), so annotate/normalize/feature_select/buscar
all run per plate rather than concatenating the whole dataset into memory
(mirrors the storage partitioning in :func:`rerx.cytotable.plate_partitions`
/ :func:`rerx.cytotable.write_partitioned_profiles`, plan.md section 24).

This module also gates buscar behind a biological QC check
(:func:`rerx.validate.check_control_separation`): buscar's own
``calculate_buscar_scores`` raises when a plate's mock and disease
controls have no separating morphology feature (empty on-signature -> a
zero-row Earth Mover's Distance computation), so a bad plate must be
detected and skipped, not allowed to crash the whole run.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from rerx.buscar import BuscarConfig, PlateBuscarResult, run_buscar_for_plate
from rerx.pycytominer import add_perturbation_column, annotate_profiles
from rerx.pycytominer import drop_flagged_outliers as _drop_flagged_outliers
from rerx.pycytominer import flag_outliers as _flag_outliers
from rerx.pycytominer import normalize_profiles as _normalize_profiles
from rerx.pycytominer import select_features as _select_features
from rerx.validate import ControlSeparationResult, check_control_separation


@dataclass(frozen=True)
class PlateFinalizeResult:
    """
    Outcome of finalizing one plate's cell profiles.

    Attributes
    ----------
    experiment : str
        Plate's experiment id.
    plate : str
        Plate id.
    annotated : pd.DataFrame
        Profiles after :func:`rerx.pycytominer.annotate_profiles` and
        :func:`rerx.pycytominer.add_perturbation_column`.
    normalized : pd.DataFrame
        Output of :func:`rerx.pycytominer.normalize_profiles`.
    normalized_path : Path
        Where the normalized profiles were written.
    feature_selected : pd.DataFrame
        Output of :func:`rerx.pycytominer.select_features`.
    feature_selected_path : Path
        Where the feature-selected profiles were written.
    control_separation : ControlSeparationResult | None
        Biological QC gate result for this plate.
    buscar : PlateBuscarResult | None
        buscar signatures/scores for this plate, or ``None`` if skipped.
    buscar_skipped_reason : str | None
        Why buscar was skipped (e.g. failed control separation, or a
        real buscar error caught after the gate still let a bad case
        through), or ``None`` if it ran.
    n_cells_flagged_outlier : int
        Cells coSMicQC flagged (any threshold set) and dropped before
        normalization (plan.md section 18, step 2). 0 when the default
        nuclei QC thresholds don't apply (e.g. MorphEm profiles).
    """

    experiment: str
    plate: str
    annotated: pd.DataFrame
    normalized: pd.DataFrame
    normalized_path: Path
    feature_selected: pd.DataFrame
    feature_selected_path: Path
    control_separation: ControlSeparationResult | None
    buscar: PlateBuscarResult | None
    buscar_skipped_reason: str | None
    n_cells_flagged_outlier: int = 0


def finalize_plate(  # noqa: PLR0913, PLR0917
    raw_profiles: pd.DataFrame,
    site_metadata: pd.DataFrame,
    run_dir: Path,
    experiment: str,
    plate: str,
    buscar_config: BuscarConfig | None = None,
    run_buscar: bool = True,
    profiler: str = "cellprofiler",
) -> PlateFinalizeResult:
    """
    Run the full per-plate finalize batch: annotate through buscar.

    Parameters
    ----------
    raw_profiles : pd.DataFrame
        One plate's merged CytoTable output (see
        :func:`rerx.cytotable.write_partitioned_profiles`'s per-plate
        groups, or :func:`rerx.cytotable.plate_partitions` directly).
    site_metadata : pd.DataFrame
        Parsed RxRx19a metadata for this plate's sites (see
        :func:`rerx.metadata.parse_metadata`).
    run_dir : Path
        Run directory; outputs land under
        ``profiles/<profiler>/{normalized,feature_selected}/experiment=<e>/plate=<p>/``
        and ``buscar/<profiler>/experiment=<e>/plate=<p>/``.
    experiment : str
        Plate's experiment id.
    plate : str
        Plate id.
    buscar_config : BuscarConfig | None
        buscar scoring configuration. Defaults to ``BuscarConfig()``.
    run_buscar : bool
        Whether to attempt buscar at all. When ``False``, only
        annotate/normalize/select_features run (useful for a fast
        per-shard partial finalize before every arm is present).
    profiler : str
        Feature source sub-tree: ``"cellprofiler"`` or ``"morphem"``.
        Selects the output paths (and the buscar feature pool comes
        from the profiles themselves either way).

    Returns
    -------
    PlateFinalizeResult
        Every artifact and QC result produced for this plate.
    """
    run_dir = Path(run_dir)
    part_dir = f"experiment={experiment}/plate={plate}"

    annotated = annotate_profiles(raw_profiles, site_metadata)
    annotated = add_perturbation_column(annotated)

    # coSMicQC outlier flag + filter, before normalization (plan.md
    # section 18, steps 2-4): flag_outliers is a no-op when the default
    # nuclei QC thresholds don't apply (e.g. MorphEm profiles have no
    # Nuclei_AreaShape_* columns), so n_cells_flagged_outlier stays 0.
    n_before_qc = len(annotated)
    flagged = _flag_outliers(annotated)
    filtered = _drop_flagged_outliers(flagged)
    n_cells_flagged_outlier = n_before_qc - len(filtered)

    normalized_path = (
        run_dir / "profiles" / profiler / "normalized" / part_dir / "profiles.parquet"
    )
    normalized = _normalize_profiles(filtered, normalized_path)

    feature_selected_path = (
        run_dir
        / "profiles"
        / profiler
        / "feature_selected"
        / part_dir
        / "profiles.parquet"
    )
    feature_selected = _select_features(normalized, feature_selected_path)

    control_separation = check_control_separation(normalized)

    buscar_result: PlateBuscarResult | None = None
    buscar_skipped_reason: str | None = None
    if not run_buscar:
        buscar_skipped_reason = "run_buscar=False"
    elif control_separation.skipped:
        buscar_skipped_reason = "control separation check was skipped (missing data)"
    elif not control_separation.passed:
        buscar_skipped_reason = (
            "mock/active_untreated controls do not separate "
            f"(median |Cohen's d| = {control_separation.median_abs_effect_size:.3f} "
            f"< {control_separation.effect_threshold}); buscar would error on an "
            "empty on-signature, so it was skipped for this plate"
        )
    else:
        buscar_dest = run_dir / "buscar" / profiler / part_dir
        try:
            buscar_result = run_buscar_for_plate(
                normalized,
                buscar_dest,
                experiment=experiment,
                plate=plate,
                profiler=profiler,
                config=buscar_config,
            )
        except (ValueError, ZeroDivisionError) as exc:
            # Belt-and-suspenders: the control-separation gate should
            # already have caught the "no on-signature" case, but buscar
            # can still fail for other edge cases -- e.g. an empty
            # off-signature dividing by zero under "affected_ratio", or a
            # perturbation with too few replicate wells; never let that
            # crash the whole finalize run.
            buscar_skipped_reason = f"buscar raised {exc.__class__.__name__}: {exc}"

    return PlateFinalizeResult(
        experiment=experiment,
        plate=plate,
        annotated=annotated,
        normalized=normalized,
        normalized_path=normalized_path,
        feature_selected=feature_selected,
        feature_selected_path=feature_selected_path,
        control_separation=control_separation,
        buscar=buscar_result,
        buscar_skipped_reason=buscar_skipped_reason,
        n_cells_flagged_outlier=n_cells_flagged_outlier,
    )

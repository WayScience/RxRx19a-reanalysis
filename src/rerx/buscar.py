"""
buscar reversal scoring for ReRx.

Implements plan.md sections 16-17: wraps ``buscar.calculate_buscar_scores``
(Polar-based) to compute on/off reversal scores for each perturbation,
measured against the healthy target (mock) with the disease reference
(active_untreated) as the on-score's normalization anchor (its own
on-score is exactly 1.0 by construction). Writes
``buscar/<profiler>/signatures.parquet`` and ``scores.parquet``.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
import polars as pl
from buscar.metrics import calculate_buscar_scores
from buscar.signatures import identify_signatures

MORPHOLOGICAL_FEATURE_PREFIXES = (
    "Cells_",
    "Cytoplasm_",
    "Nuclei_",
    "Morphem_",
    "Recursion_",
)
CONTROL_PERTURBATION = "control"

# Default perturbation labels matching rerx.pycytominer's
# RXRX_CONTROL_MOCK / RXRX_CONTROL_ACTIVE_UNTREATED (duplicated here as
# plain literals, not imported, to avoid a pycytominer<->buscar import
# cycle -- rerx.pycytominer already imports CONTROL_PERTURBATION from
# this module).
TARGET_STATE = "mock"
REF_STATE = "active_untreated"
DEFAULT_PERTURBATION_COL = "Metadata_perturbation"


@dataclass(frozen=True)
class BuscarConfig:
    """
    Parameters for a buscar analysis.

    Attributes
    ----------
    ref_state : str
        Disease (reference) perturbation label -- a value of
        ``perturbation_col`` (plan.md section 19's "viral control";
        default ``"active_untreated"``, matching
        :data:`rerx.pycytominer.RXRX_CONTROL_ACTIVE_UNTREATED`). In
        buscar's control vocabulary this is the negative control
        (disease + no effective treatment; DMSO-vehicle in other
        screens), and the on-score's normalization anchor: its own
        on-score is exactly 1.0.
    target_state : str
        Healthy (target) perturbation label -- a value of
        ``perturbation_col`` (plan.md section 19's "mock"; default
        ``"mock"``, matching :data:`rerx.pycytominer.RXRX_CONTROL_MOCK`).
        In buscar's control vocabulary this is the positive control
        (the healthy state we want treatments to move cells toward).
    perturbation_col : str
        Metadata column holding condition identifiers (default
        ``"Metadata_perturbation"``, added by
        :func:`rerx.pycytominer.add_perturbation_column`).
    state_col : str | None
        Metadata column holding cell state, if different from
        ``perturbation_col``. Left ``None`` by default: RxRx19a's
        ``Metadata_perturbation`` already carries distinct mock/
        active_untreated labels, so no separate state column is needed.
    test_method : str
        Statistical test used by ``identify_signatures``.
    on_method : str
        On-score method (only ``"emd"`` supported by buscar).
    off_method : str
        Off-score method (``"affected_ratio"`` or ``"emd"``).
    seed : int
        Random seed for reproducibility.
    """

    ref_state: str = REF_STATE
    target_state: str = TARGET_STATE
    perturbation_col: str = DEFAULT_PERTURBATION_COL
    state_col: str | None = None
    test_method: Literal["ks_test", "permutation_test", "welchs_ttest", "rank_test"] = (
        "ks_test"
    )
    on_method: Literal["emd"] = "emd"
    off_method: Literal["affected_ratio", "emd"] = "affected_ratio"
    seed: int = 0


def morphological_features(profiles: pl.DataFrame) -> list[str]:
    """
    Feature column names (Cells_/Cytoplasm_/Nuclei_ prefixed), sorted.

    Parameters
    ----------
    profiles : pl.DataFrame
        Profile table (any profiler).

    Returns
    -------
    list[str]
        Morphology feature names.
    """
    return sorted(
        c for c in profiles.columns if c.startswith(MORPHOLOGICAL_FEATURE_PREFIXES)
    )


def build_buscar_signatures(
    profiles: pl.DataFrame,
    config: BuscarConfig | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """
    Identify on/off signatures from control profiles (plan.md section 16).

    Reference (disease) and target (healthy) rows come from the controls
    table embedded in the profile metadata.

    Parameters
    ----------
    profiles : pl.DataFrame
        Annotated profiles containing control rows.
    config : BuscarConfig | None
        Scoring configuration.

    Returns
    -------
    tuple[list[str], list[str], list[str]]
        ``(on_signature, off_signature, ambiguous)`` feature lists.
    """
    cfg = config or BuscarConfig()
    pcol = cfg.perturbation_col
    ref_rows = profiles.filter(pl.col(pcol) == cfg.ref_state)
    target_rows = profiles.filter(pl.col(pcol) == cfg.target_state)
    if ref_rows.is_empty():
        raise ValueError(f"no reference rows for state {cfg.ref_state!r}")
    if target_rows.is_empty():
        raise ValueError(f"no target rows for state {cfg.target_state!r}")
    feats = morphological_features(profiles)
    on, off, ambiguous = identify_signatures(
        ref_profiles=ref_rows,
        target_profiles=target_rows,
        morph_feats=feats,
        test_method=cfg.test_method,
        seed=cfg.seed,
    )
    return on, off, ambiguous


def calculate_scores_summary(
    profiles: pl.DataFrame,
    on_signature: list[str],
    off_signature: list[str],
    config: BuscarConfig | None = None,
) -> pl.DataFrame:
    """
    Compute buscar scores across perturbations (plan.md section 17).

    Wraps ``buscar.calculate_buscar_scores`` with ReRx defaults.

    Returns
    -------
    pl.DataFrame
        Per-perturbation on/off scores (buscar's output schema).
    """
    cfg = config or BuscarConfig()
    meta_cols = [
        c for c in profiles.columns if not c.startswith(MORPHOLOGICAL_FEATURE_PREFIXES)
    ]
    return calculate_buscar_scores(
        profiles=profiles,
        meta_cols=meta_cols,
        on_morphology_signature=on_signature,
        off_morphology_signature=off_signature,
        ref_state=cfg.ref_state,
        target=cfg.target_state,
        perturbation_col=cfg.perturbation_col,
        state_col=cfg.state_col,
        on_method=cfg.on_method,
        off_method=cfg.off_method,
        seed=cfg.seed,
    )


def write_buscar_outputs(
    signatures: tuple[list[str], list[str], list[str]],
    scores: pl.DataFrame,
    dest_dir: Path,
    profiler: str = "morphem",
) -> tuple[Path, Path]:
    """
    Write ``signatures.parquet`` and ``scores.parquet`` for one profiler.

    Parameters
    ----------
    signatures : tuple[list[str], list[str], list[str]]
        ``(on, off, ambiguous)`` feature lists.
    scores : pl.DataFrame
        buscar score table.
    dest_dir : Path
        ``buscar/<profiler>`` directory inside the run.
    profiler : str
        Profiler name used in the summary JSON.

    Returns
    -------
    tuple[Path, Path]
        ``(signatures_path, scores_path)``.
    """
    on, off, ambiguous = signatures
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    sig_path = dest_dir / "signatures.parquet"
    sig_df = pd.DataFrame(
        {
            "signature_type": ["on"] * len(on)
            + ["off"] * len(off)
            + ["ambiguous"] * len(ambiguous),
            "feature": list(on) + list(off) + list(ambiguous),
        }
    )
    sig_df.to_parquet(sig_path, index=False)
    scores_path = dest_dir / "scores.parquet"
    scores.write_parquet(scores_path)
    summary = dest_dir / f"{profiler}_summary.json"
    summary.write_text(
        json.dumps(
            {
                "profiler": profiler,
                "on_signature_count": len(on),
                "off_signature_count": len(off),
                "ambiguous_count": len(ambiguous),
                "score_row_count": scores.height,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return sig_path, scores_path


@dataclass(frozen=True)
class PlateBuscarResult:
    """
    Outcome of running buscar for one plate's cell profiles.

    Attributes
    ----------
    experiment : str
        Plate's experiment id.
    plate : str
        Plate id.
    signatures : tuple[list[str], list[str], list[str]]
        ``(on, off, ambiguous)`` feature lists identified from this
        plate's own controls (plan.md section 19: "Run the analysis
        twice" per profiler, each plate's controls define its own
        signature -- signatures are not shared across plates, since each
        plate is its own biological batch).
    scores : pd.DataFrame
        Per-perturbation on/off scores for this plate.
    signatures_path : Path
        Where ``signatures.parquet`` was written.
    scores_path : Path
        Where ``scores.parquet`` was written.
    """

    experiment: str
    plate: str
    signatures: tuple[list[str], list[str], list[str]]
    scores: pd.DataFrame
    signatures_path: Path
    scores_path: Path


def run_buscar_for_plate(  # noqa: PLR0913, PLR0917
    plate_profiles: pd.DataFrame,
    dest_dir: Path,
    experiment: str,
    plate: str,
    profiler: str = "cellprofiler",
    config: BuscarConfig | None = None,
) -> PlateBuscarResult:
    """
    Run buscar signature identification and scoring for one plate.

    This is the per-plate buscar batching unit: each plate carries its
    own mock/disease control population, so signatures (which features
    move between healthy and disease) are identified fresh per plate
    rather than pooling controls across an entire multi-plate dataset
    (mirrors the per-plate batching used for normalize/select_features/
    write_partitioned_profiles; see :func:`rerx.cytotable.plate_partitions`).

    Parameters
    ----------
    plate_profiles : pd.DataFrame
        One plate's annotated cell profiles, carrying
        ``Metadata_perturbation`` (see
        :func:`rerx.pycytominer.add_perturbation_column`) and morphology
        feature columns.
    dest_dir : Path
        ``buscar/<profiler>`` directory for this plate's outputs.
    experiment : str
        Plate's experiment id (for the result record, not used in scoring).
    plate : str
        Plate id (for the result record, not used in scoring).
    profiler : str
        Profiler name used in the written summary JSON.
    config : BuscarConfig | None
        Scoring configuration. Defaults to ``BuscarConfig()`` (mock vs.
        active_untreated via ``Metadata_perturbation``).

    Returns
    -------
    PlateBuscarResult
        Signatures, scores, and the paths written.
    """
    cfg = config or BuscarConfig()
    pl_profiles = pl.from_pandas(plate_profiles)
    signatures = build_buscar_signatures(pl_profiles, cfg)
    on, off, _ambiguous = signatures
    scores = calculate_scores_summary(pl_profiles, on, off, cfg)
    sig_path, scores_path = write_buscar_outputs(
        signatures, scores, dest_dir, profiler=profiler
    )
    return PlateBuscarResult(
        experiment=experiment,
        plate=plate,
        signatures=signatures,
        scores=scores.to_pandas(),
        signatures_path=sig_path,
        scores_path=scores_path,
    )

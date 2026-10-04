"""
Pilot/run validation checks.

Implements the technical checks from plan.md section 27 (pilot exit
criteria) and section 12 ("Run SQLite integrity checks inspired by the
demo repository before conversion"):

- SQLite structural integrity (``PRAGMA integrity_check``).
- Referential integrity between Nuclei/Cells/Cytoplasm parent-child links
  (the demo repository at
  https://github.com/d33bs/demo-cellprofiler/tree/main/src/docker/
  sqlite_compartment_object_parent_foreign_keys rebuilds the schema with
  declared foreign keys and lets SQLite enforce them; this module checks
  the same relationships directly against CellProfiler's raw schema,
  which has no declared foreign keys, via anti-join queries).
- Aggregating the mechanical Parquet/crop checks already implemented in
  :mod:`rerx.cytotable` (schema consistency, cell ID uniqueness) and
  :mod:`rerx.crops` (one-to-one crop/cell join) into a single per-run
  report.
- JPEG crop decode checks (plan.md section 27: "JPEG crops decode").

Verified against a real CellProfiler 4.2.8 container run: ``PRAGMA
integrity_check`` returns ``ok`` and the Cytoplasm->Cells/Nuclei parent
links have zero orphans on that output.
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from rerx.buscar import MORPHOLOGICAL_FEATURE_PREFIXES
from rerx.crops import decode_jpeg, validate_crops_join_one_to_one
from rerx.cytotable import (
    SchemaCheckResult,
    check_schema_consistency,
    validate_unique_cell_ids,
)
from rerx.segmentation_check import flag_dim_wells

# Parent-child relationships CellProfiler writes into a shard's SQLite,
# matching the ExportToDatabase object-relationship export (plan.md
# section 12: "retain parent-child relationships").
PARENT_CHILD_RELATIONSHIPS = (
    # (child_table, child_object_col, child_parent_col,
    #  parent_table, parent_object_col)
    (
        "Per_Cytoplasm",
        "Cytoplasm_Number_Object_Number",
        "Cytoplasm_Parent_Cells",
        "Per_Cells",
        "Cells_Number_Object_Number",
    ),
    (
        "Per_Cytoplasm",
        "Cytoplasm_Number_Object_Number",
        "Cytoplasm_Parent_Nuclei",
        "Per_Nuclei",
        "Nuclei_Number_Object_Number",
    ),
)


@dataclass(frozen=True)
class SqliteIntegrityResult:
    """
    Outcome of the SQLite checks for one shard database.

    Attributes
    ----------
    sqlite_path : Path
        Database checked.
    integrity_ok : bool
        Whether ``PRAGMA integrity_check`` returned ``ok``.
    integrity_messages : list[str]
        Raw messages from ``PRAGMA integrity_check`` (``["ok"]`` on success).
    orphan_counts : dict[str, int]
        Per parent-child relationship (keyed
        ``"<child_table>.<parent_col>"``), the number of child rows whose
        parent reference does not exist.
    """

    sqlite_path: Path
    integrity_ok: bool
    integrity_messages: list[str]
    orphan_counts: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Whether every check in this result passed."""
        return self.integrity_ok and all(v == 0 for v in self.orphan_counts.values())


def check_sqlite_integrity(sqlite_path: Path) -> SqliteIntegrityResult:
    """
    Run structural and referential integrity checks on a shard's SQLite.

    Parameters
    ----------
    sqlite_path : Path
        Shard SQLite database written by CellProfiler's ``ExportToDatabase``.

    Returns
    -------
    SqliteIntegrityResult
        ``PRAGMA integrity_check`` result plus orphan counts for every
        relationship in :data:`PARENT_CHILD_RELATIONSHIPS` whose tables are
        present in the database.

    Raises
    ------
    FileNotFoundError
        If ``sqlite_path`` does not exist.
    """
    sqlite_path = Path(sqlite_path)
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"missing shard SQLite database: {sqlite_path}")

    con = sqlite3.connect(str(sqlite_path))
    try:
        cur = con.cursor()
        cur.execute("PRAGMA integrity_check")
        messages = [row[0] for row in cur.fetchall()]
        integrity_ok = messages == ["ok"]

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}

        orphan_counts: dict[str, int] = {}
        for (
            child_table,
            _child_obj_col,
            child_parent_col,
            parent_table,
            parent_obj_col,
        ) in PARENT_CHILD_RELATIONSHIPS:
            if child_table not in tables or parent_table not in tables:
                continue
            key = f"{child_table}.{child_parent_col}"
            query = f"""
                SELECT COUNT(*) FROM {child_table} c
                LEFT JOIN {parent_table} p
                    ON p.ImageNumber = c.ImageNumber
                    AND p.{parent_obj_col} = c.{child_parent_col}
                WHERE p.ImageNumber IS NULL
            """
            cur.execute(query)
            orphan_counts[key] = cur.fetchone()[0]
    finally:
        con.close()

    return SqliteIntegrityResult(
        sqlite_path=sqlite_path,
        integrity_ok=integrity_ok,
        integrity_messages=messages,
        orphan_counts=orphan_counts,
    )


@dataclass(frozen=True)
class CropDecodeResult:
    """
    Outcome of decode-checking crop JPEG columns.

    Attributes
    ----------
    checked : int
        Number of crop cells checked.
    decode_failures : list[str]
        ``Metadata_cell_id`` values where at least one channel column
        failed to decode.
    """

    checked: int
    decode_failures: list[str]

    @property
    def passed(self) -> bool:
        """Whether every checked crop decoded cleanly."""
        return not self.decode_failures


def check_crops_decode(
    crops: pd.DataFrame,
    jpeg_columns: tuple[str, ...] = (
        "crop_w1_jpeg",
        "crop_w2_jpeg",
        "crop_w3_jpeg",
        "crop_w4_jpeg",
        "crop_w5_jpeg",
    ),
) -> CropDecodeResult:
    """
    Decode every crop JPEG column to confirm the bytes are valid images.

    Parameters
    ----------
    crops : pd.DataFrame
        Crop rows (see :mod:`rerx.crops`). Must carry ``Metadata_cell_id``
        and every column in ``jpeg_columns``.
    jpeg_columns : tuple[str, ...]
        Crop JPEG columns to check.

    Returns
    -------
    CropDecodeResult
        Number checked and any cell IDs that failed to decode.

    Raises
    ------
    ValueError
        If ``crops`` is missing required columns.
    """
    missing = [c for c in ("Metadata_cell_id", *jpeg_columns) if c not in crops.columns]
    if missing:
        raise ValueError(f"crops missing required columns: {missing}")

    failures: list[str] = []
    for _, row in crops.iterrows():
        for column in jpeg_columns:
            try:
                decoded = decode_jpeg(row[column])
                if decoded.size == 0:
                    raise ValueError("decoded to an empty array")
            except Exception:
                failures.append(str(row["Metadata_cell_id"]))
                break
    return CropDecodeResult(checked=len(crops), decode_failures=failures)


@dataclass(frozen=True)
class ControlSeparationResult:
    """
    Outcome of checking that healthy and disease controls actually differ.

    A technically clean run (integrity checks all pass) can still carry
    garbage biology -- e.g. a failed stain or a dead plate -- so this
    check compares the healthy (mock) and disease (untreated/challenged)
    control populations on morphology features and flags a run whose
    controls barely separate, using each feature's Cohen's d effect size.

    Attributes
    ----------
    checked_features : int
        Number of morphology features compared (0 if skipped).
    separated_features : int
        Number of those features with ``|Cohen's d| >= effect_threshold``.
    median_abs_effect_size : float | None
        Median absolute Cohen's d across checked features, or ``None`` if
        skipped.
    skipped : bool
        ``True`` when the control column or one of the two control
        groups was absent -- not a failure, just not checkable (e.g. a
        shard-level partial run before both control types are present).
    effect_threshold : float
        Minimum median absolute Cohen's d required to pass.
    """

    checked_features: int
    separated_features: int
    median_abs_effect_size: float | None
    skipped: bool
    effect_threshold: float = 0.5

    @property
    def passed(self) -> bool:
        """Whether controls separate enough, or the check was skipped."""
        if self.skipped:
            return True
        if self.median_abs_effect_size is None:
            return True
        return self.median_abs_effect_size >= self.effect_threshold


def check_control_separation(
    profiles: pd.DataFrame,
    control_column: str = "Metadata_rxrx_control_type",
    healthy_label: str = "mock",
    disease_label: str = "active_untreated",
    effect_threshold: float = 0.5,
) -> ControlSeparationResult:
    """
    Compare healthy vs. disease control populations on morphology features.

    Uses Cohen's d (mean difference over pooled standard deviation) per
    numeric ``Cells_``/``Cytoplasm_``/``Nuclei_`` feature, then the median
    absolute effect size across features as the run-level signal: a run
    where mock and disease controls are indistinguishable (median effect
    size near zero) likely has a biological problem even if every
    technical check passes.

    Parameters
    ----------
    profiles : pd.DataFrame
        Annotated cell profiles (see :func:`rerx.pycytominer.annotate_profiles`).
    control_column : str
        Column identifying each row's control type.
    healthy_label : str
        Value of ``control_column`` for the healthy/mock population.
    disease_label : str
        Value of ``control_column`` for the disease/challenged population.
    effect_threshold : float
        Minimum median absolute Cohen's d to pass (0.5 is a conventional
        "medium" effect size).

    Returns
    -------
    ControlSeparationResult
        Per-run separation summary. ``skipped`` is ``True`` (and
        ``.passed`` is ``True``) when the control column or either
        population is missing, rather than treating that as a failure.
    """
    if control_column not in profiles.columns:
        return ControlSeparationResult(
            checked_features=0,
            separated_features=0,
            median_abs_effect_size=None,
            skipped=True,
            effect_threshold=effect_threshold,
        )
    healthy = profiles[profiles[control_column] == healthy_label]
    disease = profiles[profiles[control_column] == disease_label]
    if healthy.empty or disease.empty:
        return ControlSeparationResult(
            checked_features=0,
            separated_features=0,
            median_abs_effect_size=None,
            skipped=True,
            effect_threshold=effect_threshold,
        )

    feature_cols = [
        c
        for c in profiles.columns
        if c.startswith(MORPHOLOGICAL_FEATURE_PREFIXES)
        and pd.api.types.is_numeric_dtype(profiles[c])
    ]
    effect_sizes: list[float] = []
    for col in feature_cols:
        h = healthy[col].dropna()
        d = disease[col].dropna()
        min_samples = 2
        if len(h) < min_samples or len(d) < min_samples:
            continue
        pooled_std = ((h.std() ** 2 + d.std() ** 2) / 2) ** 0.5
        if pooled_std == 0 or pd.isna(pooled_std):
            continue
        cohens_d = (d.mean() - h.mean()) / pooled_std
        effect_sizes.append(abs(float(cohens_d)))

    if not effect_sizes:
        return ControlSeparationResult(
            checked_features=0,
            separated_features=0,
            median_abs_effect_size=None,
            skipped=True,
            effect_threshold=effect_threshold,
        )

    median_effect = float(pd.Series(effect_sizes).median())
    separated = sum(1 for e in effect_sizes if e >= effect_threshold)
    return ControlSeparationResult(
        checked_features=len(effect_sizes),
        separated_features=separated,
        median_abs_effect_size=median_effect,
        skipped=False,
        effect_threshold=effect_threshold,
    )


@dataclass(frozen=True)
class ImageQualityResult:
    """
    Outcome of checking CellProfiler's own ``MeasureImageQuality`` output
    for plate-wide dim-well outliers.

    This is the production-safe replacement for the one-off Cellpose
    cross-check that found Plate 25's AA08/E08 wells by hand (plan.md
    segmentation review): ``MeasureImageQuality`` runs on every
    production image at negligible cost, so this reads the same signal
    (per-image DNA channel max intensity) straight off a column
    CellProfiler already wrote, instead of requiring a side Cellpose run
    to notice a future dim-well problem.

    Attributes
    ----------
    flagged_wells : list[str]
        Wells whose mean DNA-channel max intensity is a plate-wide
        outlier on the dim side (see :func:`rerx.segmentation_check.flag_dim_wells`).
    skipped : bool
        ``True`` when the quality column is absent -- not a failure,
        just not checkable (e.g. a pipeline run from before this module
        was added, or `check_image_quality` is being invoked against
        a frame that was never quality-measured in the first place).
    """

    flagged_wells: list[str]
    skipped: bool

    @property
    def passed(self) -> bool:
        """Whether no well was flagged, or the check was skipped."""
        if self.skipped:
            return True
        return len(self.flagged_wells) == 0


def check_image_quality(
    profiles: pd.DataFrame,
    metric_col: str = "Image_ImageQuality_MaxIntensity_DNA",
    well_col: str = "Image_Metadata_Well",
    n_mad: float = 3.0,
) -> ImageQualityResult:
    """
    Flag wells with a plate-wide outlier-dim DNA channel, from
    CellProfiler's own per-image quality metrics.

    Parameters
    ----------
    profiles : pd.DataFrame
        Per-image (or per-cell, duplicated per image) rows carrying
        ``well_col`` and ``metric_col`` -- the ``MeasureImageQuality``
        module's ``ExportToDatabase`` output, one column per metric per
        measured channel.
    metric_col : str
        Quality column to flag on. Defaults to the DNA channel's max
        intensity (the channel used for nuclei segmentation, and the one
        that showed the Plate 25 AA08/E08 problem).
    well_col : str
        Column identifying each row's well.
    n_mad : float
        Flag threshold in median absolute deviations below the plate
        median (see :func:`rerx.segmentation_check.flag_dim_wells`).

    Returns
    -------
    ImageQualityResult
        ``skipped=True`` (and ``.passed`` is ``True``) if ``metric_col``
        is absent, rather than treating that as a failure.
    """
    if metric_col not in profiles.columns:
        return ImageQualityResult(flagged_wells=[], skipped=True)
    flagged = flag_dim_wells(
        profiles, metric_col=metric_col, well_col=well_col, n_mad=n_mad
    )
    return ImageQualityResult(flagged_wells=flagged, skipped=False)


@dataclass(frozen=True)
class PilotValidationReport:
    """
    Aggregated pilot exit-criteria checks (plan.md section 27, technical items).

    Attributes
    ----------
    sqlite_results : list[SqliteIntegrityResult]
        One entry per shard SQLite database checked.
    schema_check : SchemaCheckResult | None
        Cross-shard Parquet schema consistency, if profile Parquet paths
        were supplied.
    unique_cell_ids_ok : bool | None
        Whether ``Metadata_cell_id`` was unique across all profiles
        supplied, or ``None`` if not checked.
    crops_join_ok : bool | None
        Whether crops joined one-to-one with profiles, or ``None`` if not
        checked.
    crop_decode : CropDecodeResult | None
        JPEG decode check result, or ``None`` if not checked.
    control_separation : ControlSeparationResult | None
        Biological QC gate comparing healthy vs. disease controls, or
        ``None`` if not checked.
    image_quality : ImageQualityResult | None
        Plate-wide dim-well check from CellProfiler's own
        ``MeasureImageQuality`` output, or ``None`` if not checked.
    """

    sqlite_results: list[SqliteIntegrityResult]
    schema_check: SchemaCheckResult | None = None
    unique_cell_ids_ok: bool | None = None
    crops_join_ok: bool | None = None
    crop_decode: CropDecodeResult | None = None
    control_separation: ControlSeparationResult | None = None
    image_quality: ImageQualityResult | None = None

    @property
    def passed(self) -> bool:
        """Whether every check that was run passed."""
        checks = [r.passed for r in self.sqlite_results]
        if self.schema_check is not None:
            checks.append(self.schema_check.consistent)
        if self.unique_cell_ids_ok is not None:
            checks.append(self.unique_cell_ids_ok)
        if self.crops_join_ok is not None:
            checks.append(self.crops_join_ok)
        if self.crop_decode is not None:
            checks.append(self.crop_decode.passed)
        if self.control_separation is not None:
            checks.append(self.control_separation.passed)
        if self.image_quality is not None:
            checks.append(self.image_quality.passed)
        return all(checks)

    def summary(self) -> dict:
        """Plain-dict summary suitable for a QC log or ``run.json`` note."""
        return {
            "passed": self.passed,
            "sqlite_shards_checked": len(self.sqlite_results),
            "sqlite_shards_failed": sum(1 for r in self.sqlite_results if not r.passed),
            "schema_consistent": (
                self.schema_check.consistent if self.schema_check else None
            ),
            "unique_cell_ids_ok": self.unique_cell_ids_ok,
            "crops_join_ok": self.crops_join_ok,
            "crop_decode_checked": (
                self.crop_decode.checked if self.crop_decode else None
            ),
            "crop_decode_failures": (
                len(self.crop_decode.decode_failures) if self.crop_decode else None
            ),
            "control_separation_skipped": (
                self.control_separation.skipped if self.control_separation else None
            ),
            "control_separation_median_effect_size": (
                self.control_separation.median_abs_effect_size
                if self.control_separation
                else None
            ),
            "control_separation_passed": (
                self.control_separation.passed if self.control_separation else None
            ),
            "image_quality_skipped": (
                self.image_quality.skipped if self.image_quality else None
            ),
            "image_quality_flagged_wells": (
                self.image_quality.flagged_wells if self.image_quality else None
            ),
            "image_quality_passed": (
                self.image_quality.passed if self.image_quality else None
            ),
        }


def validate_pilot_run(
    sqlite_paths: list[Path],
    profile_parquet_paths: list[Path] | None = None,
    profiles: pd.DataFrame | None = None,
    crops: pd.DataFrame | None = None,
    check_biology: bool = True,
) -> PilotValidationReport:
    """
    Run every applicable technical exit-criterion check for a pilot run.

    Every argument beyond ``sqlite_paths`` is optional so this can be
    called incrementally (e.g. right after CellProfiler, before crops or
    Parquet conversion exist yet).

    Parameters
    ----------
    sqlite_paths : list[Path]
        Every shard SQLite database to check (plan.md section 27: "SQLite
        integrity checks pass").
    profile_parquet_paths : list[Path] | None
        Every shard's converted profile Parquet, for schema-consistency
        checking (plan.md section 27: "Parquet schema is identical across
        shards").
    profiles : pd.DataFrame | None
        Combined cell profiles (with ``Metadata_cell_id``), for
        uniqueness and crop-join checks (plan.md section 27: "Cell IDs are
        unique"). If annotated with ``Metadata_rxrx_control_type``, also
        used for the control-separation biological QC gate.
    crops : pd.DataFrame | None
        Combined crop rows, for the one-to-one join and JPEG decode checks
        (plan.md section 27: "Crop rows join one-to-one with CellProfiler
        cells", "JPEG crops decode").
    check_biology : bool
        Whether to run :func:`check_control_separation` against
        ``profiles`` when it carries a control-type column. Set to
        ``False`` to skip (e.g. for shard-level partial checks before all
        control types are staged).

    Returns
    -------
    PilotValidationReport
        Aggregated results; ``.passed`` is ``True`` only if every check
        that was run succeeded.
    """
    sqlite_results = [check_sqlite_integrity(p) for p in sqlite_paths]

    schema_check = None
    if profile_parquet_paths:
        schema_check = check_schema_consistency(profile_parquet_paths)

    unique_cell_ids_ok = None
    if profiles is not None:
        try:
            validate_unique_cell_ids(profiles)
            unique_cell_ids_ok = True
        except ValueError:
            unique_cell_ids_ok = False

    crops_join_ok = None
    crop_decode = None
    if crops is not None and profiles is not None:
        try:
            validate_crops_join_one_to_one(crops, profiles)
            crops_join_ok = True
        except ValueError:
            crops_join_ok = False
    if crops is not None:
        crop_decode = check_crops_decode(crops)

    control_separation = None
    if check_biology and profiles is not None:
        control_separation = check_control_separation(profiles)

    image_quality = None
    if profiles is not None:
        image_quality = check_image_quality(profiles)

    return PilotValidationReport(
        sqlite_results=sqlite_results,
        schema_check=schema_check,
        unique_cell_ids_ok=unique_cell_ids_ok,
        crops_join_ok=crops_join_ok,
        crop_decode=crop_decode,
        control_separation=control_separation,
        image_quality=image_quality,
    )

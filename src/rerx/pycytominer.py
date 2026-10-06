"""
Annotation, normalization, and feature selection via Pycytominer.

Implements plan.md section 18 ("Stage 9: coSMicQC and Pycytominer"),
following the pattern in cytomining/coSMicQC's
``jump_umap_analysis_with_cosmicqc.py`` example: annotate CellProfiler
profiles with RxRx19a metadata and explicit control-type columns, run
coSMicQC outlier flags, normalize against control samples, then run
feature selection.

Pycytominer's ``infer`` feature/metadata detection only recognizes
columns already prefixed ``Metadata_`` (see
``pycytominer.cyto_utils.features.infer_cp_features``); CytoTable's join
output instead prefixes site identity columns ``Image_Metadata_*``
(verified against real CytoTable output from this project's own
:mod:`rerx.cytotable`). :func:`rename_image_metadata_columns` bridges
this so Pycytominer's ``features="infer"``/``meta_features="infer"``
defaults work unmodified.

Plan.md section 18 requires three separate control-type columns instead
of overloading one:

    Metadata_rxrx_control_type       -- RxRx19a's own control semantics
                                         (mock / uv / active_untreated /
                                         treated / n/a)
    Metadata_pycytominer_control_type -- what Pycytominer's ``samples``
                                         query selects as the
                                         normalization reference
    Metadata_buscar_state             -- healthy/disease/other grouping
                                         (buscar's scoring itself groups
                                         by Metadata_perturbation; this
                                         column exists to keep the state
                                         explicit rather than overloaded,
                                         per plan.md section 18 -- see
                                         :mod:`rerx.buscar` and
                                         :func:`buscar_state`)
"""

from pathlib import Path

import pandas as pd

from rerx.buscar import CONTROL_PERTURBATION
from rerx.metadata import (
    DISEASE_CONDITION_ACTIVE,
    DISEASE_CONDITION_MOCK,
    DISEASE_CONDITION_UV,
    HEALTHY_STATE,
)
from rerx.metadata import (
    DISEASE_STATE as BUSCAR_DISEASE_STATE,
)

# Columns CytoTable prefixes with Image_Metadata_ (site identity, carried
# through Per_Image) that Pycytominer's infer=True expects to find under
# the plain Metadata_ prefix.
IMAGE_METADATA_RENAME = {
    "Image_Metadata_Experiment": "Metadata_Experiment",
    "Image_Metadata_Plate": "Metadata_Plate",
    "Image_Metadata_Well": "Metadata_Well",
    "Image_Metadata_Site": "Metadata_Site",
}

# Feature-selection operations to start with (plan.md section 18, matching
# the coSMicQC JUMP example).
DEFAULT_FEATURE_SELECT_OPERATIONS = [
    "variance_threshold",
    "correlation_threshold",
    "blocklist",
    "drop_na_columns",
]

RXRX_CONTROL_MOCK = "mock"
RXRX_CONTROL_UV = "uv"
RXRX_CONTROL_ACTIVE_UNTREATED = "active_untreated"
RXRX_CONTROL_TREATED = "treated"

PYCYTOMINER_CONTROL_REFERENCE = "control"
PYCYTOMINER_CONTROL_SAMPLE = "sample"

BUSCAR_STATE_OTHER = "other"

# coSMicQC's default nuclei QC threshold sets (see
# cosmicqc/data/qc_nuclei_thresholds_default.yml) only read these three
# Nuclei_AreaShape_* features. MorphEm profiles have none of them, so
# this is the exact check for "do the default thresholds even apply".
_COSMICQC_DEFAULT_NUCLEI_FEATURES = (
    "Nuclei_AreaShape_Area",
    "Nuclei_AreaShape_FormFactor",
    "Nuclei_AreaShape_Eccentricity",
)


def rename_image_metadata_columns(profiles: pd.DataFrame) -> pd.DataFrame:
    """
    Rename CytoTable's ``Image_Metadata_*`` columns to plain ``Metadata_*``.

    Parameters
    ----------
    profiles : pd.DataFrame
        CytoTable join output (see :func:`rerx.cytotable.convert_sqlite_to_parquet`).

    Returns
    -------
    pd.DataFrame
        Copy of ``profiles`` with any :data:`IMAGE_METADATA_RENAME` keys
        present renamed. Columns not present are left alone (MorphEm
        profiles don't carry ``Image_Metadata_*`` at all; see plan.md
        section 18: "For MorphEm profiles: annotate with the same
        metadata").
    """
    present = {k: v for k, v in IMAGE_METADATA_RENAME.items() if k in profiles.columns}
    return profiles.rename(columns=present)


def _is_missing(value: object) -> bool:
    """
    True when a metadata cell value means "not present".

    Covers ``None``, ``float("nan")``, ``pd.NA``, and empty/whitespace
    strings. Direct comparisons are unsafe for this (``pd.NA == ""``
    evaluates to ``pd.NA``, and ``bool(pd.NA)`` raises), so every
    treatment/disease "is it untreated?" decision goes through here.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        # Non-scalar or exotic dtype: treat as present.
        return False


def rxrx_control_type(row: pd.Series) -> str:
    """
    RxRx19a-native control classification for one metadata row.

    Parameters
    ----------
    row : pd.Series
        Row with ``disease_condition`` and ``treatment`` (or their
        ``Metadata_``-prefixed equivalents after annotation).

    Returns
    -------
    str
        One of :data:`RXRX_CONTROL_MOCK`, :data:`RXRX_CONTROL_UV`,
        :data:`RXRX_CONTROL_ACTIVE_UNTREATED`, or
        :data:`RXRX_CONTROL_TREATED`.
    """
    disease = row.get("disease_condition")
    if _is_missing(disease):
        disease = row.get("Metadata_disease_condition")
    treatment = row.get("treatment")
    if _is_missing(treatment):
        treatment = row.get("Metadata_treatment")
    untreated = _is_missing(treatment)
    if disease == DISEASE_CONDITION_MOCK:
        return RXRX_CONTROL_MOCK
    if disease == DISEASE_CONDITION_UV:
        return RXRX_CONTROL_UV
    if disease == DISEASE_CONDITION_ACTIVE and untreated:
        return RXRX_CONTROL_ACTIVE_UNTREATED
    return RXRX_CONTROL_TREATED


def pycytominer_control_type(rxrx_control: str) -> str:
    """
    Map an RxRx control label to the Pycytominer normalization reference.

    Mock wells are the normalization reference (plan.md section 18:
    "Normalize using explicit control samples" -- healthy/mock is ReRx's
    baseline, matching :data:`rerx.metadata.HEALTHY_STATE`).

    Parameters
    ----------
    rxrx_control : str
        Value from :func:`rxrx_control_type`.

    Returns
    -------
    str
        :data:`PYCYTOMINER_CONTROL_REFERENCE` for mock wells, else
        :data:`PYCYTOMINER_CONTROL_SAMPLE`.
    """
    if rxrx_control == RXRX_CONTROL_MOCK:
        return PYCYTOMINER_CONTROL_REFERENCE
    return PYCYTOMINER_CONTROL_SAMPLE


def buscar_state(rxrx_control: str) -> str:
    """
    Map an RxRx control label to the buscar healthy/disease state.

    Parameters
    ----------
    rxrx_control : str
        Value from :func:`rxrx_control_type`.

    Returns
    -------
    str
        :data:`rerx.metadata.HEALTHY_STATE` for mock,
        :data:`rerx.metadata.DISEASE_STATE` for UV or active/untreated
        or treated wells (all are SARS-CoV-2-challenged, so all carry
        the disease-challenge state label; note buscar's scoring
        itself does not read this column -- see the module docstring),
        else :data:`BUSCAR_STATE_OTHER`.
    """
    if rxrx_control == RXRX_CONTROL_MOCK:
        return HEALTHY_STATE
    if rxrx_control in (
        RXRX_CONTROL_UV,
        RXRX_CONTROL_ACTIVE_UNTREATED,
        RXRX_CONTROL_TREATED,
    ):
        return BUSCAR_DISEASE_STATE
    return BUSCAR_STATE_OTHER


def add_control_columns(annotated: pd.DataFrame) -> pd.DataFrame:
    """
    Add the three separate control-meaning columns (plan.md section 18).

    Parameters
    ----------
    annotated : pd.DataFrame
        Profiles already annotated with RxRx19a metadata (must carry
        ``disease_condition``/``Metadata_disease_condition`` and
        ``treatment``/``Metadata_treatment``).

    Returns
    -------
    pd.DataFrame
        Copy of ``annotated`` with ``Metadata_rxrx_control_type``,
        ``Metadata_pycytominer_control_type``, and
        ``Metadata_buscar_state`` added.

    Raises
    ------
    ValueError
        If neither the plain nor ``Metadata_``-prefixed disease/treatment
        columns are present.
    """
    has_plain = (
        "disease_condition" in annotated.columns and "treatment" in annotated.columns
    )
    has_prefixed = (
        "Metadata_disease_condition" in annotated.columns
        and "Metadata_treatment" in annotated.columns
    )
    if not (has_plain or has_prefixed):
        raise ValueError(
            "annotated profiles need disease_condition/treatment or "
            "Metadata_disease_condition/Metadata_treatment columns"
        )
    out = annotated.copy()
    rxrx = out.apply(rxrx_control_type, axis=1)
    out["Metadata_rxrx_control_type"] = rxrx
    out["Metadata_pycytominer_control_type"] = rxrx.map(pycytominer_control_type)
    out["Metadata_buscar_state"] = rxrx.map(buscar_state)
    return out


def add_perturbation_column(annotated: pd.DataFrame) -> pd.DataFrame:
    """
    Add ``Metadata_perturbation``, the stable buscar perturbation identifier.

    Plan.md section 19: ``Metadata_perturbation = <treatment>__<concentration>``,
    with untreated/control wells collapsed to a single ``"control"`` label
    (buscar's ``perturbation_col`` groups replicate wells of the same
    treatment+dose together; giving every control well a distinct
    perturbation id would prevent that grouping).

    Parameters
    ----------
    annotated : pd.DataFrame
        Profiles already annotated with RxRx19a metadata (must carry
        ``treatment``/``Metadata_treatment`` and
        ``treatment_conc``/``Metadata_treatment_conc``).

    Returns
    -------
    pd.DataFrame
        Copy of ``annotated`` with ``Metadata_perturbation`` added.

    Raises
    ------
    ValueError
        If neither the plain nor ``Metadata_``-prefixed treatment columns
        are present.
    """
    treatment_col = (
        "Metadata_treatment" if "Metadata_treatment" in annotated.columns else None
    ) or ("treatment" if "treatment" in annotated.columns else None)
    conc_col = (
        "Metadata_treatment_conc"
        if "Metadata_treatment_conc" in annotated.columns
        else None
    ) or ("treatment_conc" if "treatment_conc" in annotated.columns else None)
    if treatment_col is None or conc_col is None:
        raise ValueError(
            "annotated profiles need treatment/treatment_conc or "
            "Metadata_treatment/Metadata_treatment_conc columns"
        )

    def perturbation(row: pd.Series) -> str:
        treatment = row.get(treatment_col)
        if not _is_missing(treatment):
            conc = row.get(conc_col)
            conc_str = "" if _is_missing(conc) else str(conc)
            return f"{treatment}__{conc_str}" if conc_str else str(treatment)
        # Untreated: keep mock/uv/active_untreated distinguishable when the
        # RxRx control-type column is available (plan.md section 19 --
        # buscar scores per perturbation, so collapsing every untreated
        # arm into one "control" bucket would hide their differences).
        control_type = row.get("Metadata_rxrx_control_type")
        if control_type:
            return str(control_type)
        return CONTROL_PERTURBATION

    out = annotated.copy()
    out["Metadata_perturbation"] = out.apply(perturbation, axis=1)
    return out


def flag_outliers(profiles: pd.DataFrame) -> pd.DataFrame:
    """
    Flag coSMicQC outlier nuclei (plan.md section 18, step 2).

    Runs coSMicQC's default nuclei QC threshold sets (small/low-formfactor,
    elongated, large -- see ``cosmicqc.label_outliers``) and adds one
    ``Metadata_cqc_<set>_is_outlier`` boolean column per set. Rows are not
    removed here; see :func:`drop_flagged_outliers` for that (plan.md
    keeps QC columns outside the morphology feature set, so flag and
    drop are separate steps -- a caller may want to inspect flagged rows
    before discarding them).

    A no-op when ``profiles`` lacks the ``Nuclei_AreaShape_*`` columns
    the default thresholds read (e.g. MorphEm profiles, which have no
    CellProfiler nuclei shape features at all).

    Parameters
    ----------
    profiles : pd.DataFrame
        Annotated profiles (CellProfiler or MorphEm).

    Returns
    -------
    pd.DataFrame
        ``profiles`` with ``Metadata_cqc_*_is_outlier`` columns added, or
        unchanged if the nuclei QC thresholds don't apply.
    """
    if not all(c in profiles.columns for c in _COSMICQC_DEFAULT_NUCLEI_FEATURES):
        return profiles

    import cosmicqc

    labeled = cosmicqc.label_outliers(profiles)
    # cosmicqc returns a CytoDataFrame (a pandas subclass carrying extra
    # image/context attributes); normalize to a plain DataFrame so
    # downstream pycytominer calls (which only expect plain pandas) see
    # the type they're written against.
    return pd.DataFrame(labeled)


def drop_flagged_outliers(flagged: pd.DataFrame) -> pd.DataFrame:
    """
    Drop rows flagged by any :func:`flag_outliers` outlier column.

    Removes the ``Metadata_cqc_*_is_outlier`` columns from the result too
    -- they are QC bookkeeping, not morphology features, and normalize/
    select_features should never see them as feature columns.

    Parameters
    ----------
    flagged : pd.DataFrame
        Output of :func:`flag_outliers`.

    Returns
    -------
    pd.DataFrame
        ``flagged`` with outlier rows and QC flag columns removed. A
        no-op (returned unchanged) if no flag columns are present.
    """
    outlier_cols = [c for c in flagged.columns if c.endswith("_is_outlier")]
    if not outlier_cols:
        return flagged
    keep = ~flagged[outlier_cols].any(axis=1)
    return flagged.loc[keep].drop(columns=outlier_cols).reset_index(drop=True)


def annotate_profiles(
    profiles: pd.DataFrame,
    site_metadata: pd.DataFrame,
) -> pd.DataFrame:
    """
    Join RxRx19a site metadata onto profile rows and add control columns.

    Parameters
    ----------
    profiles : pd.DataFrame
        Profile rows (CellProfiler via CytoTable, or MorphEm). If
        CellProfiler, :func:`rename_image_metadata_columns` should be
        called first (or is a no-op for MorphEm, which has no
        ``Image_Metadata_*`` columns; plan.md section 18: "For MorphEm
        profiles: annotate with the same metadata").
    site_metadata : pd.DataFrame
        Parsed RxRx19a metadata (see :func:`rerx.metadata.parse_metadata`),
        one row per site.

    Returns
    -------
    pd.DataFrame
        ``profiles`` joined with ``site_metadata`` on experiment/plate/
        well/site, plus the three control columns from
        :func:`add_control_columns`.

    Raises
    ------
    ValueError
        If ``profiles`` is missing the join key columns after renaming.
    """
    renamed = rename_image_metadata_columns(profiles)
    required = (
        "Metadata_Experiment",
        "Metadata_Plate",
        "Metadata_Well",
        "Metadata_Site",
    )
    missing = [c for c in required if c not in renamed.columns]
    if missing:
        raise ValueError(f"profiles missing join columns after rename: {missing}")

    meta = site_metadata.rename(
        columns={
            "experiment": "Metadata_Experiment",
            "plate": "Metadata_Plate",
            "well": "Metadata_Well",
            "site": "Metadata_Site",
        }
    )
    # Join keys must match dtype; CytoTable/CellProfiler emit these as
    # strings (plate) or ints (site) depending on source, so normalize
    # both sides to string for the merge.
    left = renamed.copy()
    right = meta.copy()
    for col in required:
        left[col] = left[col].astype(str)
        right[col] = right[col].astype(str)

    joined = left.merge(right, on=list(required), how="left", suffixes=("", "_meta"))
    return add_control_columns(joined)


def normalization_samples_query(
    control_column: str = "Metadata_pycytominer_control_type",
) -> str:
    """
    Pycytominer ``samples`` query selecting the normalization reference.

    Parameters
    ----------
    control_column : str
        Column added by :func:`add_control_columns`.

    Returns
    -------
    str
        Query string for ``pycytominer.normalize(samples=...)``.
    """
    return f"{control_column} == '{PYCYTOMINER_CONTROL_REFERENCE}'"


_CELLPROFILER_FEATURE_PREFIXES = ("Cells_", "Cytoplasm_", "Nuclei_")


def _features_argument(profiles: pd.DataFrame) -> list[str] | str:
    """Pick the pycytominer ``features`` argument for ``profiles``.

    Pycytominer's ``"infer"`` only recognizes CellProfiler column
    prefixes (``Cells_``/``Cytoplasm_``/``Nuclei_``) and raises
    ``ValueError`` on frames without them (e.g. MorphEm profiles,
    whose features are ``Morphem_*``). For those, return the explicit
    feature column list instead.

    Parameters
    ----------
    profiles : pd.DataFrame
        Annotated profiles (CellProfiler or MorphEm).

    Returns
    -------
    list[str] | str
        ``"infer"`` when CellProfiler features are present, otherwise
        the explicit list of ``Morphem_*`` columns.
    """
    columns = profiles.columns
    if any(c.startswith(_CELLPROFILER_FEATURE_PREFIXES) for c in columns):
        return "infer"
    morphem = [c for c in columns if c.startswith("Morphem_")]
    if morphem:
        return morphem
    return "infer"


def normalize_profiles(
    annotated: pd.DataFrame,
    output_file: Path,
    image_features: bool = False,
    method: str = "standardize",
) -> pd.DataFrame:
    """
    Normalize annotated profiles against explicit control samples.

    Parameters
    ----------
    annotated : pd.DataFrame
        Output of :func:`annotate_profiles`.
    output_file : Path
        Destination Parquet path.
    image_features : bool
        Whether to include numeric ``Image_*`` features (plan.md keeps
        this False for CellProfiler profiles by default).
    method : str
        Pycytominer normalization method.

    Returns
    -------
    pd.DataFrame
        Normalized profiles (also written to ``output_file``).
    """
    import pycytominer

    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    pycytominer.normalize(
        profiles=annotated,
        features=_features_argument(annotated),
        image_features=image_features,
        meta_features="infer",
        method=method,
        samples=normalization_samples_query(),
        output_file=str(output_file),
        output_type="parquet",
    )
    # pycytominer returns the output path (str), not the DataFrame, when
    # output_file is given (confirmed against a real call); read it back
    # so callers get a consistent DataFrame return type.
    return pd.read_parquet(output_file)


def select_features(
    normalized: pd.DataFrame,
    output_file: Path,
    operations: list[str] | None = None,
    image_features: bool = False,
    na_cutoff: float = 0.0,
) -> pd.DataFrame:
    """
    Run Pycytominer feature selection (plan.md section 18 default operations).

    Parameters
    ----------
    normalized : pd.DataFrame
        Output of :func:`normalize_profiles`.
    output_file : Path
        Destination Parquet path.
    operations : list[str] | None
        Feature-selection operations; defaults to
        :data:`DEFAULT_FEATURE_SELECT_OPERATIONS`.
    image_features : bool
        Whether ``Image_*`` numeric features are included.
    na_cutoff : float
        Max fraction of missing values allowed per feature before it is
        dropped (matches the coSMicQC JUMP example's ``na_cutoff=0``).

    Returns
    -------
    pd.DataFrame
        Feature-selected profiles (also written to ``output_file``).
    """
    import pycytominer

    ops = (
        operations
        if operations is not None
        else list(DEFAULT_FEATURE_SELECT_OPERATIONS)
    )
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    pycytominer.feature_select(
        profiles=normalized,
        features=_features_argument(normalized),
        image_features=image_features,
        operation=ops,
        na_cutoff=na_cutoff,
        output_file=str(output_file),
        output_type="parquet",
    )
    # Same str-return behavior as normalize() when output_file is given;
    # read the written file back for a consistent DataFrame return type.
    return pd.read_parquet(output_file)

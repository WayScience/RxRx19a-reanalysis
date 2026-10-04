"""
CellProfiler SQLite to single-cell Parquet via CytoTable.

Implements plan.md section 13: convert a shard's validated CellProfiler
SQLite output into one row per cell, joining Nuclei/Cells/Cytoplasm on the
CellProfiler parent-child relationships, with a stable ``Metadata_cell_id``.

Verified against a real CellProfiler 4.2.8 container run (4 image sets, 43
cells): the built-in ``cellprofiler_sqlite`` CytoTable preset's join SQL
only carries ``Image_Metadata_Well``/``Image_Metadata_Plate`` through, so
this module overrides ``joins`` with a version that also keeps
``Image_Metadata_Experiment`` and ``Image_Metadata_Site`` (needed for
stable cell IDs and Hive-style partitioning; plan.md sections 13, 24).

CytoTable's default Parsl HighThroughputExecutor failed to register workers
in this sandboxed environment (multiprocessing SyncManager EOFError); a
plain ``parsl.configs.local_threads.config`` works reliably and is used
unless the caller supplies a different ``parsl_config``.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import parsl

CYTOTABLE_PRESET = "cellprofiler_sqlite"
CYTOTABLE_SOURCE_DATATYPE = "sqlite"

# Object-count columns CytoTable's per-compartment join keeps; these become
# the per-cell object identity alongside Metadata_ImageNumber.
OBJECT_NUMBER_COLUMNS = (
    "Cells_Number_Object_Number",
    "Nuclei_Number_Object_Number",
    "Cytoplasm_Number_Object_Number",
)

# Join SQL: CytoTable's built-in cellprofiler_sqlite preset, extended to
# also keep Image_Metadata_Experiment and Image_Metadata_Site (the preset
# only keeps Well/Plate), plus the MeasureImageQuality columns
# (Image_ImageQuality_*; see rerx.validate.check_image_quality -- the
# production-safe per-image dim-well check for the Plate 25 AA08/E08
# over-segmentation problem). Compartments and parent-child logic are
# otherwise identical to the upstream preset (cytotable.presets.config
# ["cellprofiler_sqlite"]["CONFIG_JOINS"]).
RERX_JOINS = """
    SELECT
        per_image.Metadata_ImageNumber,
        per_image.Image_Metadata_Experiment,
        per_image.Image_Metadata_Plate,
        per_image.Image_Metadata_Well,
        per_image.Image_Metadata_Site,
        COLUMNS('Image_FileName_.*'),
        COLUMNS('Image_ImageQuality_.*'),
        per_cytoplasm.* EXCLUDE (Metadata_ImageNumber),
        per_cells.* EXCLUDE (Metadata_ImageNumber),
        per_nuclei.* EXCLUDE (Metadata_ImageNumber)
    FROM
        read_parquet('per_cytoplasm.parquet') AS per_cytoplasm
    LEFT JOIN read_parquet('per_cells.parquet') AS per_cells
        USING (Metadata_ImageNumber)
    LEFT JOIN read_parquet('per_nuclei.parquet') AS per_nuclei
        USING (Metadata_ImageNumber)
    LEFT JOIN read_parquet('per_image.parquet') AS per_image
        USING (Metadata_ImageNumber)
    WHERE
        per_cells.Cells_Number_Object_Number
            = per_cytoplasm.Cytoplasm_Parent_Cells
        AND per_nuclei.Nuclei_Number_Object_Number
            = per_cytoplasm.Cytoplasm_Parent_Nuclei
"""


def local_threads_parsl_config() -> "parsl.Config":
    """
    Parsl config that works in sandboxed/restricted shells.

    CytoTable's default executor uses a multiprocessing manager that fails
    to register workers under some sandboxes (confirmed here: ``parsl.
    executors.errors.BadStateException`` from an ``EOFError`` in
    ``multiprocessing.managers.SyncManager``). ``local_threads`` avoids the
    manager process entirely.

    Returns
    -------
    parsl.config.Config
        Thread-pool Parsl config suitable for :func:`convert_sqlite_to_parquet`.
    """
    from parsl.configs.local_threads import config

    return config


def convert_sqlite_to_parquet(
    sqlite_path: Path,
    dest_path: Path,
    joins: str = RERX_JOINS,
    parsl_config: "parsl.Config | None" = None,
) -> Path:
    """
    Convert one shard's CellProfiler SQLite to a joined single-cell Parquet.

    Parameters
    ----------
    sqlite_path : Path
        Validated per-shard SQLite database (see
        :mod:`rerx.validate` for the integrity checks to run first;
        plan.md section 12 requires these before conversion).
    dest_path : Path
        Output Parquet path. This is CytoTable's raw join output, an
        intermediate artifact -- write it to scratch (e.g. alongside the
        shard's SQLite), not into the run's final
        ``profiles/cellprofiler/raw/`` directory. The durable per-run
        artifact is the *partitioned* Parquet from
        :func:`write_partitioned_profiles` (after :func:`add_cell_ids`),
        which is what :mod:`rerx.catalog`'s ``cellprofiler_raw`` table
        globs on; writing this intermediate into the same directory
        creates two files with different schemas in one glob (confirmed
        during e2e testing: DuckDB's catalog registration fails with a
        "Column ... was not found in file" error).
    joins : str
        DuckDB join SQL passed to CytoTable. Defaults to :data:`RERX_JOINS`.
    parsl_config : object | None
        Parsl config for CytoTable's executor. Defaults to
        :func:`local_threads_parsl_config`.

    Returns
    -------
    Path
        The written Parquet path (as returned by CytoTable).

    Raises
    ------
    FileNotFoundError
        If ``sqlite_path`` does not exist.
    """
    from cytotable import convert

    sqlite_path = Path(sqlite_path)
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"missing shard SQLite database: {sqlite_path}")
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = parsl_config if parsl_config is not None else local_threads_parsl_config()
    result = convert(
        source_path=str(sqlite_path),
        dest_path=str(dest_path),
        dest_datatype="parquet",
        source_datatype=CYTOTABLE_SOURCE_DATATYPE,
        preset=CYTOTABLE_PRESET,
        joins=joins,
        parsl_config=cfg,
    )
    return Path(result) if isinstance(result, str) else dest_path


def make_cell_id(row: pd.Series) -> str:
    """
    Stable ``Metadata_cell_id`` for one joined-profile row (plan.md section 13).

    Built from the site's identity plus the per-object numbers, so the ID
    is stable across reruns of the same shard and unique within a run
    (each compartment's ``Number_Object_Number`` is per-image, not global).

    Parameters
    ----------
    row : pd.Series
        A row from the CytoTable output, must carry
        ``Image_Metadata_Experiment``, ``Image_Metadata_Plate``,
        ``Image_Metadata_Well``, ``Image_Metadata_Site``, and
        :data:`OBJECT_NUMBER_COLUMNS`.

    Returns
    -------
    str
        ``{experiment}_{plate}_{well}_{site}_c{cells}_n{nuclei}``.
    """
    return (
        f"{row['Image_Metadata_Experiment']}_{row['Image_Metadata_Plate']}_"
        f"{row['Image_Metadata_Well']}_{row['Image_Metadata_Site']}_"
        f"c{int(row['Cells_Number_Object_Number'])}_"
        f"n{int(row['Nuclei_Number_Object_Number'])}"
    )


def add_cell_ids(profiles: pd.DataFrame) -> pd.DataFrame:
    """
    Add ``Metadata_cell_id`` to a CytoTable output frame.

    Parameters
    ----------
    profiles : pd.DataFrame
        CytoTable join output (see :func:`convert_sqlite_to_parquet`).

    Returns
    -------
    pd.DataFrame
        Copy of ``profiles`` with ``Metadata_cell_id`` inserted as the
        first column.

    Raises
    ------
    ValueError
        If required identifying columns are missing.
    """
    required = (
        "Image_Metadata_Experiment",
        "Image_Metadata_Plate",
        "Image_Metadata_Well",
        "Image_Metadata_Site",
        *OBJECT_NUMBER_COLUMNS,
    )
    missing = [c for c in required if c not in profiles.columns]
    if missing:
        raise ValueError(f"profiles missing required columns: {missing}")
    out = profiles.copy()
    out.insert(0, "Metadata_cell_id", out.apply(make_cell_id, axis=1))
    return out


def validate_unique_cell_ids(profiles: pd.DataFrame) -> None:
    """
    Check ``Metadata_cell_id`` uniqueness (plan.md section 27 exit criterion).

    Raises
    ------
    ValueError
        If ``Metadata_cell_id`` is missing or has duplicates.
    """
    if "Metadata_cell_id" not in profiles.columns:
        raise ValueError("profiles missing Metadata_cell_id; call add_cell_ids first")
    dupes = profiles["Metadata_cell_id"][profiles["Metadata_cell_id"].duplicated()]
    if not dupes.empty:
        raise ValueError(
            f"{len(dupes)} duplicate Metadata_cell_id values, e.g. {dupes.iloc[0]!r}"
        )


def plate_partitions(
    profiles: pd.DataFrame,
) -> list[tuple[tuple[str, str], pd.DataFrame]]:
    """
    Group cell profiles into per-(experiment, plate) partitions.

    This is the batching unit for scalable finalize/annotate/normalize/
    buscar processing: a full RxRx19a run spans many plates, and each
    plate is its own biological batch (its own control population), so
    every downstream step -- Pycytominer normalization, feature
    selection, buscar scoring, partitioned Parquet writes -- should run
    per plate rather than concatenating the whole dataset into memory at
    once (plan.md section 24 already partitions storage this way; this
    extends the same boundary to computation).

    Parameters
    ----------
    profiles : pd.DataFrame
        Cell profiles with ``Image_Metadata_Experiment`` and
        ``Image_Metadata_Plate`` columns.

    Returns
    -------
    list[tuple[tuple[str, str], pd.DataFrame]]
        ``((experiment, plate), group_df)`` pairs, sorted by
        ``(experiment, plate)``, each a copy safe to mutate.

    Raises
    ------
    ValueError
        If required columns are missing.
    """
    required = ("Image_Metadata_Experiment", "Image_Metadata_Plate")
    missing = [c for c in required if c not in profiles.columns]
    if missing:
        raise ValueError(f"profiles missing required columns: {missing}")
    groups: list[tuple[tuple[str, str], pd.DataFrame]] = []
    for _, group in profiles.groupby(list(required), sort=True):
        experiment = str(group["Image_Metadata_Experiment"].iloc[0])
        plate = str(group["Image_Metadata_Plate"].iloc[0])
        groups.append(((experiment, plate), group.copy()))
    return groups


def write_partitioned_profiles(
    profiles: pd.DataFrame,
    dest_root: Path,
) -> list[Path]:
    """
    Write cell profiles partitioned by experiment and plate (plan.md section 24).

    One Parquet file per ``(experiment, plate)`` pair, Hive-style
    (``dest_root/experiment=<e>/plate=<p>/profiles.parquet``), ZSTD
    compressed. Not partitioned by well or cell ID (plan.md section 24:
    avoid high-cardinality partitions).

    Parameters
    ----------
    profiles : pd.DataFrame
        Profiles with ``Metadata_cell_id`` already added.
    dest_root : Path
        Destination root (``profiles/cellprofiler/raw`` inside the run).

    Returns
    -------
    list[Path]
        Paths written, one per experiment/plate partition, sorted.

    Raises
    ------
    ValueError
        If required partition columns are missing.
    """
    dest_root = Path(dest_root)
    written: list[Path] = []
    for (experiment, plate), group in plate_partitions(profiles):
        part_dir = dest_root / f"experiment={experiment}" / f"plate={plate}"
        part_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = part_dir / "profiles.parquet.tmp"
        final_path = part_dir / "profiles.parquet"
        group.to_parquet(tmp_path, index=False, compression="zstd")
        tmp_path.rename(final_path)
        written.append(final_path)
    return sorted(written)


@dataclass(frozen=True)
class SchemaCheckResult:
    """
    Outcome of comparing shard Parquet schemas (plan.md section 27).

    Attributes
    ----------
    consistent : bool
        Whether every shard has the same column set.
    reference_columns : list[str]
        Columns of the first shard, used as the reference.
    mismatches : dict[str, tuple[list[str], list[str]]]
        For any inconsistent shard: ``path -> (missing, extra)`` columns
        relative to the reference.
    """

    consistent: bool
    reference_columns: list[str]
    mismatches: dict[str, tuple[list[str], list[str]]]


def check_schema_consistency(parquet_paths: list[Path]) -> SchemaCheckResult:
    """
    Verify every shard Parquet has an identical column set (plan.md section 27).

    Parameters
    ----------
    parquet_paths : list[Path]
        Shard Parquet files to compare.

    Returns
    -------
    SchemaCheckResult
        Whether schemas are consistent, and any mismatches found.

    Raises
    ------
    ValueError
        If ``parquet_paths`` is empty.
    """
    import pyarrow.parquet as pq

    if not parquet_paths:
        raise ValueError("parquet_paths must not be empty")
    paths = [Path(p) for p in parquet_paths]
    reference_columns = pq.read_schema(paths[0]).names
    reference_set = set(reference_columns)
    mismatches: dict[str, tuple[list[str], list[str]]] = {}
    for path in paths[1:]:
        columns = set(pq.read_schema(path).names)
        if columns != reference_set:
            missing = sorted(reference_set - columns)
            extra = sorted(columns - reference_set)
            mismatches[str(path)] = (missing, extra)
    return SchemaCheckResult(
        consistent=not mismatches,
        reference_columns=reference_columns,
        mismatches=mismatches,
    )


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """SHA-256 hex digest of a file (used for the source manifest hash)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()

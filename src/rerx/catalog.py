"""
Frozen DuckLake catalog builder for completed ReRx runs.

Implements plan.md section 20 ("Stage 11: Frozen DuckLake catalog"): after
a run reaches ``_SUCCESS``, build a read-only DuckLake catalog over its
Parquet files using ``ducklake_add_data_files()`` (see
https://ducklake.select/2025/10/24/frozen-ducklake/), so the files
themselves stay canonical and the catalog is disposable/rebuildable.

Verified locally: ``ducklake_add_data_files`` requires the target table to
already exist with a schema derived from the file before registering it
(confirmed against this project's real CytoTable output Parquet -- 43
rows queried back correctly through the catalog).

Plan.md keeps DuckLake out of the write path entirely (design decision
#10: "Use DuckLake only as a derived catalog over completed Parquet
files") -- this module only ever reads existing Parquet and registers it;
it never writes profile/crop data.
"""

from dataclasses import dataclass
from pathlib import Path

# Table name -> glob (relative to the run root) of the Parquet files that
# belong to it, per plan.md section 20's suggested table list. Tables
# whose glob matches no files are skipped (a pilot run may not populate
# every stage yet -- e.g. no baseline comparison until MorphEm runs).
CATALOG_TABLES: dict[str, str] = {
    "metadata": "metadata/rxrx19a.parquet",
    "cellprofiler_raw": "profiles/cellprofiler/raw/**/*.parquet",
    "cellprofiler_normalized": "profiles/cellprofiler/normalized/**/*.parquet",
    "cellprofiler_feature_selected": (
        "profiles/cellprofiler/feature_selected/**/*.parquet"
    ),
    "crops": "crops/cells/**/*.parquet",
    "morphem_raw": "profiles/morphem/raw/**/*.parquet",
    "morphem_normalized": "profiles/morphem/normalized/**/*.parquet",
    "fused_feature_selected": "profiles/fused/feature_selected/**/*.parquet",
    "recursion_site_embeddings": "baseline/recursion_site_embeddings/**/*.parquet",
    "embedding_comparison_metrics": "comparisons/recursion_vs_morphem/**/*.parquet",
    "buscar_cellprofiler_signatures": ("buscar/cellprofiler/**/signatures.parquet"),
    "buscar_cellprofiler_scores": "buscar/cellprofiler/**/scores.parquet",
    "buscar_morphem_signatures": "buscar/morphem/**/signatures.parquet",
    "buscar_morphem_scores": "buscar/morphem/**/scores.parquet",
}


@dataclass(frozen=True)
class CatalogTableResult:
    """
    Outcome of registering one table's Parquet files into the catalog.

    Attributes
    ----------
    table : str
        Table name (a key of :data:`CATALOG_TABLES`).
    files : list[Path]
        Parquet files that were registered.
    row_count : int
        Rows visible through the catalog after registration.
    """

    table: str
    files: list[Path]
    row_count: int


@dataclass(frozen=True)
class CatalogBuildResult:
    """
    Outcome of building a run's Frozen DuckLake catalog.

    Attributes
    ----------
    catalog_path : Path
        The ``.ducklake`` metadata file written.
    data_path : Path
        Directory DuckLake stores registered file references under.
    tables : list[CatalogTableResult]
        One entry per table that had at least one matching Parquet file.
    skipped_tables : list[str]
        Tables from :data:`CATALOG_TABLES` with no matching files (not an
        error -- a pilot run may not have every stage populated yet).
    """

    catalog_path: Path
    data_path: Path
    tables: list[CatalogTableResult]
    skipped_tables: list[str]


def find_table_files(run_root: Path, glob: str) -> list[Path]:
    """
    Resolve one table's glob against a run root, sorted for determinism.

    Parameters
    ----------
    run_root : Path
        Run directory (see :mod:`rerx.runs`).
    glob : str
        Glob pattern relative to ``run_root`` (may contain ``**``).

    Returns
    -------
    list[Path]
        Matching Parquet files, sorted by path.
    """
    return sorted(Path(run_root).glob(glob))


def build_run_catalog(
    run_root: Path,
    catalog_path: Path,
    data_path: Path,
    tables: dict[str, str] | None = None,
) -> CatalogBuildResult:
    """
    Build (or rebuild) a Frozen DuckLake catalog over one run's Parquet.

    Parameters
    ----------
    run_root : Path
        Completed run directory (should carry ``_SUCCESS``; plan.md
        section 21: "Build the PetaLibrary DuckLake catalog" happens at
        run completion, after publishing validated shards).
    catalog_path : Path
        Destination ``.ducklake`` metadata file. Deleted and rebuilt if it
        already exists (catalogs are disposable per plan.md section 20).
    data_path : Path
        Directory DuckLake uses for its own bookkeeping (not a copy of
        the Parquet data -- ``ducklake_add_data_files`` registers files
        in place; plan.md section 20: "Parquet remains canonical").
    tables : dict[str, str] | None
        Table name -> glob mapping. Defaults to :data:`CATALOG_TABLES`.

    Returns
    -------
    CatalogBuildResult
        Registered tables (with row counts) and any tables skipped for
        having no matching files.

    Raises
    ------
    FileNotFoundError
        If ``run_root`` does not exist.
    """
    import duckdb

    run_root = Path(run_root)
    if not run_root.is_dir():
        raise FileNotFoundError(f"missing run directory: {run_root}")

    catalog_path = Path(catalog_path)
    data_path = Path(data_path)
    if catalog_path.exists():
        catalog_path.unlink()
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.mkdir(parents=True, exist_ok=True)

    table_globs = tables if tables is not None else CATALOG_TABLES

    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake")
        con.execute("LOAD ducklake")
        con.execute(
            f"ATTACH 'ducklake:{catalog_path}' AS rerx_catalog "
            f"(DATA_PATH '{data_path}')"
        )
        con.execute("USE rerx_catalog")

        results: list[CatalogTableResult] = []
        skipped: list[str] = []
        for table, glob in sorted(table_globs.items()):
            files = find_table_files(run_root, glob)
            if not files:
                skipped.append(table)
                continue
            file_list = ", ".join(f"'{f}'" for f in files)
            con.execute(
                f"CREATE TABLE {table} AS "
                f"SELECT * FROM read_parquet([{file_list}], union_by_name=true) LIMIT 0"
            )
            for f in files:
                con.execute(
                    "CALL ducklake_add_data_files(?, ?, ?, allow_missing => true)",
                    ["rerx_catalog", table, str(f)],
                )
            row_count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            results.append(
                CatalogTableResult(
                    table=table,
                    files=files,
                    row_count=int(row_count[0]) if row_count else 0,
                )
            )
    finally:
        con.close()

    return CatalogBuildResult(
        catalog_path=catalog_path,
        data_path=data_path,
        tables=results,
        skipped_tables=skipped,
    )

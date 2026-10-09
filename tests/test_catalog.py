"""
Tests for the catalog module.
"""

from pathlib import Path

import pandas as pd
import pytest

from rerx.catalog import (
    CATALOG_TABLES,
    CatalogBuildResult,
    build_run_catalog,
    find_table_files,
)


def _make_run_with_profiles(run_root: Path) -> None:
    raw_dir = run_root / "profiles" / "cellprofiler" / "raw"
    raw_dir.mkdir(parents=True)
    df = pd.DataFrame(
        {
            "Metadata_cell_id": ["a", "b", "c"],
            "Cells_AreaShape_Area": [100.0, 110.0, 120.0],
        }
    )
    df.to_parquet(raw_dir / "shard0.parquet", index=False)

    crops_dir = run_root / "crops" / "cells"
    crops_dir.mkdir(parents=True)
    crops = pd.DataFrame(
        {"Metadata_cell_id": ["a", "b", "c"], "crop_w1_jpeg": [b"x", b"y", b"z"]}
    )
    crops.to_parquet(crops_dir / "shard0.parquet", index=False)


def test_find_table_files_matches_glob(tmp_path: Path) -> None:
    _make_run_with_profiles(tmp_path)
    files = find_table_files(tmp_path, "profiles/cellprofiler/raw/**/*.parquet")
    assert len(files) == 1
    assert files[0].name == "shard0.parquet"


def test_find_table_files_no_match_returns_empty(tmp_path: Path) -> None:
    files = find_table_files(tmp_path, "profiles/morphem/raw/**/*.parquet")
    assert files == []


def test_build_run_catalog_registers_present_tables(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    _make_run_with_profiles(run_root)
    catalog_path = tmp_path / "catalog.ducklake"
    data_path = tmp_path / "data"

    result = build_run_catalog(run_root, catalog_path, data_path)

    assert isinstance(result, CatalogBuildResult)
    assert catalog_path.exists()
    table_names = {t.table for t in result.tables}
    assert table_names == {"cellprofiler_raw", "crops"}
    raw_table = next(t for t in result.tables if t.table == "cellprofiler_raw")
    assert raw_table.row_count == 3
    assert len(raw_table.files) == 1


def test_build_run_catalog_skips_missing_tables(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    _make_run_with_profiles(run_root)
    catalog_path = tmp_path / "catalog.ducklake"
    data_path = tmp_path / "data"

    result = build_run_catalog(run_root, catalog_path, data_path)

    skipped = set(result.skipped_tables)
    present = {t.table for t in result.tables}
    assert skipped == set(CATALOG_TABLES) - present
    assert "morphem_raw" in skipped
    assert "buscar_cellprofiler_scores" in skipped


def test_build_run_catalog_queryable_with_fresh_connection(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    _make_run_with_profiles(run_root)
    catalog_path = tmp_path / "catalog.ducklake"
    data_path = tmp_path / "data"

    build_run_catalog(run_root, catalog_path, data_path)

    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL ducklake")
    con.execute("LOAD ducklake")
    con.execute(f"ATTACH 'ducklake:{catalog_path}' AS cat (DATA_PATH '{data_path}')")
    con.execute("USE cat")
    rows = con.execute(
        "SELECT Metadata_cell_id FROM cellprofiler_raw ORDER BY 1"
    ).fetchall()
    con.close()
    assert rows == [("a",), ("b",), ("c",)]


def test_build_run_catalog_rebuild_overwrites(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    _make_run_with_profiles(run_root)
    catalog_path = tmp_path / "catalog.ducklake"
    data_path = tmp_path / "data"

    build_run_catalog(run_root, catalog_path, data_path)
    # Rebuilding must not fail even though the catalog file already exists
    # (plan.md section 20: catalogs are disposable/rebuildable).
    result = build_run_catalog(run_root, catalog_path, data_path)
    assert catalog_path.exists()
    assert {t.table for t in result.tables} == {"cellprofiler_raw", "crops"}


def test_build_run_catalog_missing_run_root_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="missing run directory"):
        build_run_catalog(
            tmp_path / "does_not_exist",
            tmp_path / "catalog.ducklake",
            tmp_path / "data",
        )


def test_build_run_catalog_preserves_columns_that_differ_by_plate(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    first = run_root / "profiles" / "experiment=HRCE-1" / "plate=1"
    second = run_root / "profiles" / "experiment=HRCE-1" / "plate=2"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    pd.DataFrame({"Metadata_cell_id": ["a"], "Cells_x": [1.0]}).to_parquet(
        first / "profiles.parquet", index=False
    )
    pd.DataFrame({"Metadata_cell_id": ["b"], "Cells_y": [2.0]}).to_parquet(
        second / "profiles.parquet", index=False
    )
    catalog_path, data_path = tmp_path / "catalog.ducklake", tmp_path / "data"
    build_run_catalog(
        run_root,
        catalog_path,
        data_path,
        tables={"profiles": "profiles/**/*.parquet"},
    )
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL ducklake")
    con.execute("LOAD ducklake")
    con.execute(f"ATTACH 'ducklake:{catalog_path}' AS cat (DATA_PATH '{data_path}')")
    try:
        assert con.execute(
            "SELECT Metadata_cell_id, Cells_x, Cells_y FROM cat.profiles "
            "ORDER BY Metadata_cell_id"
        ).fetchall() == [("a", 1.0, None), ("b", None, 2.0)]
    finally:
        con.close()


def test_build_run_catalog_custom_tables(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    _make_run_with_profiles(run_root)
    catalog_path = tmp_path / "catalog.ducklake"
    data_path = tmp_path / "data"

    result = build_run_catalog(
        run_root,
        catalog_path,
        data_path,
        tables={"just_crops": "crops/cells/**/*.parquet"},
    )
    assert {t.table for t in result.tables} == {"just_crops"}
    assert result.skipped_tables == []

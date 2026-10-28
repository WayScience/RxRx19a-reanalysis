"""
Tests for the catalog module.
"""

from pathlib import Path
from typing import TypedDict

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from rerx.catalog import (
    CATALOG_TABLES,
    CatalogBuildResult,
    build_run_catalog,
    find_table_files,
)


class _CatalogOptions(TypedDict, total=False):
    memory_limit: str
    threads: int


class _ObservedConnection:
    """Delegate to a real connection, rejecting unbounded metadata binding."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection
        self.empty_schemas: list[pa.Schema] = []

    def execute(self, query: str, *args: object) -> duckdb.DuckDBPyConnection:
        assert "read_parquet" not in query.lower()
        return self.connection.execute(query, *args)

    def register(self, name: str, value: pa.Table) -> duckdb.DuckDBPyConnection:
        assert value.num_rows == 0
        self.empty_schemas.append(value.schema)
        return self.connection.register(name, value)

    def unregister(self, name: str) -> None:
        self.connection.unregister(name)

    def close(self) -> None:
        self.connection.close()


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


@pytest.mark.parametrize(
    ("narrow_type", "wide_type"),
    [(pa.int32(), pa.int64()), (pa.float32(), pa.float64())],
)
def test_catalog_streams_schemas_and_promotes_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    narrow_type: pa.DataType,
    wide_type: pa.DataType,
) -> None:
    import duckdb

    run_root = tmp_path / "run"
    run_root.mkdir()
    pq.write_table(
        pa.table({"id": ["a"], "shared": pa.array([1], type=narrow_type), "x": [1.0]}),
        run_root / "a.parquet",
    )
    pq.write_table(
        pa.table({"id": ["b"], "shared": pa.array([2], type=wide_type), "y": [2.0]}),
        run_root / "b.parquet",
    )
    real_connect = duckdb.connect
    observed = _ObservedConnection(real_connect())
    monkeypatch.setattr(duckdb, "connect", lambda **kwargs: observed)
    real_read_schema = pq.read_schema
    schema_paths: list[Path] = []

    def read_one_schema(path: Path) -> pa.Schema:
        assert isinstance(path, Path)
        schema_paths.append(path)
        return real_read_schema(path)

    def no_data_read(*args: object, **kwargs: object) -> None:
        pytest.fail("schema inference must not read Parquet cell data")

    monkeypatch.setattr(pq, "read_schema", read_one_schema)
    monkeypatch.setattr(pq, "read_table", no_data_read)
    catalog_path, data_path = tmp_path / "catalog.ducklake", tmp_path / "data"
    result = build_run_catalog(
        run_root, catalog_path, data_path, tables={"profiles": "*.parquet"}
    )
    assert schema_paths == sorted(run_root.glob("*.parquet"))
    assert len(observed.empty_schemas) == 1
    assert observed.empty_schemas[0].field("shared").type == wide_type
    assert result.tables[0].row_count == 2
    # Bypass the patched connection factory for an independent catalog read.
    with real_connect() as con:
        con.execute("LOAD ducklake")
        con.execute(
            f"ATTACH 'ducklake:{catalog_path}' AS cat (DATA_PATH '{data_path}')"
        )
        assert con.execute(
            "SELECT id, shared, x, y FROM cat.profiles ORDER BY id"
        ).fetchall() == [("a", 1, 1.0, None), ("b", 2, None, 2.0)]


@pytest.mark.parametrize(
    ("options", "expected_memory", "expected_threads"),
    [({}, "3.7 GiB", 2), ({"memory_limit": "512MB", "threads": 1}, "488.2 MiB", 1)],
)
def test_catalog_limits_are_applied_before_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: _CatalogOptions,
    expected_memory: str,
    expected_threads: int,
) -> None:
    import duckdb

    real_connect = duckdb.connect
    settings: list[tuple[str, int, bool]] = []

    class ResourceConnection(_ObservedConnection):
        def execute(self, query: str, *args: object) -> duckdb.DuckDBPyConnection:
            if query == "INSTALL ducklake":
                row = self.connection.execute(
                    "SELECT current_setting('memory_limit'), "
                    "current_setting('threads'), "
                    "current_setting('enable_external_file_cache')"
                ).fetchone()
                assert row is not None
                settings.append((row[0], row[1], row[2]))
            return super().execute(query, *args)

    monkeypatch.setattr(
        duckdb, "connect", lambda **kwargs: ResourceConnection(real_connect(**kwargs))
    )
    build_run_catalog(
        tmp_path, tmp_path / "catalog.ducklake", tmp_path / "data", **options
    )
    assert settings == [(expected_memory, expected_threads, False)]


def test_catalog_logs_bounded_registration_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    # Empty shards keep this real DuckLake registration test small.
    for index in range(101):
        pq.write_table(
            pa.table({"id": pa.array([], type=pa.int64())}),
            run_root / f"{index:03}.parquet",
        )
    build_run_catalog(
        run_root,
        tmp_path / "catalog.ducklake",
        tmp_path / "data",
        tables={"profiles": "*.parquet"},
    )
    lines = capsys.readouterr().out.splitlines()
    for event in ("schema_start", "schema_end", "verify_start", "table_end"):
        assert any(
            f"event={event}" in line and "table=profiles" in line for line in lines
        )
    progress = [line for line in lines if "event=register_progress" in line]
    assert len(progress) == 2
    assert "registered=100" in progress[0]
    assert "registered=101" in progress[1]
    schema_progress = [line for line in lines if "event=schema_progress" in line]
    assert len(schema_progress) == 2
    assert "table=profiles" in schema_progress[0]
    assert "scanned=100 files=101" in schema_progress[0]
    assert "scanned=101 files=101" in schema_progress[1]
    assert all("rss_mib=" in line and "peak_rss_mib=" in line for line in lines)


def test_catalog_rejects_incompatible_schemas(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    pq.write_table(pa.table({"value": [1]}), run_root / "a.parquet")
    pq.write_table(pa.table({"value": ["not a number"]}), run_root / "b.parquet")
    with pytest.raises(pa.ArrowTypeError, match="value"):
        build_run_catalog(
            run_root,
            tmp_path / "catalog.ducklake",
            tmp_path / "data",
            tables={"profiles": "*.parquet"},
        )

"""
Tests for the cytotable module.
"""

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from rerx.cytotable import (
    OBJECT_NUMBER_COLUMNS,
    SchemaCheckResult,
    add_cell_ids,
    check_schema_consistency,
    convert_sqlite_to_parquet,
    make_cell_id,
    plate_partitions,
    sha256_file,
    validate_unique_cell_ids,
    write_partitioned_profiles,
)


def _sample_profiles() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Metadata_ImageNumber": [1, 1, 2],
            "Image_Metadata_Experiment": ["HRCE-1", "HRCE-1", "HRCE-1"],
            "Image_Metadata_Plate": ["25", "25", "25"],
            "Image_Metadata_Well": ["A01", "A01", "A02"],
            "Image_Metadata_Site": [1, 1, 1],
            "Cells_Number_Object_Number": [1, 2, 1],
            "Nuclei_Number_Object_Number": [1, 2, 1],
            "Cytoplasm_Number_Object_Number": [1, 2, 1],
            "Cells_AreaShape_Area": [100.0, 120.0, 90.0],
        }
    )


def test_make_cell_id_is_stable_and_unique() -> None:
    profiles = _sample_profiles()
    ids = profiles.apply(make_cell_id, axis=1)
    assert ids.tolist() == [
        "HRCE-1_25_A01_1_c1_n1",
        "HRCE-1_25_A01_1_c2_n2",
        "HRCE-1_25_A02_1_c1_n1",
    ]
    assert ids.is_unique


def test_add_cell_ids_inserts_first_column() -> None:
    profiles = _sample_profiles()
    out = add_cell_ids(profiles)
    assert out.columns[0] == "Metadata_cell_id"
    assert len(out) == len(profiles)
    # Original frame is untouched.
    assert "Metadata_cell_id" not in profiles.columns


def test_add_cell_ids_missing_columns_raises() -> None:
    profiles = _sample_profiles().drop(columns=["Image_Metadata_Site"])
    with pytest.raises(ValueError, match="missing required columns"):
        add_cell_ids(profiles)


def test_validate_unique_cell_ids_passes_for_unique() -> None:
    profiles = add_cell_ids(_sample_profiles())
    validate_unique_cell_ids(profiles)  # must not raise


def test_validate_unique_cell_ids_detects_duplicates() -> None:
    profiles = add_cell_ids(_sample_profiles())
    duped = pd.concat([profiles, profiles.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate Metadata_cell_id"):
        validate_unique_cell_ids(duped)


def test_validate_unique_cell_ids_requires_column() -> None:
    profiles = _sample_profiles()
    with pytest.raises(ValueError, match="Metadata_cell_id"):
        validate_unique_cell_ids(profiles)


def test_write_partitioned_profiles_by_experiment_and_plate(tmp_path: Path) -> None:
    profiles = add_cell_ids(_sample_profiles())
    # Add a second plate so partitioning actually splits into two files.
    other_plate = _sample_profiles().assign(Image_Metadata_Plate="26")
    profiles = pd.concat([profiles, add_cell_ids(other_plate)], ignore_index=True)
    dest_root = tmp_path / "profiles" / "cellprofiler" / "raw"

    written = write_partitioned_profiles(profiles, dest_root)

    assert len(written) == 2
    assert all(p.name == "profiles.parquet" for p in written)
    assert (
        dest_root / "experiment=HRCE-1" / "plate=25" / "profiles.parquet"
    ) in written
    assert (
        dest_root / "experiment=HRCE-1" / "plate=26" / "profiles.parquet"
    ) in written
    for p in written:
        df = pd.read_parquet(p)
        assert "Metadata_cell_id" in df.columns
    # No leftover temp files (atomic rename).
    assert not list(dest_root.rglob("*.tmp"))


def test_write_partitioned_profiles_missing_columns_raises(tmp_path: Path) -> None:
    profiles = _sample_profiles().drop(columns=["Image_Metadata_Plate"])
    with pytest.raises(ValueError, match="missing required columns"):
        write_partitioned_profiles(profiles, tmp_path)


def _write_parquet(path: Path, columns: list[str]) -> None:
    table = pa.table({c: [1] for c in columns})
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def test_check_schema_consistency_all_match(tmp_path: Path) -> None:
    cols = ["Metadata_cell_id", "Cells_AreaShape_Area"]
    p1 = tmp_path / "a.parquet"
    p2 = tmp_path / "b.parquet"
    _write_parquet(p1, cols)
    _write_parquet(p2, cols)

    result = check_schema_consistency([p1, p2])

    assert isinstance(result, SchemaCheckResult)
    assert result.consistent
    assert result.mismatches == {}


def test_check_schema_consistency_detects_mismatch(tmp_path: Path) -> None:
    p1 = tmp_path / "a.parquet"
    p2 = tmp_path / "b.parquet"
    _write_parquet(p1, ["Metadata_cell_id", "Cells_AreaShape_Area"])
    _write_parquet(p2, ["Metadata_cell_id", "Cells_AreaShape_Perimeter"])

    result = check_schema_consistency([p1, p2])

    assert not result.consistent
    assert str(p2) in result.mismatches
    missing, extra = result.mismatches[str(p2)]
    assert missing == ["Cells_AreaShape_Area"]
    assert extra == ["Cells_AreaShape_Perimeter"]


def test_check_schema_consistency_empty_raises() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        check_schema_consistency([])


def test_sha256_file(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_bytes(b"hello world")
    digest = sha256_file(path)
    assert len(digest) == 64
    # Deterministic.
    assert digest == sha256_file(path)


def test_object_number_columns_constant() -> None:
    assert OBJECT_NUMBER_COLUMNS == (
        "Cells_Number_Object_Number",
        "Nuclei_Number_Object_Number",
        "Cytoplasm_Number_Object_Number",
    )


def test_plate_partitions_groups_by_experiment_and_plate() -> None:
    profiles = add_cell_ids(_sample_profiles())
    other_plate = _sample_profiles().assign(Image_Metadata_Plate="26")
    profiles = pd.concat([profiles, add_cell_ids(other_plate)], ignore_index=True)

    groups = plate_partitions(profiles)

    assert [key for key, _ in groups] == [("HRCE-1", "25"), ("HRCE-1", "26")]
    assert sum(len(df) for _, df in groups) == len(profiles)
    for (experiment, plate), df in groups:
        assert (df["Image_Metadata_Experiment"] == experiment).all()
        assert (df["Image_Metadata_Plate"] == plate).all()


def test_plate_partitions_missing_columns_raises() -> None:
    profiles = _sample_profiles().drop(columns=["Image_Metadata_Plate"])
    with pytest.raises(ValueError, match="missing required columns"):
        plate_partitions(profiles)


def test_convert_sqlite_to_parquet_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="missing shard SQLite database"):
        convert_sqlite_to_parquet(
            tmp_path / "does_not_exist.sqlite",
            tmp_path / "out.parquet",
        )

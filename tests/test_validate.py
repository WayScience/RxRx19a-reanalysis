"""
Tests for the validate module.
"""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from rerx.crops import encode_jpeg
from rerx.cytotable import add_cell_ids
from rerx.validate import (
    CropDecodeResult,
    ImageQualityResult,
    PilotValidationReport,
    SqliteIntegrityResult,
    check_control_separation,
    check_crops_decode,
    check_image_quality,
    check_sqlite_integrity,
    validate_pilot_run,
)


def _make_clean_sqlite(path: Path) -> None:
    con = sqlite3.connect(str(path))
    cur = con.cursor()
    cur.execute(
        "CREATE TABLE Per_Nuclei (ImageNumber INT, Nuclei_Number_Object_Number INT)"
    )
    cur.execute(
        "CREATE TABLE Per_Cells (ImageNumber INT, Cells_Number_Object_Number INT)"
    )
    cur.execute(
        "CREATE TABLE Per_Cytoplasm (ImageNumber INT, "
        "Cytoplasm_Number_Object_Number INT, Cytoplasm_Parent_Cells INT, "
        "Cytoplasm_Parent_Nuclei INT)"
    )
    cur.executemany("INSERT INTO Per_Nuclei VALUES (?, ?)", [(1, 1), (1, 2)])
    cur.executemany("INSERT INTO Per_Cells VALUES (?, ?)", [(1, 1), (1, 2)])
    cur.executemany(
        "INSERT INTO Per_Cytoplasm VALUES (?, ?, ?, ?)",
        [(1, 1, 1, 1), (1, 2, 2, 2)],
    )
    con.commit()
    con.close()


def _make_broken_sqlite(path: Path) -> None:
    con = sqlite3.connect(str(path))
    cur = con.cursor()
    cur.execute(
        "CREATE TABLE Per_Nuclei (ImageNumber INT, Nuclei_Number_Object_Number INT)"
    )
    cur.execute(
        "CREATE TABLE Per_Cells (ImageNumber INT, Cells_Number_Object_Number INT)"
    )
    cur.execute(
        "CREATE TABLE Per_Cytoplasm (ImageNumber INT, "
        "Cytoplasm_Number_Object_Number INT, Cytoplasm_Parent_Cells INT, "
        "Cytoplasm_Parent_Nuclei INT)"
    )
    cur.executemany("INSERT INTO Per_Nuclei VALUES (?, ?)", [(1, 1)])
    cur.executemany("INSERT INTO Per_Cells VALUES (?, ?)", [(1, 1)])
    # Cytoplasm object 2 references a Cells object (99) that does not exist.
    cur.executemany(
        "INSERT INTO Per_Cytoplasm VALUES (?, ?, ?, ?)",
        [(1, 1, 1, 1), (1, 2, 99, 1)],
    )
    con.commit()
    con.close()


def test_check_sqlite_integrity_clean_db_passes(tmp_path: Path) -> None:
    db_path = tmp_path / "cells.sqlite"
    _make_clean_sqlite(db_path)

    result = check_sqlite_integrity(db_path)

    assert isinstance(result, SqliteIntegrityResult)
    assert result.integrity_ok
    assert result.integrity_messages == ["ok"]
    assert result.orphan_counts["Per_Cytoplasm.Cytoplasm_Parent_Cells"] == 0
    assert result.orphan_counts["Per_Cytoplasm.Cytoplasm_Parent_Nuclei"] == 0
    assert result.passed


def test_check_sqlite_integrity_detects_orphans(tmp_path: Path) -> None:
    db_path = tmp_path / "cells.sqlite"
    _make_broken_sqlite(db_path)

    result = check_sqlite_integrity(db_path)

    assert result.integrity_ok  # structurally valid SQLite, just orphaned rows
    assert result.orphan_counts["Per_Cytoplasm.Cytoplasm_Parent_Cells"] == 1
    assert not result.passed


def test_check_sqlite_integrity_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="missing shard SQLite database"):
        check_sqlite_integrity(tmp_path / "does_not_exist.sqlite")


def _sample_crops(n: int = 2, corrupt_last: bool = False) -> pd.DataFrame:
    import numpy as np

    rows = []
    for i in range(n):
        arr = np.full((8, 8), 100 + i, dtype=np.uint8)
        row = {"Metadata_cell_id": f"cell-{i}"}
        for channel in (1, 2, 3, 4, 5):
            row[f"crop_w{channel}_jpeg"] = encode_jpeg(arr)
        rows.append(row)
    if corrupt_last and rows:
        rows[-1]["crop_w1_jpeg"] = b"not-a-real-jpeg"
    return pd.DataFrame(rows)


def test_check_crops_decode_all_valid() -> None:
    crops = _sample_crops(3)
    result = check_crops_decode(crops)
    assert isinstance(result, CropDecodeResult)
    assert result.checked == 3
    assert result.decode_failures == []
    assert result.passed


def test_check_crops_decode_detects_corrupt_jpeg() -> None:
    crops = _sample_crops(3, corrupt_last=True)
    result = check_crops_decode(crops)
    assert result.checked == 3
    assert result.decode_failures == ["cell-2"]
    assert not result.passed


def test_check_crops_decode_missing_columns_raises() -> None:
    crops = pd.DataFrame({"Metadata_cell_id": ["a"]})
    with pytest.raises(ValueError, match="missing required columns"):
        check_crops_decode(crops)


def test_validate_pilot_run_sqlite_only(tmp_path: Path) -> None:
    db_path = tmp_path / "cells.sqlite"
    _make_clean_sqlite(db_path)

    report = validate_pilot_run(sqlite_paths=[db_path])

    assert isinstance(report, PilotValidationReport)
    assert report.passed
    assert len(report.sqlite_results) == 1
    assert report.schema_check is None
    assert report.unique_cell_ids_ok is None
    summary = report.summary()
    assert summary["passed"] is True
    assert summary["sqlite_shards_checked"] == 1
    assert summary["sqlite_shards_failed"] == 0


def test_validate_pilot_run_detects_sqlite_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "cells.sqlite"
    _make_broken_sqlite(db_path)

    report = validate_pilot_run(sqlite_paths=[db_path])

    assert not report.passed
    assert report.summary()["sqlite_shards_failed"] == 1


def _base_profiles() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Metadata_ImageNumber": [1, 2],
            "Image_Metadata_Experiment": ["HRCE-1", "HRCE-1"],
            "Image_Metadata_Plate": ["25", "25"],
            "Image_Metadata_Well": ["A01", "A02"],
            "Image_Metadata_Site": [1, 1],
            "Cells_Number_Object_Number": [1, 1],
            "Nuclei_Number_Object_Number": [1, 1],
            "Cytoplasm_Number_Object_Number": [1, 1],
        }
    )


def test_validate_pilot_run_full_pipeline(tmp_path: Path) -> None:
    db_path = tmp_path / "cells.sqlite"
    _make_clean_sqlite(db_path)

    profiles = add_cell_ids(_base_profiles())
    crops = pd.DataFrame({"Metadata_cell_id": profiles["Metadata_cell_id"]})
    for channel in (1, 2, 3, 4, 5):
        import numpy as np

        crops[f"crop_w{channel}_jpeg"] = [
            encode_jpeg(np.full((8, 8), 50, dtype=np.uint8)) for _ in range(len(crops))
        ]

    report = validate_pilot_run(
        sqlite_paths=[db_path],
        profiles=profiles,
        crops=crops,
    )

    assert report.passed
    assert report.unique_cell_ids_ok is True
    assert report.crops_join_ok is True
    assert report.crop_decode is not None
    assert report.crop_decode.passed


def test_validate_pilot_run_detects_duplicate_cell_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "cells.sqlite"
    _make_clean_sqlite(db_path)

    profiles = add_cell_ids(_base_profiles())
    duped = pd.concat([profiles, profiles.iloc[[0]]], ignore_index=True)

    report = validate_pilot_run(sqlite_paths=[db_path], profiles=duped)

    assert not report.passed
    assert report.unique_cell_ids_ok is False


def _control_profiles(separated: bool) -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(0)
    n = 40
    mock_shift = 0.0
    disease_shift = 6.0 if separated else 0.1
    mock = pd.DataFrame(
        {
            "Metadata_rxrx_control_type": ["mock"] * n,
            "Cells_AreaShape_Area": rng.normal(mock_shift, 1.0, n),
            "Nuclei_Intensity_MeanIntensity_DNA": rng.normal(mock_shift, 1.0, n),
        }
    )
    disease = pd.DataFrame(
        {
            "Metadata_rxrx_control_type": ["active_untreated"] * n,
            "Cells_AreaShape_Area": rng.normal(disease_shift, 1.0, n),
            "Nuclei_Intensity_MeanIntensity_DNA": rng.normal(disease_shift, 1.0, n),
        }
    )
    return pd.concat([mock, disease], ignore_index=True)


def test_check_control_separation_passes_when_controls_differ() -> None:
    profiles = _control_profiles(separated=True)
    result = check_control_separation(profiles)
    assert result.passed
    assert result.checked_features > 0
    assert result.separated_features > 0


def test_check_control_separation_fails_when_controls_overlap() -> None:
    profiles = _control_profiles(separated=False)
    result = check_control_separation(profiles)
    assert not result.passed


def test_check_control_separation_missing_control_column_is_skipped() -> None:
    profiles = pd.DataFrame({"Cells_AreaShape_Area": [1.0, 2.0]})
    result = check_control_separation(profiles)
    assert result.skipped
    assert result.checked_features == 0


def test_check_control_separation_missing_one_side_is_skipped() -> None:
    profiles = _control_profiles(separated=True)
    mock_only = profiles[profiles["Metadata_rxrx_control_type"] == "mock"]
    result = check_control_separation(mock_only)
    assert result.skipped


def _quality_profiles(dim_well: str | None) -> pd.DataFrame:
    rows = []
    normal_wells = {"AA02": [0.46, 0.57], "AA09": [0.44, 0.40], "AA16": [0.48, 0.50]}
    for well, values in normal_wells.items():
        for site, value in enumerate(values, start=1):
            rows.append(
                {
                    "Image_Metadata_Well": well,
                    "Image_Metadata_Site": site,
                    "Image_ImageQuality_MaxIntensity_DNA": value,
                }
            )
    if dim_well is not None:
        for site in (1, 2):
            rows.append(
                {
                    "Image_Metadata_Well": dim_well,
                    "Image_Metadata_Site": site,
                    "Image_ImageQuality_MaxIntensity_DNA": 0.20,
                }
            )
    return pd.DataFrame(rows)


def test_check_image_quality_flags_a_dim_well() -> None:
    profiles = _quality_profiles(dim_well="AA08")
    result = check_image_quality(profiles)

    assert isinstance(result, ImageQualityResult)
    assert result.flagged_wells == ["AA08"]
    assert not result.passed


def test_check_image_quality_passes_when_no_well_is_dim() -> None:
    profiles = _quality_profiles(dim_well=None)
    result = check_image_quality(profiles)

    assert result.flagged_wells == []
    assert result.passed


def test_check_image_quality_skipped_when_column_missing() -> None:
    profiles = pd.DataFrame({"Image_Metadata_Well": ["AA02"]})
    result = check_image_quality(profiles)

    assert result.skipped
    assert result.passed

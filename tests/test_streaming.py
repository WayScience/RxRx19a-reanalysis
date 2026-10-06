"""
Tests for streaming (per-plate / per-shard) driver helpers.

The driver commands must not hold whole-dataset tables in memory at
full scale (~60M cells). These tests cover the small helpers added to
src/rerx/streaming.py that let finalize/fuse/validation run one plate
or one shard at a time.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from rerx.streaming import (
    iter_partition_frames,
    iter_shard_partitions,
    partition_parquet_paths,
    read_partitioned_parquets,
    shard_parquet_pairs,
    shards_by_partition,
)


def _shard_frame(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """(cell_id, experiment, plate) rows with one feature column."""
    return pd.DataFrame(
        {
            "Metadata_cell_id": [r[0] for r in rows],
            "Image_Metadata_Experiment": [r[1] for r in rows],
            "Image_Metadata_Plate": [r[2] for r in rows],
            "Cells_AreaShape_Area": [1.0] * len(rows),
        }
    )


def _write_shard(dest: Path, frame: pd.DataFrame) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(dest, index=False)
    return dest


def _profiles_frame(cell_ids: list[str], experiment: str, plate: str) -> pd.DataFrame:
    n = len(cell_ids)
    return pd.DataFrame(
        {
            "Metadata_cell_id": cell_ids,
            "Image_Metadata_Experiment": [experiment] * n,
            "Image_Metadata_Plate": [plate] * n,
        }
    )


def _write_profiles(dest: Path, frame: pd.DataFrame) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(dest / "profiles.parquet", index=False)


def test_partition_parquet_paths_groups_by_experiment_plate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "profiles"
    _write_profiles(
        root / "experiment=HRCE-1" / "plate=25",
        _profiles_frame(["a"], "HRCE-1", "25"),
    )
    _write_profiles(
        root / "experiment=HRCE-1" / "plate=26",
        _profiles_frame(["b"], "HRCE-1", "26"),
    )
    _write_profiles(
        root / "experiment=HRCE-2" / "plate=1",
        _profiles_frame(["c"], "HRCE-2", "1"),
    )

    groups = partition_parquet_paths(root)
    assert [k for k, _ in groups] == [
        ("HRCE-1", "25"),
        ("HRCE-1", "26"),
        ("HRCE-2", "1"),
    ]
    # Each group holds the partition's parquet file.
    for _, paths in groups:
        assert len(paths) == 1
        assert paths[0].name == "profiles.parquet"


def test_partition_parquet_paths_empty_root_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no parquet files"):
        partition_parquet_paths(tmp_path / "missing")


def test_read_partitioned_parquets_reads_one_group(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    _write_profiles(
        root / "experiment=HRCE-1" / "plate=25",
        _profiles_frame(["a", "b"], "HRCE-1", "25"),
    )
    _write_profiles(
        root / "experiment=HRCE-1" / "plate=26",
        _profiles_frame(["c"], "HRCE-1", "26"),
    )

    groups = partition_parquet_paths(root)
    frame = read_partitioned_parquets(groups[0][1])
    # Only the first partition's rows are in memory.
    assert len(frame) == 2
    assert set(frame["Metadata_cell_id"]) == {"a", "b"}


def test_shard_parquet_pairs_matches_by_stem(tmp_path: Path) -> None:
    crops = tmp_path / "crops" / "cells"
    profiles = tmp_path / "cytotable"
    crops.mkdir(parents=True, exist_ok=True)
    profiles.mkdir(parents=True, exist_ok=True)
    for sid in ("shard-0000", "shard-0001"):
        (crops / f"{sid}.parquet").touch()
        (profiles / f"{sid}.parquet").touch()
    # An unmatched crop shard must be reported, not silently dropped.
    (crops / "shard-0002.parquet").touch()

    pairs, unmatched = shard_parquet_pairs(
        sorted(crops.glob("*.parquet")), sorted(profiles.glob("*.parquet"))
    )
    assert [(c.name, p.name) for c, p in pairs] == [
        (f"{s}.parquet", f"{s}.parquet") for s in ("shard-0000", "shard-0001")
    ]
    assert [p.name for p in unmatched] == ["shard-0002.parquet"]


def test_shards_by_partition_groups_shard_files_by_plate(tmp_path: Path) -> None:
    # Two shards, both rows from plate 25 -> one partition group.
    _write_shard(
        tmp_path / "cytotable" / "shard-0000.parquet",
        _shard_frame([("a", "HRCE-1", "25"), ("b", "HRCE-1", "25")]),
    )
    _write_shard(
        tmp_path / "cytotable" / "shard-0001.parquet",
        _shard_frame([("c", "HRCE-1", "25")]),
    )

    groups = shards_by_partition(sorted((tmp_path / "cytotable").glob("*.parquet")))
    assert [k for k, _ in groups] == [("HRCE-1", "25")]
    assert [p.name for p in groups[0][1]] == [
        "shard-0000.parquet",
        "shard-0001.parquet",
    ]


def test_shards_by_partition_empty_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no parquet files"):
        shards_by_partition([])


def test_iter_shard_partitions_yields_one_plate_at_a_time(
    tmp_path: Path,
) -> None:
    # Shards span two plates; the iterator must yield each plate's
    # rows once, streaming (never both plates' data in memory).
    _write_shard(
        tmp_path / "cytotable" / "shard-0000.parquet",
        _shard_frame([("a", "HRCE-1", "25"), ("b", "HRCE-1", "25")]),
    )
    _write_shard(
        tmp_path / "cytotable" / "shard-0001.parquet",
        _shard_frame([("c", "HRCE-1", "26"), ("d", "HRCE-1", "26")]),
    )

    seen: list[tuple[tuple[str, str], int]] = []
    for key, frame in iter_shard_partitions(
        sorted((tmp_path / "cytotable").glob("*.parquet"))
    ):
        seen.append((key, len(frame)))
    assert seen == [(("HRCE-1", "25"), 2), (("HRCE-1", "26"), 2)]


def test_iter_shard_partitions_raises_on_empty(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no parquet files"):
        list(iter_shard_partitions([]))


def test_iter_partition_frames_yields_plate_frames_from_partitions(
    tmp_path: Path,
) -> None:
    _write_profiles(
        tmp_path / "experiment=HRCE-1" / "plate=25",
        _profiles_frame(["a", "b"], "HRCE-1", "25"),
    )
    _write_profiles(
        tmp_path / "experiment=HRCE-1" / "plate=26",
        _profiles_frame(["c"], "HRCE-1", "26"),
    )

    seen: list[tuple[tuple[str, str], int]] = []
    for key, frame in iter_partition_frames(tmp_path):
        seen.append((key, len(frame)))
    assert seen == [(("HRCE-1", "25"), 2), (("HRCE-1", "26"), 1)]


def test_iter_partition_frames_raises_on_empty(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no parquet files"):
        list(iter_partition_frames(tmp_path / "missing"))

"""
Streaming (constant-memory) readers for partitioned run outputs.

The full RxRx19a dataset is ~60M cells; any driver step that
concatenates every shard's Parquet into one DataFrame before
processing would need a large fraction of a terabyte of RAM. The
helpers here let driver commands iterate one plate (or one shard) at
a time instead, reading only that group's files into memory.

Memory note: the steps that genuinely need whole-dataset tables
(the embeddings ZIP conversion, the fused write with its sidecar)
stay whole-dataset by design; their Slurm memory requirements are
documented in the README instead of being streamed here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pandas as pd


def partition_parquet_paths(
    root: Path,
    glob_pattern: str = "*/*/*.parquet",
) -> list[tuple[tuple[str, str], list[Path]]]:
    """
    Group partitioned Parquet files by their (experiment, plate) directory.

    Expects Hive-style partitions (``experiment=<e>/plate=<p>/``), the
    layout written by :func:`rerx.cytotable.write_partitioned_profiles`
    and the finalize stages.

    Parameters
    ----------
    root : Path
        Partition root (for example ``profiles/cellprofiler/raw``).
    glob_pattern : str
        Glob for files under ``root``; defaults to one level of
        experiment/plate directories.

    Returns
    -------
    list[tuple[tuple[str, str], list[Path]]]
        ``((experiment, plate), paths)`` groups sorted by partition.

    Raises
    ------
    FileNotFoundError
        If no files match (an empty tree is a caller error: reading a
        missing stage should fail loudly, not silently yield nothing).
    """
    root = Path(root)
    paths = sorted(root.glob(glob_pattern))
    if not paths:
        raise FileNotFoundError(f"no parquet files under {root}")

    grouped: dict[tuple[str, str], list[Path]] = {}
    for path in paths:
        parts = path.parts
        # .../<root>/experiment=<e>/plate=<p>/<file>
        exp = parts[-3].removeprefix("experiment=")
        plate = parts[-2].removeprefix("plate=")
        grouped.setdefault((exp, plate), []).append(path)
    return sorted(grouped.items())


def read_partitioned_parquets(paths: list[Path]) -> pd.DataFrame:
    """
    Read and concatenate one group's Parquet files (one plate's rows).

    Parameters
    ----------
    paths : list[Path]
        The files of a single (experiment, plate) partition, as
        returned by :func:`partition_parquet_paths`.

    Returns
    -------
    pd.DataFrame
        The partition's combined rows (only this plate in memory).
    """
    frames = [pd.read_parquet(p) for p in paths]
    if len(frames) == 1:
        return frames[0]
    return pd.concat(frames, ignore_index=True)


def shard_parquet_pairs(
    crop_paths: list[Path],
    profile_paths: list[Path],
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """
    Match crop shard files to profile shard files by stem (shard id).

    Lets crop-join validation run one shard at a time instead of
    loading every crop and every profile into memory together.

    Parameters
    ----------
    crop_paths : list[Path]
        Crop shard Parquet files (``crops/cells/<shard_id>.parquet``).
    profile_paths : list[Path]
        Profile shard Parquet files (``cytotable/<shard_id>.parquet``).

    Returns
    -------
    tuple[list[tuple[Path, Path]], list[Path]]
        ``(pairs, unmatched_crops)``: matched
        ``(crop_path, profile_path)`` pairs sorted by stem, plus crop
        files with no matching profile shard (a caller error to raise
        on, not silently skip).
    """
    profiles_by_stem = {p.stem: p for p in profile_paths}
    pairs: list[tuple[Path, Path]] = []
    unmatched: list[Path] = []
    for crop in crop_paths:
        profile = profiles_by_stem.get(crop.stem)
        if profile is None:
            unmatched.append(crop)
        else:
            pairs.append((crop, profile))
    return pairs, unmatched


def shards_by_partition(
    shard_paths: list[Path],
    experiment_column: str = "Image_Metadata_Experiment",
    plate_column: str = "Image_Metadata_Plate",
) -> list[tuple[tuple[str, str], list[Path]]]:
    """
    Group shard Parquet files by the (experiment, plate) they contain.

    Reads only the two metadata columns (never feature data) to build
    the grouping, so this is safe at any scale.

    Parameters
    ----------
    shard_paths : list[Path]
        Shard Parquet files (one per CellProfiler shard).
    experiment_column : str
        Column carrying the experiment name.
    plate_column : str
        Column carrying the plate name.

    Returns
    -------
    list[tuple[tuple[str, str], list[Path]]]
        ``((experiment, plate), shard_paths)`` groups sorted by
        partition.

    Raises
    ------
    FileNotFoundError
        If ``shard_paths`` is empty.
    """
    if not shard_paths:
        raise FileNotFoundError("no parquet files supplied")

    grouped: dict[tuple[str, str], list[Path]] = {}
    for path in shard_paths:
        frame = pd.read_parquet(path, columns=[experiment_column, plate_column])
        for exp, plate in frame.drop_duplicates().itertuples(index=False):
            grouped.setdefault((str(exp), str(plate)), []).append(path)
    return sorted(grouped.items())


def iter_shard_partitions(
    shard_paths: list[Path],
    experiment_column: str = "Image_Metadata_Experiment",
    plate_column: str = "Image_Metadata_Plate",
) -> Iterator[tuple[tuple[str, str], pd.DataFrame]]:
    """
    Yield ``(experiment, plate) -> rows`` one plate at a time from shards.

    The driver-level streaming primitive for finalize: each iteration
    reads that plate's shard files (and filters rows to the partition
    when a shard spans more than one), so only one plate's profiles
    are ever in memory. Use :func:`shards_by_partition` first if the
    per-plate file grouping is needed separately.

    Parameters
    ----------
    shard_paths : list[Path]
        Shard Parquet files to stream over.
    experiment_column, plate_column : str
        Metadata columns naming the partition.

    Yields
    ------
    tuple[tuple[str, str], pd.DataFrame]
        ``((experiment, plate), plate_rows)`` sorted by partition.

    Raises
    ------
    FileNotFoundError
        If ``shard_paths`` is empty.
    """
    for (experiment, plate), paths in shards_by_partition(
        shard_paths, experiment_column, plate_column
    ):
        frames = [pd.read_parquet(p) for p in paths]
        rows = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
        rows = rows[
            (rows[experiment_column].astype(str) == experiment)
            & (rows[plate_column].astype(str) == plate)
        ]
        yield (experiment, plate), rows


def iter_partition_frames(
    root: Path,
    glob_pattern: str = "*/*/*.parquet",
) -> Iterator[tuple[tuple[str, str], pd.DataFrame]]:
    """
    Yield one plate's rows at a time from a partitioned Parquet tree.

    The streaming reader over the durable
    ``experiment=<e>/plate=<p>/`` layout written by finalize and
    fusion: each iteration reads only that partition's files.

    Parameters
    ----------
    root : Path
        Partition tree root.
    glob_pattern : str
        File glob under ``root``.

    Yields
    ------
    tuple[tuple[str, str], pd.DataFrame]
        ``((experiment, plate), frame)`` sorted by partition.

    Raises
    ------
    FileNotFoundError
        If no files match.
    """
    for (experiment, plate), paths in partition_parquet_paths(root, glob_pattern):
        yield (experiment, plate), read_partitioned_parquets(paths)

"""
Fuse CellProfiler and MorphEm feature matrices on the cell key.

Both finalized spaces carry ``Metadata_cell_id`` (real pilot data: 100%
overlap — every MorphEm cell has a CP counterpart). The fused frame keeps
CP metadata columns, CP feature columns, and appends the ``Morphem_``
features. Fusion is an inner join: cells present in only one space are
dropped with a warning, since downstream analysis (annotation, feature
selection, buscar) requires both views of the same cell.

The join happens at this seam deliberately (project precedent:
rename-at-merge-seam in FINALIZE) — the two spaces disagree on some
metadata spellings (``Metadata_site_id`` vs capitalized
``Metadata_Experiment/Plate/Well/Site``), but they agree on
``Metadata_cell_id``, which is the stable cell-level identity.
"""

import warnings
from pathlib import Path
from typing import Any

import pandas as pd

_KEY = "Metadata_cell_id"


def fuse_features(cp: pd.DataFrame, morphem: pd.DataFrame) -> pd.DataFrame:
    """
    Inner-join CP and MorphEm profiles on ``Metadata_cell_id``.

    Parameters
    ----------
    cp : pd.DataFrame
        Finalized CellProfiler profiles (metadata + feature columns).
    morphem : pd.DataFrame
        Finalized MorphEm profiles (metadata + ``Morphem_`` columns).

    Returns
    -------
    pd.DataFrame
        CP metadata and features with the MorphEm feature block appended,
        one row per cell present in both spaces.

    Raises
    ------
    ValueError
        If the two frames share no cells, or if any MorphEm column name
        collides with a CP column (other than ``Metadata_cell_id``).
    """
    for name, df in (("cp", cp), ("morphem", morphem)):
        if _KEY not in df.columns:
            raise ValueError(f"{name} frame is missing the join key {_KEY}")

    shared = set(cp[_KEY]) & set(morphem[_KEY])
    if not shared:
        raise ValueError(
            f"no shared cells between CP ({len(cp)}) and MorphEm "
            f"({len(morphem)}) on {_KEY}"
        )
    if len(shared) < len(cp) or len(shared) < len(morphem):
        warnings.warn(
            f"fuse_features: inner join kept {len(shared)} of "
            f"{len(cp)} CP and {len(morphem)} MorphEm cells "
            f"(cells dropped on one or both sides)",
            stacklevel=2,
        )

    cp_cols = set(cp.columns)
    # CP metadata is canonical; drop MorphEm's duplicate site-level
    # metadata before joining (the two spaces spell some of these
    # differently, e.g. Metadata_site_id vs capitalized CP columns).
    # A colliding feature column is still an error -- it would silently
    # shadow a CP feature.
    drop_cols = [c for c in morphem.columns if c != _KEY and c.startswith("Metadata_")]
    collisions = [c for c in morphem.columns if c != _KEY and c in cp_cols]
    if collisions and not set(collisions).issubset(set(drop_cols)):
        raise ValueError(f"MorphEm columns collide with CP columns: {collisions[:5]}")

    morphem_block = morphem[
        [c for c in morphem.columns if c == _KEY or c.startswith("Morphem_")]
    ]
    fused = cp.merge(morphem_block, on=_KEY, how="inner", validate="one_to_one")
    return fused.reset_index(drop=True)


def pair_fused_partitions(
    cp_paths: list[Path],
    morphem_paths: list[Path],
) -> list[tuple[Path, Path]]:
    """
    Match finalized CellProfiler and MorphEm files by experiment/plate.

    Both spaces write one ``profiles.parquet`` per
    ``experiment=<e>/plate=<p>`` partition; pairing by that relative
    partition path (instead of sorted file order) keeps every plate's
    CP table fused with its own MorphEm table. Unmatched partitions on
    either side are skipped, matching ``fuse_features``' inner-join
    behavior.

    Parameters
    ----------
    cp_paths : list[Path]
        Finalized CellProfiler partition files.
    morphem_paths : list[Path]
        Finalized MorphEm partition files.

    Returns
    -------
    list[tuple[Path, Path]]
        ``(cp_path, morphem_path)`` pairs sharing a partition, in
        ``cp_paths`` order.
    """

    def partition_key(path: Path) -> str:
        parts = Path(path).parts
        # .../<profiler>/feature_selected/experiment=<e>/plate=<p>/profiles.parquet
        return "/".join(parts[-3:-1])

    morphem_by_key = {partition_key(p): p for p in morphem_paths}
    return [
        (cp_path, morphem_by_key[key])
        for cp_path in cp_paths
        if (key := partition_key(cp_path)) in morphem_by_key
    ]


# Fixed label for the fused table: the common, simple fusion approach
# (concatenate the two feature blocks per cell). Written verbatim into
# the fusion.json sidecar so the output answers "what kind of fused".
FUSION_KIND = "early-fusion:feature-concatenation"

# Feature-column prefixes of each source space, in fuse order.
_CP_FEATURE_PREFIXES = ("Cells_", "Cytoplasm_", "Nuclei_")
_MORPHEM_PREFIX = "Morphem_"

# Fixed array layout: every embedding column is one fixed-width typed
# scalar column (float32/float64), never a variable-length list column
# -- matching the Morphem raw space (5 x 384 Morphem_c*_d* floats) and
# the Recursion site embeddings (1024 fixed feature_N doubles).
FUSED_LAYOUT = "fixed-width-scalar-columns"


def fusion_metadata(
    fused: pd.DataFrame,
    *,
    cp_source: str = "profiles/cellprofiler/feature_selected",
    morphem_source: str = "profiles/morphem/feature_selected",
    cp_rows: int | None = None,
    morphem_rows: int | None = None,
) -> dict[str, Any]:
    """
    Build the "what kind of fused" label sidecar for a fused table.

    Parameters
    ----------
    fused : pd.DataFrame
        A fused frame from :func:`fuse_features`.
    cp_source : str
        Label for the CellProfiler input space.
    morphem_source : str
        Label for the MorphEm input space.
    cp_rows : int | None
        Row count of the CP frame *before* the inner join, if known.
        Used to report how many CP-only cells the join dropped.
    morphem_rows : int | None
        Row count of the MorphEm frame before the inner join, if known.

    Returns
    -------
    dict[str, object]
        JSON-serializable metadata describing the fusion: kind, join
        key, sources, row count, per-source feature counts, and (when
        ``cp_rows``/``morphem_rows`` are given) non-overlapping rows
        dropped by the inner join on each side -- the durable record
        that fusion drops non-overlapping cells, beyond the transient
        ``fuse_features`` warning.
    """
    cp_features = [c for c in fused.columns if c.startswith(_CP_FEATURE_PREFIXES)]
    morphem_features = [c for c in fused.columns if c.startswith(_MORPHEM_PREFIX)]
    return {
        "kind": FUSION_KIND,
        "layout": FUSED_LAYOUT,
        "join_key": _KEY,
        "sources": {
            "cellprofiler": cp_source,
            "morphem": morphem_source,
        },
        "rows": len(fused),
        "cellprofiler_feature_cols": len(cp_features),
        "morphem_feature_cols": len(morphem_features),
        "cellprofiler_input_rows": cp_rows,
        "morphem_input_rows": morphem_rows,
        "cellprofiler_rows_dropped": (
            cp_rows - len(fused) if cp_rows is not None else None
        ),
        "morphem_rows_dropped": (
            morphem_rows - len(fused) if morphem_rows is not None else None
        ),
    }


def write_fused_profiles(
    fused: pd.DataFrame,
    run_root: Path,
    *,
    cp_rows: int | None = None,
    morphem_rows: int | None = None,
    write_sidecar: bool = True,
) -> list[Path]:
    """
    Write the fused table, partitioned by experiment and plate, plus
    (by default) the fusion.json label sidecar at
    ``profiles/fused/fusion.json``.

    Parameters
    ----------
    fused : pd.DataFrame
        A fused frame from :func:`fuse_features`.
    run_root : Path
        Run root directory (the parquet files land under
        ``<run_root>/profiles/fused/feature_selected/``).
    cp_rows : int | None
        Row count of the CP frame before the inner join, if known
        (recorded in the sidecar's drop-count fields).
    morphem_rows : int | None
        Row count of the MorphEm frame before the inner join, if known.
    write_sidecar : bool
        Whether to write ``fusion.json`` from this call's ``fused``
        frame alone. Set ``False`` when fusing multiple experiment/plate
        pairs one at a time (plan.md's per-plate batching): writing the
        sidecar after every pair overwrites it with that pair's counts
        instead of the run's totals -- call :func:`write_fusion_sidecar`
        once after accumulating totals across every pair instead.

    Returns
    -------
    list[Path]
        Parquet paths written, one per experiment/plate partition.

    Raises
    ------
    ValueError
        If partition columns are missing from the fused frame.
    """
    from pathlib import Path

    from rerx.cytotable import plate_partitions

    required = ("Metadata_Experiment", "Metadata_Plate")
    missing = [c for c in required if c not in fused.columns]
    if missing:
        raise ValueError(f"fused profiles missing required columns: {missing}")

    run_root = Path(run_root)
    dest_root = run_root / "profiles" / "fused" / "feature_selected"
    written: list[Path] = []
    for (experiment, plate), group in plate_partitions(
        fused.rename(
            columns={
                "Metadata_Experiment": "Image_Metadata_Experiment",
                "Metadata_Plate": "Image_Metadata_Plate",
            }
        )
    ):
        part_dir = dest_root / f"experiment={experiment}" / f"plate={plate}"
        part_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = part_dir / "profiles.parquet.tmp"
        final_path = part_dir / "profiles.parquet"
        group.to_parquet(tmp_path, index=False)
        tmp_path.replace(final_path)
        written.append(final_path)

    if write_sidecar:
        write_fusion_sidecar(
            run_root,
            fusion_metadata(fused, cp_rows=cp_rows, morphem_rows=morphem_rows),
        )
    return written


def write_fusion_sidecar(run_root: Path, metadata: dict[str, Any]) -> Path:
    """
    Write ``profiles/fused/fusion.json`` from an already-built metadata dict.

    Separated from :func:`write_fused_profiles` so callers fusing more
    than one experiment/plate pair (plan.md's per-plate batching) can
    accumulate ``rows``/``cellprofiler_input_rows``/``morphem_input_rows``
    totals across every pair and write the sidecar once, instead of it
    being overwritten by each pair's own counts.

    Parameters
    ----------
    run_root : Path
        Run root directory; the sidecar lands at
        ``<run_root>/profiles/fused/fusion.json``.
    metadata : dict[str, object]
        A dict shaped like :func:`fusion_metadata`'s output (typically
        that function's output with ``rows``/``cellprofiler_input_rows``/
        ``morphem_input_rows``/drop fields replaced with run totals).

    Returns
    -------
    Path
        The sidecar path written.
    """
    import json
    from pathlib import Path

    run_root = Path(run_root)
    sidecar = run_root / "profiles" / "fused" / "fusion.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return sidecar

#!/usr/bin/env python
"""Alpine pilot task driver.

One CLI entry point per pipeline stage, wrapping the tested rerx library
functions (the proven sequence lives in tests/test_pilot_e2e.py). The
Nextflow workflow and the launcher call these subcommands so the
orchestration stays thin and the logic stays in the library.

Environment (set by scripts/alpine_launch.sh / nextflow.config):
    RERX_REPO       repo checkout (src/ + pipelines/ inside)
    RERX_RUN_DIR    durable run directory (/pl/active/koala/ReRx/runs/<id>)
    RERX_SCRATCH    scratch root (/scratch/alpine/<user>/rerx)
    RERX_SOURCE     staged source images root
    RERX_SIF        apptainer image for CellProfiler
    RERX_RUN_ID     run id (rxrx19a-pilot-...-g<sha>)

Subcommands:
    prepare             download metadata, select pilot wells, plan shards
    download            fetch the pilot's PNGs into RERX_SOURCE
    cellprofiler <sid>  stage + run CellProfiler for one shard
    cytotable <sid>     SQLite -> joined single-cell Parquet for one shard
    crops <sid>         per-cell JPEG crops for one shard
    morphem <sid>       MorphEm-embed one crop shard (inside morphem.sif)
    finalize            merge, annotate/normalize/select, validate, catalog
    recursion-buscar    buscar-score Recursion's published site embeddings
    projection          Recursion-style on/off-perturbation scores per plate
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    import pandas as pd

    from rerx.run_summary import PlateSummary

RUN_DIR = Path(os.environ["RERX_RUN_DIR"])
SCRATCH = Path(os.environ["RERX_SCRATCH"])
SOURCE = Path(os.environ["RERX_SOURCE"])
SIF = Path(os.environ["RERX_SIF"])
MORPHEM_SIF = Path(os.environ.get("RERX_MORPHEM_SIF", str(SIF.parent / "morphem.sif")))
RUN_ID = os.environ["RERX_RUN_ID"]
REPO = Path(os.environ["RERX_REPO"])
SHARD_SIZE = int(os.environ.get("RERX_SHARD_SIZE", "24"))

sys.path.insert(0, str(REPO / "src"))


def _log(msg: str) -> None:
    print(f"[rerx {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_selection() -> list[dict]:
    with (RUN_DIR / "selection.json").open() as fh:
        return json.load(fh)


def _shards_path() -> Path:
    return RUN_DIR / "shards.json"


def _load_shards() -> list[dict]:
    with _shards_path().open() as fh:
        return json.load(fh)


def _task_dir(shard_id: str) -> Path:
    return SCRATCH / RUN_ID / shard_id


def _pipeline_path() -> Path:
    return REPO / "pipelines" / "rxrx19a.cppipe"


def cmd_prepare() -> None:
    """Download metadata, select pilot wells, write selection + shard plan."""
    from rerx.cellprofiler import shard_image_sets
    from rerx.metadata import (
        ImageSetID,
        download_metadata,
        parse_metadata,
        select_pilot_wells,
    )

    inputs = RUN_DIR / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)

    _log("downloading RxRx19a metadata")
    csv_path = download_metadata(inputs)

    _log("parsing metadata")
    metadata = parse_metadata(csv_path)
    (RUN_DIR / "metadata").mkdir(parents=True, exist_ok=True)
    metadata.to_parquet(RUN_DIR / "metadata" / "rxrx19a.parquet", index=False)

    # RERX_PILOT_SCALE multiplies every pilot arm size (1 = base pilot).
    arm_scale = int(os.environ.get("RERX_PILOT_SCALE", "1"))
    pilot = select_pilot_wells(
        metadata, cell_type="HRCE", max_wells=42 * arm_scale, arm_scale=arm_scale
    )
    _log(f"pilot selection: {len(pilot)} site rows (arm_scale={arm_scale})")
    pilot.to_json(RUN_DIR / "selection.json", orient="records")

    image_sets = [
        ImageSetID(
            experiment=str(r["experiment"]),
            plate=str(r["plate"]),
            well=str(r["well"]),
            site=int(r["site"]),
        )
        for r in pilot.to_dict("records")
    ]
    shards = shard_image_sets(image_sets, shard_size=SHARD_SIZE)
    plan = [
        {"shard_id": s.shard_id, "site_ids": [ids.site_id for ids in s.image_sets]}
        for s in shards
    ]
    _shards_path().write_text(json.dumps(plan, indent=2))
    _log(f"wrote {len(plan)} shards to {_shards_path()}")


def cmd_download() -> None:
    """Fetch every pilot site's five channel PNGs from GCS into RERX_SOURCE."""
    from rerx.metadata import ImageSetID

    rows = _load_selection()
    jobs: list[tuple[str, Path]] = []
    for r in rows:
        ids = ImageSetID(
            experiment=str(r["experiment"]),
            plate=str(r["plate"]),
            well=str(r["well"]),
            site=int(r["site"]),
        )
        for channel in (1, 2, 3, 4, 5):
            url = ids.image_url(channel)
            dest = SOURCE / ids.image_path(channel)
            jobs.append((url, dest))

    def fetch(job: tuple[str, Path]) -> None:
        url, dest = job
        if dest.is_file() and dest.stat().st_size > 0:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        rc = subprocess.run(
            ["curl", "-fsSL", "--retry", "3", "-o", str(tmp), url],
            timeout=300,
            check=False,
        ).returncode
        if rc != 0:
            raise SystemExit(f"download failed rc={rc}: {url}")
        tmp.rename(dest)

    _log(f"downloading ~{len(jobs)} PNGs")
    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in pool.map(fetch, jobs):
            pass
    missing = [d for _, d in jobs if not d.is_file()]
    if missing:
        raise SystemError(f"{len(missing)} downloads missing, e.g. {missing[0]}")
    _log(f"all PNGs staged under {SOURCE}")


def cmd_cellprofiler(shard_id: str) -> None:
    """Stage and run CellProfiler for one shard inside the sif."""
    from rerx.cellprofiler import ImageSetShard, run_cellprofiler_shard
    from rerx.metadata import ImageSetID

    shard_plan = {s["shard_id"]: s for s in _load_shards()}[shard_id]
    by_site = {r["site_id"]: r for r in _load_selection()}
    image_sets = [
        ImageSetID(
            experiment=str(by_site[sid]["experiment"]),
            plate=str(by_site[sid]["plate"]),
            well=str(by_site[sid]["well"]),
            site=int(by_site[sid]["site"]),
        )
        for sid in shard_plan["site_ids"]
    ]
    shard = ImageSetShard(shard_id=shard_id, image_sets=image_sets)
    result = run_cellprofiler_shard(
        shard=shard,
        source_root=SOURCE,
        scratch_root=SCRATCH,
        run_id=RUN_ID,
        pipeline_path=_pipeline_path(),
        container_image=str(SIF),
        runtime="apptainer",
        timeout=7200,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"cellprofiler {shard_id} failed rc={result.returncode}; "
            f"log: {result.log_path}"
        )
    if result.sqlite_path is None:
        raise SystemExit(f"cellprofiler {shard_id} produced no SQLite db")
    _log(f"{shard_id}: sqlite={result.sqlite_path} masks={len(result.mask_paths)}")


def _shard_sqlite(shard_id: str) -> Path:
    dbs = sorted(_task_dir(shard_id).glob("output/*.sqlite"))
    if not dbs:
        raise SystemExit(
            f"no SQLite db for shard {shard_id} under {_task_dir(shard_id)}"
        )
    return dbs[0]


def _shard_profiles_path(shard_id: str) -> Path:
    return SCRATCH / RUN_ID / "cytotable" / f"{shard_id}.parquet"


def cmd_cytotable(shard_id: str) -> None:
    """Convert one shard's SQLite into a joined single-cell Parquet."""
    import pandas as pd

    from rerx.cytotable import (
        add_cell_ids,
        convert_sqlite_to_parquet,
        validate_unique_cell_ids,
    )
    from rerx.validate import check_sqlite_integrity

    sqlite_path = _shard_sqlite(shard_id)
    integrity = check_sqlite_integrity(sqlite_path)
    if not integrity.passed:
        raise SystemExit(f"sqlite integrity failed for {shard_id}: {integrity}")

    dest = _shard_profiles_path(shard_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    parquet_path = convert_sqlite_to_parquet(sqlite_path, dest)
    profiles = pd.read_parquet(parquet_path)
    profiles = add_cell_ids(profiles)
    validate_unique_cell_ids(profiles)
    profiles.to_parquet(parquet_path, index=False)
    _log(f"{shard_id}: {len(profiles)} single cells -> {parquet_path}")


def cmd_crops(shard_id: str) -> None:
    """Per-cell JPEG crops for one shard (durable output on the run dir)."""
    import pandas as pd

    from rerx.crops import (
        crop_site_cells,
        load_site_crop_inputs,
        validate_crops_join_one_to_one,
        write_crops_parquet,
    )
    from rerx.metadata import ImageSetID

    task_dir = _task_dir(shard_id)
    images_dir = task_dir / "images"
    profiles = pd.read_parquet(_shard_profiles_path(shard_id))

    mask_by_site: dict[tuple[str, int], Path] = {}
    for p in sorted(task_dir.glob("output/*_MaskCells*.tiff")):
        stem = p.stem  # e.g. A01_s1_w1_MaskCells
        well = stem.split("_")[0]
        site = int(stem.split("_")[1][1:])
        mask_by_site[(well, site)] = p

    shard_plan = {s["shard_id"]: s for s in _load_shards()}[shard_id]
    by_site = {r["site_id"]: r for r in _load_selection()}
    frames = []
    for sid in shard_plan["site_ids"]:
        r = by_site[sid]
        ids = ImageSetID(
            experiment=str(r["experiment"]),
            plate=str(r["plate"]),
            well=str(r["well"]),
            site=int(r["site"]),
        )
        mask_path = mask_by_site[(ids.well, ids.site)]
        inputs = load_site_crop_inputs(ids, images_dir, mask_path)
        cell_rows = profiles.loc[
            (profiles["Image_Metadata_Well"] == ids.well)
            & (profiles["Image_Metadata_Site"].astype(str) == str(ids.site))
        ]
        frames.append(crop_site_cells(inputs, cell_rows))
    crops = pd.concat(frames, ignore_index=True)
    validate_crops_join_one_to_one(crops, profiles)
    dest = RUN_DIR / "crops" / "cells" / f"{shard_id}.parquet"
    write_crops_parquet(crops, dest)
    _log(f"{shard_id}: {len(crops)} crops -> {dest}")


def cmd_morphem(shard_id: str) -> None:
    """Embed one crop shard with MorphEm (runs INSIDE morphem.sif).

    The heavy lifting (torch/transformers) lives in the morphem
    container; this wrapper only points it at the run tree. The
    Nextflow MORPHEM process bind-mounts the same paths.
    """
    cmd = [
        "apptainer",
        "exec",
        "--bind",
        f"{REPO}:/rerx:ro",
        "--bind",
        f"{RUN_DIR}:/rerx_run",
        str(MORPHEM_SIF),
        "python",
        "/rerx/scripts/morphem_embed.py",
        shard_id,
        "--crops-dir",
        "/rerx_run/crops/cells",
        "--dest-root",
        "/rerx_run",
    ]
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        raise SystemExit(f"morphem {shard_id} failed rc={rc}")


def _finalize_profiles(
    profiles: "pd.DataFrame | Iterable[tuple[tuple[str, str], pd.DataFrame]]",
    profiler: str,
    buscar: bool,
) -> list["PlateSummary"]:
    """Annotate/normalize/select (and buscar) per plate for one profiler.

    ``profiles`` may be a single in-memory frame (pilot scale) or a
    sequence of per-plate frames already partitioned by the caller
    (streaming path at full scale). Only one plate's rows are in
    memory at a time either way.

    Returns one :class:`rerx.run_summary.PlateSummary` per plate
    finalized, for the operational run summary (see ``cmd_finalize``).
    """
    import pandas as pd

    from rerx.cytotable import plate_partitions
    from rerx.finalize import finalize_plate
    from rerx.run_summary import PlateSummary

    pilot = pd.DataFrame(_load_selection())
    normalized_rows = 0
    selected_cols = None
    plates = 0
    plate_summaries: list[PlateSummary] = []
    # Streaming: when the caller passes an iterable of pre-partitioned
    # (key, frame) pairs (iter_shard_partitions / iter_partition_frames
    # output), consume it directly; only a plain DataFrame needs
    # plate_partitions grouping.
    pairs: Iterable[tuple[tuple[str, str], pd.DataFrame]] = (
        profiles  # type: ignore[assignment]
        if hasattr(profiles, "__iter__") and not isinstance(profiles, pd.DataFrame)
        else plate_partitions(profiles)  # type: ignore[arg-type]
    )
    for (experiment, plate), plate_profiles in pairs:
        plate_metadata = pilot[
            (pilot["experiment"].astype(str) == experiment)
            & (pilot["plate"].astype(str) == plate)
        ]
        n_cells_input = len(plate_profiles)
        result = finalize_plate(
            raw_profiles=plate_profiles,
            site_metadata=plate_metadata,
            run_dir=RUN_DIR,
            experiment=experiment,
            plate=plate,
            profiler=profiler,
            run_buscar=buscar,
        )
        # finalize_plate already wrote the normalized and
        # feature-selected parquets for this plate; keep only counts
        # (not the frames) so memory stays per-plate.
        normalized_rows += len(result.normalized)
        selected_cols = result.feature_selected.shape[1]
        plates += 1
        cs = result.control_separation
        plate_summaries.append(
            PlateSummary(
                experiment=experiment,
                plate=plate,
                profiler=profiler,
                n_cells_input=n_cells_input,
                n_cells_flagged_outlier=result.n_cells_flagged_outlier,
                n_cells_normalized=len(result.normalized),
                n_feature_selected_cols=result.feature_selected.shape[1],
                control_separation_passed=cs.passed if cs else None,
                control_separation_skipped=cs.skipped if cs else None,
                control_separation_median_effect_size=(
                    cs.median_abs_effect_size if cs else None
                ),
                buscar_status="skipped" if result.buscar_skipped_reason else "scored",
                buscar_skipped_reason=result.buscar_skipped_reason,
            )
        )
        if result.buscar_skipped_reason:
            _log(
                f"{profiler} {experiment}/{plate}: buscar skipped -- "
                f"{result.buscar_skipped_reason}"
            )
        else:
            _log(f"{profiler} {experiment}/{plate}: buscar scored")
        if result.n_cells_flagged_outlier:
            _log(
                f"{profiler} {experiment}/{plate}: coSMicQC flagged "
                f"{result.n_cells_flagged_outlier} outlier cell(s) "
                "(dropped before normalization)"
            )
    _log(
        f"{profiler}: annotated/normalized {normalized_rows} cells "
        f"across {plates} plate(s); feature_selected -> {selected_cols} cols"
    )
    return plate_summaries


def _fuse_finalized_profiles() -> None:
    """Fuse finalized CP and MorphEm profiles on Metadata_cell_id.

    Pairs each plate's CP table with its own MorphEm table by
    experiment/plate partition (not sorted-file order), and fuses
    every shared pair; unmatched partitions are skipped (inner-join
    behavior, same as fuse_features itself).

    Streams: one plate pair is loaded, fused, and written at a time;
    no whole-dataset fused frame is held.
    """
    import pandas as pd

    from rerx.fuse import (
        fuse_features,
        pair_fused_partitions,
        write_fused_profiles,
    )

    cp_selected = sorted(
        (RUN_DIR / "profiles" / "cellprofiler" / "feature_selected").glob(
            "*/*/*.parquet"
        )
    )
    morphem_selected = sorted(
        (RUN_DIR / "profiles" / "morphem" / "feature_selected").glob("*/*/*.parquet")
    )
    if not (cp_selected and morphem_selected):
        _log("fused output skipped: missing finalized CP or MorphEm")
        return
    pairs = pair_fused_partitions(cp_selected, morphem_selected)
    if not pairs:
        _log(
            "fused output skipped: no shared experiment/plate "
            "partitions between CP and MorphEm profiles"
        )
        return
    fused_rows = 0
    fused_cols = 0
    cp_rows_total = 0
    morphem_rows_total = 0
    dropped_rows_total = 0
    partitions_written: list[Path] = []
    for cp_path, me_path in pairs:
        cp_df = pd.read_parquet(cp_path)
        me_df = pd.read_parquet(me_path)
        fused = fuse_features(cp_df, me_df)
        written = write_fused_profiles(
            fused, RUN_DIR, cp_rows=len(cp_df), morphem_rows=len(me_df)
        )
        dropped = max(len(cp_df), len(me_df)) - len(fused)
        if dropped:
            _log(
                f"fused {cp_path.parent.parent.name}/{cp_path.parent.name}: "
                f"dropped {dropped} non-overlapping cell(s) "
                f"(CP {len(cp_df)}, MorphEm {len(me_df)} -> {len(fused)} fused)"
            )
        partitions_written.extend(written)
        fused_rows += len(fused)
        fused_cols = fused.shape[1]
        cp_rows_total += len(cp_df)
        morphem_rows_total += len(me_df)
        dropped_rows_total += dropped
        del fused, cp_df, me_df
    _log(
        f"fused: {fused_rows} cells x {fused_cols} cols from "
        f"{len(pairs)} plate(s) -> {len(partitions_written)} partition(s) "
        f"({dropped_rows_total} non-overlapping cell(s) dropped across "
        f"{cp_rows_total} CP / {morphem_rows_total} MorphEm input cells)"
    )


def cmd_recursion_buscar() -> None:
    """buscar-score Recursion's published site embeddings per plate."""
    import pandas as pd

    from rerx.buscar import run_buscar_for_plate
    from rerx.cytotable import plate_partitions
    from rerx.embeddings import build_recursion_buscar_profiles

    emb_dir = RUN_DIR / "baseline" / "recursion_site_embeddings"
    emb_path = emb_dir / "recursion_site_embeddings.parquet"
    if not emb_path.is_file():
        # Fetch and convert the published embeddings on first use
        # (idempotent: download_embeddings skips an existing ZIP).
        from rerx.embeddings import (
            convert_embeddings,
            download_embeddings,
        )

        cache_root = (
            Path(os.environ.get("RERX_EMBEDDINGS_CACHE", str(RUN_DIR / "source-cache")))
            / "rxrx19a"
        )
        _log("recursion: downloading published site embeddings")
        zip_path, _digest = download_embeddings(cache_root)
        convert_embeddings(zip_path, emb_dir)
    embeddings = pd.read_parquet(emb_path)
    selection = pd.DataFrame(_load_selection())
    profiles = build_recursion_buscar_profiles(embeddings, selection)
    _log(f"recursion: annotated {len(profiles)} site embeddings")

    # Site embeddings are one row per (well, site); average to one row
    # per well (the replicate unit buscar scores) so the KS test has
    # the same per-treatment sample sizes as the other profilers.
    feature_cols = [c for c in profiles.columns if c.startswith("Recursion_")]
    group_cols = [
        "experiment",
        "plate",
        "well",
        "Metadata_rxrx_control_type",
        "Metadata_perturbation",
        "Metadata_buscar_state",
    ]
    well_profiles = (
        profiles.groupby(group_cols, as_index=False)[feature_cols]
        .median()
        .reset_index(drop=True)
    )
    # plate_partitions groups on (Image_Metadata_Experiment,
    # Image_Metadata_Plate); supply those names for the embeddings.
    well_profiles = well_profiles.rename(  # type: ignore[dict-item]
        columns={
            "experiment": "Image_Metadata_Experiment",
            "plate": "Image_Metadata_Plate",
            "well": "Image_Metadata_Well",
        }
    )
    for (experiment, plate), plate_profiles in plate_partitions(well_profiles):
        run_buscar_for_plate(
            plate_profiles=plate_profiles,
            dest_dir=RUN_DIR / "buscar" / "recursion",
            experiment=experiment,
            plate=plate,
            profiler="recursion",
        )
        _log(f"recursion {experiment}/{plate}: buscar scored")


def cmd_projection() -> None:
    """Recursion-style on/off-perturbation scores for each profiler."""
    import pandas as pd

    from rerx.projection import projection_scores
    from rerx.streaming import iter_partition_frames

    for profiler in ("cellprofiler", "morphem"):
        norm_dir = RUN_DIR / "profiles" / profiler / "normalized"
        norm_paths = sorted(norm_dir.glob("*/*/*.parquet"))
        if not norm_paths:
            _log(f"projection skipped: no normalized {profiler} profiles")
            continue
        # Feature columns are identical across a profiler's plates;
        # read them from the first partition's schema only.
        import pyarrow.parquet as pq

        first_cols = pq.read_schema(norm_paths[0]).names
        feat_cols = [
            c
            for c in first_cols
            if c.startswith(("Cells_", "Cytoplasm_", "Nuclei_", "Morphem_"))
        ]
        dest_dir = RUN_DIR / "projection" / profiler
        dest_dir.mkdir(parents=True, exist_ok=True)
        for (experiment, plate), normed in iter_partition_frames(norm_dir):
            # Score the well-level aggregate (one row per well, the
            # replicate unit), matching how the pilot reports score.
            agg = pd.DataFrame(
                normed.groupby(
                    [
                        "Metadata_Experiment",
                        "Metadata_Plate",
                        "Metadata_Well",
                        "Metadata_perturbation",
                    ],
                    as_index=False,
                )[feat_cols].median()
            )
            scores = None
            try:
                scores = projection_scores(
                    agg,
                    feature_cols=feat_cols,
                )
            except ValueError as exc:
                _log(f"projection {profiler} {experiment}/{plate}: skipped -- {exc}")
                continue
            out = (
                dest_dir
                / f"experiment={experiment}"
                / f"plate={plate}"
                / "scores.parquet"
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            scores.to_parquet(out, index=False)
            _log(
                f"projection {profiler} {experiment}/{plate}: "
                f"{len(scores)} perturbations scored -> {out}"
            )
            del normed, agg, scores


def _append_partitioned_profiles(frame: "pd.DataFrame", dest_root: Path) -> None:
    """Append one frame's rows into the partitioned Parquet layout.

    Streaming helper for raw shards that need regrouping into the
    ``experiment=<e>/plate=<p>/`` layout (e.g. the MorphEm raw
    shards, which are per-crop-shard, not per-plate). Reads one
    caller-provided frame at a time; partitions accumulate on disk
    over multiple appends, so memory stays one-frame.
    """
    import pandas as pd

    from rerx.cytotable import plate_partitions

    for (experiment, plate), part in plate_partitions(frame):  # type: ignore[arg-type]
        part_dir = dest_root / f"experiment={experiment}" / f"plate={plate}"
        part_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = part_dir / "profiles.parquet.tmp"
        final_path = part_dir / "profiles.parquet"
        rows = part
        if final_path.exists():
            prior = pd.read_parquet(final_path)
            rows = pd.concat([prior, part], ignore_index=True)
            del prior
        rows.to_parquet(tmp_path, index=False, compression="zstd")
        tmp_path.replace(final_path)
        del rows


def _validate_run_streaming(
    shard_parquets: list[Path],
    sqlite_paths: list[Path],
    crop_dir: Path,
) -> dict:
    """Run the run's validation checks one shard at a time.

    Returns a plain-dict validation summary (sqlite/schema/crop-decode
    counts), suitable for the operational run summary. Raises
    SystemExit on the first failure.
    """
    import pandas as pd

    from rerx.cytotable import check_schema_consistency
    from rerx.streaming import shard_parquet_pairs
    from rerx.validate import (
        check_crops_decode,
        check_sqlite_integrity,
        validate_crops_join_one_to_one,
    )

    for sqlite_path in sqlite_paths:
        result = check_sqlite_integrity(sqlite_path)
        if not result.passed:
            raise SystemExit(
                f"sqlite integrity failed for {sqlite_path.name}: "
                f"{result.integrity_messages} orphans={result.orphan_counts}"
            )
    schema_check = check_schema_consistency(shard_parquets)
    if not schema_check.consistent:
        raise SystemExit(f"shard schema mismatch: {schema_check.mismatches}")

    crop_paths = sorted(crop_dir.glob("*.parquet"))
    pairs, unmatched = shard_parquet_pairs(crop_paths, shard_parquets)
    if unmatched:
        raise SystemExit(
            f"crop shards without profile shards: {len(unmatched)} "
            f"(e.g. {unmatched[0].name})"
        )
    decoded_total = 0
    for crop_path, profile_path in pairs:
        crops = pd.read_parquet(crop_path)
        profiles = pd.read_parquet(profile_path)
        decode = check_crops_decode(crops)
        if not decode.passed:
            raise SystemExit(
                f"crop JPEG decode failures in {crop_path.name}: "
                f"{decode.decode_failures[:5]}"
            )
        try:
            validate_crops_join_one_to_one(crops, profiles)
        except ValueError as exc:
            raise SystemExit(f"crop/profile join failed for {crop_path.name}: {exc}")
        decoded_total += decode.checked
        del crops, profiles
    _log(
        f"validation: {len(sqlite_paths)} sqlite shard(s) integrity OK; "
        f"{len(shard_parquets)} shard schemas consistent; "
        f"{len(pairs)} crop shard(s) join + decode checks passed "
        f"({decoded_total} cells)"
    )
    return {
        "passed": True,
        "sqlite_shards_checked": len(sqlite_paths),
        "sqlite_shards_failed": 0,
        "schema_consistent": True,
        "crop_shard_pairs_checked": len(pairs),
        "crop_decode_checked": decoded_total,
    }


def cmd_finalize() -> None:
    """Finalize (annotate/normalize/select/buscar), fuse, validate, catalog.

    Streaming at every stage (no whole-dataset frames in memory):

    - CP profiles stream per plate straight from the shard parquets.
    - The MorphEm raw shards are partitioned once, plate by plate
      (each plate's rows are the only ones in memory), then finalized
      per plate from that partitioned layout.
    - Fusion pairs CP and MorphEm tables per plate and writes each
      fused partition immediately.
    - Validation runs one shard at a time (crop join + JPEG decode
      against that shard's own profile shard).
    - The catalog build globs paths only (DuckDB reads files
      lazily).
    """
    import pandas as pd

    from rerx.catalog import build_run_catalog
    from rerx.qc_notebook import build_crop_review_notebook
    from rerx.run_summary import RunSummary, write_run_summary
    from rerx.streaming import iter_partition_frames, iter_shard_partitions

    shard_parquets = sorted((SCRATCH / RUN_ID / "cytotable").glob("*.parquet"))
    if not shard_parquets:
        raise SystemExit(f"no shard parquets under {SCRATCH / RUN_ID / 'cytotable'}")

    plate_summaries: list[PlateSummary] = []

    # 1. Finalize CellProfiler profiles per plate, streamed from the
    #    shard parquets (each plate is its own biological batch).
    #    The durable partitioned raw layout is a required catalog and
    #    buscar input, so write it as the same per-plate pass.
    plate_summaries += _finalize_profiles(
        iter_shard_partitions(shard_parquets), profiler="cellprofiler", buscar=True
    )

    # 1b. Durable raw partitions (catalog + buscar input): written
    #     plate by plate from the same shard parquets, streaming.
    raw_dir = RUN_DIR / "profiles" / "cellprofiler" / "raw"
    for (experiment, plate), frame in iter_shard_partitions(shard_parquets):
        part_dir = raw_dir / f"experiment={experiment}" / f"plate={plate}"
        part_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = part_dir / "profiles.parquet.tmp"
        final_path = part_dir / "profiles.parquet"
        frame.to_parquet(tmp_path, index=False, compression="zstd")
        tmp_path.replace(final_path)

    # 2. MorphEm pass: partition the raw shards plate by plate (only
    #    when every expected morphem shard is present; a partial set
    #    would silently produce incomplete scores).
    morphem_parquets = sorted(
        (RUN_DIR / "profiles" / "morphem" / "raw").glob("*.parquet")
    )
    morphem_shards = {p.stem for p in morphem_parquets}
    expected = {s["shard_id"] for s in _load_shards()}
    if morphem_shards == expected:
        # Crop-carried metadata uses plain lowercase names
        # (Metadata_experiment, Metadata_plate, ...); the shared
        # finalize layer expects the cytotable-style Image_Metadata_*
        # names (annotate_profiles then maps them to
        # Metadata_Experiment/Plate/Well/Site for the site join), so
        # rename at this seam, one shard at a time.
        morphem_partitions = RUN_DIR / "profiles" / "morphem" / "raw_partitioned"
        if morphem_partitions.is_dir():
            # Clear any prior attempt's partitions first:
            # _append_partitioned_profiles merges onto whatever is
            # already on disk, so a rerun without this would
            # duplicate every row already partitioned last time.
            shutil.rmtree(morphem_partitions)
        for path in morphem_parquets:
            frame = pd.read_parquet(path).rename(
                columns={
                    "Metadata_experiment": "Image_Metadata_Experiment",
                    "Metadata_plate": "Image_Metadata_Plate",
                    "Metadata_well": "Image_Metadata_Well",
                    "Metadata_site": "Image_Metadata_Site",
                }
            )
            _append_partitioned_profiles(frame, morphem_partitions)
        _log(f"morphem: partitioned {len(morphem_parquets)} raw shard(s)")
        plate_summaries += _finalize_profiles(
            iter_partition_frames(morphem_partitions),
            profiler="morphem",
            buscar=True,
        )

        # Fused output: early feature-concatenation fusion of the two
        # finalized spaces on Metadata_cell_id (see rerx.fuse).
        # Labeled in profiles/fused/fusion.json ("what kind of fused").
        _fuse_finalized_profiles()
    elif morphem_shards:
        missing = sorted(expected - morphem_shards)
        _log(
            f"morphem finalize skipped: {len(missing)} shard(s) missing "
            f"(e.g. {missing[0]}); rerun after MORPHEM completes"
        )
    else:
        _log("morphem finalize skipped: no morphem shards present")

    # 3. Validation, one shard at a time (see _validate_run_streaming).
    sqlite_paths = sorted(SCRATCH.glob(f"{RUN_ID}/*/output/*.sqlite"))
    validation_summary = _validate_run_streaming(
        shard_parquets,
        sqlite_paths,
        crop_dir=RUN_DIR / "crops" / "cells",
    )

    # 3b. Operational run summary (plan.md: pipeline health, not
    # biology -- shard/plate counts, coSMicQC flag rates, buscar skip
    # reasons, validation results). Separate from the reports/ HTML
    # reports, which answer "what did we discover" instead.
    run_summary = RunSummary(
        run_id=RUN_ID, plates=plate_summaries, validation=validation_summary
    )
    summary_path = write_run_summary(run_summary, RUN_DIR / "run_summary.json")
    _log(f"run summary -> {summary_path} ({run_summary.totals()})")

    # 3c. Human crop spot-check notebook (plan.md section 16/27: "random
    # crop and segmentation QC looks correct"). A convenience output, not
    # a validation gate -- skipped quietly if crops never ran for this
    # run (e.g. a finalize-only rerun).
    try:
        notebook_path = build_crop_review_notebook(run_dir=RUN_DIR)
        _log(f"crop spot-check notebook -> {notebook_path}")
    except FileNotFoundError as exc:
        _log(f"crop spot-check notebook skipped: {exc}")

    # 4. Catalog + success marker.
    build_run_catalog(
        run_root=RUN_DIR,
        catalog_path=RUN_DIR / "catalog" / "run.ducklake",
        data_path=RUN_DIR / "catalog" / "ducklake_data",
    )
    (RUN_DIR / "_SUCCESS").write_text(RUN_ID + "\n")
    _log(f"run complete: {RUN_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="rerx-tasks")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("download")
    for name in ("cellprofiler", "cytotable", "crops", "morphem"):
        p = sub.add_parser(name)
        p.add_argument("shard_id")
    sub.add_parser("finalize")
    sub.add_parser("recursion-buscar")
    sub.add_parser("projection")
    args = parser.parse_args()

    # Dispatch table keeps main() under the complexity budget as
    # subcommands grow (C901 at 11 branches otherwise).
    shard_commands = ("cellprofiler", "cytotable", "crops", "morphem")
    no_arg_commands = {
        "prepare": cmd_prepare,
        "download": cmd_download,
        "finalize": cmd_finalize,
        "recursion-buscar": cmd_recursion_buscar,
        "projection": cmd_projection,
    }
    if args.command in shard_commands:
        {
            "cellprofiler": cmd_cellprofiler,
            "cytotable": cmd_cytotable,
            "crops": cmd_crops,
            "morphem": cmd_morphem,
        }[args.command](args.shard_id)
    else:
        no_arg_commands[args.command]()


if __name__ == "__main__":
    main()

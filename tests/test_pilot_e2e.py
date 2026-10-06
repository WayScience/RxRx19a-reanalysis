"""
Full local pilot pipeline against a real CellProfiler container.

Marked ``pilot_e2e`` (deselected by default -- see pyproject.toml's
``addopts``); run explicitly with ``uv run pytest -m pilot_e2e``.

Exercises every stage a real Alpine pilot run will hit, in order, against
one small synthetic 4-well/1-site "plate" and a real
``cellprofiler/cellprofiler:4.2.8`` Docker container (Apptainer is the
Alpine runtime; Docker is what :mod:`rerx.cellprofiler` supports locally):

    shard -> stage+run CellProfiler -> SQLite integrity ->
    CytoTable convert -> cell IDs -> partitioned Parquet write ->
    crops -> pilot validation report -> Pycytominer annotate/normalize/
    feature_select -> Frozen DuckLake catalog -> query back through a
    fresh connection.

Two real bugs were found and fixed by first running this scenario
manually (before this test existed):

1. ``run_cellprofiler_shard`` bound the *unresolved* ``source_root`` into
   the container, but ``stage_shard_images`` symlinks to the fully
   *resolved* source path. Whenever any component of ``source_root`` is
   itself a symlink (macOS's ``/tmp`` -> ``/private/tmp``; a symlinked
   HPC scratch mount), the bind mount misses the real target and
   CellProfiler silently returns 0 image sets. Fixed by resolving
   ``source_root`` once in ``run_cellprofiler_shard`` and binding that.
2. CytoTable's raw join output must not be written directly into
   ``profiles/cellprofiler/raw/`` -- that is where the *partitioned*
   Parquet (different schema) also lives, and DuckLake's catalog
   registration then fails to glob a consistent schema. Documented in
   ``convert_sqlite_to_parquet``'s docstring; this test writes the
   CytoTable intermediate to scratch instead.

This test is a regression guard for both, plus a general readiness check
before spending Alpine allocation time: if this fails locally, the pilot
will fail on Alpine for the same reason.
"""

import shutil
import subprocess
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from PIL import Image

from rerx.catalog import build_run_catalog
from rerx.cellprofiler import (
    cellprofiler_version_command,
    run_cellprofiler_shard,
    shard_image_sets,
)
from rerx.crops import (
    crop_site_cells,
    load_site_crop_inputs,
    validate_crops_join_one_to_one,
    write_crops_parquet,
)
from rerx.cytotable import (
    add_cell_ids,
    convert_sqlite_to_parquet,
    validate_unique_cell_ids,
    write_partitioned_profiles,
)
from rerx.metadata import ImageSetID
from rerx.pycytominer import annotate_profiles, normalize_profiles, select_features
from rerx.runs import Run, make_run_dir, make_run_id
from rerx.validate import check_sqlite_integrity, validate_pilot_run

CELLPROFILER_IMAGE = "cellprofiler/cellprofiler:4.2.8"
PIPELINE_PATH = Path(__file__).resolve().parent.parent / "pipelines" / "rxrx19a.cppipe"

PILOT_WELLS = [
    ("A01", "Mock", ""),
    ("A02", "UV Inactivated SARS-CoV-2", ""),
    ("A03", "Active SARS-CoV-2", ""),
    ("A04", "Active SARS-CoV-2", "remdesivir"),
]


def _docker_available() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10, check=False
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _generate_synthetic_source(root: Path) -> None:
    """Realistic-noise 5-channel PNGs for 4 wells (matches gold-standard fixture)."""
    rng = np.random.default_rng(42)
    plate_dir = root / "HRCE-1" / "Plate25"
    plate_dir.mkdir(parents=True, exist_ok=True)
    for well, _disease, _treatment in PILOT_WELLS:
        nuclei = [
            (
                int(rng.integers(40, 471)),
                int(rng.integers(40, 471)),
                int(rng.integers(10, 22)),
            )
            for _ in range(10)
        ]
        yy, xx = np.mgrid[0:512, 0:512]
        for channel in range(1, 6):
            arr = np.full((512, 512), 10.0, dtype=np.float64)
            for cx, cy, r in nuclei:
                d2 = (xx - cx) ** 2 + (yy - cy) ** 2
                if channel == 1:
                    arr += 200.0 * np.exp(-d2 / (2 * (r * 0.6) ** 2))
                elif channel in (2, 3, 5):
                    arr += 80.0 * np.exp(-d2 / (2 * (r * 2.2) ** 2))
                else:
                    arr += 130.0 * np.exp(-d2 / (2 * 5.0**2))
            arr += rng.normal(0, 4.0, arr.shape)
            arr = np.clip(arr, 0, 255).astype(np.uint8)
            Image.fromarray(arr, mode="L").save(plate_dir / f"{well}_s1_w{channel}.png")


@pytest.mark.pilot_e2e
@pytest.mark.skipif(not _docker_available(), reason="docker is not available/running")
@pytest.mark.skipif(
    not PIPELINE_PATH.is_file(), reason=f"missing pipeline file: {PIPELINE_PATH}"
)
def test_full_local_pilot_pipeline(tmp_path: Path) -> None:  # noqa: PLR0915
    source_root = tmp_path / "source"
    scratch_root = tmp_path / "scratch"
    datasets_root = tmp_path / "datasets"
    _generate_synthetic_source(source_root)

    metadata_df = pd.DataFrame(
        [
            {
                "experiment": "HRCE-1",
                "plate": "25",
                "well": well,
                "site": 1,
                "disease_condition": disease,
                "treatment": treatment,
                "cell_type": "HRCE",
            }
            for well, disease, treatment in PILOT_WELLS
        ]
    )
    image_sets = [
        ImageSetID(
            experiment=r["experiment"], plate=r["plate"], well=r["well"], site=r["site"]
        )
        for r in metadata_df.to_dict("records")
    ]

    # Run identity (plan.md sections 5-6).
    run_id = make_run_id(scope="pilot", commit="e2etest")
    run_dir_path = make_run_dir(datasets_root, run_id)
    run = Run(run_id=run_id, root=run_dir_path)
    run.set_marker("_RUNNING")

    # Container version check (plan.md section 11).
    ver_cmd = cellprofiler_version_command(
        container_image=CELLPROFILER_IMAGE, runtime="docker"
    )
    ver = subprocess.run(
        ver_cmd, capture_output=True, text=True, timeout=120, check=False
    )
    assert ver.returncode == 0
    assert "4.2.8" in ver.stdout

    # Shard (plan.md section 12).
    shards = shard_image_sets(image_sets, shard_size=24)
    assert len(shards) == 1
    shard = shards[0]

    # Stage + run CellProfiler (plan.md sections 11-12).
    result = run_cellprofiler_shard(
        shard=shard,
        source_root=source_root,
        scratch_root=scratch_root,
        run_id=run_id,
        pipeline_path=PIPELINE_PATH,
        container_image=CELLPROFILER_IMAGE,
        runtime="docker",
        timeout=600,
    )
    assert result.returncode == 0, result.log_path.read_text()
    assert result.sqlite_path is not None
    assert len(result.mask_paths) == len(image_sets) * 3  # Nuclei/Cells/Cytoplasm

    # SQLite integrity (plan.md sections 12, 27).
    integrity = check_sqlite_integrity(result.sqlite_path)
    assert integrity.passed

    # CytoTable conversion (plan.md section 13). Intermediate goes to
    # scratch, not the run's profiles/cellprofiler/raw/ (see module note).
    scratch_profile_path = scratch_root / run_id / "cytotable" / "shard0.parquet"
    profile_parquet = convert_sqlite_to_parquet(
        result.sqlite_path, scratch_profile_path
    )
    profiles = pd.read_parquet(profile_parquet)
    profiles = add_cell_ids(profiles)
    validate_unique_cell_ids(profiles)
    assert len(profiles) > 0

    raw_dir = run_dir_path / "profiles" / "cellprofiler" / "raw"
    partition_paths = write_partitioned_profiles(profiles, raw_dir)
    assert partition_paths

    # Crops (plan.md section 14).
    mask_by_site = {}
    for p in result.mask_paths:
        if "MaskCells" in p.name:
            stem = p.stem  # e.g. A01_s1_w1_MaskCells
            well = stem.split("_")[0]
            site = int(stem.split("_")[1][1:])
            mask_by_site[(well, site)] = p

    crop_frames = []
    for ids in image_sets:
        mask_path = mask_by_site[(ids.well, ids.site)]
        inputs = load_site_crop_inputs(ids, result.images_dir, mask_path)
        cell_rows = profiles[
            (profiles["Image_Metadata_Well"] == ids.well)
            & (profiles["Image_Metadata_Site"].astype(str) == str(ids.site))
        ]
        crop_frames.append(crop_site_cells(inputs, cell_rows))
    crops = pd.concat(crop_frames, ignore_index=True)
    validate_crops_join_one_to_one(crops, profiles)
    write_crops_parquet(crops, run_dir_path / "crops" / "cells" / "shard0.parquet")

    # Aggregated pilot validation report (plan.md section 27).
    report = validate_pilot_run(
        sqlite_paths=[result.sqlite_path],
        profile_parquet_paths=partition_paths,
        profiles=profiles,
        crops=crops,
    )
    assert report.passed, report.summary()

    # Pycytominer annotate/normalize/feature_select (plan.md section 18).
    annotated = annotate_profiles(profiles, metadata_df)
    assert "Metadata_rxrx_control_type" in annotated.columns
    assert set(annotated["Metadata_rxrx_control_type"]) == {
        "mock",
        "uv",
        "active_untreated",
        "treated",
    }

    normalized = normalize_profiles(
        annotated,
        run_dir_path / "profiles" / "cellprofiler" / "normalized" / "shard0.parquet",
    )
    assert len(normalized) == len(annotated)

    feature_selected = select_features(
        normalized,
        run_dir_path
        / "profiles"
        / "cellprofiler"
        / "feature_selected"
        / "shard0.parquet",
    )
    assert len(feature_selected) == len(normalized)
    assert (
        feature_selected.shape[1] < normalized.shape[1]
    )  # selection actually dropped columns

    # Frozen DuckLake catalog (plan.md section 20), then query back
    # through a completely fresh connection ("Anyone must still be able
    # to inspect the dataset with plain tools").
    run.set_marker("_SUCCESS")
    catalog_result = build_run_catalog(
        run_root=run_dir_path,
        catalog_path=run_dir_path / "catalog" / "run.ducklake",
        data_path=run_dir_path / "catalog" / "data",
    )
    table_names = {t.table for t in catalog_result.tables}
    assert {
        "cellprofiler_raw",
        "cellprofiler_normalized",
        "cellprofiler_feature_selected",
        "crops",
    } <= table_names

    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake")
        con.execute("LOAD ducklake")
        catalog_file = run_dir_path / "catalog" / "run.ducklake"
        con.execute(f"ATTACH 'ducklake:{catalog_file}' AS chk (READ_ONLY)")
        row_count = con.execute("SELECT COUNT(*) FROM chk.cellprofiler_raw").fetchone()
        assert row_count is not None
        assert row_count[0] == len(profiles)
    finally:
        con.close()

    shutil.rmtree(scratch_root, ignore_errors=True)

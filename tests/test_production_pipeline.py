"""Full-dataset preparation and per-shard image staging."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from rerx import metadata


def _dist_version(name: str) -> str:
    """Installed distribution version for a Python package (best effort)."""
    import importlib.metadata

    return importlib.metadata.version(name)


def _hash(path: Path) -> str:
    """Hex sha256 of a file, matching rerx.manifest.sha256_file."""
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


_MORPHEM_MODEL_REVISION = "0e8d58787421f83f975634d72420d85c5dfc9c2c"


def _load_tasks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    for name, value in {
        "RERX_REPO": repo,
        "RERX_RUN_DIR": tmp_path / "runs" / "rxrx19a-full-test-g0000000",
        "RERX_SCRATCH": tmp_path / "scratch",
        "RERX_SOURCE": tmp_path / "source",
        "RERX_SIF": tmp_path / "cp.sif",
        "RERX_RUN_ID": "rxrx19a-full-test-g0000000",
        "RERX_SCOPE": "full",
        "RERX_SHARD_SIZE": "2",
    }.items():
        monkeypatch.setenv(name, str(value))
    spec = importlib.util.spec_from_file_location(
        "rerx_tasks_test", repo / "scripts/rerx_tasks.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_prepare_includes_every_site_and_plans_shards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = _load_tasks(monkeypatch, tmp_path)
    rows = []
    for experiment, cell_type in (("HRCE-1", "HRCE"), ("VERO-1", "VERO")):
        for site in (1, 2, 3):
            rows.append(
                {
                    "site_id": f"{experiment}_1_A01_{site}",
                    "experiment": experiment,
                    "cell_type": cell_type,
                    "plate": "1",
                    "well": "A01",
                    "site": site,
                    # real metadata stores empty treatments as pd.NA in
                    # string columns (control wells have no treatment)
                    "treatment": pd.NA,
                }
            )
    sites = pd.DataFrame(rows).astype({"treatment": "string"})
    monkeypatch.setattr(
        metadata, "download_metadata", lambda inputs: inputs / "metadata.csv"
    )
    monkeypatch.setattr(metadata, "parse_metadata", lambda path: sites)
    tasks.cmd_prepare()
    selection = json.loads((tasks.RUN_DIR / "selection.json").read_text())
    plan = json.loads((tasks.RUN_DIR / "shards.json").read_text())
    assert len(selection) == 6
    assert {row["cell_type"] for row in selection} == {"HRCE", "VERO"}
    assert len(plan) == 4
    assert sorted(len(shard["site_ids"]) for shard in plan) == [1, 1, 2, 2]
    for experiment in ("HRCE-1", "VERO-1"):
        plate_plan = json.loads(
            (tasks.RUN_DIR / "plate-plans" / f"{experiment}-Plate1.json").read_text()
        )
        assert len(plate_plan["shard_ids"]) == 2
        assert len(plate_plan["sites"]) == 3
    counts = (tasks.RUN_DIR / "shard_plate_counts.tsv").read_text().splitlines()
    assert len(counts) == 4
    assert set(counts) == {
        f"{shard['shard_id']}\t{shard['shard_id'].rsplit('-', 1)[0]}\t2"
        for shard in plan
    }
    for shard in plan:
        rows = json.loads(
            (tasks.RUN_DIR / "shard-plans" / f"{shard['shard_id']}.json").read_text()
        )
        assert [row["site_id"] for row in rows] == shard["site_ids"]


def test_download_one_shard_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = _load_tasks(monkeypatch, tmp_path)
    shard_id = "HRCE-1-Plate1-0000"
    plan_dir = tasks.RUN_DIR / "shard-plans"
    plan_dir.mkdir(parents=True)
    plan_dir.joinpath(f"{shard_id}.json").write_text(
        json.dumps([{"experiment": "HRCE-1", "plate": "1", "well": "A01", "site": 1}])
    )
    downloaded = []

    def fake_run(command: list[str], **_kwargs: object) -> object:
        dest = Path(command[command.index("-o") + 1])
        dest.write_bytes(b"png")
        downloaded.append(dest)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    tasks.cmd_download(shard_id)
    assert len(downloaded) == 5
    assert len(list(tasks.SOURCE.rglob("*.png"))) == 5
    assert all("HRCE-1" in str(path) for path in downloaded)
    tasks.cmd_download(shard_id)
    assert len(downloaded) == 5


def test_full_run_rejects_pilot_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = _load_tasks(monkeypatch, tmp_path)
    monkeypatch.setattr(tasks, "RUN_ID", "pilot-dev")
    with pytest.raises(ValueError, match="full run ID"):
        tasks.cmd_prepare()


def test_partitioned_morphem_shards_are_written_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = _load_tasks(monkeypatch, tmp_path)
    frame = pd.DataFrame(
        {
            "Metadata_cell_id": ["cell-1"],
            "Image_Metadata_Experiment": ["HRCE-1"],
            "Image_Metadata_Plate": ["25"],
            "Morphem_c1_d0": [0.5],
        }
    )
    root = tmp_path / "raw_partitioned"
    tasks._write_partitioned_shard(frame, root, "shard-0000")
    tasks._write_partitioned_shard(
        frame.assign(Metadata_cell_id=["cell-2"]), root, "shard-0001"
    )
    paths = sorted(root.glob("*/*/*.parquet"))
    assert [path.name for path in paths] == ["shard-0000.parquet", "shard-0001.parquet"]
    assert sum(len(pd.read_parquet(path)) for path in paths) == 2
    tasks._write_partitioned_shard(frame, root, "shard-0000")
    assert len(list(root.glob("*/*/*.parquet"))) == 2


def _stage_one_plate_fixture(
    tasks, plate_id: str = "HRCE-1-Plate1"
) -> tuple[str, Path, Path]:
    """Write one plate plan, shard plan, and the four shard artifacts.

    Returns (shard_id, morphem_parquet, cp_feature_selected_path).
    """
    shard_id = f"{plate_id}-0000"
    plate_plan = tasks.RUN_DIR / "plate-plans" / f"{plate_id}.json"
    plate_plan.parent.mkdir(parents=True)
    plate_plan.write_text(
        json.dumps(
            {
                "experiment": "HRCE-1",
                "plate": "1",
                "shard_ids": [shard_id],
                "sites": [
                    {"experiment": "HRCE-1", "plate": "1", "well": "A01", "site": 1}
                ],
            }
        )
    )
    (tasks.RUN_DIR / "shards.json").write_text(json.dumps([{"shard_id": shard_id}]))
    cp = tasks.SCRATCH / tasks.RUN_ID / "cytotable" / f"{shard_id}.parquet"
    me = tasks.RUN_DIR / "profiles" / "morphem" / "raw" / f"{shard_id}.parquet"
    crops = tasks.RUN_DIR / "crops" / "cells" / f"{shard_id}.parquet"
    sqlite = tasks.SCRATCH / tasks.RUN_ID / shard_id / "output" / "DefaultDB.sqlite"
    cp.parent.mkdir(parents=True)
    me.parent.mkdir(parents=True)
    pd.DataFrame(
        {
            "Metadata_cell_id": ["cell-1"],
            "Image_Metadata_Experiment": ["HRCE-1"],
            "Image_Metadata_Plate": ["1"],
            "Cells_AreaShape_Area": [1.0],
        }
    ).to_parquet(cp)
    pd.DataFrame(
        {
            "Metadata_cell_id": ["cell-1"],
            "Metadata_experiment": ["HRCE-1"],
            "Metadata_plate": ["1"],
            "Morphem_c1_d0": [0.5],
        }
    ).to_parquet(me)
    for path in (crops, sqlite):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"valid fixture placeholder")
    selected = (
        tasks.RUN_DIR
        / "profiles"
        / "cellprofiler"
        / "feature_selected"
        / "experiment=HRCE-1"
        / "plate=1"
        / "profiles.parquet"
    )
    return shard_id, me, selected


def _fake_finalize_profile(tasks, profiler: str, selection: pd.DataFrame) -> Path:
    """Write a one-cell normalized + feature-selected partition; return path."""
    selected = pd.DataFrame(
        {
            "Metadata_cell_id": ["cell-1"],
            "Metadata_Experiment": ["HRCE-1"],
            "Metadata_Plate": ["1"],
        }
    )
    part = tasks.RUN_DIR / "profiles" / profiler
    for stage in ("normalized", "feature_selected"):
        path = part / stage / "experiment=HRCE-1" / "plate=1" / "profiles.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        selected.to_parquet(path, index=False)
    return (
        part / "feature_selected" / "experiment=HRCE-1" / "plate=1" / "profiles.parquet"
    )


def test_cytotable_id_failure_preserves_existing_shard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from rerx import cytotable, validate

    tasks = _load_tasks(monkeypatch, tmp_path)
    shard_id, _me, _selected = _stage_one_plate_fixture(tasks, "HRCE-1-Plate1")
    dest = tasks._shard_profiles_path(shard_id)
    original = dest.read_bytes()
    monkeypatch.setattr(
        validate, "check_sqlite_integrity", lambda _path: SimpleNamespace(passed=True)
    )

    def convert(_source: Path, target: Path) -> Path:
        pd.DataFrame({"new": [1]}).to_parquet(target, index=False)
        return target

    def fail_ids(_frame: pd.DataFrame) -> pd.DataFrame:
        raise ValueError("bad cell identifiers")

    monkeypatch.setattr(cytotable, "convert_sqlite_to_parquet", convert)
    monkeypatch.setattr(cytotable, "add_cell_ids", fail_ids)
    with pytest.raises(ValueError, match="bad cell identifiers"):
        tasks.cmd_cytotable(shard_id)
    assert dest.read_bytes() == original


def test_plate_finalize_and_complete_write_success_only_at_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from rerx import catalog
    from rerx.catalog import CatalogBuildResult, CatalogTableResult
    from rerx.run_summary import PlateSummary

    tasks = _load_tasks(monkeypatch, tmp_path)
    plate_id = "HRCE-1-Plate1"
    _shard_id, _me, _selected = _stage_one_plate_fixture(tasks, plate_id)
    # Real hashable container files, so run.json records real hashes.
    tasks.SIF.parent.mkdir(parents=True, exist_ok=True)
    tasks.SIF.write_bytes(b"cellprofiler sif")
    tasks.MORPHEM_SIF.write_bytes(b"morphem sif")
    monkeypatch.setattr(
        tasks, "_validate_run_streaming", lambda *_args, **_kwargs: {"passed": True}
    )

    def fake_finalize(profiles, profiler, buscar, selection):
        key, frame = next(profiles)
        assert key == ("HRCE-1", "1")
        assert len(frame) == 1
        _fake_finalize_profile(tasks, profiler, selection)
        return [
            PlateSummary(
                experiment="HRCE-1",
                plate="1",
                profiler=profiler,
                n_cells_input=1,
                n_cells_flagged_outlier=0,
                n_cells_normalized=1,
                n_feature_selected_cols=1,
                control_separation_passed=True,
                control_separation_skipped=False,
                control_separation_median_effect_size=1.0,
                buscar_status="scored",
                buscar_skipped_reason=None,
            )
        ]

    monkeypatch.setattr(tasks, "_finalize_profiles", fake_finalize)
    tasks.cmd_finalize_plate(plate_id)
    output = capsys.readouterr().out
    stages = [
        "start",
        "validate_start",
        "validate_done",
        "cellprofiler_load_start",
        "cellprofiler_load_done",
        "cellprofiler_write_raw_start",
        "cellprofiler_write_raw_done",
        "cellprofiler_finalize_start",
        "cellprofiler_finalize_done",
        "morphem_load_start",
        "morphem_load_done",
        "morphem_finalize_start",
        "morphem_finalize_done",
        "fusion_load_start",
        "fusion_load_done",
        "fusion_start",
        "fusion_done",
        "fusion_write_start",
        "fusion_write_done",
        "summary_write_start",
        "done",
    ]
    positions = [output.index(f"plate={plate_id} stage={stage} ") for stage in stages]
    assert positions == sorted(positions)
    assert "rows=1 columns=" in output
    assert not (tasks.RUN_DIR / "_SUCCESS").exists()
    assert not (tasks.RUN_DIR / "run.json").exists()
    assert (tasks.RUN_DIR / "qc" / "plates" / f"{plate_id}.json").is_file()

    def fake_catalog(*, run_root, catalog_path, data_path):
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text("test catalog")
        return CatalogBuildResult(
            catalog_path=catalog_path,
            data_path=data_path,
            tables=[
                CatalogTableResult(table="crops", files=[catalog_path], row_count=1),
                CatalogTableResult(
                    table="fused_feature_selected", files=[catalog_path], row_count=2
                ),
            ],
            skipped_tables=[],
        )

    monkeypatch.setattr(catalog, "build_run_catalog", fake_catalog)
    tasks.cmd_complete_full()
    completion_output = capsys.readouterr().out
    completion_stages = [
        "start",
        "validate_shards_start",
        "validate_shards_done",
        "validate_plates_start",
        "validate_plates_done",
        "write_summaries_start",
        "write_summaries_done",
        "catalog_start",
        "catalog_done",
        "run_json_written",
        "success_marker_written",
    ]
    positions = [
        completion_output.index(f"complete_full stage={stage} ")
        for stage in completion_stages
    ]
    assert positions == sorted(positions)
    assert (tasks.RUN_DIR / "_SUCCESS").read_text() == tasks.RUN_ID + "\n"
    summary = json.loads((tasks.RUN_DIR / "run_summary.json").read_text())
    assert summary["totals"]["plates_finalized"] == 2
    run_json = json.loads((tasks.RUN_DIR / "run.json").read_text())
    assert run_json["status"] == "success"
    assert run_json["catalog_row_counts"] == {
        "crops": 1,
        "fused_feature_selected": 2,
    }
    assert run_json["crop_count"] == 1
    assert run_json["cell_count"] == 2  # fused cell rows, not the crops table
    assert run_json["cellprofiler_pipeline_hash"] == _hash(tasks._pipeline_path())
    assert run_json["cellprofiler_container_hash"] == _hash(tasks.SIF)
    assert run_json["morphem_container_hash"] == _hash(tasks.MORPHEM_SIF)
    assert run_json["morphem_model_revision"] == _MORPHEM_MODEL_REVISION
    assert run_json["pycytominer_version"] == _dist_version("pycytominer")
    assert run_json["buscar_version"] == _dist_version("buscar")
    assert run_json["cellprofiler_version"] is None  # from container at run time


def test_complete_full_crash_between_run_json_and_marker_keeps_run_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crash after run.json but before _SUCCESS must not claim success."""
    from rerx import catalog
    from rerx.catalog import CatalogBuildResult, CatalogTableResult
    from rerx.runs import Run

    tasks = _load_tasks(monkeypatch, tmp_path)
    plate_id = "HRCE-1-Plate1"
    _shard_id, _me, _selected = _stage_one_plate_fixture(tasks, plate_id)
    monkeypatch.setattr(
        tasks, "_validate_run_streaming", lambda *_args, **_kwargs: {"passed": True}
    )

    def fake_finalize(profiles, profiler, buscar, selection):
        next(profiles)
        _fake_finalize_profile(tasks, profiler, selection)
        from rerx.run_summary import PlateSummary

        return [
            PlateSummary(
                experiment="HRCE-1",
                plate="1",
                profiler=profiler,
                n_cells_input=1,
                n_cells_flagged_outlier=0,
                n_cells_normalized=1,
                n_feature_selected_cols=1,
                control_separation_passed=True,
                control_separation_skipped=False,
                control_separation_median_effect_size=1.0,
                buscar_status="scored",
                buscar_skipped_reason=None,
            )
        ]

    monkeypatch.setattr(tasks, "_finalize_profiles", fake_finalize)
    tasks.cmd_finalize_plate(plate_id)

    def fake_catalog(*, run_root, catalog_path, data_path):
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text("test catalog")
        return CatalogBuildResult(
            catalog_path=catalog_path,
            data_path=data_path,
            tables=[
                CatalogTableResult(table="crops", files=[catalog_path], row_count=1)
            ],
            skipped_tables=[],
        )

    monkeypatch.setattr(catalog, "build_run_catalog", fake_catalog)
    # Simulate the process dying right after run.json is written but
    # before _SUCCESS: the marker write raises as if the task crashed.
    real_write_text = Path.write_text

    def crash_on_success_marker(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if self.name == "_SUCCESS":
            raise OSError("simulated crash before _SUCCESS")
        return real_write_text(self, data, encoding, errors, newline)

    monkeypatch.setattr(Path, "write_text", crash_on_success_marker)
    with pytest.raises(OSError, match="simulated crash"):
        tasks.cmd_complete_full()
    assert not (tasks.RUN_DIR / "_SUCCESS").exists()
    run_json = json.loads((tasks.RUN_DIR / "run.json").read_text())
    assert run_json["status"] != "success"
    run = Run(run_id=tasks.RUN_ID, root=tasks.RUN_DIR)
    assert run.status() != "success"


def test_complete_full_retry_after_marker_clears_stale_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A retry must clear _SUCCESS before rebuilding; a failed rebuild must
    not leave a stale success marker beside a removed/partial catalog."""
    from rerx import catalog
    from rerx.catalog import CatalogBuildResult, CatalogTableResult
    from rerx.runs import Run

    tasks = _load_tasks(monkeypatch, tmp_path)
    plate_id = "HRCE-1-Plate1"
    _shard_id, _me, _selected = _stage_one_plate_fixture(tasks, plate_id)
    monkeypatch.setattr(
        tasks, "_validate_run_streaming", lambda *_args, **_kwargs: {"passed": True}
    )
    from rerx.run_summary import PlateSummary

    def fake_finalize(profiles, profiler, buscar, selection):
        next(profiles)
        _fake_finalize_profile(tasks, profiler, selection)
        return [
            PlateSummary(
                experiment="HRCE-1",
                plate="1",
                profiler=profiler,
                n_cells_input=1,
                n_cells_flagged_outlier=0,
                n_cells_normalized=1,
                n_feature_selected_cols=1,
                control_separation_passed=True,
                control_separation_skipped=False,
                control_separation_median_effect_size=1.0,
                buscar_status="scored",
                buscar_skipped_reason=None,
            )
        ]

    monkeypatch.setattr(tasks, "_finalize_profiles", fake_finalize)
    tasks.cmd_finalize_plate(plate_id)

    def _result(catalog_path, data_path):
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text("test catalog")
        return CatalogBuildResult(
            catalog_path=catalog_path,
            data_path=data_path,
            tables=[
                CatalogTableResult(table="crops", files=[catalog_path], row_count=1)
            ],
            skipped_tables=[],
        )

    def passing_catalog(*, run_root, catalog_path, data_path):
        return _result(catalog_path, data_path)

    def failing_catalog(*, run_root, catalog_path, data_path):
        # Real build_run_catalog unlinks an existing catalog before rebuilding.
        if catalog_path.exists():
            catalog_path.unlink()
        raise OSError("catalog write failed on retry")

    # 1. First completion succeeds: _SUCCESS + catalog + finalized run.json.
    monkeypatch.setattr(catalog, "build_run_catalog", passing_catalog)
    tasks.cmd_complete_full()
    assert (tasks.RUN_DIR / "_SUCCESS").is_file()
    assert (tasks.RUN_DIR / "catalog" / "run.ducklake").is_file()
    run = Run(run_id=tasks.RUN_ID, root=tasks.RUN_DIR)
    assert run.status() == "success"

    # 2. Retry with a failing catalog rebuild: the stale marker must be gone
    #    before the rebuild (and remain gone after its failure).
    monkeypatch.setattr(catalog, "build_run_catalog", failing_catalog)
    with pytest.raises(OSError, match="catalog write failed on retry"):
        tasks.cmd_complete_full()
    assert not (tasks.RUN_DIR / "_SUCCESS").exists()
    assert not (tasks.RUN_DIR / "catalog" / "run.ducklake").exists()
    assert run.status() == "incomplete"


def test_complete_full_rejects_missing_artifacts_and_unplanned_shards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from rerx import catalog

    tasks = _load_tasks(monkeypatch, tmp_path)
    plate_id = "HRCE-1-Plate1"
    shard_id, me, selected = _stage_one_plate_fixture(tasks, plate_id)
    monkeypatch.setattr(
        tasks, "_validate_run_streaming", lambda *_args, **_kwargs: {"passed": True}
    )
    from rerx.run_summary import PlateSummary

    def fake_finalize(profiles, profiler, buscar, selection):
        next(profiles)
        _fake_finalize_profile(tasks, profiler, selection)
        return [
            PlateSummary(
                experiment="HRCE-1",
                plate="1",
                profiler=profiler,
                n_cells_input=1,
                n_cells_flagged_outlier=0,
                n_cells_normalized=1,
                n_feature_selected_cols=1,
                control_separation_passed=True,
                control_separation_skipped=False,
                control_separation_median_effect_size=1.0,
                buscar_status="scored",
                buscar_skipped_reason=None,
            )
        ]

    monkeypatch.setattr(tasks, "_finalize_profiles", fake_finalize)
    tasks.cmd_finalize_plate(plate_id)

    def fake_catalog(*, run_root, catalog_path, data_path):
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text("test catalog")

    monkeypatch.setattr(catalog, "build_run_catalog", fake_catalog)
    # A missing morphem shard must block completion.
    me.unlink()
    with pytest.raises(ValueError, match="morphem"):
        tasks.cmd_complete_full()
    assert not (tasks.RUN_DIR / "_SUCCESS").exists()
    me.write_bytes(b"restored for completion check")
    # A shard in shards.json that no plate plan covers must block completion.
    (tasks.RUN_DIR / "shards.json").write_text(
        json.dumps([{"shard_id": shard_id}, {"shard_id": f"{plate_id}-0001"}])
    )
    with pytest.raises(ValueError, match="plate plans omit"):
        tasks.cmd_complete_full()
    (tasks.RUN_DIR / "shards.json").write_text(json.dumps([{"shard_id": shard_id}]))
    # A deleted feature-selected profile must block completion.
    selected.unlink()
    with pytest.raises(ValueError, match="missing cellprofiler feature_selected"):
        tasks.cmd_complete_full()
    assert not (tasks.RUN_DIR / "_SUCCESS").exists()


def test_full_plate_finalization_runs_real_analysis_on_small_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise real per-plate normalization, buscar, fusion, and catalog."""
    import numpy as np

    tasks = _load_tasks(monkeypatch, tmp_path)
    fixture_spec = importlib.util.spec_from_file_location(
        "finalize_fixture", Path(__file__).with_name("test_finalize.py")
    )
    assert fixture_spec and fixture_spec.loader
    fixtures = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixtures)
    cp = fixtures._plate_raw_profiles().drop(
        columns=["_disease_condition", "_treatment", "_treatment_conc"]
    )
    meta = fixtures._site_metadata()
    cells = len(cp)
    rng = np.random.default_rng(42)
    me = cp[["Metadata_cell_id"]].copy()
    for name, column in (
        ("experiment", "Image_Metadata_Experiment"),
        ("plate", "Image_Metadata_Plate"),
        ("well", "Image_Metadata_Well"),
        ("site", "Image_Metadata_Site"),
    ):
        me[f"Metadata_{name}"] = cp[column].to_numpy()
    for dim in range(8):
        shift = (cp["Image_Metadata_Well"] != "A01").to_numpy(dtype=float)
        me[f"Morphem_c1_d{dim}"] = shift * 3 + rng.normal(0, 1, cells)

    plate_id, shard_id = "HRCE-1-Plate25", "HRCE-1-Plate25-0000"
    plan = tasks.RUN_DIR / "plate-plans" / f"{plate_id}.json"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        json.dumps(
            {
                "experiment": "HRCE-1",
                "plate": "25",
                "shard_ids": [shard_id],
                "sites": meta.to_dict("records"),
            }
        )
    )
    (tasks.RUN_DIR / "shards.json").write_text(json.dumps([{"shard_id": shard_id}]))
    cp_path = tasks.SCRATCH / tasks.RUN_ID / "cytotable" / f"{shard_id}.parquet"
    me_path = tasks.RUN_DIR / "profiles" / "morphem" / "raw" / f"{shard_id}.parquet"
    crop_path = tasks.RUN_DIR / "crops" / "cells" / f"{shard_id}.parquet"
    sqlite_path = (
        tasks.SCRATCH / tasks.RUN_ID / shard_id / "output" / "DefaultDB.sqlite"
    )
    for path in (cp_path, me_path, crop_path, sqlite_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    cp.to_parquet(cp_path, index=False)
    me.to_parquet(me_path, index=False)
    from rerx.crops import encode_jpeg

    jpeg = encode_jpeg(np.zeros((96, 96), dtype=np.uint8))
    crop_frame = pd.DataFrame({"Metadata_cell_id": cp["Metadata_cell_id"]})
    for channel in range(1, 6):
        crop_frame[f"crop_w{channel}_jpeg"] = jpeg
    crop_frame.to_parquet(crop_path, index=False)
    sqlite_path.write_bytes(b"synthetic fixture; validation tested separately")
    monkeypatch.setattr(
        tasks, "_validate_run_streaming", lambda *_args, **_kwargs: {"passed": True}
    )

    tasks.cmd_finalize_plate(plate_id)
    fused = pd.read_parquet(
        tasks.RUN_DIR
        / "profiles"
        / "fused"
        / "feature_selected"
        / "experiment=HRCE-1"
        / "plate=25"
        / "profiles.parquet"
    )
    assert len(fused) == cells
    assert "Metadata_cell_id" in fused.columns
    summary = json.loads(
        (tasks.RUN_DIR / "qc" / "plates" / f"{plate_id}.json").read_text()
    )
    assert {p["profiler"] for p in summary["plates"]} == {"cellprofiler", "morphem"}
    assert summary["fusion"]["rows"] == cells
    with monkeypatch.context() as patcher:
        patcher.setattr(
            "rerx.catalog.build_run_catalog",
            Mock(side_effect=OSError("catalog write failed")),
        )
        with pytest.raises(OSError, match="catalog write failed"):
            tasks.cmd_complete_full()
    assert (
        not (tasks.RUN_DIR / "_SUCCESS").exists()
        and not (tasks.RUN_DIR / "run.json").exists()
    )
    pd.testing.assert_frame_equal(
        pd.read_parquet(
            tasks.RUN_DIR
            / "profiles/fused/feature_selected"
            / "experiment=HRCE-1/plate=25/profiles.parquet"
        ),
        fused,
    )
    # Retry only completion, reusing the already finalized plate.
    tasks.cmd_complete_full()
    assert json.loads((tasks.RUN_DIR / "run.json").read_text())["status"] == "success"
    assert (tasks.RUN_DIR / "_SUCCESS").is_file() and (
        tasks.RUN_DIR / "catalog" / "run.ducklake"
    ).is_file()


def test_full_completion_waits_for_every_plate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = _load_tasks(monkeypatch, tmp_path)
    plans = tasks.RUN_DIR / "plate-plans"
    plans.mkdir(parents=True)
    for plate_id in ("HRCE-1-Plate1", "HRCE-1-Plate2"):
        plans.joinpath(f"{plate_id}.json").write_text("{}")
    with pytest.raises(ValueError, match=r"missing.*Plate"):
        tasks.cmd_complete_full()
    assert not (tasks.RUN_DIR / "_SUCCESS").exists()


def test_publish_requires_completed_upstream_stages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = _load_tasks(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError, match="run_summary"):
        tasks.cmd_publish()
    assert not (tasks.RUN_DIR / "_SUCCESS").exists()


def test_publish_writes_run_json_with_counts_before_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Publish must record catalog counts in run.json before _SUCCESS."""
    from rerx import catalog
    from rerx.catalog import CatalogBuildResult, CatalogTableResult

    tasks = _load_tasks(monkeypatch, tmp_path)
    tasks.RUN_DIR.mkdir(parents=True)
    (tasks.RUN_DIR / "run_summary.json").write_text("{}")
    baseline = (
        tasks.RUN_DIR
        / "baseline"
        / "recursion_site_embeddings"
        / "recursion_site_embeddings.parquet"
    )
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(b"embeddings")
    for profiler in ("cellprofiler", "morphem"):
        scores = tasks.RUN_DIR / "projection" / profiler / "p" / "scores.parquet"
        scores.parent.mkdir(parents=True)
        scores.write_bytes(b"scores")

    def fake_catalog(*, run_root, catalog_path, data_path):
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text("test catalog")
        return CatalogBuildResult(
            catalog_path=catalog_path,
            data_path=data_path,
            tables=[
                CatalogTableResult(
                    table="recursion_site_embeddings", files=[catalog_path], row_count=3
                )
            ],
            skipped_tables=[],
        )

    monkeypatch.setattr(catalog, "build_run_catalog", fake_catalog)
    tasks.cmd_publish()
    run_json = json.loads((tasks.RUN_DIR / "run.json").read_text())
    assert run_json["status"] == "success"
    assert run_json["catalog_row_counts"] == {"recursion_site_embeddings": 3}
    assert (tasks.RUN_DIR / "_SUCCESS").is_file()


def test_planned_shards_require_all_four_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = _load_tasks(monkeypatch, tmp_path)
    expected = {"shard-0000", "shard-0001"}
    cp = [tmp_path / "cytotable" / f"{sid}.parquet" for sid in expected]
    crops = [tmp_path / "crops" / f"{sid}.parquet" for sid in expected]
    morphem = [tmp_path / "morphem" / f"{sid}.parquet" for sid in expected]
    sqlite = [tmp_path / sid / "output" / "DefaultDB.sqlite" for sid in expected]
    for path in [*cp, *crops, *morphem, *sqlite]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
    tasks._verify_planned_shards(expected, cp, crops, morphem, sqlite)
    crops[0].unlink()
    with pytest.raises(ValueError, match=r"crops.*missing"):
        tasks._verify_planned_shards(expected, cp, crops[1:], morphem, sqlite)
    crops[0].write_bytes(b"data")
    cp[0].write_bytes(b"")
    with pytest.raises(ValueError, match=r"cytotable.*empty"):
        tasks._verify_planned_shards(expected, cp, crops, morphem, sqlite)

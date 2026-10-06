"""
Tests for the cellprofiler module.
"""

from pathlib import Path

import pytest

from rerx.cellprofiler import (
    CellProfilerResult,
    ImageSetShard,
    cellprofiler_command,
    cellprofiler_version_command,
    collect_cellprofiler_outputs,
    container_hash,
    pipeline_hash,
    run_cellprofiler_shard,
    shard_image_sets,
    stage_shard_images,
)
from rerx.metadata import ImageSetID


def make_ids(experiment: str, plate: str, well: str, site: int) -> ImageSetID:
    return ImageSetID(experiment=experiment, plate=plate, well=well, site=site)


def test_shard_image_sets_groups_by_plate_and_chunks() -> None:
    sites = [make_ids("HRCE-1", "25", f"A{i:02d}", 1) for i in range(1, 6)]
    sites += [make_ids("HRCE-1", "26", "B01", 1)]
    shards = shard_image_sets(sites, shard_size=2)
    # Plate 25: 5 sites -> chunks of 2,2,1; Plate 26: 1 site -> chunk of 1.
    shard_ids = [s.shard_id for s in shards]
    assert shard_ids == [
        "HRCE-1-Plate25-0000",
        "HRCE-1-Plate25-0001",
        "HRCE-1-Plate25-0002",
        "HRCE-1-Plate26-0000",
    ]
    assert [len(s.image_sets) for s in shards] == [2, 2, 1, 1]
    # Order preserved within a plate.
    assert shards[0].image_sets[0].well == "A01"
    assert shards[0].image_sets[1].well == "A02"


def test_shard_image_sets_rejects_non_positive_shard_size() -> None:
    with pytest.raises(ValueError, match="shard_size must be positive"):
        shard_image_sets([make_ids("HRCE-1", "25", "A01", 1)], shard_size=0)


def test_shard_image_sets_empty_input() -> None:
    assert shard_image_sets([]) == []


def test_shard_task_dir() -> None:
    shard = ImageSetShard(shard_id="HRCE-1-Plate25-0000", image_sets=[])
    task_dir = shard.task_dir(Path("/scratch"), "rxrx19a-pilot-x-gabc")
    assert task_dir == Path("/scratch/rxrx19a-pilot-x-gabc/HRCE-1-Plate25-0000")


def _write_source_image(source_root: Path, ids: ImageSetID, channel: int) -> Path:
    path = source_root / ids.image_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-png-bytes")
    return path


def test_stage_shard_images_creates_symlinks(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    ids = make_ids("HRCE-1", "25", "A01", 1)
    for channel in (1, 2, 3, 4, 5):
        _write_source_image(source_root, ids, channel)
    shard = ImageSetShard(shard_id="HRCE-1-Plate25-0000", image_sets=[ids])
    task_dir = tmp_path / "scratch" / "run" / shard.shard_id

    images_dir = stage_shard_images(shard, source_root, task_dir)

    assert images_dir == task_dir / "images"
    for channel in (1, 2, 3, 4, 5):
        linked = images_dir / ids.image_path(channel)
        assert linked.is_symlink()
        assert linked.read_bytes() == b"fake-png-bytes"


def test_stage_shard_images_idempotent(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    ids = make_ids("HRCE-1", "25", "A01", 1)
    for channel in (1, 2, 3, 4, 5):
        _write_source_image(source_root, ids, channel)
    shard = ImageSetShard(shard_id="HRCE-1-Plate25-0000", image_sets=[ids])
    task_dir = tmp_path / "scratch" / "run" / shard.shard_id

    stage_shard_images(shard, source_root, task_dir)
    # Second call must not raise (dest already exists).
    images_dir = stage_shard_images(shard, source_root, task_dir)
    assert (images_dir / ids.image_path(1)).is_symlink()


def test_stage_shard_images_missing_file_raises(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    ids = make_ids("HRCE-1", "25", "A01", 1)
    # Only channel 1 exists; channel 2 is missing.
    _write_source_image(source_root, ids, 1)
    shard = ImageSetShard(shard_id="HRCE-1-Plate25-0000", image_sets=[ids])
    task_dir = tmp_path / "scratch" / "run" / shard.shard_id

    with pytest.raises(FileNotFoundError, match="missing staged source image"):
        stage_shard_images(shard, source_root, task_dir)


def test_stage_shard_images_rejects_dotfile_task_dir(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    ids = make_ids("HRCE-1", "25", "A01", 1)
    for channel in (1, 2, 3, 4, 5):
        _write_source_image(source_root, ids, channel)
    shard = ImageSetShard(shard_id="HRCE-1-Plate25-0000", image_sets=[ids])
    task_dir = tmp_path / ".cache" / "scratch" / "run" / shard.shard_id

    with pytest.raises(ValueError, match="dot-prefixed path component"):
        stage_shard_images(shard, source_root, task_dir)


def test_cellprofiler_command_apptainer(tmp_path: Path) -> None:
    pipeline_path = tmp_path / "pipelines" / "rxrx19a.cppipe"
    pipeline_path.parent.mkdir(parents=True)
    pipeline_path.write_text("CellProfiler Pipeline\n")
    command = cellprofiler_command(
        images_dir=Path("/scratch/run/shard/images"),
        output_dir=Path("/scratch/run/shard/output"),
        pipeline_path=pipeline_path,
        container_image="containers/cellprofiler.sif",
        extra_binds=(Path("/peta/source-cache/rxrx19a"),),
        runtime="apptainer",
    )
    assert command[:3] == ["apptainer", "run", "--cleanenv"]
    assert "containers/cellprofiler.sif" in command
    assert "--run" in command and "--run-headless" in command
    assert command[command.index("-p") + 1] == str(pipeline_path.resolve())
    assert command[command.index("-i") + 1] == "/scratch/run/shard/images"
    assert command[command.index("-o") + 1] == "/scratch/run/shard/output"
    # Both the shard dirs, the pipeline's directory, and the extra source
    # bind must all be present.
    assert "--bind" in command
    joined = " ".join(command)
    assert "/scratch/run/shard/images:/scratch/run/shard/images" in joined
    assert "/scratch/run/shard/output:/scratch/run/shard/output" in joined
    assert "/peta/source-cache/rxrx19a:/peta/source-cache/rxrx19a" in joined
    assert (
        f"{pipeline_path.parent.resolve()}:{pipeline_path.parent.resolve()}" in joined
    )


def test_cellprofiler_command_docker(tmp_path: Path) -> None:
    pipeline_path = tmp_path / "rxrx19a.cppipe"
    pipeline_path.write_text("CellProfiler Pipeline\n")
    command = cellprofiler_command(
        images_dir=Path("/scratch/images"),
        output_dir=Path("/scratch/output"),
        pipeline_path=pipeline_path,
        container_image="cellprofiler/cellprofiler:4.2.8",
        runtime="docker",
    )
    assert command[:3] == ["docker", "run", "--rm"]
    assert "cellprofiler/cellprofiler:4.2.8" in command
    assert "--run" in command and "--run-headless" in command


def test_cellprofiler_version_command() -> None:
    apptainer_cmd = cellprofiler_version_command(
        "containers/cellprofiler.sif",
        runtime="apptainer",
    )
    assert apptainer_cmd == [
        "apptainer",
        "run",
        "--cleanenv",
        "containers/cellprofiler.sif",
        "--version",
    ]
    docker_cmd = cellprofiler_version_command(
        "cellprofiler/cellprofiler:4.2.8",
        runtime="docker",
    )
    assert docker_cmd == [
        "docker",
        "run",
        "--rm",
        "cellprofiler/cellprofiler:4.2.8",
        "--version",
    ]


def test_collect_cellprofiler_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "cells.sqlite").write_bytes(b"")
    (output_dir / "A01_s1_w1_MaskNuclei.tiff").write_bytes(b"")
    (output_dir / "A01_s1_w1_MaskCells.tiff").write_bytes(b"")
    (output_dir / "A01_s1_w1_MaskCytoplasm.tiff").write_bytes(b"")
    (output_dir / "A01_s1_w1.png").write_bytes(b"")  # not a mask; ignored

    sqlite_path, mask_paths = collect_cellprofiler_outputs(output_dir)

    assert sqlite_path == output_dir / "cells.sqlite"
    assert len(mask_paths) == 3
    assert all("_Mask" in p.name for p in mask_paths)


def test_collect_cellprofiler_outputs_missing_sqlite(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    sqlite_path, mask_paths = collect_cellprofiler_outputs(output_dir)
    assert sqlite_path is None
    assert mask_paths == []


def test_run_cellprofiler_shard_stages_and_records_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Full orchestration wiring, with the container call replaced by a stub
    subprocess.run so this test runs without Docker/Apptainer or a network.
    """
    source_root = tmp_path / "source"
    ids = make_ids("HRCE-1", "25", "A01", 1)
    for channel in (1, 2, 3, 4, 5):
        _write_source_image(source_root, ids, channel)
    shard = ImageSetShard(shard_id="HRCE-1-Plate25-0000", image_sets=[ids])
    scratch_root = tmp_path / "scratch"

    calls = []

    class FakeCompletedProcess:
        returncode = 0
        stdout = "ran ok"
        stderr = ""

    def fake_run(command, capture_output, text, timeout, check):
        calls.append(command)
        # Simulate CellProfiler writing its outputs.
        output_dir = Path(command[command.index("-o") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "cells.sqlite").write_bytes(b"")
        (output_dir / "A01_s1_w1_MaskNuclei.tiff").write_bytes(b"")
        return FakeCompletedProcess()

    monkeypatch.setattr("rerx.cellprofiler.subprocess.run", fake_run)

    result = run_cellprofiler_shard(
        shard=shard,
        source_root=source_root,
        scratch_root=scratch_root,
        run_id="rxrx19a-pilot-test-gabc",
        runtime="apptainer",
    )

    assert len(calls) == 1
    assert isinstance(result, CellProfilerResult)
    assert result.returncode == 0
    assert result.sqlite_path is not None
    assert result.sqlite_path.name == "cells.sqlite"
    assert len(result.mask_paths) == 1
    assert result.log_path.exists()
    assert "ran ok" in result.log_path.read_text()


def test_pipeline_hash_and_container_hash(tmp_path: Path) -> None:
    pipeline = tmp_path / "rxrx19a.cppipe"
    pipeline.write_text("CellProfiler Pipeline\n")
    sif = tmp_path / "cellprofiler.sif"
    sif.write_bytes(b"fake-sif-bytes")

    ph = pipeline_hash(pipeline)
    ch = container_hash(sif)

    assert len(ph) == 64
    assert len(ch) == 64
    assert ph != ch

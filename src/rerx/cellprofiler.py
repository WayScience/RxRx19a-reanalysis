"""
CellProfiler execution for ReRx: sharding, staging, container invocation.

Implements plan.md sections 11-12: build shards of nearby image sets, stage
their source PNGs into a task scratch directory, run the pinned
``pipelines/rxrx19a.cppipe`` pipeline through the Apptainer/Singularity
container on Alpine (or Docker locally for testing), and collect the
per-shard SQLite database and mask TIFFs it writes.

CellProfiler's headless CLI (``cellprofiler --run --run-headless``) takes a
plain input directory via ``-i`` and recursively processes every image file
under it (see ``cellprofiler/__main__.py``); there is no shard file-list
flag. So each shard gets its own task directory holding only that shard's
images, built with symlinks back to the shared source cache.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from rerx.manifest import sha256_file
from rerx.metadata import CHANNELS, ImageSetID

# Container image path/tag; overridden per environment (Alpine .sif path,
# or a Docker tag for local testing).
CELLPROFILER_CONTAINER_IMAGE = "containers/cellprofiler.sif"
CELLPROFILER_PIPELINE = Path("pipelines/rxrx19a.cppipe")

Runtime = Literal["apptainer", "docker"]


@dataclass(frozen=True)
class ImageSetShard:
    """
    One shard of nearby image sets for a single CellProfiler task.

    Attributes
    ----------
    shard_id : str
        Stable identifier, e.g. ``HRCE-1-Plate25-0000``.
    image_sets : list[ImageSetID]
        Sites in this shard, in the order they will be staged.
    """

    shard_id: str
    image_sets: list[ImageSetID] = field(default_factory=list)

    def task_dir(self, scratch_root: Path, run_id: str) -> Path:
        """Task scratch directory for this shard (plan.md section 12)."""
        return Path(scratch_root) / run_id / self.shard_id


def shard_image_sets(
    image_sets: Iterable[ImageSetID],
    shard_size: int = 24,
) -> list[ImageSetShard]:
    """
    Group image sets into shards of nearby sites (plan.md section 12).

    Sites are grouped by ``(experiment, plate)`` first, preserving data
    locality, then chunked into shards of at most ``shard_size`` sites
    each. Order is deterministic: sites are not reordered within a plate.

    Parameters
    ----------
    image_sets : Iterable[ImageSetID]
        Sites to shard, typically the pilot selection.
    shard_size : int
        Maximum sites per shard. Tune during the pilot (plan.md section 12
        says prefer jobs of tens of minutes, not one-per-image or
        multi-hour monoliths).

    Returns
    -------
    list[ImageSetShard]
        Shards in ``(experiment, plate, chunk index)`` order.

    Raises
    ------
    ValueError
        If ``shard_size`` is not positive.
    """
    if shard_size <= 0:
        raise ValueError(f"shard_size must be positive, got {shard_size}")
    by_plate: dict[tuple[str, str], list[ImageSetID]] = {}
    for ids in image_sets:
        by_plate.setdefault((ids.experiment, ids.plate), []).append(ids)

    shards: list[ImageSetShard] = []
    for experiment, plate in sorted(by_plate):
        sites = by_plate[(experiment, plate)]
        for chunk_index, start in enumerate(range(0, len(sites), shard_size)):
            chunk = sites[start : start + shard_size]
            shard_id = f"{experiment}-Plate{plate}-{chunk_index:04d}"
            shards.append(ImageSetShard(shard_id=shard_id, image_sets=chunk))
    return shards


def stage_shard_images(
    shard: ImageSetShard,
    source_root: Path,
    task_dir: Path,
    channels: Iterable[int] = CHANNELS,
) -> Path:
    """
    Symlink a shard's source PNGs into its task directory (plan.md section 12).

    Preserves the RxRx19a relative layout
    (``<experiment>/Plate<n>/<well>_s<site>_w<channel>.png``) under
    ``<task_dir>/images`` so the pipeline's metadata regular expressions
    (which extract Experiment/Plate from the folder path) still match.

    Symlinks, not copies: cheap and idempotent, but both ``source_root``
    and ``task_dir`` must be bound into the container (see
    :func:`cellprofiler_command`) or the links will not resolve.

    The pipeline's Images module filters out any path with a dot-prefixed
    directory component (``directory doesnot containregexp "[\\\\/]\\."``,
    e.g. ``.cache``); confirmed against the real container that this
    silently drops every image and yields zero image sets. ``task_dir``
    must not sit under a dotfile directory.

    Parameters
    ----------
    shard : ImageSetShard
        Shard to stage.
    source_root : Path
        Root of the staged/cached RxRx19a source images.
    task_dir : Path
        Task scratch directory (``<scratch>/<run_id>/<shard_id>``).
    channels : Iterable[int]
        Channels to link per site.

    Returns
    -------
    Path
        The staged images directory (``<task_dir>/images``).

    Raises
    ------
    FileNotFoundError
        If a required source image is missing.
    ValueError
        If ``task_dir`` has a dot-prefixed path component (would be
        silently filtered out by the pipeline's Images module).
    """
    source_root = Path(source_root)
    task_dir = Path(task_dir)
    if any(part.startswith(".") for part in task_dir.resolve().parts):
        raise ValueError(
            f"task_dir has a dot-prefixed path component: {task_dir} "
            "(the pipeline's Images module filters these out, yielding "
            "zero image sets; use a scratch root without dotfile "
            "directories, e.g. not under ~/.cache)"
        )
    images_dir = task_dir / "images"
    for ids in shard.image_sets:
        for channel in channels:
            rel = ids.image_path(channel)
            source = source_root / rel
            if not source.is_file():
                raise FileNotFoundError(f"missing staged source image: {source}")
            dest = images_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_symlink() or dest.exists():
                continue
            dest.symlink_to(source.resolve())
    return images_dir


def cellprofiler_version_command(
    container_image: str = CELLPROFILER_CONTAINER_IMAGE,
    runtime: Runtime = "apptainer",
) -> list[str]:
    """
    Build the container command that prints the pinned CellProfiler version.

    Used to validate the container image (plan.md section 11: "Validate the
    image with ``cellprofiler --version`` on Alpine") and to fill in
    ``run.json``'s ``cellprofiler_version`` field.

    Parameters
    ----------
    container_image : str
        Path to the ``.sif`` (Apptainer) or tag (Docker).
    runtime : Runtime
        Container runtime to target.

    Returns
    -------
    list[str]
        Command tokens.
    """
    if runtime == "apptainer":
        return ["apptainer", "run", "--cleanenv", container_image, "--version"]
    return ["docker", "run", "--rm", container_image, "--version"]


def cellprofiler_command(  # noqa: PLR0913
    images_dir: Path,
    output_dir: Path,
    pipeline_path: Path = CELLPROFILER_PIPELINE,
    container_image: str = CELLPROFILER_CONTAINER_IMAGE,
    extra_binds: Iterable[Path] = (),
    runtime: Runtime = "apptainer",
) -> list[str]:
    """
    Build the headless CellProfiler container command for one shard.

    Mirrors the verified invocation (plan.md section 11 container pattern,
    confirmed end-to-end against the real CellProfiler 4.2.8 container):
    ``--run --run-headless -p <pipeline> -i <images_dir> -o <output_dir>``.

    Parameters
    ----------
    images_dir : Path
        Staged shard images directory (see :func:`stage_shard_images`).
    output_dir : Path
        Directory CellProfiler writes SQLite + mask TIFFs into.
    pipeline_path : Path
        Pinned pipeline file (``pipelines/rxrx19a.cppipe``). Resolved to an
        absolute path and its parent directory is bound into the container
        (a relative path is meaningless once ``-p`` crosses the container
        boundary, and CellProfiler needs the file itself reachable).
    container_image : str
        ``.sif`` path (Apptainer) or tag (Docker).
    extra_binds : Iterable[Path]
        Additional host directories to bind read-write (for example the
        source cache root, so shard symlinks resolve inside the container).
    runtime : Runtime
        Container runtime to target. Alpine uses Apptainer; Docker is for
        local testing only (plan.md section 11 requires Singularity/Apptainer
        on Alpine).

    Returns
    -------
    list[str]
        Command tokens, ready for subprocess or a Slurm job script.
    """
    images_dir = Path(images_dir)
    output_dir = Path(output_dir)
    pipeline_path = Path(pipeline_path).resolve()
    pipeline_args = [
        "--run",
        "--run-headless",
        "-p",
        str(pipeline_path),
        "-i",
        str(images_dir),
        "-o",
        str(output_dir),
    ]
    binds = (images_dir, output_dir, pipeline_path.parent, *extra_binds)
    if runtime == "apptainer":
        command = ["apptainer", "run", "--cleanenv"]
        for bind in binds:
            command += ["--bind", f"{bind}:{bind}"]
        command += [container_image, *pipeline_args]
        return command
    command = ["docker", "run", "--rm"]
    for bind in binds:
        command += ["-v", f"{bind}:{bind}"]
    command += [container_image, *pipeline_args]
    return command


@dataclass
class CellProfilerResult:
    """
    Outcome of running one shard through CellProfiler.

    Attributes
    ----------
    shard : ImageSetShard
        Shard that was processed.
    images_dir : Path
        Staged input directory used for this run.
    output_dir : Path
        Directory CellProfiler wrote outputs into.
    command : list[str]
        Container command that was executed.
    returncode : int
        Process exit code (0 on success).
    log_path : Path
        Combined stdout/stderr log file for the run.
    sqlite_path : Path | None
        Discovered per-shard SQLite database, if any.
    mask_paths : list[Path]
        Discovered mask TIFF outputs.
    """

    shard: ImageSetShard
    images_dir: Path
    output_dir: Path
    command: list[str]
    returncode: int
    log_path: Path
    sqlite_path: Path | None
    mask_paths: list[Path]


def collect_cellprofiler_outputs(output_dir: Path) -> tuple[Path | None, list[Path]]:
    """
    Find the SQLite database and mask TIFFs a shard run produced.

    Parameters
    ----------
    output_dir : Path
        Directory CellProfiler wrote outputs into.

    Returns
    -------
    tuple[Path | None, list[Path]]
        ``(sqlite_path, mask_paths)``. ``sqlite_path`` is ``None`` when no
        ``.sqlite`` file is present. ``mask_paths`` holds every ``*_Mask*.tiff``
        file, sorted, matching the pipeline's SaveImages suffixes
        (``_MaskNuclei``, ``_MaskCells``, ``_MaskCytoplasm``).
    """
    output_dir = Path(output_dir)
    sqlite_files = sorted(output_dir.glob("*.sqlite"))
    sqlite_path = sqlite_files[0] if sqlite_files else None
    mask_paths = sorted(output_dir.glob("*_Mask*.tiff"))
    return sqlite_path, mask_paths


def run_cellprofiler_shard(  # noqa: PLR0913
    shard: ImageSetShard,
    source_root: Path,
    scratch_root: Path,
    run_id: str,
    pipeline_path: Path = CELLPROFILER_PIPELINE,
    container_image: str = CELLPROFILER_CONTAINER_IMAGE,
    runtime: Runtime = "apptainer",
    timeout: int = 3600,
) -> CellProfilerResult:
    """
    Stage, run, and collect outputs for one shard (plan.md sections 12, 23).

    Follows the atomic output rule (plan.md section 23): everything happens
    inside the shard's task directory; the caller is responsible for moving
    validated outputs into the durable run directory afterward.

    Parameters
    ----------
    shard : ImageSetShard
        Shard to process.
    source_root : Path
        Root of the staged/cached RxRx19a source images.
    scratch_root : Path
        Alpine (or local) scratch root.
    run_id : str
        Run identifier, used to namespace the task directory.
    pipeline_path : Path
        Pinned pipeline file.
    container_image : str
        Container image path/tag.
    runtime : Runtime
        Container runtime.
    timeout : int
        Subprocess timeout in seconds.

    Returns
    -------
    CellProfilerResult
        Run outcome, including discovered SQLite and mask outputs.

    Raises
    ------
    FileNotFoundError
        If a required source image is missing (raised during staging).
    """
    task_dir = shard.task_dir(scratch_root, run_id)
    # Resolve once, up front: stage_shard_images() symlinks to the fully
    # resolved source path (see its docstring), so the bind mount below
    # must target that same resolved path. Passing the raw source_root
    # would silently break every image set whenever any component of
    # source_root is itself a symlink (e.g. macOS's /tmp -> /private/tmp,
    # or an HPC scratch mount aliased through a symlink) -- CellProfiler
    # reports 0 image sets with no error at all (confirmed via a real
    # container run during e2e testing).
    source_root = Path(source_root).resolve()
    images_dir = stage_shard_images(shard, source_root, task_dir)
    output_dir = task_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = cellprofiler_command(
        images_dir=images_dir,
        output_dir=output_dir,
        pipeline_path=pipeline_path,
        container_image=container_image,
        extra_binds=(source_root,),
        runtime=runtime,
    )
    log_path = task_dir / "cellprofiler.log"
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"$ {' '.join(command)}\n{proc.stdout}\n{proc.stderr}\n")
    sqlite_path, mask_paths = collect_cellprofiler_outputs(output_dir)
    return CellProfilerResult(
        shard=shard,
        images_dir=images_dir,
        output_dir=output_dir,
        command=command,
        returncode=proc.returncode,
        log_path=log_path,
        sqlite_path=sqlite_path,
        mask_paths=mask_paths,
    )


def pipeline_hash(pipeline_path: Path = CELLPROFILER_PIPELINE) -> str:
    """SHA-256 of the pinned pipeline file (``run.json``'s pipeline hash)."""
    return sha256_file(Path(pipeline_path))


def container_hash(container_image_path: Path) -> str:
    """SHA-256 of a container image file (``.sif``; ``run.json``'s container hash)."""
    return sha256_file(Path(container_image_path))

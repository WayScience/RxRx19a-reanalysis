"""
Run identity, directories, markers, and run.json for ReRx runs.

Implements plan.md sections 5, 6, 25, and 26: timestamped run IDs, run
directory layout, status markers, run.json contents, and run listing.
"""

import datetime
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

RUNNING = "_RUNNING"
FAILED = "_FAILED"
SUCCESS = "_SUCCESS"
PROTECTED = "_PROTECTED"
MIRRORED = "_MIRRORED"

MARKERS = (RUNNING, FAILED, SUCCESS, PROTECTED, MIRRORED)

RUN_LAYOUT = {
    "manifest": [
        "source_files.parquet",
        "image_sets.parquet",
        "selection.parquet",
        "outputs.parquet",
        "files.parquet",
    ],
    "metadata": ["rxrx19a.parquet", "controls.parquet"],
    "profiles/cellprofiler": ["raw", "annotated", "normalized", "feature_selected"],
    "profiles/morphem": ["raw", "annotated", "normalized"],
    "crops/cells": [],
    "buscar/cellprofiler": [],
    "buscar/morphem": [],
    "baseline/recursion_site_embeddings": [],
    "comparisons/recursion_vs_morphem": [],
    "qc/summaries": [],
    "qc/images": [],
    "catalog": [],
    "logs": [],
}

RUN_JSON_FIELDS = [
    "run_id",
    "scope",
    "created_at_utc",
    "git_commit",
    "source_manifest_hash",
    "configuration_hash",
    "cellprofiler_version",
    "cellprofiler_pipeline_hash",
    "cellprofiler_container_hash",
    "morphem_model_revision",
    "morphem_container_hash",
    "pycytominer_version",
    "buscar_version",
    "source_image_sets",
    "processed_image_sets",
    "cell_count",
    "crop_count",
    "morphem_profile_count",
    "recursion_embedding_source_sha256",
    "recursion_embedding_site_count",
    "morphem_site_count",
    "matched_embedding_site_count",
    "status",
]


def utc_now() -> datetime.datetime:
    """
    Current UTC time, seconds resolution.

    Returns
    -------
    datetime.datetime
        Timezone-aware UTC datetime.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def git_commit(repo: str | Path = ".") -> str:
    """
    Short git commit hash for a repository.

    Parameters
    ----------
    repo : str | Path
        Repository path. Defaults to the current directory.

    Returns
    -------
    str
        Seven-character commit hash, or ``"unknown"`` when unavailable.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def make_run_id(
    scope: str = "pilot",
    timestamp: datetime.datetime | None = None,
    commit: str | None = None,
    repo: str | Path = ".",
) -> str:
    """
    Create a sortable run ID (plan.md section 5).

    Parameters
    ----------
    scope : str
        ``"pilot"`` or ``"full"``. Anything else is rejected.
    timestamp : datetime.datetime | None
        UTC timestamp for the run. Defaults to now.
    commit : str | None
        Git commit hash to embed. Defaults to the current repo HEAD.
    repo : str | Path
        Repository used when ``commit`` is not given.

    Returns
    -------
    str
        Run ID like ``rxrx19a-pilot-20260925T153000Z-g1a2b3c4``.

    Raises
    ------
    ValueError
        If ``scope`` is not ``"pilot"`` or ``"full"``.
    """
    if scope not in ("pilot", "full"):
        raise ValueError(f"scope must be 'pilot' or 'full', got {scope!r}")
    ts = (timestamp or utc_now()).astimezone(datetime.timezone.utc)
    ts_str = ts.strftime("%Y%m%dT%H%M%SZ")
    sha = commit or git_commit(repo)
    return f"rxrx19a-{scope}-{ts_str}-g{sha}"


def validate_run_id(run_id: str) -> None:
    """
    Check a run ID's shape (used by cleanup commands; plan.md section 26).

    Raises
    ------
    ValueError
        If the run ID does not match ``rxrx19a-<scope>-<timestamp>-g<sha>``.
    """
    parts = run_id.split("-")
    expected_part_count = 4
    if (
        len(parts) != expected_part_count
        or parts[0] != "rxrx19a"
        or parts[1] not in ("pilot", "full")
        or not parts[3].startswith("g")
    ):
        raise ValueError(f"malformed run_id: {run_id!r}")


def make_run_dir(root: Path, run_id: str, parents: bool = True) -> Path:
    """
    Create the standard run directory skeleton under ``root`` (plan.md section 6).

    Parameters
    ----------
    root : Path
        Datasets root (for example ``$RERX_PETA_ROOT/datasets``).
    run_id : str
        Validated run ID.

    Returns
    -------
    Path
        The created run directory.
    """
    validate_run_id(run_id)
    run_dir = Path(root) / run_id
    for rel, files in RUN_LAYOUT.items():
        d = run_dir / rel
        d.mkdir(parents=parents, exist_ok=True)
        for name in files:
            if (d / name).exists():
                continue
            if name in ("raw", "annotated", "normalized", "feature_selected"):
                (d / name).mkdir(parents=True, exist_ok=True)
    return run_dir


@dataclass
class Run:
    """
    A single dataset run directory and its state.

    Attributes
    ----------
    run_id : str
        Full run identifier.
    root : Path
        Run directory on durable storage.
    """

    run_id: str
    root: Path

    def marker_path(self, marker: str) -> Path:
        """Path of a status marker file inside the run directory."""
        return self.root / marker

    def has_marker(self, marker: str) -> bool:
        """Whether a marker file is present."""
        if marker not in MARKERS:
            raise ValueError(f"unknown marker {marker!r}")
        return self.marker_path(marker).exists()

    def set_marker(self, marker: str, note: str | None = None) -> Path:
        """
        Write a marker file.

        Parameters
        ----------
        marker : str
            One of ``_RUNNING``, ``_FAILED``, ``_SUCCESS``, ``_PROTECTED``,
            ``_MIRRORED``.
        note : str | None
            Optional extra line written inside the marker file.

        Returns
        -------
        Path
            Marker file path.
        """
        if marker not in MARKERS:
            raise ValueError(f"unknown marker {marker!r}")
        p = self.marker_path(marker)
        content = f"written_at_utc={utc_now().isoformat()}"
        if note:
            content += f"\n{note}"
        p.write_text(content + "\n")
        return p

    def clear_marker(self, marker: str) -> "Run":
        """Remove a marker file if present."""
        p = self.marker_path(marker)
        if p.exists():
            p.unlink()
        return self

    def run_json_path(self) -> Path:
        """Path of the run's ``run.json``."""
        return self.root / "run.json"

    def write_run_json(self, data: dict) -> Path:
        """
        Write ``run.json`` with plan-required fields (plan.md section 25).

        Unknown fields are preserved; missing known fields are filled with
        ``None`` so consumers can rely on the full schema.
        """
        payload: dict[str, object] = {"run_id": self.run_id}
        for key in RUN_JSON_FIELDS:
            payload[key] = data.get(key)
        payload.update(data)
        path = self.run_json_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def read_run_json(self) -> dict:
        """Read and return the run's ``run.json`` contents."""
        return json.loads(self.run_json_path().read_text())

    def status(self) -> str:
        """Derive run status from markers, falling back to run.json."""
        for marker, label in (
            (SUCCESS, "success"),
            (MIRRORED, "mirrored"),
            (FAILED, "failed"),
            (RUNNING, "running"),
        ):
            if self.has_marker(marker):
                return label
        try:
            return self.read_run_json().get("status") or "unknown"
        except (FileNotFoundError, json.JSONDecodeError):
            return "unknown"


@dataclass
class RunListing:
    """
    All runs found under a datasets root.

    Attributes
    ----------
    runs : list[Run]
        One entry per run directory, in run-ID sort order.
    """

    runs: list[Run] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.runs)


def list_runs(datasets_root: Path) -> RunListing:
    """
    List run directories under a datasets root.

    Parameters
    ----------
    datasets_root : Path
        Directory containing one subdirectory per run.

    Returns
    -------
    RunListing
        Sorted listing of runs.
    """
    root = Path(datasets_root)
    runs = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir():
                try:
                    validate_run_id(child.name)
                except ValueError:
                    continue
                runs.append(Run(run_id=child.name, root=child))
    return RunListing(runs=runs)

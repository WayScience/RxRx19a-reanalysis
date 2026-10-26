"""Flushed stage boundaries without inspecting or retaining profile contents."""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic

try:
    import resource
except ImportError:  # Windows has neither resource nor /proc.
    resource = None  # type: ignore[assignment]


def memory_mib() -> tuple[float | None, float | None]:
    """Return current and peak process RSS in MiB; unavailable values are None.

    Linux exposes current RSS through /proc. Other platforms still report
    resource's peak RSS, rather than mislabeling a high-water mark as current.
    """
    current = peak = None
    if sys.platform == "linux":
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    current = float(line.split()[1]) / 1024
                    break
        except (OSError, ValueError, IndexError):
            pass
    if resource is not None:
        try:
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak = value / (1024 * 1024 if sys.platform == "darwin" else 1024)
        except (OSError, ValueError):
            pass
    return current, peak


def log_stage(stage: str, **details: object) -> None:
    """Print a stage label and process RSS immediately, even on buffered stdout."""
    current, peak = memory_mib()
    rss = "unavailable" if current is None else f"{current:.1f}"
    high_water = "unavailable" if peak is None else f"{peak:.1f}"
    fields = " ".join(f"{key}={value}" for key, value in details.items())
    print(
        f"[rerx {datetime.now():%H:%M:%S}] {stage} {fields} "
        f"rss_mib={rss} peak_rss_mib={high_water}",
        flush=True,
    )


@dataclass
class StageProgress:
    """Only dimensions and a short outcome, never a reference to a frame."""

    shape: tuple[int, int]
    outcome: str = "ok"


@contextmanager
def stage(
    name: str,
    *,
    experiment: str,
    plate: str,
    profiler: str,
    shape: tuple[int, int],
) -> Iterator[StageProgress]:
    """Log start/end or failure, preserving ordinary exception propagation."""
    progress = StageProgress(shape)
    started = monotonic()

    def emit(event: str, **details: object) -> None:
        log_stage(
            f"stage={name} event={event}",
            experiment=experiment,
            plate=plate,
            profiler=profiler,
            rows=progress.shape[0],
            cols=progress.shape[1],
            elapsed_s=f"{monotonic() - started:.3f}",
            **details,
        )

    emit("start")
    try:
        yield progress
    except Exception as exc:
        emit("failed", error=type(exc).__name__)
        raise
    else:
        emit("end", outcome=progress.outcome)

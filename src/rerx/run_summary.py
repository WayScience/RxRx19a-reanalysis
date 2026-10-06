"""
Operational run-summary reporting: pipeline health, not biology.

Aggregates per-plate finalize outcomes (coSMicQC flag counts, control
separation QC, buscar status) plus the run's validation report into one
JSON payload -- shard counts, QC pass/fail, buscar-skip reasons. This is
deliberately separate from the biology-focused reports under
``reports/`` (phenotypic overview, buscar reversal): this module answers
"did the pipeline run correctly", not "what did we discover".
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PlateSummary:
    """
    One plate's finalize outcome for one profiler.

    Attributes
    ----------
    experiment : str
        Plate's experiment id.
    plate : str
        Plate id.
    profiler : str
        ``"cellprofiler"`` or ``"morphem"``.
    n_cells_input : int
        Cells annotated before coSMicQC filtering.
    n_cells_flagged_outlier : int
        Cells coSMicQC flagged and dropped (see
        :func:`rerx.pycytominer.flag_outliers`). 0 when the default
        nuclei QC thresholds don't apply.
    n_cells_normalized : int
        Cells remaining after normalization (post-QC-filter).
    n_feature_selected_cols : int
        Column count after feature selection.
    control_separation_passed : bool | None
        Biological QC gate result, or ``None`` if not checked.
    control_separation_skipped : bool | None
        Whether the gate itself was skipped (missing data).
    control_separation_median_effect_size : float | None
        Median |Cohen's d| across features, or ``None``.
    buscar_status : str
        ``"scored"`` or ``"skipped"``.
    buscar_skipped_reason : str | None
        Why buscar was skipped, or ``None`` if it ran.
    """

    experiment: str
    plate: str
    profiler: str
    n_cells_input: int
    n_cells_flagged_outlier: int
    n_cells_normalized: int
    n_feature_selected_cols: int
    control_separation_passed: bool | None
    control_separation_skipped: bool | None
    control_separation_median_effect_size: float | None
    buscar_status: str
    buscar_skipped_reason: str | None

    def to_dict(self) -> dict:
        """Plain-dict form, safe for ``json.dumps`` with no custom encoder."""
        return asdict(self)


@dataclass
class RunSummary:
    """
    Whole-run operational summary: every plate plus the validation report.

    Attributes
    ----------
    run_id : str
        The run's identifier (see :mod:`rerx.runs`).
    plates : list[PlateSummary]
        One entry per (profiler, plate) finalized.
    validation : dict | None
        :meth:`rerx.validate.PilotValidationReport.summary` output, or
        ``None`` if validation hasn't run yet.
    """

    run_id: str
    plates: list[PlateSummary] = field(default_factory=list)
    validation: dict | None = None

    def totals(self) -> dict:
        """Aggregate counts across every plate (pipeline-health headline numbers)."""
        return {
            "plates_finalized": len(self.plates),
            "cells_input": sum(p.n_cells_input for p in self.plates),
            "cells_flagged_outlier": sum(
                p.n_cells_flagged_outlier for p in self.plates
            ),
            "cells_normalized": sum(p.n_cells_normalized for p in self.plates),
            "buscar_scored": sum(
                1 for p in self.plates if p.buscar_status == "scored"
            ),
            "buscar_skipped": sum(
                1 for p in self.plates if p.buscar_status == "skipped"
            ),
        }

    def to_dict(self) -> dict:
        """Plain-dict form, safe for ``json.dumps`` with no custom encoder."""
        return {
            "run_id": self.run_id,
            "totals": self.totals(),
            "plates": [p.to_dict() for p in self.plates],
            "validation": self.validation,
        }


def write_run_summary(summary: RunSummary, path: Path) -> Path:
    """
    Write a :class:`RunSummary` to ``path`` as indented JSON.

    Parameters
    ----------
    summary : RunSummary
        The summary to write.
    path : Path
        Destination file (parent directories are created).

    Returns
    -------
    Path
        ``path``, for chaining.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n")
    return path

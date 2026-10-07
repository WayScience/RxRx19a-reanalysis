"""
Pipeline run report data prep: builds the JSON payload for
reports/pipeline_run.html from a finalized run's durable outputs plus
Slurm job accounting.

This is an operational/pipeline-health report (shard counts, timing,
per-stage QC pass/fail, cosmicqc flag rates, fuse drop counts) -- not
a biology report. It describes how the run went, not what the cells
show.

Reads directly from the run's durable storage (pass --run-dir) and,
optionally, pre-fetched Slurm `sacct` job rows (pass --sacct-file,
one line per job: JobID|JobName|Elapsed|State -- see
`sacct -X -P --format=JobID,JobName,Elapsed,State`). Slurm timing is
optional: the report renders without it, just without the timing
section's numbers.

Usage (from repo root):

    uv run python reports/scripts/pipeline_run_report_data.py \\
        <run-dir> [--sacct-file sacct.txt] \\
        [--out reports/data/pipeline_run_report_data.json]
"""

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from rerx.fuse import fuse_features, fusion_metadata
from rerx.pycytominer import flag_outliers

MORPH_PREFIXES = ("Cells_", "Cytoplasm_", "Nuclei_")
SACCT_FIELD_COUNT = 4


def _to_secs(elapsed: str) -> int:
    """Parse a Slurm ``sacct`` Elapsed value into seconds.

    Handles every form ``sacct``'s ``Elapsed`` field can take:
    ``SS``, ``MM:SS``, ``HH:MM:SS``, and day-prefixed
    ``D-HH:MM:SS``.
    """
    hms_parts = 3
    days = 0
    rest = elapsed
    if "-" in elapsed:
        day_part, rest = elapsed.split("-", 1)
        days = int(day_part)
    parts = [int(p) for p in rest.split(":")]
    while len(parts) < hms_parts:
        parts.insert(0, 0)
    h, m, s = parts
    return days * 86400 + h * 3600 + m * 60 + s


def slurm_stage_timing(sacct_lines: list[str]) -> dict:
    """Aggregate `sacct -X -P` rows (JobID|JobName|Elapsed|State) by
    Nextflow process name (the ALL-CAPS token after ``nf-``)."""
    stages: dict[str, list[tuple[int, str]]] = {}
    for raw_line in sacct_lines:
        line = raw_line.strip()
        if not line or line.startswith("JobID"):
            continue
        parts = line.split("|")
        if len(parts) < SACCT_FIELD_COUNT:
            continue
        _jobid, name, elapsed, state = parts[:SACCT_FIELD_COUNT]
        m = re.match(r"nf-([A-Z]+)_", name)
        stage = m.group(1) if m else name
        stages.setdefault(stage, []).append((_to_secs(elapsed), state))

    summary = {}
    for stage, jobs in stages.items():
        secs = [s for s, _ in jobs]
        summary[stage] = {
            "n_jobs": len(jobs),
            "n_completed": sum(1 for _, st in jobs if st == "COMPLETED"),
            "total_cpu_minutes": round(sum(secs) / 60, 1),
            "mean_minutes": round((sum(secs) / len(secs)) / 60, 1),
            "max_minutes": round(max(secs) / 60, 1),
        }
    return summary


def _read_all_partitions(base_dir: Path) -> pd.DataFrame:
    """Read and concatenate every ``*/*/*.parquet`` partition under
    ``base_dir`` (one file per experiment/plate), not just the first
    match -- a run with more than one plate must have every plate's
    cells counted."""
    paths = sorted(base_dir.glob("*/*/*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no partitions found under {base_dir}")
    return pd.concat((pd.read_parquet(p) for p in paths), ignore_index=True)


def build_payload(
    run_dir: Path,
    sacct_lines: list[str] | None = None,
) -> dict:
    raw = _read_all_partitions(run_dir / "profiles" / "cellprofiler" / "raw")
    fs_cp = _read_all_partitions(
        run_dir / "profiles" / "cellprofiler" / "feature_selected"
    )
    morphem_fs_dir = run_dir / "profiles" / "morphem" / "feature_selected"
    fs_me = _read_all_partitions(morphem_fs_dir) if morphem_fs_dir.is_dir() else None

    validation_path = run_dir / "validation_report.txt"
    validation = (
        json.loads(validation_path.read_text()) if validation_path.is_file() else None
    )

    flagged = flag_outliers(raw)
    outlier_cols = [c for c in flagged.columns if c.startswith("Metadata_cqc_")]
    n_flagged = 0
    if outlier_cols:
        flagged_any = flagged[outlier_cols].any(axis=1)
        n_flagged = int(flagged_any.sum())

    payload = {
        "run_id": run_dir.name,
        "n_raw_cells": len(raw),
        "n_feature_selected_cp_cells": len(fs_cp),
        "n_wells": int(raw["Image_Metadata_Well"].nunique()),
        "n_sites": int(
            raw[["Image_Metadata_Well", "Image_Metadata_Site"]]
            .drop_duplicates()
            .shape[0]
        ),
        "n_cp_features_selected": len(
            [c for c in fs_cp.columns if c.startswith(MORPH_PREFIXES)]
        ),
        "validation": validation,
        "cosmicqc_n_flagged_outlier": n_flagged,
        "cosmicqc_flag_rate": round(n_flagged / len(raw), 4) if len(raw) else None,
        "cosmicqc_per_threshold_set": {c: int(flagged[c].sum()) for c in outlier_cols},
    }

    if fs_me is not None:
        payload["n_feature_selected_morphem_cells"] = len(fs_me)
        payload["n_morphem_features_selected"] = len(
            [c for c in fs_me.columns if c.startswith("Morphem_")]
        )
        fused = fuse_features(fs_cp, fs_me)
        meta = fusion_metadata(fused, cp_rows=len(fs_cp), morphem_rows=len(fs_me))
        payload["fuse_n_fused_cells"] = len(fused)
        payload["fuse_cellprofiler_rows_dropped"] = meta["cellprofiler_rows_dropped"]
        payload["fuse_morphem_rows_dropped"] = meta["morphem_rows_dropped"]
    else:
        payload["n_feature_selected_morphem_cells"] = None
        payload["n_morphem_features_selected"] = None
        payload["fuse_n_fused_cells"] = None
        payload["fuse_cellprofiler_rows_dropped"] = None
        payload["fuse_morphem_rows_dropped"] = None

    run_summary_path = run_dir / "run_summary.json"
    run_summary = (
        json.loads(run_summary_path.read_text()) if run_summary_path.is_file() else None
    )

    buscar_scores = sorted((run_dir / "buscar").glob("*/*/scores.parquet"))
    if run_summary is not None:
        # run_summary.json's per-plate buscar_status is finalize()'s own
        # durable record of whether buscar actually ran -- prefer it
        # over inferring from validation or guessing from file globs.
        plates = run_summary.get("plates", [])
        skipped_plates = [p for p in plates if p.get("buscar_status") == "skipped"]
        if not plates:
            buscar_skipped_reason = "unknown (run_summary.json has no plates)"
        elif not skipped_plates:
            buscar_skipped_reason = None
        else:
            reasons = {
                p.get("buscar_skipped_reason")
                for p in skipped_plates
                if p.get("buscar_skipped_reason")
            }
            buscar_skipped_reason = (
                "; ".join(sorted(reasons))
                if reasons
                else f"{len(skipped_plates)} of {len(plates)} plate(s) skipped buscar"
            )
    elif buscar_scores:
        buscar_skipped_reason = None
    elif validation and validation.get("control_separation_skipped"):
        buscar_skipped_reason = "control separation check was skipped (missing data)"
    else:
        # No run_summary.json and no durable buscar scores -- nothing
        # establishes that scoring completed, so don't report this as
        # "ran" (None) when we actually don't know.
        buscar_skipped_reason = "unknown (no run_summary.json or buscar scores found)"
    payload["buscar_skipped_reason"] = buscar_skipped_reason

    if sacct_lines is not None:
        payload["slurm_stage_timing"] = slurm_stage_timing(sacct_lines)

    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--sacct-file", type=Path, default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent
        / "data"
        / "pipeline_run_report_data.json",
    )
    args = parser.parse_args()

    sacct_lines = args.sacct_file.read_text().splitlines() if args.sacct_file else None
    payload = build_payload(args.run_dir, sacct_lines)
    args.out.write_text(json.dumps(payload, indent=None))
    print(
        f"pipeline run payload: {payload['n_raw_cells']} cells, run {payload['run_id']}"
    )


if __name__ == "__main__":
    main()

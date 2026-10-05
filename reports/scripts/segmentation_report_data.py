"""
Segmentation configuration check report data prep: builds the JSON
payload for reports/segmentation_check.html from the saved
Cellpose-vs-CellProfiler comparison (plan.md section 12's segmentation
review).

This is a methods/decision report, not biology: reports/data/
segmentation_cellpose_comparison.json is the *baseline* Otsu-threshold
comparison (pooled counts -- 7,039 CellProfiler nuclei vs. Cellpose's
independent 3,360 -- match plan.md's original finding exactly) that
surfaced the dim-well over-segmentation problem and justified switching
`IdentifyPrimaryObjects`'s thresholding method to Robust Background in
`pipelines/rxrx19a.cppipe`. It is the evidence behind that decision, not
a validation of the fix (the post-fix re-run numbers -- 13.3% mean
error -- live only as prose in plan.md, not as saved structured data).
See docs/src/segmentation-config-check.md for the full reusable
procedure and src/rerx/segmentation_check.py for the tested functions
behind it.

Reads reports/data/segmentation_cellpose_comparison.json directly --
no new computation, just reshaping it for the report's per-well and
dim-vs-normal summaries.

Usage (from repo root):

    uv run python reports/scripts/segmentation_report_data.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "data"
SRC = HERE / "segmentation_cellpose_comparison.json"
OUT = HERE / "segmentation_report_data.json"

# plan.md's segmentation review: AA08/E08 were the two measurably
# dimmer wells that Otsu's default thresholding over-segmented 3-7x
# relative to the Cellpose reference, while every other sampled well
# matched Cellpose within +/-25%. This comparison (Otsu, pre-fix) is
# the evidence that motivated switching to Robust Background.
DIM_WELLS = {"AA08", "E08"}


def pct_error(candidate: int, reference: int) -> float:
    return abs(candidate - reference) / reference * 100 if reference else 0.0


def build_payload() -> dict:
    raw = json.loads(SRC.read_text())
    per_site = raw["per_site"]

    wells: dict[str, list[dict]] = {}
    for row in per_site:
        wells.setdefault(row["well"], []).append(row)

    per_well = []
    for well in sorted(wells):
        rows = wells[well]
        n_cp = sum(r["n_cellprofiler"] for r in rows)
        n_cpo = sum(r["n_cellpose"] for r in rows)
        per_well.append(
            {
                "well": well,
                "condition": rows[0]["condition"],
                "dim_well": well in DIM_WELLS,
                "n_sites": len(rows),
                "n_cellprofiler": n_cp,
                "n_cellpose": n_cpo,
                "pct_error": round(pct_error(n_cp, n_cpo), 1),
                "mean_pixel_iou": round(
                    sum(r["pixel_iou_any_overlap"] for r in rows) / len(rows), 3
                ),
                "mean_match_rate_iou50": round(
                    sum(r["match_rate_cp_side"] for r in rows) / len(rows), 3
                ),
            }
        )

    dim_rows = [w for w in per_well if w["dim_well"]]
    normal_rows = [w for w in per_well if not w["dim_well"]]

    def mean_pct_error(rows: list[dict]) -> float | None:
        if not rows:
            return None
        return round(sum(r["pct_error"] for r in rows) / len(rows), 1)

    payload = {
        "n_sites": raw["n_sites"],
        "pooled": raw["pooled"],
        "per_well": per_well,
        "per_site": per_site,
        "dim_wells": sorted(DIM_WELLS),
        "mean_pct_error_dim_wells": mean_pct_error(dim_rows),
        "mean_pct_error_normal_wells": mean_pct_error(normal_rows),
        "mean_pct_error_all_wells": mean_pct_error(per_well),
        # Method this comparison was run with (the problem-surfacing
        # baseline), and the method the finding led the production
        # pipeline to switch to -- see plan.md for the post-fix numbers
        # (not re-derived here; no saved structured data for that run).
        "compared_method": "Otsu",
        "adopted_method": "Robust Background",
    }
    return payload


def main() -> None:
    payload = build_payload()
    OUT.write_text(json.dumps(payload, indent=None))
    print(
        f"segmentation report payload: {payload['n_sites']} sites, "
        f"{len(payload['per_well'])} wells "
        f"(mean abs % error vs Cellpose: dim wells "
        f"{payload['mean_pct_error_dim_wells']}%, normal wells "
        f"{payload['mean_pct_error_normal_wells']}%)"
    )


if __name__ == "__main__":
    main()

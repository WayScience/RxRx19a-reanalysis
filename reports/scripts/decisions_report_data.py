"""
Pipeline decisions report data: a small, hand-curated record of
decisions made about non-segmentation pipeline components, each with
the options actually tried and why one was (or wasn't yet) chosen.

This is the only report whose payload is not derived from a stored
Parquet/JSON analysis output -- plan.md's investigation sections are
prose, not structured data, so this script is the structured record of
those findings. Keep entries terse and specific (numbers, not vibes);
when a decision mirrors a stored analysis (the segmentation threshold
choice mirrors reports/segmentation_check.html), say so and point there
instead of duplicating the chart.

Usage (from repo root):

    uv run python reports/scripts/decisions_report_data.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "data"
OUT = HERE / "decisions_report_data.json"

# Each entry: component, question, options tried (name/outcome/chosen),
# evidence pointer, and status (decided | pending). "pending" means
# plan.md calls for a validation step that has not been run/recorded
# yet -- the current default is in use, but not confirmed as final.
DECISIONS: list[dict] = [
    {
        "id": "segmentation_threshold",
        "component": "CellProfiler nuclei segmentation",
        "question": (
            "Which thresholding method for IdentifyPrimaryObjects's nuclei detection?"
        ),
        "status": "decided",
        "options": [
            {
                "name": "Otsu (CellProfiler default)",
                "outcome": (
                    "Baseline. Over-segmented 2 of 6 sampled wells 3-7x "
                    "vs. an independent Cellpose reference (mean abs. "
                    "count error 107% pooled across 24 sites)."
                ),
                "chosen": False,
            },
            {
                "name": (
                    "Threshold strategy: Adaptive (same Otsu, computed per-window)"
                ),
                "outcome": (
                    "Made it worse -- total object count rose to 8,293 "
                    "from the baseline's 7,039."
                ),
                "chosen": False,
            },
            {
                "name": "Threshold correction factor: 1.3",
                "outcome": (
                    "Partial improvement (total 5,203) but the two dim "
                    "wells still ran 2-3x over Cellpose's counts."
                ),
                "chosen": False,
            },
            {
                "name": (
                    "Thresholding method: Minimum Cross-Entropy "
                    "(precedent: WayScience "
                    "pediatric_cancer_atlas_profiling)"
                ),
                "outcome": (
                    "Better than Otsu (29.8% pooled mean abs. error) "
                    "but worse than Robust Background (13.3%); visual "
                    "mask overlays showed messier splits than Robust "
                    "Background on the same dim well."
                ),
                "chosen": False,
            },
            {
                "name": "Thresholding method: Robust Background",
                "outcome": (
                    "Fixed it: mean abs. count error vs. Cellpose "
                    "dropped from 107% to 13% on Plate 25's 24-site "
                    "sample, without under-segmenting the already-fine "
                    "wells. Re-validated on Plates 1 and 13 (48 more "
                    "sites): mean error 5.0% vs. Otsu's 7.3%. A later "
                    "visual side-by-side (all variants on the same "
                    "six sites, scripts/"
                    "segmentation_cytodataframe_compare.py) confirmed "
                    "its mask boundaries hug the nuclei on the dim "
                    "wells where the others fragment or over-split."
                ),
                "chosen": True,
            },
        ],
        "evidence": (
            "plan.md section 12 (segmentation review); "
            "reports/segmentation_check.html; "
            "src/rerx/segmentation_check.py"
        ),
    },
    {
        "id": "image_quality_monitoring_scope",
        "component": "MeasureImageQuality module scope",
        "question": (
            "Which image channels should the embedded "
            "MeasureImageQuality QC module run on, so a future "
            "dim-well problem surfaces automatically?"
        ),
        "status": "decided",
        "options": [
            {
                "name": "All 5 channels",
                "outcome": (
                    "Crashed: hit SQLite's Per_Image column limit in "
                    "CellProfiler's ExportToDatabase ('too many "
                    "columns on Per_Image')."
                ),
                "chosen": False,
            },
            {
                "name": (
                    "DNA (w1 Hoechst) channel only -- the channel "
                    "used for nuclei segmentation and the one that "
                    "showed the Plate 25 problem"
                ),
                "outcome": (
                    "Works. Adds ~0.2-0.4s per image (not measurably "
                    "slower in the same Slurm job); the resulting "
                    "Image_ImageQuality_MaxIntensity_DNA column "
                    "correctly separates the dim wells (0.17-0.26) "
                    "from normal wells (0.40-0.57)."
                ),
                "chosen": True,
            },
        ],
        "evidence": (
            "plan.md section 12; rerx.validate.check_image_quality; "
            "rerx.segmentation_check.flag_dim_wells"
        ),
    },
    {
        "id": "illumination_correction",
        "component": "Illumination correction",
        "question": (
            "Should an illumination-correction module (CellProfiler's "
            "own, or BaSiC) be added to fix the dim-well "
            "over-segmentation?"
        ),
        "status": "decided",
        "options": [
            {
                "name": "Add a CorrectIllumination* module",
                "outcome": (
                    "Not tried. The finding was whole-well brightness "
                    "differences (two dim wells vs. four normal wells "
                    "on the same plate), not within-field "
                    "shading/vignetting, which is what illumination "
                    "correction targets -- the wrong tool for this "
                    "specific problem."
                ),
                "chosen": False,
            },
            {
                "name": (
                    "No illumination-correction step; fix via "
                    "thresholding method instead"
                ),
                "outcome": (
                    "Adopted -- see the segmentation_threshold decision above."
                ),
                "chosen": True,
            },
        ],
        "evidence": "plan.md section 12",
    },
    {
        "id": "buscar_aggregation_level",
        "component": "buscar reversal scoring aggregation level",
        "question": (
            "Score buscar reversal at the single-cell level, or "
            "aggregate to one row per well first?"
        ),
        "status": "decided",
        "options": [
            {
                "name": "Single-cell",
                "outcome": (
                    "QC gate failed: median |Cohen's d| 0.339 across "
                    "1,482 features, below the 0.5 pass threshold -- "
                    "mock and disease single cells overlap too much "
                    "to trust a score at this level."
                ),
                "chosen": False,
            },
            {
                "name": "Well-level aggregate",
                "outcome": (
                    "QC gate passed: median |Cohen's d| 1.199 across "
                    "1,431 features -- mock and disease wells clearly "
                    "separate once cell-to-cell noise is averaged "
                    "out. buscar scoring runs at this level."
                ),
                "chosen": True,
            },
        ],
        "evidence": (
            "rerx.validate.check_control_separation; reports/buscar_reversal.html"
        ),
    },
    {
        "id": "crop_jpeg_quality",
        "component": "Cell crop encoding",
        "question": (
            "Store per-cell crops as lossless images, or JPEG, and at what quality?"
        ),
        "status": "pending",
        "options": [
            {
                "name": "Lossless crops",
                "outcome": (
                    "Not in use. plan.md calls for comparing MorphEm "
                    "embeddings from lossless in-memory crops against "
                    "embeddings from stored JPEG crops before ruling "
                    "this out."
                ),
                "chosen": False,
            },
            {
                "name": "JPEG quality 95 (current default)",
                "outcome": (
                    "In use as the starting point (plan.md: 'start "
                    "with high-quality JPEG... validate the choice "
                    "before freezing it'). The lossless-vs-JPEG "
                    "MorphEm embedding comparison has not yet been "
                    "run or recorded, so this default is not yet a "
                    "confirmed decision."
                ),
                "chosen": True,
            },
        ],
        "evidence": "plan.md section 14; src/rerx/crops.py (DEFAULT_JPEG_QUALITY)",
    },
    {
        "id": "pilot_scale",
        "component": "Pilot run size",
        "question": (
            "How big should a pilot run be, and how should a repeat pilot scale up?"
        ),
        "status": "decided",
        "options": [
            {
                "name": "Fixed 42-well pilot (arm_scale=1, the default)",
                "outcome": (
                    "Unchanged from the original pilot design (plan.md "
                    "section 9: roughly 24-48 wells, all four sites "
                    "per well)."
                ),
                "chosen": True,
            },
            {
                "name": (
                    "Proportionally scaled pilot (RERX_PILOT_SCALE "
                    "env var / arm_scale > 1)"
                ),
                "outcome": (
                    "arm_scale multiplies every pilot arm's well "
                    "count by the same factor, so control ratios stay "
                    "fixed while the pilot grows -- e.g. scale=4 "
                    "gives a 120-well 'pilot-x4' run. Not a redesign "
                    "of the selection logic, just a size knob on it."
                ),
                "chosen": True,
            },
        ],
        "evidence": (
            "src/rerx/metadata.py (select_pilot_wells, arm_scale); "
            "workflows/main.nf; scripts/alpine_launch.sh"
        ),
    },
]


def main() -> None:
    payload = {"decisions": DECISIONS}
    OUT.write_text(json.dumps(payload, indent=None))
    n_decided = sum(1 for d in DECISIONS if d["status"] == "decided")
    print(
        f"decisions report payload: {len(DECISIONS)} components "
        f"({n_decided} decided, {len(DECISIONS) - n_decided} pending)"
    )


if __name__ == "__main__":
    main()

"""
Tests for rerx.run_summary (operational pipeline-health reporting).
"""

import json
from pathlib import Path

from rerx.run_summary import PlateSummary, RunSummary, write_run_summary


def _plate(
    profiler: str = "cellprofiler",
    plate: str = "25",
    buscar_status: str = "scored",
    buscar_skipped_reason: str | None = None,
) -> PlateSummary:
    return PlateSummary(
        experiment="HRCE-1",
        plate=plate,
        profiler=profiler,
        n_cells_input=100,
        n_cells_flagged_outlier=3,
        n_cells_normalized=97,
        n_feature_selected_cols=42,
        control_separation_passed=True,
        control_separation_skipped=False,
        control_separation_median_effect_size=0.8,
        buscar_status=buscar_status,
        buscar_skipped_reason=buscar_skipped_reason,
    )


def test_plate_summary_to_dict_is_json_safe() -> None:
    plate = _plate()
    payload = plate.to_dict()
    # Must round-trip through json.dumps with no custom encoder.
    json.dumps(payload)
    assert payload["experiment"] == "HRCE-1"
    assert payload["plate"] == "25"
    assert payload["profiler"] == "cellprofiler"
    assert payload["n_cells_flagged_outlier"] == 3
    assert payload["buscar_status"] == "scored"


def test_run_summary_aggregate_counts() -> None:
    summary = RunSummary(
        run_id="rxrx19a-pilot-20260101T000000Z-gabc1234",
        plates=[
            _plate(plate="25"),
            _plate(plate="26", buscar_status="skipped", buscar_skipped_reason="x"),
            _plate(profiler="morphem", plate="25"),
        ],
        validation=None,
    )
    totals = summary.totals()
    assert totals["plates_finalized"] == 3
    assert totals["cells_input"] == 300
    assert totals["cells_flagged_outlier"] == 9
    assert totals["cells_normalized"] == 291
    assert totals["buscar_scored"] == 2
    assert totals["buscar_skipped"] == 1


def test_run_summary_totals_empty_plates() -> None:
    summary = RunSummary(run_id="rxrx19a-pilot-x", plates=[], validation=None)
    totals = summary.totals()
    assert totals["plates_finalized"] == 0
    assert totals["cells_input"] == 0
    assert totals["buscar_scored"] == 0
    assert totals["buscar_skipped"] == 0


def test_run_summary_to_dict_includes_validation() -> None:
    summary = RunSummary(
        run_id="rxrx19a-pilot-x",
        plates=[_plate()],
        validation={"passed": True, "sqlite_shards_checked": 1},
    )
    payload = summary.to_dict()
    json.dumps(payload)
    assert payload["run_id"] == "rxrx19a-pilot-x"
    assert payload["validation"]["passed"] is True
    assert len(payload["plates"]) == 1
    assert "totals" in payload


def test_write_run_summary_writes_json_file(tmp_path: Path) -> None:
    summary = RunSummary(run_id="rxrx19a-pilot-x", plates=[_plate()], validation=None)
    out = write_run_summary(summary, tmp_path / "run_summary.json")
    assert out.is_file()
    loaded = json.loads(out.read_text())
    assert loaded["run_id"] == "rxrx19a-pilot-x"
    assert loaded["totals"]["plates_finalized"] == 1

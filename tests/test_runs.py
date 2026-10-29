"""Tests for rerx.runs: markers, run.json fields, and status derivation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rerx.runs import RUN_JSON_FIELDS, Run


def _run(tmp_path: Path) -> Run:
    return Run(run_id="rxrx19a-full-20260101T000000Z-g0000000", root=tmp_path)


def test_status_prefers_success_marker_over_run_json(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run.write_run_json({"status": "incomplete"})
    (tmp_path / "_SUCCESS").write_text("rxrx19a-full-20260101T000000Z-g0000000\n")
    assert run.status() == "success"


def test_status_rejects_unverified_success_claim(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run.write_run_json({"status": "success"})
    assert not (tmp_path / "_SUCCESS").exists()
    assert run.status() == "incomplete"


def test_status_passes_through_non_success_recordings(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run.write_run_json({"status": "failed"})
    assert run.status() == "failed"


def test_status_is_unknown_without_run_json(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.status() == "unknown"


def test_write_run_json_fills_missing_plan_fields_with_null(tmp_path: Path) -> None:
    run = _run(tmp_path)
    path = run.write_run_json({"status": "incomplete", "custom_field": 1})
    payload = json.loads(path.read_text())
    assert payload["run_id"] == run.run_id
    assert payload["status"] == "incomplete"
    assert payload["custom_field"] == 1
    for key in RUN_JSON_FIELDS:
        assert key in payload
    assert payload["cell_count"] is None


def test_write_run_json_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    """run.json must be replaced atomically; readers never see a partial file."""
    run = _run(tmp_path)
    run.write_run_json({"status": "incomplete"})
    path = run.write_run_json({"status": "success", "extra": [1, 2]})
    payload = json.loads(path.read_text())
    assert payload["status"] == "success"
    assert payload["extra"] == [1, 2]
    # No leftover temp files beside run.json.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "run.json"]
    assert leftovers == []


def test_write_run_json_preserves_previous_file_on_serialization_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payload that cannot serialize must not clobber the existing run.json."""
    run = _run(tmp_path)
    run.write_run_json({"status": "incomplete"})
    original = (tmp_path / "run.json").read_text()
    with pytest.raises(TypeError):
        run.write_run_json({"status": "success", "bad": object()})  # type: ignore[dict-item]
    assert (tmp_path / "run.json").read_text() == original
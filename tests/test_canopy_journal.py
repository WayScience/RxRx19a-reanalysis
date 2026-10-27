"""
Tests for the run journal: append-only JSONL events with resume
(canopy harness, journal slice).
"""

import json
from pathlib import Path

from canopy.journal import Journal, load_journal, replay


def _events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_append_writes_one_json_line(tmp_path: Path) -> None:
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "run_started", "run_id": "r1"})
    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "run_started"


def test_load_journal_returns_events_in_order(tmp_path: Path) -> None:
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "a", "seq": 1})
    journal.append({"event": "b", "seq": 2})
    loaded = load_journal(tmp_path / "journal.jsonl")
    assert [event["event"] for event in loaded] == ["a", "b"]


def test_load_journal_missing_file_is_empty(tmp_path: Path) -> None:
    loaded = load_journal(tmp_path / "nope.jsonl")
    assert loaded == []


def test_load_journal_tolerates_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text('{"event": "a"}\n\n{"event": "b"}\n', encoding="utf-8")
    loaded = load_journal(path)
    assert [event["event"] for event in loaded] == ["a", "b"]


def test_load_journal_rejects_corrupt_line(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text('{"event": "a"}\n{oops\n', encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="line 2"):
        load_journal(path)


def test_journal_replay_positions_run_at_last_completed_stage(
    tmp_path: Path,
) -> None:
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "run_started", "node": "triage"})
    journal.append({"event": "node_completed", "node": "triage"})
    journal.append({"event": "node_started", "node": "checks"})
    events = load_journal(tmp_path / "journal.jsonl")
    current = _current_node(events)
    assert current == "checks"


def _current_node(events: list[dict]) -> str | None:
    current = None
    for event in events:
        if event["event"] == "node_started":
            current = event["node"]
        elif event["event"] == "node_completed":
            current = None
    return current


def test_journal_counts_attempts_per_node(tmp_path: Path) -> None:
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "node_started", "node": "triage", "attempt": 1})
    journal.append({"event": "node_started", "node": "triage", "attempt": 2})
    journal.append({"event": "node_completed", "node": "triage", "attempt": 2})
    events = load_journal(tmp_path / "journal.jsonl")
    attempts = {}
    for event in events:
        if event["event"] == "node_started":
            attempts[event["node"]] = event.get("attempt", 1)
    assert attempts["triage"] == 2


def test_journal_replay_after_interrupted_loop_iteration(
    tmp_path: Path,
) -> None:
    # a gate looped back once, then the process died mid-retry:
    # resume must restart the interrupted node, not the whole run
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "run_started", "node": "implement"})
    journal.append({"event": "node_started", "node": "implement", "attempt": 1})
    journal.append({"event": "node_completed", "node": "implement", "attempt": 1})
    journal.append({"event": "node_started", "node": "checks", "attempt": 1})
    journal.append({"event": "node_completed", "node": "checks", "attempt": 1})
    journal.append({"event": "gate_failed", "node": "gate", "looped_to": "implement"})
    journal.append({"event": "node_started", "node": "implement", "attempt": 2})
    events = load_journal(tmp_path / "journal.jsonl")
    assert _current_node(events) == "implement"
    started = [
        e for e in events if e["event"] == "node_started" and e["node"] == "implement"
    ]
    assert len(started) == 2


def test_journal_records_gate_outcomes(tmp_path: Path) -> None:
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "gate_passed", "node": "gate"})
    journal.append({"event": "term_reached", "node": "done", "action": "needs_review"})
    events = load_journal(tmp_path / "journal.jsonl")
    assert events[-1]["event"] == "term_reached"
    assert events[-2]["event"] == "gate_passed"


def test_replay_restores_stage_outputs(tmp_path: Path) -> None:
    # a run that dies mid-flow must resume with its stage outputs intact
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "run_started", "node": "plan"})
    journal.append({"event": "node_started", "node": "plan", "attempt": 1})
    journal.append(
        {
            "event": "node_completed",
            "node": "plan",
            "attempt": 1,
            "output": {"summary": "s", "changed_paths": ["src/a.py"]},
        }
    )
    journal.append({"event": "node_started", "node": "review", "attempt": 1})
    position = replay(load_journal(tmp_path / "journal.jsonl"))
    assert position.outputs["plan"] == {"summary": "s", "changed_paths": ["src/a.py"]}
    assert position.changed_paths == ["src/a.py"]


def test_replay_restores_check_results(tmp_path: Path) -> None:
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "run_started", "node": "plan"})
    journal.append(
        {"event": "node_completed", "node": "lint", "passed": True, "attempt": 1}
    )
    journal.append(
        {"event": "node_completed", "node": "pytest", "passed": False, "attempt": 1}
    )
    position = replay(load_journal(tmp_path / "journal.jsonl"))
    assert position.checks == {"lint": True, "pytest": False}


def test_replay_reports_pending_loop_target(tmp_path: Path) -> None:
    # a gate failed and looped back, then the process died:
    # the position must say where the loop was heading
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "run_started", "node": "plan"})
    journal.append({"event": "node_completed", "node": "plan", "attempt": 1})
    journal.append({"event": "gate_failed", "node": "gate", "looped_to": "implement"})
    position = replay(load_journal(tmp_path / "journal.jsonl"))
    assert position.pending_loop_to == "implement"


def test_replay_drops_check_state_across_a_loop(tmp_path: Path) -> None:
    # checks before a gate failure belong to the dead round: the next
    # round re-runs them
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "run_started", "node": "plan"})
    journal.append(
        {"event": "node_completed", "node": "pytest", "passed": True, "attempt": 1}
    )
    journal.append({"event": "gate_failed", "node": "gate", "looped_to": "plan"})
    journal.append(
        {"event": "node_completed", "node": "pytest", "passed": True, "attempt": 2}
    )
    position = replay(load_journal(tmp_path / "journal.jsonl"))
    assert position.checks == {"pytest": True}


def test_replay_clears_pending_loop_once_target_starts(tmp_path: Path) -> None:
    # a gate looped back and the target completed: the pending loop is
    # spent, and a resume must not rewind to it again
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "run_started", "node": "plan"})
    journal.append({"event": "node_completed", "node": "plan", "attempt": 1})
    journal.append({"event": "node_completed", "node": "checks", "passed": True})
    journal.append({"event": "gate_failed", "node": "gate", "looped_to": "plan"})
    journal.append({"event": "node_started", "node": "plan", "attempt": 2})
    journal.append({"event": "node_completed", "node": "plan", "attempt": 2})
    position = replay(load_journal(tmp_path / "journal.jsonl"))
    assert position.pending_loop_to is None
    # re-completion moves plan to the end, so _entry resumes forward
    # from plan (through checks), not from the stale pre-loop checks
    assert position.completed_nodes[-1] == "plan"


def test_replay_clears_pending_loop_on_gate_pass(tmp_path: Path) -> None:
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "gate_failed", "node": "gate", "looped_to": "plan"})
    journal.append({"event": "gate_passed", "node": "gate"})
    position = replay(load_journal(tmp_path / "journal.jsonl"))
    assert position.pending_loop_to is None


def test_replay_treats_run_failed_as_terminal(tmp_path: Path) -> None:
    journal = Journal(path=tmp_path / "journal.jsonl")
    journal.append({"event": "gate_failed", "node": "gate", "looped_to": "plan"})
    journal.append({"event": "run_failed", "reason": "loop bound exceeded"})
    position = replay(load_journal(tmp_path / "journal.jsonl"))
    assert position.terminated is True
    assert position.failed_reason == "loop bound exceeded"

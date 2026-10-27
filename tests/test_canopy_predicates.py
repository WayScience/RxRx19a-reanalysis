"""
Tests for predicate evaluation: how a gate judges a run's state
(canopy harness, predicate slice).
"""

import pytest

from canopy.model import validate_predicate
from canopy.predicates import evaluate_predicate


def _run_state(**overrides: object) -> dict:
    state = {
        "checks": {"passed": True, "failed": [], "all_passed": True},
        "ticket": {"labels": ["bug"], "effort": "s"},
        "changed_paths": ["src/canopy/runner.py"],
        "outputs": {},
    }
    state.update(overrides)
    return state


def test_checks_predicate_passes_when_all_checks_pass() -> None:
    predicate = {"kind": "checks", "status": "passed"}
    assert evaluate_predicate(predicate, _run_state()) is True


def test_checks_predicate_fails_when_a_check_failed() -> None:
    predicate = {"kind": "checks", "status": "passed"}
    state = _run_state(
        checks={"passed": False, "failed": ["pytest"], "all_passed": False}
    )
    assert evaluate_predicate(predicate, state) is False


def test_checks_predicate_failed_status_inverts() -> None:
    predicate = {"kind": "checks", "status": "failed"}
    state = _run_state(
        checks={"passed": False, "failed": ["pytest"], "all_passed": False}
    )
    assert evaluate_predicate(predicate, state) is True


def test_effort_predicate_lte() -> None:
    predicate = {"kind": "effort", "op": "lte", "value": "m"}
    assert evaluate_predicate(predicate, _run_state()) is True
    state = _run_state(ticket={"labels": [], "effort": "l"})
    assert evaluate_predicate(predicate, state) is False


def test_effort_predicate_gte() -> None:
    predicate = {"kind": "effort", "op": "gte", "value": "m"}
    state = _run_state(ticket={"labels": [], "effort": "s"})
    assert evaluate_predicate(predicate, state) is False
    state = _run_state(ticket={"labels": [], "effort": "l"})
    assert evaluate_predicate(predicate, state) is True


def test_labels_predicate_requires_one_matching_label() -> None:
    predicate = {"kind": "labels", "labels": ["bug", "infra"]}
    assert evaluate_predicate(predicate, _run_state()) is True
    state = _run_state(ticket={"labels": ["docs"], "effort": "s"})
    assert evaluate_predicate(predicate, state) is False


def test_paths_predicate_matches_changed_paths() -> None:
    predicate = {"kind": "paths", "glob": "src/canopy/**"}
    assert evaluate_predicate(predicate, _run_state()) is True
    state = _run_state(changed_paths=["docs/readme.md"])
    assert evaluate_predicate(predicate, state) is False


def test_paths_predicate_handles_star_glob() -> None:
    predicate = {"kind": "paths", "glob": "tests/*"}
    state = _run_state(changed_paths=["tests/test_canopy_model.py"])
    assert evaluate_predicate(predicate, state) is True


def test_output_predicate_matches_a_prior_stage_field() -> None:
    predicate = {
        "kind": "output",
        "node": "review",
        "field": "verdict",
        "equals": "pass",
    }
    state = _run_state(outputs={"review": {"verdict": "pass"}})
    assert evaluate_predicate(predicate, state) is True
    state = _run_state(outputs={"review": {"verdict": "fail"}})
    assert evaluate_predicate(predicate, state) is False


def test_output_predicate_fails_when_node_missing() -> None:
    predicate = {
        "kind": "output",
        "node": "review",
        "field": "verdict",
        "equals": "pass",
    }
    state = _run_state(outputs={})
    assert evaluate_predicate(predicate, state) is False


def test_labels_predicate_treats_string_labels_as_no_labels() -> None:
    # a ticket with labels: "bug" (a bare string, not a list) must not
    # match by character set or crash
    predicate = {"kind": "labels", "labels": ["bug"]}
    state = _run_state(ticket={"labels": "bug", "effort": "s"})
    assert evaluate_predicate(predicate, state) is False


def test_labels_predicate_treats_null_labels_as_no_labels() -> None:
    predicate = {"kind": "labels", "labels": ["bug"]}
    state = _run_state(ticket={"labels": None, "effort": "s"})
    assert evaluate_predicate(predicate, state) is False


def test_labels_predicate_ignores_non_string_entries() -> None:
    predicate = {"kind": "labels", "labels": ["bug"]}
    state = _run_state(ticket={"labels": ["bug", ["nested"]], "effort": "s"})
    assert evaluate_predicate(predicate, state) is True


def test_labels_predicate_treats_non_dict_ticket_as_no_labels() -> None:
    predicate = {"kind": "labels", "labels": ["bug"]}
    assert evaluate_predicate(predicate, {"ticket": None}) is False


def test_evaluate_predicate_raises_on_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown predicate kind"):
        evaluate_predicate({"kind": "nope"}, _run_state())


def test_validate_predicate_accepts_output_kind() -> None:
    predicate = {
        "kind": "output",
        "node": "review",
        "field": "verdict",
        "equals": "pass",
    }
    assert validate_predicate(predicate) is None


def test_validate_predicate_rejects_output_kind_without_fields() -> None:
    predicate = {"kind": "output"}
    assert validate_predicate(predicate) is not None


def test_validate_predicate_rejects_output_kind_with_bad_field() -> None:
    predicate = {"kind": "output", "node": "review", "field": "verdict", "equals": None}
    assert validate_predicate(predicate) is not None


def test_validator_and_evaluator_agree_on_kinds() -> None:
    # every predicate the validator accepts, the evaluator can evaluate
    for predicate in [
        {"kind": "checks", "status": "passed"},
        {"kind": "checks", "status": "failed"},
        {"kind": "effort", "op": "lte", "value": "m"},
        {"kind": "labels", "labels": ["agentic"]},
        {"kind": "paths", "glob": "src/rerx/**"},
    ]:
        assert validate_predicate(predicate) is None
        assert isinstance(evaluate_predicate(predicate, _run_state()), bool)

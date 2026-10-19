"""
Tests for agentic workflow documents: loading and validation
(agentic harness, workflow model slice).
"""

import json
from pathlib import Path

import pytest

from rerx.agentic.model import Workflow, load_workflow, validate_document


def _doc() -> dict:
    return {
        "workflow_version": "1.0",
        "start": "triage",
        "ticket": None,
        "nodes": [
            {
                "id": "triage",
                "type": "llm",
                "skill": "triage",
                "max_retries": 1,
            },
            {
                "id": "checks",
                "type": "infra",
                "command": ["uv", "run", "--frozen", "pytest", "-q"],
            },
            {
                "id": "gate",
                "type": "flow",
                "kind": "gate",
                "predicate": {"kind": "checks", "status": "passed"},
            },
            {
                "id": "done",
                "type": "term",
                "action": "needs_review",
            },
        ],
        "edges": [
            {"from": "triage", "to": "checks"},
            {"from": "checks", "to": "gate"},
            {"from": "gate", "to": "done"},
            {"from": "gate", "to": "triage", "kind": "loop"},
        ],
    }


def test_validate_document_accepts_minimal_loop() -> None:
    errors = validate_document(_doc())
    assert errors == []


def test_validate_document_rejects_unknown_version() -> None:
    doc = _doc()
    doc["workflow_version"] = "2.0"
    errors = validate_document(doc)
    assert any("workflow_version" in error for error in errors)


def test_validate_document_rejects_missing_start() -> None:
    doc = _doc()
    del doc["start"]
    errors = validate_document(doc)
    assert any("start" in error for error in errors)


def test_validate_document_rejects_start_not_in_nodes() -> None:
    doc = _doc()
    doc["start"] = "nope"
    errors = validate_document(doc)
    assert any("start" in error for error in errors)


def test_validate_document_rejects_duplicate_node_ids() -> None:
    doc = _doc()
    doc["nodes"].append(dict(doc["nodes"][0]))
    errors = validate_document(doc)
    assert any("duplicate" in error for error in errors)


def test_validate_document_rejects_bad_node_id() -> None:
    doc = _doc()
    doc["nodes"][0]["id"] = "Bad Id!"
    errors = validate_document(doc)
    assert any("id" in error for error in errors)


def test_validate_document_requires_llm_skill() -> None:
    doc = _doc()
    del doc["nodes"][0]["skill"]
    errors = validate_document(doc)
    assert any("triage" in error and "skill" in error for error in errors)


def test_validate_document_requires_infra_command() -> None:
    doc = _doc()
    del doc["nodes"][1]["command"]
    errors = validate_document(doc)
    assert any("checks" in error and "command" in error for error in errors)


def test_validate_document_rejects_infra_command_with_empty_string() -> None:
    doc = _doc()
    doc["nodes"][1]["command"] = [""]
    errors = validate_document(doc)
    assert any("command" in error for error in errors)


def test_validate_document_requires_gate_predicate() -> None:
    doc = _doc()
    del doc["nodes"][2]["predicate"]
    errors = validate_document(doc)
    assert any("gate" in error and "predicate" in error for error in errors)


def test_validate_document_rejects_unknown_term_action() -> None:
    doc = _doc()
    doc["nodes"][3]["action"] = "delete_everything"
    errors = validate_document(doc)
    assert any("action" in error for error in errors)


def test_validate_document_rejects_edge_to_unknown_node() -> None:
    doc = _doc()
    doc["edges"].append({"from": "triage", "to": "ghost"})
    errors = validate_document(doc)
    assert any("ghost" in error for error in errors)


def test_validate_document_requires_gate_loop_edge() -> None:
    doc = _doc()
    doc["edges"] = [edge for edge in doc["edges"] if edge.get("kind") != "loop"]
    errors = validate_document(doc)
    assert any("loop" in error for error in errors)


def test_validate_document_rejects_out_edge_from_term() -> None:
    doc = _doc()
    doc["edges"].append({"from": "done", "to": "triage"})
    errors = validate_document(doc)
    assert any("done" in error and "term" in error for error in errors)


def test_validate_document_rejects_unreachable_node() -> None:
    doc = _doc()
    doc["nodes"].append({"id": "orphan", "type": "infra", "command": ["true"]})
    errors = validate_document(doc)
    assert any("orphan" in error and "reach" in error for error in errors)


def test_validate_document_rejects_node_that_cannot_reach_a_term() -> None:
    doc = _doc()
    doc["nodes"].append({"id": "island", "type": "term", "action": "needs_review"})
    errors = validate_document(doc)
    assert any("island" in error for error in errors)


def test_validate_document_rejects_multiple_default_edges_from_one_node() -> None:
    doc = _doc()
    doc["nodes"].append({"id": "extra", "type": "term", "action": "failed"})
    doc["edges"].append({"from": "triage", "to": "extra"})
    errors = validate_document(doc)
    assert any("triage" in error and "default" in error for error in errors)


def test_validate_document_rejects_loop_edge_from_non_gate() -> None:
    doc = _doc()
    doc["edges"].append({"from": "checks", "to": "triage", "kind": "loop"})
    errors = validate_document(doc)
    assert any("checks" in error and "loop" in error for error in errors)


def test_load_workflow_reads_and_validates(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(_doc()), encoding="utf-8")
    workflow = load_workflow(path)
    assert isinstance(workflow, Workflow)
    assert workflow.start == "triage"
    assert set(workflow.nodes) == {"triage", "checks", "gate", "done"}
    assert len(workflow.edges) == 4


def test_load_workflow_raises_on_invalid_document(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    doc = _doc()
    doc["nodes"][0]["id"] = "Bad Id!"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid workflow document"):
        load_workflow(path)


def test_validate_document_rejects_output_predicate_referencing_unknown_node() -> None:
    doc = _doc()
    doc["nodes"][2]["predicate"] = {
        "kind": "output",
        "node": "ghost-reviewer",
        "field": "verdict",
        "equals": "pass",
    }
    errors = validate_document(doc)
    assert any("ghost-reviewer" in error for error in errors)


def test_validate_document_rejects_unknown_top_level_key() -> None:
    doc = _doc()
    doc["surprise"] = True
    errors = validate_document(doc)
    assert any("surprise" in error for error in errors)


def test_validate_document_survives_malformed_edges() -> None:
    # malformed edges must produce errors, not a traceback
    doc = _doc()
    doc["edges"].append({"from": "triage"})
    errors = validate_document(doc)
    assert any("from and to" in error for error in errors)
    doc["edges"].append("oops")
    errors = validate_document(doc)
    assert any("must be an object" in error for error in errors)

"""
Tests for the shipped canopy artifacts: the default workflow document
and the procedure skills under canopy/.
"""

import json
from pathlib import Path

from canopy.model import load_workflow, validate_document
from canopy.skills import load_skill

REPO_ROOT = Path(__file__).resolve().parent.parent
CANOPY = REPO_ROOT / "canopy"


def test_default_workflow_document_is_valid() -> None:
    path = CANOPY / "workflows" / "default.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert validate_document(document) == []
    workflow = load_workflow(path)
    assert workflow.start == "triage"
    assert workflow.nodes["gate"].predicate == {
        "kind": "checks",
        "status": "passed",
    }


def test_every_workflow_skill_referenced_exists() -> None:
    path = CANOPY / "workflows" / "default.json"
    workflow = load_workflow(path)
    for node in workflow.nodes.values():
        if node.type == "llm" and node.skill is not None:
            skill = load_skill(CANOPY / "skills" / node.skill)
            assert skill.contract is not None, (
                f"skill {node.skill} declares no output contract"
            )


def test_example_ticket_is_valid_json() -> None:
    path = CANOPY / "tickets" / "example.json"
    ticket = json.loads(path.read_text(encoding="utf-8"))
    assert "title" in ticket

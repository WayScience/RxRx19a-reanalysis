"""
Tests for the skill loader: procedure files, output contracts, and the
responder protocol (agentic harness, skills slice).
"""

import json
from pathlib import Path

import pytest

from rerx.agentic.skills import (
    ContractViolation,
    LoadedSkill,
    load_skill,
    validate_output,
)


def _write_skill(
    tmp_path: Path, body: str, name: str = "triage"
) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    path = directory / "SKILL.md"
    path.write_text(body, encoding="utf-8")
    return directory


def test_load_skill_reads_body_and_contract(tmp_path: Path) -> None:
    body = """# Triage

Decide what a ticket asks for.

## Output contract

```json
{"type": "object", "required": ["category"], "properties": {"category": {"type": "string", "enum": ["bug", "feature", "docs"]}}}
```

## Steps
1. Read the ticket.
"""
    directory = _write_skill(tmp_path, body)
    skill = load_skill(directory)
    assert isinstance(skill, LoadedSkill)
    assert skill.name == "triage"
    assert "Decide what a ticket asks for" in skill.body
    assert skill.contract is not None
    assert skill.contract["required"] == ["category"]


def test_load_skill_without_contract(tmp_path: Path) -> None:
    body = "# Report\n\nWrite the summary."
    directory = _write_skill(tmp_path, body, name="report")
    skill = load_skill(directory)
    assert skill.contract is None


def test_load_skill_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_skill(tmp_path / "nope")


def test_load_skill_rejects_invalid_contract_json(tmp_path: Path) -> None:
    body = "# Triage\n\n## Output contract\n\n```json\n{not json}\n```\n"
    directory = _write_skill(tmp_path, body)
    with pytest.raises(ValueError, match="contract"):
        load_skill(directory)


def test_validate_output_accepts_matching_output() -> None:
    contract = {
        "type": "object",
        "required": ["category"],
        "properties": {
            "category": {"type": "string", "enum": ["bug", "feature"]}
        },
        "additionalProperties": False,
    }
    errors = validate_output({"category": "bug"}, contract)
    assert errors == []


def test_validate_output_rejects_missing_required_field() -> None:
    contract = {
        "type": "object",
        "required": ["category"],
        "properties": {"category": {"type": "string"}},
    }
    errors = validate_output({}, contract)
    assert any("category" in error for error in errors)


def test_validate_output_rejects_wrong_enum() -> None:
    contract = {
        "type": "object",
        "required": ["category"],
        "properties": {
            "category": {"type": "string", "enum": ["bug", "feature"]}
        },
    }
    errors = validate_output({"category": "chore"}, contract)
    assert any("category" in error for error in errors)


def test_validate_output_rejects_wrong_type() -> None:
    contract = {
        "type": "object",
        "required": ["category"],
        "properties": {"category": {"type": "integer"}},
    }
    errors = validate_output({"category": "bug"}, contract)
    assert any("category" in error for error in errors)


def test_validate_output_rejects_unknown_field() -> None:
    contract = {
        "type": "object",
        "required": ["category"],
        "properties": {"category": {"type": "string"}},
        "additionalProperties": False,
    }
    errors = validate_output({"category": "bug", "extra": 1}, contract)
    assert any("extra" in error for error in errors)


def test_contract_violation_carries_errors() -> None:
    violation = ContractViolation("category: required field missing", errors=["x"])
    assert violation.errors == ["x"]
    assert "category" in str(violation)


def test_responders_are_callables_declared_in_protocol() -> None:
    # the harness drives any backend through one protocol: a callable
    # (prompt) -> str, and the skills module re-exports it for tests
    from rerx.agentic import skills as skills_module

    assert callable(skills_module.load_skill)
    assert callable(skills_module.validate_output)


def test_load_skill_strips_rerx_local_header(tmp_path: Path) -> None:
    body = (
        "______________________________________________________________________\n"
        "\n"
        "## name: triage description: 'Decide what a ticket asks for'\n"
        "\n"
        "# Triage\n\nDecide what a ticket asks for.\n"
    )
    directory = _write_skill(tmp_path, body)
    skill = load_skill(directory)
    assert "## name:" not in skill.body
    assert "# Triage" in skill.body


def test_load_skill_contract_block_named_output_contract(
    tmp_path: Path,
) -> None:
    body = (
        "# Triage\n\n"
        "## Output contract\n\n"
        "```json\n"
        '{"type": "object", "required": ["category"]}\n'
        "```\n"
    )
    directory = _write_skill(tmp_path, body)
    skill = load_skill(directory)
    assert skill.contract is not None
    assert skill.contract["required"] == ["category"]


def test_load_skill_uses_only_the_output_contract_heading(
    tmp_path: Path,
) -> None:
    body = (
        "# Triage\n\n"
        "## Other contract\n\n"
        "```json\n"
        '{"type": "object", "required": ["nope"]}\n'
        "```\n\n"
        "## Output contract\n\n"
        "```json\n"
        '{"type": "object", "required": ["category"]}\n'
        "```\n"
    )
    directory = _write_skill(tmp_path, body)
    skill = load_skill(directory)
    assert skill.contract is not None
    assert skill.contract["required"] == ["category"]
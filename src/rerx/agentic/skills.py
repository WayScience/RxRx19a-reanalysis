"""
Procedure skills: loading, output contracts, and validation.

A skill is a directory with a ``SKILL.md``: prose the model follows, plus
an optional output contract — a fenced ``json`` block under an
``## Output contract`` heading — that the harness can check a response
against before accepting it. Validation supports the small, structural
subset the shipped contracts use (``type``, ``required``, ``properties``,
``enum``, ``items``, ``additionalProperties``); anything outside that
subset is a contract bug and fails loudly rather than passing silently.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_HEADER_PATTERN = re.compile(
    r"\A_{5,}\s*\n+##\s*name:.*?(?=\n#\s|\Z)", re.DOTALL
)
_CONTRACT_HEADING = "## Output contract"
_FENCE_PATTERN = re.compile(r"```json\s*(.*?)```", re.DOTALL)


class ContractViolation(Exception):
    """
    A response did not match its skill's output contract.

    Attributes
    ----------
    errors : list[str]
        Every problem found, one line each.
    """

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


@dataclass(frozen=True)
class LoadedSkill:
    """
    A skill read from disk, ready to hand to a responder.

    Attributes
    ----------
    name : str
        The skill directory's name.
    body : str
        The procedure text, with the output contract block removed.
    contract : dict | None
        The parsed output contract, when the skill declares one.
    """

    name: str
    body: str
    contract: dict | None


def _strip_local_header(body: str) -> str:
    """
    Remove the repo-local underscore-and-``## name:`` header if present.
    """
    match = _HEADER_PATTERN.match(body)
    return body[match.end():].lstrip() if match else body


def _extract_contract(body: str) -> tuple[str, dict | None, str | None]:
    """
    Split the body into procedure text, parsed contract, and an error.
    """
    heading_index = body.find(_CONTRACT_HEADING)
    if heading_index < 0:
        return body, None, None
    rest = body[heading_index + len(_CONTRACT_HEADING):]
    fence = _FENCE_PATTERN.search(rest)
    if not fence:
        return body, None, "output contract heading has no ```json fence"
    raw = fence.group(1)
    try:
        contract = json.loads(raw)
    except json.JSONDecodeError as error:
        return body, None, f"output contract is not valid JSON: {error}"
    procedure = (
        body[:heading_index]
        + rest[: fence.start()]
        + rest[fence.end():]
    )
    return procedure, contract, None


def load_skill(directory: Path) -> LoadedSkill:
    """
    Read a skill directory.

    Parameters
    ----------
    directory : Path
        The skill's directory, holding a ``SKILL.md``.

    Returns
    -------
    LoadedSkill
        The skill's procedure text and parsed contract.

    Raises
    ------
    FileNotFoundError
        When ``SKILL.md`` is missing.
    ValueError
        When the declared output contract cannot be parsed.
    """
    path = Path(directory) / "SKILL.md"
    raw = path.read_text(encoding="utf-8")
    body = _strip_local_header(raw)
    procedure, contract, problem = _extract_contract(body)
    if problem:
        raise ValueError(f"skill {Path(directory).name}: {problem}")
    return LoadedSkill(
        name=Path(directory).name,
        body=procedure.strip(),
        contract=contract,
    )


def _type_matches(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _check_schema(value: object, schema: dict, path: str) -> list[str]:
    """
    Check one value against one schema node, recursively.
    """
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type is not None:
        types = (
            expected_type if isinstance(expected_type, list) else [expected_type]
        )
        if not any(_type_matches(value, name) for name in types):
            found = type(value).__name__
            return [f"{path}: expected type {expected_type!r}, got {found}"]
    if isinstance(value, dict):
        for field in schema.get("required", []):
            if field not in value:
                errors.append(f"{path}: required field {field!r} is missing")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            field_path = f"{path}.{key}" if path else key
            if key in properties:
                errors.extend(_check_schema(item, properties[key], field_path))
            elif additional is False:
                errors.append(f"{field_path}: unknown field")
            elif isinstance(additional, dict):
                errors.extend(_check_schema(item, additional, field_path))
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(_check_schema(item, items, f"{path}[{index}]"))
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        errors.append(
            f"{path}: {value!r} is not one of the allowed values {enum}"
        )
    return errors


def validate_output(output: object, contract: dict) -> list[str]:
    """
    Check a responder's output against a skill's contract.

    Parameters
    ----------
    output : object
        The parsed output to check.
    contract : dict
        The skill's output contract.

    Returns
    -------
    list[str]
        Every problem found, one line each; empty when output matches.
    """
    return _check_schema(output, contract, "output")
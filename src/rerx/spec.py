"""
Experiment specs: decisions, rationale, and universes.

An experiment spec is a small YAML document that records every
consequential choice in an experiment before it runs: its *decisions*
(each with a written rationale and a closed set of options), its
*outputs* (each naming the decisions that shape it), and its
*universes* (each picking one option per decision it varies). The
format is deliberately astra-flavored but self-contained: plain YAML,
no schema tooling, one validator.

The point is auditability: any single number in the results traces back
to the choices that produced it, and the rationale for each choice is
written down next to the choice, not in a commit message or a chat.
"""

from __future__ import annotations

from pathlib import Path

SPEC_VERSIONS = ("1.0",)

OUTPUT_TYPES = ("metric", "figure", "report", "data", "table")

KNOWN_TOP = {"version", "name", "description", "decisions", "outputs", "universes"}
KNOWN_DECISION_FIELDS = {"label", "rationale", "default", "options"}
KNOWN_OPTION_FIELDS = {"label", "description"}
KNOWN_OUTPUT_FIELDS = {"id", "type", "decisions", "description", "recipe"}
KNOWN_UNIVERSE_FIELDS = {"description", "decisions"}


def _validate_decision(name: str, decision: object) -> list[str]:
    errors: list[str] = []
    where = f"decision {name!r}"
    if not isinstance(decision, dict):
        return [f"{where}: a decision must be a mapping"]
    unknown = set(decision) - KNOWN_DECISION_FIELDS
    if unknown:
        errors.append(
            f"{where}: unknown fields {sorted(unknown)} "
            f"(known: {' '.join(sorted(KNOWN_DECISION_FIELDS))})"
        )
    rationale = decision.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        errors.append(f"{where}: needs a rationale, a written reason for the choice")
    options = decision.get("options")
    if not isinstance(options, dict) or not options:
        errors.append(f"{where}: needs a non-empty options mapping")
        return errors
    for option_name, option in options.items():
        if not isinstance(option, dict):
            errors.append(f"{where}: option {option_name!r} must be a mapping")
            continue
        unknown_option = set(option) - KNOWN_OPTION_FIELDS
        if unknown_option:
            errors.append(
                f"{where}: option {option_name!r} has unknown fields "
                f"{sorted(unknown_option)}"
            )
    default = decision.get("default")
    if default is not None and default not in options:
        errors.append(
            f"{where}: default {default!r} is not one of the options {sorted(options)}"
        )
    return errors


def _validate_outputs(  # noqa: C901
    outputs: object, decision_names: set[str]
) -> list[str]:
    errors: list[str] = []
    if not isinstance(outputs, list):
        return ["outputs: must be a list"]
    seen: set[str] = set()
    for index, output in enumerate(outputs):
        where = f"output {index}"
        if not isinstance(output, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        output_id = output.get("id")
        if not isinstance(output_id, str) or not output_id:
            errors.append(f"{where}: needs a string id")
        elif output_id in seen:
            errors.append(f"{where}: duplicate output id {output_id!r}")
        else:
            seen.add(output_id)
            where = f"output {output_id!r}"
        unknown = set(output) - KNOWN_OUTPUT_FIELDS
        if unknown:
            errors.append(
                f"{where}: unknown fields {sorted(unknown)} "
                f"(known: {' '.join(sorted(KNOWN_OUTPUT_FIELDS))})"
            )
        output_type = output.get("type")
        if output_type is not None and output_type not in OUTPUT_TYPES:
            errors.append(
                f"{where}: type must be one of {' '.join(OUTPUT_TYPES)}, "
                f"got {output_type!r}"
            )
        shaped_by = output.get("decisions")
        if not isinstance(shaped_by, list) or not shaped_by:
            errors.append(
                f"{where}: must name the decisions that shape it "
                "(a non-empty decisions list)"
            )
        else:
            for decision_name in shaped_by:
                if decision_name not in decision_names:
                    errors.append(f"{where}: names unknown decision {decision_name!r}")
    return errors


def _validate_universes(universes: object, decisions: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    if not isinstance(universes, dict) or not universes:
        return ["universes: must be a non-empty mapping of universe name to picks"]
    for universe_name, universe in universes.items():
        where = f"universe {universe_name!r}"
        if not isinstance(universe, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        unknown = set(universe) - KNOWN_UNIVERSE_FIELDS
        if unknown:
            errors.append(
                f"{where}: unknown fields {sorted(unknown)} "
                f"(known: {' '.join(sorted(KNOWN_UNIVERSE_FIELDS))})"
            )
        picks = universe.get("decisions")
        if not isinstance(picks, dict) or not picks:
            errors.append(f"{where}: needs a non-empty decisions mapping of picks")
            continue
        for decision_name, picked in picks.items():
            if decision_name not in decisions:
                errors.append(f"{where}: picks unknown decision {decision_name!r}")
                continue
            options = decisions[decision_name].get("options", {})
            if isinstance(options, dict) and picked not in options:
                errors.append(
                    f"{where}: picks {picked!r} for {decision_name!r}, "
                    f"which is not one of the options {sorted(options)}"
                )
    return errors


def validate_spec(spec: object) -> list[str]:
    """
    Validate an experiment spec and list every problem.

    Parameters
    ----------
    spec : Any
        The parsed spec document.

    Returns
    -------
    list[str]
        Errors, in document order; empty when the spec is valid.
    """
    if not isinstance(spec, dict):
        return ["a spec must be a mapping"]
    errors: list[str] = []
    version = spec.get("version")
    if version not in SPEC_VERSIONS:
        errors.append(
            f"version must be one of {' '.join(SPEC_VERSIONS)}, got {version!r}"
        )
    unknown_top = set(spec) - KNOWN_TOP
    if unknown_top:
        errors.append(
            f"unknown top-level fields {sorted(unknown_top)} "
            f"(known: {' '.join(sorted(KNOWN_TOP))})"
        )
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name must be a non-empty string")
    decisions = spec.get("decisions")
    if not isinstance(decisions, dict) or not decisions:
        errors.append("decisions must be a non-empty mapping")
        decisions = {}
    for decision_name, decision in decisions.items():
        errors.extend(_validate_decision(decision_name, decision))
    outputs = spec.get("outputs", [])
    errors.extend(_validate_outputs(outputs, set(decisions)))
    universes = spec.get("universes")
    errors.extend(_validate_universes(universes, decisions))
    return errors


def load_spec(path: Path) -> dict:
    """
    Read and validate a YAML experiment spec.

    Parameters
    ----------
    path : Path
        The YAML spec document.

    Returns
    -------
    dict
        The parsed spec.

    Raises
    ------
    ValueError
        When the spec is invalid. The message names the first problem.
    """
    import yaml

    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    errors = validate_spec(document)
    if errors:
        raise ValueError(
            f"invalid experiment spec: {errors[0]} ({len(errors)} problems)"
        )
    return document

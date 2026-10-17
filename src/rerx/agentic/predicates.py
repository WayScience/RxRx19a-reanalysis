"""
Predicate evaluation: how a gate judges a run's state.

A predicate is one structured test over the run's state — its check
results, its ticket, or the paths its change touched. The evaluator is
deliberately total: every predicate the document validator accepts
(:func:`rerx.agentic.model.validate_predicate`) evaluates without
surprise, and an unknown kind is a programming error, not a silent
``False``.
"""

from __future__ import annotations

import fnmatch

from rerx.agentic.model import validate_predicate

_EFFORT_ORDER = ("xs", "s", "m", "l", "xl")


def evaluate_predicate(predicate: dict, state: dict) -> bool:
    """
    Evaluate one predicate against a run's state.

    Parameters
    ----------
    predicate : dict
        A validated predicate, as written in the workflow document.
    state : dict
        The run's state: ``checks`` (with ``passed``), ``ticket`` (with
        ``labels`` and ``effort``), and ``changed_paths``.

    Returns
    -------
    bool
        Whether the predicate holds.

    Raises
    ------
    ValueError
        When the predicate's kind is unknown. Validation happens before
        a run starts; a predicate that reaches evaluation unvalidated is
        a harness bug, and failing loudly beats guessing.
    """
    problem = validate_predicate(predicate)
    if problem:
        raise ValueError(problem)
    kind = predicate["kind"]
    if kind == "checks":
        checks = state.get("checks", {})
        all_passed = bool(checks.get("all_passed", False))
        wants_passed = predicate["status"] == "passed"
        return all_passed is wants_passed
    if kind == "effort":
        ticket = state.get("ticket", {})
        actual = ticket.get("effort")
        if actual not in _EFFORT_ORDER:
            return False
        target = _EFFORT_ORDER.index(predicate["value"])
        value = _EFFORT_ORDER.index(actual)
        op = predicate["op"]
        if op == "lt":
            return value < target
        if op == "lte":
            return value <= target
        if op == "eq":
            return value == target
        if op == "gte":
            return value >= target
        return value > target
    if kind == "labels":
        ticket = state.get("ticket", {})
        labels = set(ticket.get("labels", []))
        return bool(labels & set(predicate["labels"]))
    if kind == "paths":
        glob = predicate["glob"]
        changed = state.get("changed_paths", [])
        return any(fnmatch.fnmatch(path, glob) for path in changed)
    if kind == "output":
        outputs = state.get("outputs", {})
        node_output = outputs.get(predicate["node"], {})
        value = node_output.get(predicate["field"])
        return value == predicate["equals"]
    raise ValueError(f"unknown predicate kind {predicate.get('kind')!r}")
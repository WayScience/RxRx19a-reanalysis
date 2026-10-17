"""
Workflow documents: the in-memory model, validation, and loading.

A document is a small JSON graph: an entry node, typed nodes, and typed
edges. Validation is closed: a field the schema does not know is an error,
not something silently carried. The interpreter (see ``runner.py``) reads
the same :class:`Workflow` object this module produces, so there is one
parser and one validation pass per document.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

WORKFLOW_VERSIONS = ("1.0",)

NODE_TYPES = ("llm", "infra", "flow", "term")

TERM_ACTIONS = ("open_pr", "needs_review", "failed")

NODE_ID_PATTERN_MESSAGE = (
    "a node id is lower-case letters, digits, or `-`, "
    "starts and ends with a letter or digit, and is at most 64 characters"
)


@dataclass(frozen=True)
class Node:
    """
    One stage of a workflow.

    Attributes
    ----------
    id : str
        Human-readable identifier, unique within the document.
    type : str
        One of ``llm``, ``infra``, ``flow``, ``term``.
    skill : str | None
        The procedure skill an ``llm`` node follows.
    max_retries : int
        Attempt bound for an ``llm`` node.
    command : tuple[str, ...] | None
        The argv an ``infra`` node runs.
    kind : str | None
        For ``flow`` nodes: ``gate`` (holds, and loops back on failure).
    predicate : dict | None
        For ``flow`` nodes: the structured test, evaluated against the
        run's check results.
    action : str | None
        For ``term`` nodes: what to do when the run ends there.
    """

    id: str
    type: str
    skill: str | None = None
    max_retries: int = 0
    command: tuple[str, ...] | None = None
    kind: str | None = None
    predicate: dict | None = None
    action: str | None = None


@dataclass(frozen=True)
class Edge:
    """
    One connection between nodes.

    Attributes
    ----------
    from_node : str
        The node the edge leaves.
    target : str
        The node the edge enters.
    kind : str
        ``default`` is the plain path, ``loop`` is the edge a failed
        gate follows back to an earlier stage.
    """

    from_node: str
    target: str
    kind: str = "default"


@dataclass(frozen=True)
class Workflow:
    """
    A validated workflow document.

    Attributes
    ----------
    version : str
        The document's declared version, from the supported set.
    start : str
        The node a run begins at.
    nodes : dict[str, Node]
        Nodes by id.
    edges : tuple[Edge, ...]
        Every connection, in document order.
    """

    version: str
    start: str
    nodes: dict[str, Node]
    edges: tuple[Edge, ...]


NODE_ID_MAX_LENGTH = 64
SKILL_NAME_MAX_LENGTH = 128
MAX_STAGE_RETRIES = 10


def _is_node_id(node_id: str) -> bool:
    return (
        bool(node_id)
        and len(node_id) <= NODE_ID_MAX_LENGTH
        and node_id[0].isalnum()
        and node_id[-1].isalnum()
        and all(char.isalnum() or char == "-" for char in node_id)
        and node_id == node_id.lower()
    )


def _is_slug(text: str) -> bool:
    return (
        bool(text)
        and len(text) <= SKILL_NAME_MAX_LENGTH
        and all(char.isalnum() or char == "-" for char in text)
        and text == text.lower()
    )


def _is_path_glob(glob: str) -> bool:
    return (
        bool(glob)
        and not glob.startswith("/")
        and ".." not in glob
        and not any(char.isspace() for char in glob)
    )


def validate_predicate(  # noqa: C901, PLR0911, PLR0912
    predicate: dict,
) -> str | None:
    """
    Validate one structured predicate.

    Parameters
    ----------
    predicate : dict
        The predicate as written in the document.

    Returns
    -------
    str | None
        The problem, or ``None`` when the predicate is well formed.
    """
    kind = predicate.get("kind")
    if kind == "checks":
        status = predicate.get("status")
        if status not in ("passed", "failed"):
            return f"a checks predicate needs status passed or failed, got {status!r}"
        return None
    if kind == "effort":
        op = predicate.get("op")
        if op not in ("lt", "lte", "eq", "gte", "gt"):
            return f"an effort predicate needs an op from lt lte eq gte gt, got {op!r}"
        value = predicate.get("value")
        if value not in ("xs", "s", "m", "l", "xl"):
            return f"an effort predicate needs a value from xs s m l xl, got {value!r}"
        return None
    if kind == "labels":
        labels = predicate.get("labels")
        if not isinstance(labels, list) or not labels:
            return "a labels predicate needs a non-empty labels list"
        if any(not isinstance(label, str) or not label for label in labels):
            return "a labels predicate needs every label to be a non-empty string"
        return None
    if kind == "paths":
        glob = predicate.get("glob")
        if not isinstance(glob, str) or not _is_path_glob(glob):
            return "a paths predicate needs a safe relative glob, like src/rerx/**"
        return None
    if kind == "output":
        node = predicate.get("node")
        field = predicate.get("field")
        equals = predicate.get("equals")
        if not isinstance(node, str) or not _is_node_id(node):
            return "an output predicate needs a node id from this document"
        if not isinstance(field, str) or not field:
            return "an output predicate needs a field name"
        if not isinstance(equals, str):
            return "an output predicate needs a string equals value"
        return None
    return f"unknown predicate kind {kind!r}"


def _validate_node(node: object, index: int) -> list[str]:  # noqa: C901, PLR0912
    errors: list[str] = []
    where = f"node {index}"
    if not isinstance(node, dict):
        return [f"{where}: a node must be an object"]
    node_id = node.get("id")
    if not isinstance(node_id, str) or not _is_node_id(node_id):
        errors.append(f"{where}: {NODE_ID_PATTERN_MESSAGE}")
        if not isinstance(node_id, str):
            return errors
    else:
        where = f"node {node_id!r}"
    node_type = node.get("type")
    if node_type not in NODE_TYPES:
        errors.append(
            f"{where}: type must be one of llm infra flow term, got {node_type!r}"
        )
        return errors
    known_fields = (
        "id",
        "type",
        "skill",
        "max_retries",
        "command",
        "kind",
        "predicate",
        "action",
    )
    unknown = set(node) - set(known_fields)
    if unknown:
        errors.append(
            f"{where}: unknown fields {sorted(unknown)} "
            f"(known: {' '.join(known_fields)})"
        )
    if node_type == "llm":
        skill = node.get("skill")
        if not isinstance(skill, str) or not _is_slug(skill):
            errors.append(
                f"{where}: an llm node needs a skill, a lower-case hyphenated name"
            )
        retries = node.get("max_retries", 0)
        if (
            not isinstance(retries, int)
            or isinstance(retries, bool)
            or not 0 <= retries <= MAX_STAGE_RETRIES
        ):
            errors.append(
                f"{where}: max_retries must be an integer "
                f"from 0 to {MAX_STAGE_RETRIES}, got {retries!r}"
            )
    elif node_type == "infra":
        command = node.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) or not arg for arg in command)
        ):
            errors.append(f"{where}: an infra node needs a non-empty command list")
        if node.get("max_retries", 0) != 0:
            errors.append(f"{where}: an infra node has no retries")
    elif node_type == "flow":
        kind = node.get("kind")
        if kind != "gate":
            errors.append(f"{where}: a flow node's kind must be gate, got {kind!r}")
        predicate = node.get("predicate")
        if not isinstance(predicate, dict):
            errors.append(f"{where}: a flow node needs a predicate object")
        else:
            problem = validate_predicate(predicate)
            if problem:
                errors.append(f"{where}: {problem}")
    else:
        action = node.get("action")
        if action not in TERM_ACTIONS:
            errors.append(
                f"{where}: a term node's action must be one of "
                f"{' '.join(TERM_ACTIONS)}, got {action!r}"
            )
    return errors


def _reachable(start: str, edges: list[dict]) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], set()).add(edge["to"])
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for target in adjacency.get(current, set()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


def _can_reach_term(
    node_id: str, node_types: dict[str, str], edges: list[dict]
) -> bool:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], set()).add(edge["to"])
    seen: set[str] = set()
    frontier = [node_id]
    while frontier:
        current = frontier.pop()
        if node_types.get(current) == "term":
            return True
        if current in seen:
            continue
        seen.add(current)
        frontier.extend(adjacency.get(current, set()) - seen)
    return False


def validate_document(document: object) -> list[str]:  # noqa: C901, PLR0912, PLR0915
    """
    Validate a workflow document and list every problem.

    Parameters
    ----------
    document : object
        The document as parsed from JSON.

    Returns
    -------
    list[str]
        Errors, in document order; empty when the document is valid.
    """
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["a workflow document must be a JSON object"]
    version = document.get("workflow_version")
    if version not in WORKFLOW_VERSIONS:
        errors.append(
            f"workflow_version must be one of {' '.join(WORKFLOW_VERSIONS)}, "
            f"got {version!r}"
        )
    known_top = {"workflow_version", "start", "ticket", "nodes", "edges"}
    unknown_top = set(document) - known_top
    if unknown_top:
        errors.append(
            f"unknown top-level fields {sorted(unknown_top)} "
            f"(known: {' '.join(sorted(known_top))})"
        )
    nodes = document.get("nodes")
    edges = document.get("edges")
    start = document.get("start")
    node_ids: list[str] = []
    node_types: dict[str, str] = {}
    node_kinds: dict[str, str] = {}
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
        nodes = []
    if not isinstance(edges, list):
        errors.append("edges must be a list")
        edges = []
    if not isinstance(start, str):
        errors.append("start must be a node id string")
    seen_ids: set[str] = set()
    for index, node in enumerate(nodes):
        for error in _validate_node(node, index):
            errors.append(error)
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            node_id = node["id"]
            if node_id in seen_ids:
                errors.append(f"node {node_id!r}: duplicate node id")
            else:
                seen_ids.add(node_id)
                node_ids.append(node_id)
            if node.get("type") in NODE_TYPES:
                node_types[node_id] = node["type"]
                if node.get("kind") is not None:
                    node_kinds[node_id] = node["kind"]
    if isinstance(start, str) and start in seen_ids and node_types.get(start) == "term":
        errors.append(f"start node {start!r} cannot be a term node")
    known_ids = set(node_ids)
    if isinstance(start, str) and known_ids and start not in known_ids:
        errors.append(f"start node {start!r} is not in nodes")
    default_out: dict[str, int] = {}
    gate_loops: dict[str, str] = {}
    valid_edges: list[dict] = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(f"edge {index}: an edge must be an object")
            continue
        source = edge.get("from")
        target = edge.get("to")
        where = f"edge {index} ({source!r} -> {target!r})"
        unknown = set(edge) - {"from", "to", "kind"}
        if unknown:
            errors.append(f"{where}: unknown fields {sorted(unknown)}")
        if not isinstance(source, str) or not isinstance(target, str):
            errors.append(f"{where}: from and to must be node ids")
            continue
        valid_edges.append(edge)
        if source not in known_ids:
            errors.append(f"{where}: from node {source!r} is not in nodes")
        if target not in known_ids:
            errors.append(f"{where}: to node {target!r} is not in nodes")
        kind = edge.get("kind", "default")
        if kind not in ("default", "loop"):
            errors.append(f"{where}: kind must be default or loop, got {kind!r}")
        if source in node_types and node_types[source] == "term":
            errors.append(
                f"{where}: a term node {source!r} cannot have an outgoing edge"
            )
        if kind == "loop":
            if source not in node_types:
                pass
            elif node_types[source] != "flow" or node_kinds.get(source) != "gate":
                errors.append(
                    f"{where}: a loop edge must leave a gate node, "
                    f"but {source!r} is a {node_types[source]} node"
                )
            else:
                gate_loops[source] = target
        elif source in node_types:
            default_out[source] = default_out.get(source, 0) + 1
            if default_out[source] > 1:
                errors.append(f"{where}: a node may have at most one default out-edge")
    for gate, loop_target in gate_loops.items():
        if node_types.get(loop_target) == "term":
            errors.append(f"node {gate!r}: a loop edge must not point at a term node")
    for node_id, node_kind in node_kinds.items():
        if node_kind == "gate" and node_id not in gate_loops:
            errors.append(
                f"node {node_id!r}: a gate needs a loop edge back to an earlier stage"
            )
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "flow":
            continue
        predicate = node.get("predicate")
        if not isinstance(predicate, dict) or predicate.get("kind") != "output":
            continue
        referenced = predicate.get("node")
        if isinstance(referenced, str) and referenced not in known_ids:
            errors.append(
                f"node {node.get('id')!r}: an output predicate references "
                f"unknown node {referenced!r}"
            )
    if isinstance(start, str) and start in known_ids:
        reachable = _reachable(start, valid_edges)
        for node_id in node_ids:
            if node_id not in reachable:
                errors.append(f"node {node_id!r}: unreachable from start")
        for node_id in node_ids:
            if not _can_reach_term(node_id, node_types, valid_edges):
                errors.append(f"node {node_id!r}: cannot reach a term node")
        for gate in gate_loops:
            if gate not in reachable:
                pass
    return errors


def load_workflow(path: Path) -> Workflow:
    """
    Read, validate, and parse a workflow document.

    Parameters
    ----------
    path : Path
        The JSON document to read.

    Returns
    -------
    Workflow
        The parsed document.

    Raises
    ------
    ValueError
        When the document is invalid. The message names the first problem.
    """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_document(document)
    if errors:
        raise ValueError(
            f"invalid workflow document: {errors[0]} ({len(errors)} problems)"
        )
    nodes = {
        node["id"]: Node(
            id=node["id"],
            type=node["type"],
            skill=node.get("skill"),
            max_retries=node.get("max_retries", 0),
            command=tuple(node.get("command", ())),
            kind=node.get("kind"),
            predicate=node.get("predicate"),
            action=node.get("action"),
        )
        for node in document["nodes"]
    }
    edges = tuple(
        Edge(
            from_node=edge["from"],
            target=edge["to"],
            kind=edge.get("kind", "default"),
        )
        for edge in document["edges"]
    )
    return Workflow(
        version=document["workflow_version"],
        start=document["start"],
        nodes=nodes,
        edges=edges,
    )

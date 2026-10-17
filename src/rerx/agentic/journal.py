"""
The run journal: an append-only JSONL event log with replay.

Every stage boundary is written before the work happens, so a process
that dies mid-run leaves a journal that says exactly where it stopped.
Replay folds the events into the position the run resumes from: the node
it was in, the attempts it has spent, and whether a gate already looped.
The journal is intentionally dumb — it records and replays; deciding
what to do with a position is the runner's job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JournalPosition:
    """
    Where a run resumes, folded from journal events.

    Attributes
    ----------
    current_node : str | None
        The node a run was inside when the journal ends — ``None`` when
        the last node completed, meaning the run continues from the
        default edge forward, or from a pending gate loop.
    attempts : dict[str, int]
        Highest attempt number recorded per node.
    completed_nodes : list[str]
        Nodes that completed, in order (deduplicated, latest kept).
    terminated : bool
        Whether the journal ends at a term node.
    term_action : str | None
        The action the term node declared, when terminated.
    outputs : dict[str, dict]
        The validated output each llm stage produced, from
        ``node_completed`` events that carry one.
    changed_paths : list[str]
        The most recent ``changed_paths`` the run saw, from stage
        outputs.
    checks : dict[str, bool]
        Check results for the current round, keyed by node id; a gate
        failure resets the round, so checks before it are not carried.
    pending_loop_to : str | None
        The node a failed gate looped toward when the journal ends.
    """

    current_node: str | None
    attempts: dict[str, int]
    completed_nodes: list[str]
    terminated: bool
    term_action: str | None
    outputs: dict[str, dict]
    changed_paths: list[str]
    checks: dict[str, bool]
    pending_loop_to: str | None


@dataclass(frozen=True)
class Journal:
    """
    An append-only event log at ``path``.

    Attributes
    ----------
    path : Path
        The JSONL file events are appended to.
    """

    path: Path

    def append(self, event: dict) -> None:
        """
        Write one event as one JSON line, flushed immediately.

        Parameters
        ----------
        event : dict
            The event to record. Must carry an ``event`` name.
        """
        line = json.dumps(event, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def load_journal(path: Path) -> list[dict]:
    """
    Read every event from a journal.

    Parameters
    ----------
    path : Path
        The JSONL journal.

    Returns
    -------
    list[dict]
        Events in write order.

    Raises
    ------
    ValueError
        When a line is not valid JSON, naming its line number.
    """
    journal_path = Path(path)
    if not journal_path.exists():
        return []
    events: list[dict] = []
    text = journal_path.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(
                f"journal {journal_path} line {number} is not valid JSON: {error}"
            ) from error
    return events


def replay(events: list[dict]) -> JournalPosition:
    """
    Fold journal events into the position a run resumes from.

    Parameters
    ----------
    events : list[dict]
        Events in write order, as :func:`load_journal` returns.

    Returns
    -------
    JournalPosition
        The folded position.
    """
    current_node: str | None = None
    attempts: dict[str, int] = {}
    completed: dict[str, None] = {}
    terminated = False
    term_action: str | None = None
    outputs: dict[str, dict] = {}
    changed_paths: list[str] = []
    checks: dict[str, bool] = {}
    pending_loop_to: str | None = None
    for event in events:
        name = event.get("event")
        node = event.get("node")
        if name == "run_started":
            continue
        if name == "node_started" and isinstance(node, str):
            current_node = node
            attempts[node] = event.get("attempt", attempts.get(node, 0) + 1)
        elif name == "node_completed" and isinstance(node, str):
            completed[node] = None
            current_node = None
            output = event.get("output")
            if isinstance(output, dict):
                outputs[node] = output
                paths = output.get("changed_paths")
                if isinstance(paths, list) and paths:
                    changed_paths = [str(path) for path in paths]
            if "passed" in event:
                checks[node] = bool(event["passed"])
        elif name == "gate_failed":
            current_node = None
            looped_to = event.get("looped_to")
            pending_loop_to = looped_to if isinstance(looped_to, str) else None
            checks = {}
        elif name == "gate_passed":
            current_node = None
        elif name == "term_reached":
            terminated = True
            term_action = event.get("action")
            current_node = None
    return JournalPosition(
        current_node=current_node,
        attempts=attempts,
        completed_nodes=list(completed),
        terminated=terminated,
        term_action=term_action,
        outputs=outputs,
        changed_paths=changed_paths,
        checks=checks,
        pending_loop_to=pending_loop_to,
    )
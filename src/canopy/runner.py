"""
The runner: a workflow walked to completion, journaled as it goes.

One run takes a ticket through the graph: model stages ask a responder
following a procedure skill, check stages run real commands in the
workdir, gates judge the run's state and loop failed work back bounded
by each node's ``max_retries``, and term nodes decide the outcome. Every
stage boundary is appended to a JSONL journal before it happens, so a
process that dies mid-run leaves a position :func:`resume` can fold and
continue from — completed nodes are skipped, an interrupted node is
re-attempted, and a finished run is not redone.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from canopy.journal import Journal, JournalPosition, load_journal, replay
from canopy.model import Node, Workflow
from canopy.predicates import evaluate_predicate
from canopy.skills import load_skill, validate_output

Responder = Callable[[str], str]

MAX_WALK_STEPS = 1000

# Attempt history longer than this spills to a sidecar file instead of
# diluting the prompt's middle (attention is not uniform across a long
# prompt; the middle is where context is most easily lost).
ATTEMPT_HISTORY_INLINE_LIMIT = 10_000


def _skill_name(node: Node) -> str:
    """
    The skill an llm node references; validation guarantees it is set.
    """
    assert node.skill is not None, "validation guarantees llm nodes have a skill"
    return node.skill


@dataclass(frozen=True)
class RunResult:
    """
    How a run ended.

    Attributes
    ----------
    outcome : str
        A term node's action, or ``failed``.
    node : str | None
        The term node the run ended at, when it reached one.
    failed_reason : str | None
        Why the run failed, when it did.
    outputs : dict[str, dict]
        The validated outputs of every llm stage, for the person who
        judges the result: the report, the review findings, the
        changed paths.
    changed_paths : list[str]
        Every path the run's stages claim to have touched.
    """

    outcome: str
    node: str | None = None
    failed_reason: str | None = None
    outputs: dict[str, dict] = field(default_factory=dict)
    changed_paths: list[str] = field(default_factory=list)


@dataclass
class _RunState:
    ticket: dict
    checks: dict[str, bool] = field(default_factory=dict)
    check_output: dict[str, str] = field(default_factory=dict)
    changed_paths: list[str] = field(default_factory=list)
    outputs: dict[str, dict] = field(default_factory=dict)


_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _first_balanced_object(text: str) -> str | None:  # noqa: C901
    """
    Find the first balanced ``{...}`` span in text, honoring strings.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_json(response: str) -> dict:
    """
    Pull one JSON object out of a response.

    Responders wrap payloads in prose and fences; the contract check
    should judge the payload, not the wrapper. The first JSON object
    found wins: a fenced block if there is one, else the first balanced
    ``{...}`` span.

    Parameters
    ----------
    response : str
        The raw response text.

    Returns
    -------
    dict
        The parsed payload.

    Raises
    ------
    ValueError
        When no JSON object can be found or parsed.
    """
    candidates: list[str] = []
    for match in _FENCE_PATTERN.finditer(response):
        candidates.append(match.group(1))
    balanced = _first_balanced_object(response)
    if balanced is not None:
        candidates.append(balanced)
    candidates.append(response)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("response contains no JSON object")


def _default_edge(workflow: Workflow, node_id: str) -> str | None:
    for edge in workflow.edges:
        if edge.from_node == node_id and edge.kind == "default":
            return edge.target
    return None


def _loop_edge(workflow: Workflow, gate: str) -> str | None:
    for edge in workflow.edges:
        if edge.from_node == gate and edge.kind == "loop":
            return edge.target
    return None


@dataclass
class AgenticRunner:
    """
    Walk a workflow for one ticket to a terminal outcome.

    Attributes
    ----------
    workflow : Workflow
        The validated document to walk.
    skills_dir : Path
        Directory holding one ``<name>/SKILL.md`` per referenced skill.
    ask : Responder
        Callable that takes a prompt and returns a response string.
    workdir : Path
        Where check commands run.
    journal_path : Path
        The JSONL journal for this run.
    """

    workflow: Workflow
    skills_dir: Path
    ask: Responder
    workdir: Path
    journal_path: Path
    check_timeout_s: int = 600

    def __post_init__(self) -> None:
        self.journal = Journal(path=self.journal_path)

    def _prompt(self, node: Node, state: _RunState, extra: str = "") -> str:
        skill = load_skill(Path(self.skills_dir) / _skill_name(node))
        # Prompt order follows attention, not chronology: models attend
        # most reliably to the start and the end of a long prompt. The
        # stable context (procedure, contract, ticket, prior outputs)
        # leads; the decisive evidence (changed paths, this round's
        # check results, the latest attempt) closes. Oversized attempt
        # history spills to a sidecar file the responder can read on
        # demand instead of diluting the middle.
        prompt = f"{skill.body}"
        if skill.contract is not None:
            prompt += (
                "\n\n# Output contract\n\nReply with a JSON object that "
                f"satisfies this schema:\n{json.dumps(skill.contract, indent=2)}"
            )
        prompt += f"\n\n# Ticket\n\n{json.dumps(state.ticket, indent=2)}"
        if state.outputs:
            evidence = json.dumps(state.outputs, indent=2)
            prompt += f"\n\n# Prior stage outputs\n\n{evidence}"
        prompt += f"\n\n# Working directory\n\n{Path(self.workdir).resolve()}"
        if state.changed_paths:
            prompt += "\n\n# Changed paths\n\n" + "\n".join(
                f"- {path}" for path in state.changed_paths
            )
        if state.check_output:
            prompt += "\n\n# Check results this round\n\n" + json.dumps(
                state.check_output, indent=2
            )
        if extra:
            if len(extra) > ATTEMPT_HISTORY_INLINE_LIMIT:
                sidecar = Path(self.workdir) / f"{node.id}.attempt-history.txt"
                sidecar.write_text(extra, encoding="utf-8")
                prompt += (
                    "\n\n# Previous attempt\n\nThe full attempt history is "
                    f"long; it is written to the history file "
                    f"{sidecar}. Read it if you need it."
                )
            else:
                prompt += f"\n\n# Previous attempt\n\n{extra}"
        return prompt

    def _run_llm(self, node: Node, state: _RunState) -> dict | None:
        """
        Ask once, validate against the contract, re-ask with violations
        while attempts remain. Returns the accepted output, or None when
        attempts ran out.
        """
        extra = ""
        spent = 0
        while spent <= node.max_retries:
            try:
                response = self.ask(self._prompt(node, state, extra))
            except RuntimeError as error:
                self.journal.append(
                    {"event": "stage_failed", "node": node.id, "reason": str(error)}
                )
                return None
            try:
                output = extract_json(response)
            except ValueError as error:
                spent += 1
                if spent > node.max_retries:
                    self.journal.append(
                        {"event": "stage_failed", "node": node.id, "reason": str(error)}
                    )
                    return None
                extra = f"Your previous response was not valid JSON: {error}"
                continue
            skill = load_skill(Path(self.skills_dir) / _skill_name(node))
            if skill.contract is not None:
                violations = validate_output(output, skill.contract)
                if violations:
                    spent += 1
                    if spent > node.max_retries:
                        self.journal.append(
                            {
                                "event": "stage_failed",
                                "node": node.id,
                                "reason": "; ".join(violations),
                            }
                        )
                        return None
                    extra = (
                        "Your previous response violated the output contract:\n"
                        + "\n".join(f"- {violation}" for violation in violations)
                    )
                    continue
            return output
        return None

    def _run_infra(self, node: Node) -> tuple[bool, str]:
        command = list(node.command or ())
        try:
            completed = subprocess.run(
                command,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.check_timeout_s,
            )
        except subprocess.TimeoutExpired:
            self.journal.append(
                {
                    "event": "check_failed",
                    "node": node.id,
                    "reason": f"timed out after {self.check_timeout_s}s",
                }
            )
            return False, f"timed out after {self.check_timeout_s}s"
        except OSError as error:
            self.journal.append(
                {
                    "event": "check_failed",
                    "node": node.id,
                    "reason": f"{command[0]}: {error}",
                }
            )
            return False, f"{command[0]}: {error}"
        passed = completed.returncode == 0
        event: dict = {
            "event": "check_passed" if passed else "check_failed",
            "node": node.id,
            "exit_code": completed.returncode,
        }
        if not passed:
            event["stderr"] = completed.stderr[-2000:]
        self.journal.append(event)
        detail = (
            "exit 0"
            if passed
            else f"exit {completed.returncode}: {completed.stderr[-2000:]}"
        )
        return passed, detail

    def _fail(self, reason: str, state: _RunState) -> RunResult:
        """
        Journal a terminal failure and build its result.

        Parameters
        ----------
        reason : str
            Why the run failed.
        state : _RunState
            The run's state, for the evidence fields.
        """
        self.journal.append({"event": "run_failed", "reason": reason})
        return RunResult(
            outcome="failed",
            failed_reason=reason,
            outputs=dict(state.outputs),
            changed_paths=list(state.changed_paths),
        )

    def run(self, ticket: dict) -> RunResult:  # noqa: C901, PLR0911, PLR0912, PLR0915
        """
        Walk the workflow for one ticket.

        Parameters
        ----------
        ticket : dict
            The work request, with optional ``labels`` and ``effort``.

        Returns
        -------
        RunResult
            How the run ended and why.
        """
        events = load_journal(self.journal_path)
        position = replay(events)
        if position.terminated:
            return RunResult(
                outcome=(position.failed_reason and "failed")
                or position.term_action
                or "needs_review",
                node=None,
                failed_reason=position.failed_reason,
                outputs=dict(position.outputs),
                changed_paths=list(position.changed_paths),
            )
        state = _RunState(
            ticket=ticket,
            checks=dict(position.checks),
            check_output={},
            changed_paths=list(position.changed_paths),
            outputs=dict(position.outputs),
        )
        attempts = dict(position.attempts)
        node_id: str | None = position.current_node
        if node_id is None:
            if position.pending_loop_to is not None:
                # the run died just as a gate looped back: resume at the
                # loop target, not the forward edge
                node_id = position.pending_loop_to
            else:
                node_id = self._entry(position)
        self.journal.append({"event": "run_started", "node": node_id})
        guard = 0
        while node_id is not None:
            guard += 1
            if guard > MAX_WALK_STEPS:
                return self._fail("run did not terminate", state)
            node = self.workflow.nodes[node_id]
            node_type = node.type
            attempt = attempts.get(node_id, 0) + 1
            attempts[node_id] = attempt
            if node_type == "llm":
                self.journal.append(
                    {"event": "node_started", "node": node_id, "attempt": attempt}
                )
                output = self._run_llm(node, state)
                if output is None:
                    return self._fail(
                        f"stage {node_id!r} exhausted its attempts", state
                    )
                state.outputs[node_id] = output
                paths = output.get("changed_paths")
                if isinstance(paths, list) and paths:
                    state.changed_paths = [str(path) for path in paths]
                self.journal.append(
                    {
                        "event": "node_completed",
                        "node": node_id,
                        "attempt": attempt,
                        "output": output,
                    }
                )
                node_id = _default_edge(self.workflow, node_id)
            elif node_type == "infra":
                self.journal.append(
                    {"event": "node_started", "node": node_id, "attempt": attempt}
                )
                passed, detail = self._run_infra(node)
                self.journal.append(
                    {
                        "event": "node_completed",
                        "node": node_id,
                        "passed": passed,
                        "attempt": attempt,
                    }
                )
                state.checks[node_id] = passed
                state.check_output[node_id] = detail
                node_id = _default_edge(self.workflow, node_id)
            elif node_type == "flow":
                predicate = node.predicate
                assert predicate is not None
                failed_checks = sorted(
                    node for node, ok in state.checks.items() if not ok
                )
                predicate_state = {
                    "checks": {
                        "all_passed": all(state.checks.values())
                        if state.checks
                        else False,
                        "failed": failed_checks,
                    },
                    "ticket": state.ticket,
                    "changed_paths": state.changed_paths,
                    "outputs": state.outputs,
                }
                holds = evaluate_predicate(predicate, predicate_state)
                if holds:
                    self.journal.append({"event": "gate_passed", "node": node_id})
                    node_id = _default_edge(self.workflow, node_id)
                else:
                    loop_target = _loop_edge(self.workflow, node_id)
                    self.journal.append(
                        {
                            "event": "gate_failed",
                            "node": node_id,
                            "looped_to": loop_target,
                            "failed_checks": failed_checks,
                        }
                    )
                    if loop_target is None:
                        return self._fail(
                            f"gate {node_id!r} failed with no loop edge", state
                        )
                    loop_node = self.workflow.nodes[loop_target]
                    loop_attempts = attempts.get(loop_target, 0) + 1
                    if loop_attempts > loop_node.max_retries + 1:
                        return self._fail(
                            f"gate {node_id!r} looped to {loop_target!r} "
                            f"more times than its max_retries allows",
                            state,
                        )
                    state.checks = {}
                    node_id = loop_target
            else:
                self.journal.append(
                    {
                        "event": "term_reached",
                        "node": node_id,
                        "action": node.action,
                    }
                )
                return RunResult(
                    outcome=node.action or "needs_review",
                    node=node_id,
                    outputs=dict(state.outputs),
                    changed_paths=list(state.changed_paths),
                )
        return self._fail("run left the graph", state)

    def _entry(self, position: JournalPosition) -> str:
        """
        Pick the node a fresh or fully-caught-up resume starts from.
        """
        if not position.completed_nodes:
            return self.workflow.start
        last = position.completed_nodes[-1]
        nxt = _default_edge(self.workflow, last)
        if nxt is not None:
            return nxt
        return self.workflow.start

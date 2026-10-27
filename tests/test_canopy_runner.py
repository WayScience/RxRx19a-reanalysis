"""
Tests for the canopy runner: the walk over a workflow, with a scripted
responder, real subprocess checks, gate loops, and journal resume
(canopy harness, runner slice).
"""

import json
from pathlib import Path

import pytest

from canopy.model import load_workflow
from canopy.responders import CommandResponder
from canopy.runner import AgenticRunner, RunResult, _RunState, extract_json


def _skills_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "skills"
    plan = directory / "plan"
    plan.mkdir(parents=True)
    (plan / "SKILL.md").write_text(
        "# Plan\n\nPlan the change.\n\n## Output contract\n\n```json\n"
        '{"type": "object", "required": ["summary", "changed_paths"],'
        ' "properties": {"summary": {"type": "string"},'
        ' "changed_paths": {"type": "array", "items": {"type": "string"}}},'
        ' "additionalProperties": false}\n'
        "```\n",
        encoding="utf-8",
    )
    review = directory / "review"
    review.mkdir()
    (review / "SKILL.md").write_text(
        "# Review\n\nReview the change.\n\n## Output contract\n\n```json\n"
        '{"type": "object", "required": ["verdict", "findings", "changed_paths"],'
        ' "properties": {"verdict": {"type": "string", "enum": ["pass", "fail"]},'
        ' "findings": {"type": "array", "items": {"type": "object"}},'
        ' "changed_paths": {"type": "array", "items": {"type": "string"}}},'
        ' "additionalProperties": false}\n'
        "```\n",
        encoding="utf-8",
    )
    return directory


def _workflow_path(tmp_path: Path, command: list[str], max_retries: int = 1) -> Path:
    document = {
        "workflow_version": "1.0",
        "start": "plan",
        "nodes": [
            {"id": "plan", "type": "llm", "skill": "plan", "max_retries": max_retries},
            {"id": "checks", "type": "infra", "command": command},
            {
                "id": "gate",
                "type": "flow",
                "kind": "gate",
                "predicate": {"kind": "checks", "status": "passed"},
            },
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [
            {"from": "plan", "to": "checks"},
            {"from": "checks", "to": "gate"},
            {"from": "gate", "to": "done"},
            {"from": "gate", "to": "plan", "kind": "loop"},
        ],
    }
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _runner(
    tmp_path: Path,
    ask,
    command: list[str] = ["true"],
    max_retries: int = 1,
    journal: str = "journal.jsonl",
) -> AgenticRunner:
    workflow = load_workflow(_workflow_path(tmp_path, command, max_retries))
    return AgenticRunner(
        workflow=workflow,
        skills_dir=_skills_dir(tmp_path),
        ask=ask,
        workdir=tmp_path,
        journal_path=tmp_path / journal,
    )


def _valid_output() -> str:
    return json.dumps({"summary": "fix the thing", "changed_paths": ["src/rerx/x.py"]})


def test_happy_path_reaches_term(tmp_path: Path) -> None:
    calls: list[str] = []

    def ask(prompt: str) -> str:
        calls.append(prompt)
        return _valid_output()

    runner = _runner(tmp_path, ask)
    result = runner.run(ticket={"title": "fix bug", "labels": ["bug"]})
    assert isinstance(result, RunResult)
    assert result.outcome == "needs_review"
    assert len(calls) == 1
    assert "Plan the change" in calls[0]
    assert "fix bug" in calls[0]


def test_passing_check_is_required_before_term(tmp_path: Path) -> None:
    # the gate holds on a failing check and loops back to plan once,
    # then the check passes and the run ends at the term
    script = tmp_path / "flaky.sh"
    script.write_text(
        "#!/bin/sh\n"
        'if [ -f "$0.flag" ]; then exit 0; else touch "$0.flag"; exit 1; fi\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    runner = _runner(
        tmp_path, lambda prompt: _valid_output(), command=["sh", str(script)]
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "needs_review"
    events = [
        json.loads(line)
        for line in (tmp_path / "journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    names = [event["event"] for event in events]
    assert "gate_failed" in names
    plan_starts = [
        event
        for event in events
        if event["event"] == "node_started" and event["node"] == "plan"
    ]
    assert len(plan_starts) == 2


def test_exhausted_loop_retries_fail_the_run(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        lambda prompt: _valid_output(),
        command=["false"],
        max_retries=1,
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "failed"


def test_zero_retries_fails_on_first_gate_loop(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        lambda prompt: _valid_output(),
        command=["false"],
        max_retries=0,
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "failed"
    events = [
        json.loads(line)
        for line in (tmp_path / "journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    plan_starts = [
        event
        for event in events
        if event["event"] == "node_started" and event["node"] == "plan"
    ]
    assert len(plan_starts) == 1


def test_invalid_then_valid_output_retries_within_attempt(tmp_path: Path) -> None:
    responses = iter(["this is not json", _valid_output()])

    def ask(prompt: str) -> str:
        return next(responses)

    runner = _runner(tmp_path, ask, max_retries=1)
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "needs_review"


def test_contract_violation_then_valid_output_retries(tmp_path: Path) -> None:
    responses = iter([json.dumps({"summary": "missing paths"}), _valid_output()])

    def ask(prompt: str) -> str:
        return next(responses)

    runner = _runner(tmp_path, ask, max_retries=1)
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "needs_review"


def test_persistent_contract_violations_fail_the_run(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        lambda prompt: json.dumps({"summary": "missing paths"}),
        max_retries=1,
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "failed"


def test_changed_paths_reach_gate_predicates(tmp_path: Path) -> None:
    # a paths gate: the implement output's changed_paths decide the branch
    document = {
        "workflow_version": "1.0",
        "start": "plan",
        "nodes": [
            {"id": "plan", "type": "llm", "skill": "plan", "max_retries": 1},
            {
                "id": "gate",
                "type": "flow",
                "kind": "gate",
                "predicate": {"kind": "paths", "glob": "src/rerx/**"},
            },
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [
            {"from": "plan", "to": "gate"},
            {"from": "gate", "to": "done"},
            {"from": "gate", "to": "plan", "kind": "loop"},
        ],
    }
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    skills = _skills_dir(tmp_path)
    runner = AgenticRunner(
        workflow=load_workflow(path),
        skills_dir=skills,
        ask=lambda prompt: _valid_output(),
        workdir=tmp_path,
        journal_path=tmp_path / "j.jsonl",
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "needs_review"
    # and now output that touches nothing matching the glob, with no
    # retries left after the loop, fails the run
    runner = AgenticRunner(
        workflow=load_workflow(path),
        skills_dir=skills,
        ask=lambda prompt: json.dumps(
            {"summary": "docs only", "changed_paths": ["README.md"]}
        ),
        workdir=tmp_path,
        journal_path=tmp_path / "j2.jsonl",
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "failed"


def test_resume_skips_completed_nodes(tmp_path: Path) -> None:
    # journal says plan completed and nothing else: resume walks from
    # checks and never re-asks the responder
    runner = _runner(tmp_path, lambda prompt: _valid_output())
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps({"event": "run_started", "node": "plan"})
        + "\n"
        + json.dumps({"event": "node_started", "node": "plan", "attempt": 1})
        + "\n"
        + json.dumps({"event": "node_completed", "node": "plan", "attempt": 1})
        + "\n",
        encoding="utf-8",
    )
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return _valid_output()

    runner.ask = ask
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "needs_review"
    assert asked == []


def test_resume_restarts_interrupted_node_with_next_attempt(tmp_path: Path) -> None:
    # journal says plan was re-entered (attempt 2) but never completed:
    # resume re-asks plan once and completes
    runner = _runner(tmp_path, lambda prompt: _valid_output())
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps({"event": "run_started", "node": "plan"})
        + "\n"
        + json.dumps({"event": "node_started", "node": "plan", "attempt": 1})
        + "\n"
        + json.dumps({"event": "node_completed", "node": "plan", "attempt": 1})
        + "\n"
        + json.dumps({"event": "gate_failed", "node": "gate", "looped_to": "plan"})
        + "\n"
        + json.dumps({"event": "node_started", "node": "plan", "attempt": 2})
        + "\n",
        encoding="utf-8",
    )
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return _valid_output()

    runner.ask = ask
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "needs_review"
    assert len(asked) == 1


def test_resume_of_finished_run_does_not_redo_work(tmp_path: Path) -> None:
    runner = _runner(tmp_path, lambda prompt: _valid_output())
    first = runner.run(ticket={"title": "t"})
    assert first.outcome == "needs_review"
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return _valid_output()

    runner.ask = ask
    second = runner.run(ticket={"title": "t"})
    assert second.outcome == "needs_review"
    assert asked == []


def test_missing_infra_command_fails_the_check_not_the_harness(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        lambda prompt: _valid_output(),
        command=["definitely-not-a-command-xyz"],
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "failed"


def test_extract_json_parses_direct_and_fenced_payloads() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('prose ```json\n{"a": 1}\n``` more') == {"a": 1}
    assert extract_json('thinking... {"a": 1} ...trailing') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no json here at all")


def test_extract_json_prefers_fenced_payload_over_earlier_prose_object() -> None:
    # prose mentions an example object before the fenced payload:
    # the fence wins
    response = 'as in {"note": "a"} reply with ```json\n{"a": 1}\n```'
    assert extract_json(response) == {"a": 1}


def test_extract_json_takes_first_balanced_object_without_a_fence() -> None:
    response = 'thinking {"note": "a"} then {"a": 1}'
    assert extract_json(response) == {"note": "a"}


def test_review_verdict_gates_the_term_via_output_predicate(tmp_path: Path) -> None:
    # review stage verdict routes the gate: pass goes to the term,
    # fail loops back to implement bounded by its max_retries
    document = {
        "workflow_version": "1.0",
        "start": "implement",
        "nodes": [
            {"id": "implement", "type": "llm", "skill": "plan", "max_retries": 1},
            {"id": "review", "type": "llm", "skill": "review", "max_retries": 0},
            {
                "id": "gate",
                "type": "flow",
                "kind": "gate",
                "predicate": {
                    "kind": "output",
                    "node": "review",
                    "field": "verdict",
                    "equals": "pass",
                },
            },
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [
            {"from": "implement", "to": "review"},
            {"from": "review", "to": "gate"},
            {"from": "gate", "to": "done"},
            {"from": "gate", "to": "implement", "kind": "loop"},
        ],
    }
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    skills = _skills_dir(tmp_path)
    outputs = iter(
        [
            json.dumps({"summary": "v1", "changed_paths": ["a.py"]}),
            json.dumps({"verdict": "fail", "findings": [], "changed_paths": []}),
            json.dumps({"summary": "v2", "changed_paths": ["a.py"]}),
            json.dumps({"verdict": "pass", "findings": [], "changed_paths": []}),
        ]
    )

    runner = AgenticRunner(
        workflow=load_workflow(path),
        skills_dir=skills,
        ask=lambda prompt: next(outputs),
        workdir=tmp_path,
        journal_path=tmp_path / "j.jsonl",
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "needs_review"
    events = [
        json.loads(line)
        for line in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [e["event"] for e in events].count("gate_failed") == 1


def test_resume_restores_gate_state(tmp_path: Path) -> None:
    # run dies after a gate loop: resume must go back to the loop target
    # (not forward), with checks cleared and attempts preserved
    runner = _runner(tmp_path, lambda prompt: _valid_output(), command=["false"])
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps({"event": "run_started", "node": "plan"})
        + "\n"
        + json.dumps({"event": "node_started", "node": "plan", "attempt": 1})
        + "\n"
        + json.dumps(
            {
                "event": "node_completed",
                "node": "plan",
                "attempt": 1,
                "output": {"summary": "v1", "changed_paths": ["src/a.py"]},
            }
        )
        + "\n"
        + json.dumps(
            {
                "event": "check_failed",
                "node": "checks",
                "exit_code": 1,
                "stderr": "boom",
            }
        )
        + "\n"
        + json.dumps({"event": "gate_failed", "node": "gate", "looped_to": "plan"})
        + "\n",
        encoding="utf-8",
    )
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return _valid_output()

    runner.ask = ask
    result = runner.run(ticket={"title": "t"})
    # max_retries=1 allows one loop round; the check still fails, so
    # the run must fail, but only after re-asking triage
    assert result.outcome == "failed"
    assert len(asked) == 1


def test_resume_after_loop_target_recompletes_runs_checks_next(
    tmp_path: Path,
) -> None:
    # plan completed, checks completed, the gate looped back to plan,
    # and plan completed again -- then the process died. Resume must
    # continue forward from plan (re-running checks with the new round),
    # not jump straight to the gate with the stale pre-loop checks.
    runner = _runner(tmp_path, lambda prompt: _valid_output(), command=["true"])
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps({"event": "run_started", "node": "plan"})
        + "\n"
        + json.dumps({"event": "node_started", "node": "plan", "attempt": 1})
        + "\n"
        + json.dumps(
            {
                "event": "node_completed",
                "node": "plan",
                "attempt": 1,
                "output": {"summary": "v1", "changed_paths": ["src/a.py"]},
            }
        )
        + "\n"
        + json.dumps({"event": "node_started", "node": "checks", "attempt": 1})
        + "\n"
        + json.dumps(
            {"event": "node_completed", "node": "checks", "passed": False, "attempt": 1}
        )
        + "\n"
        + json.dumps({"event": "gate_failed", "node": "gate", "looped_to": "plan"})
        + "\n"
        + json.dumps({"event": "node_started", "node": "plan", "attempt": 2})
        + "\n"
        + json.dumps(
            {
                "event": "node_completed",
                "node": "plan",
                "attempt": 2,
                "output": {"summary": "v2", "changed_paths": ["src/a.py"]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return _valid_output()

    runner.ask = ask
    result = runner.run(ticket={"title": "t"})
    # the fix round's checks ran again (the flag makes them pass), so
    # the gate passes this time without re-asking plan
    assert result.outcome == "needs_review"
    assert asked == []
    events = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line
    ]
    resumed = [
        e["node"]
        for e in events
        if e["event"] == "node_started" and e["node"] == "checks"
    ]
    assert len(resumed) == 2


def test_checks_accumulate_across_multiple_check_nodes(tmp_path: Path) -> None:
    # two check nodes: the gate must see both, and one failure fails it
    document = {
        "workflow_version": "1.0",
        "start": "triage",
        "nodes": [
            {"id": "triage", "type": "llm", "skill": "plan", "max_retries": 0},
            {"id": "lint", "type": "infra", "command": ["true"]},
            {"id": "pytest", "type": "infra", "command": ["false"]},
            {
                "id": "gate",
                "type": "flow",
                "kind": "gate",
                "predicate": {"kind": "checks", "status": "passed"},
            },
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [
            {"from": "triage", "to": "lint"},
            {"from": "lint", "to": "pytest"},
            {"from": "pytest", "to": "gate"},
            {"from": "gate", "to": "done"},
            {"from": "gate", "to": "triage", "kind": "loop"},
        ],
    }
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    runner = AgenticRunner(
        workflow=load_workflow(path),
        skills_dir=_skills_dir(tmp_path),
        ask=lambda prompt: _valid_output(),
        workdir=tmp_path,
        journal_path=tmp_path / "j.jsonl",
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "failed"
    events = [
        json.loads(line)
        for line in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    # the gate's verdict journaled which checks it saw
    gate_failed = next(e for e in events if e["event"] == "gate_failed")
    assert gate_failed["failed_checks"] == ["pytest"]


def test_prompt_carries_prior_stage_outputs_and_check_failures(tmp_path: Path) -> None:
    # the second stage's prompt must include the first stage's output
    # and, when checks failed, the failing check's output
    document = {
        "workflow_version": "1.0",
        "start": "triage",
        "nodes": [
            {"id": "triage", "type": "llm", "skill": "plan", "max_retries": 1},
            {"id": "review", "type": "llm", "skill": "review", "max_retries": 0},
            {"id": "checks", "type": "infra", "command": ["false"]},
            {
                "id": "gate",
                "type": "flow",
                "kind": "gate",
                "predicate": {"kind": "checks", "status": "passed"},
            },
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [
            {"from": "triage", "to": "review"},
            {"from": "review", "to": "checks"},
            {"from": "checks", "to": "gate"},
            {"from": "gate", "to": "done"},
            {"from": "gate", "to": "triage", "kind": "loop"},
        ],
    }
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        if "# Review" in prompt:
            return json.dumps({"verdict": "pass", "findings": [], "changed_paths": []})
        return _valid_output()

    runner = AgenticRunner(
        workflow=load_workflow(path),
        skills_dir=_skills_dir(tmp_path),
        ask=ask,
        workdir=tmp_path,
        journal_path=tmp_path / "j.jsonl",
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "failed"
    # round 1 review saw the triage output
    assert '"summary": "fix the thing"' in prompts[1]
    # round 2 triage saw the failing check evidence and prior outputs
    assert "# Check results this round" in prompts[2]
    assert "exit 1" in prompts[2]
    assert "# Prior stage outputs" in prompts[2]


def test_prompt_puts_decisive_evidence_at_the_end(tmp_path: Path) -> None:
    # attention budget is not uniform across a prompt: the decisive
    # evidence (check failures, changed paths, latest attempt) belongs
    # at the end, stable context (ticket, prior outputs) earlier
    document = {
        "workflow_version": "1.0",
        "start": "triage",
        "nodes": [
            {"id": "triage", "type": "llm", "skill": "plan", "max_retries": 1},
            {"id": "review", "type": "llm", "skill": "review", "max_retries": 0},
            {"id": "checks", "type": "infra", "command": ["false"]},
            {
                "id": "gate",
                "type": "flow",
                "kind": "gate",
                "predicate": {"kind": "checks", "status": "passed"},
            },
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [
            {"from": "triage", "to": "review"},
            {"from": "review", "to": "checks"},
            {"from": "checks", "to": "gate"},
            {"from": "gate", "to": "done"},
            {"from": "gate", "to": "triage", "kind": "loop"},
        ],
    }
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        if "# Review" in prompt:
            return json.dumps({"verdict": "fail", "findings": [], "changed_paths": []})
        return _valid_output()

    runner = AgenticRunner(
        workflow=load_workflow(path),
        skills_dir=_skills_dir(tmp_path),
        ask=ask,
        workdir=tmp_path,
        journal_path=tmp_path / "j.jsonl",
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "failed"
    fix_prompt = prompts[2]
    # order: ... prior outputs ... working dir, then the tail holds
    # changed paths, check results, and the latest attempt context
    assert fix_prompt.index("# Prior stage outputs") < fix_prompt.index(
        "# Check results this round"
    )
    assert fix_prompt.index("# Working directory") < fix_prompt.index("# Changed paths")
    assert fix_prompt.index("# Changed paths") < fix_prompt.index(
        "# Check results this round"
    )
    assert fix_prompt.index("# Check results this round") < len(fix_prompt)


def test_prompt_writes_history_sidecar_for_oversized_attempts(
    tmp_path: Path,
) -> None:
    # when the previous-attempt context is huge, the prompt keeps a
    # short tail and points at a sidecar file holding the full text
    big = "x" * 100_000
    runner = _runner(tmp_path, lambda prompt: _valid_output(), max_retries=1)
    node = runner.workflow.nodes["plan"]
    state = _RunState(ticket={"title": "t"})
    prompt = runner._prompt(node, state, extra=big)
    assert len(prompt) < 20_000
    assert "history file" in prompt.lower()
    sidecar = tmp_path / "plan.attempt-history.txt"
    assert sidecar.exists()
    assert big in sidecar.read_text(encoding="utf-8")


def test_command_responder_pipes_prompt_and_captures_stdout(tmp_path: Path) -> None:
    script = tmp_path / "responder.sh"
    script.write_text(
        '#!/bin/sh\nread prompt\nprintf \'{"summary": "got: %s"}\\n\' "$prompt"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    responder = CommandResponder(command=("sh", str(script)))
    assert responder("hello ticket") == '{"summary": "got: hello ticket"}'


def test_command_responder_raises_when_command_fails(tmp_path: Path) -> None:
    responder = CommandResponder(command=("false",))
    with pytest.raises(RuntimeError, match="responder"):
        responder("prompt")


def test_command_responder_times_out_a_hanging_command() -> None:
    responder = CommandResponder(command=("sleep", "10"), timeout_s=1)
    with pytest.raises(RuntimeError, match="timed out"):
        responder("prompt")


def test_command_responder_reports_missing_command_as_stage_failure() -> None:
    responder = CommandResponder(command=("definitely-not-a-command-xyz",))
    with pytest.raises(RuntimeError, match="could not start"):
        responder("prompt")


def test_prompt_includes_the_output_contract(tmp_path: Path) -> None:
    # the model must see the schema it is required to satisfy
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return _valid_output()

    runner = _runner(tmp_path, ask)
    runner.run(ticket={"title": "t"})
    assert "changed_paths" in prompts[0]
    assert "required" in prompts[0]


def test_resume_of_failed_run_does_not_redo_work(tmp_path: Path) -> None:
    # a failed run is terminal: re-running with the same journal must
    # not rewind to the loop target and redo stages
    runner = _runner(
        tmp_path,
        lambda prompt: _valid_output(),
        command=["false"],
        max_retries=0,
    )
    first = runner.run(ticket={"title": "t"})
    assert first.outcome == "failed"
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return _valid_output()

    runner.ask = ask
    second = runner.run(ticket={"title": "t"})
    assert second.outcome == "failed"
    assert second.failed_reason is not None
    assert asked == []


def test_resume_of_finished_run_returns_the_evidence(tmp_path: Path) -> None:
    runner = _runner(tmp_path, lambda prompt: _valid_output())
    first = runner.run(ticket={"title": "t"})
    assert first.outcome == "needs_review"
    assert first.outputs["plan"]["summary"] == "fix the thing"
    second = runner.run(ticket={"title": "t"})
    assert second.outcome == "needs_review"
    assert second.outputs["plan"]["summary"] == "fix the thing"
    assert second.changed_paths == ["src/rerx/x.py"]


def test_runner_check_times_out(tmp_path: Path) -> None:
    # a check that never exits must fail the check, not hang the run
    document = {
        "workflow_version": "1.0",
        "start": "checks",
        "nodes": [
            {"id": "checks", "type": "infra", "command": ["sleep", "30"]},
            {
                "id": "gate",
                "type": "flow",
                "kind": "gate",
                "predicate": {"kind": "checks", "status": "passed"},
            },
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [
            {"from": "checks", "to": "gate"},
            {"from": "gate", "to": "done"},
            {"from": "gate", "to": "checks", "kind": "loop"},
        ],
    }
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    runner = AgenticRunner(
        workflow=load_workflow(path),
        skills_dir=_skills_dir(tmp_path),
        ask=lambda prompt: _valid_output(),
        workdir=tmp_path,
        journal_path=tmp_path / "j.jsonl",
        check_timeout_s=1,
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "failed"
    events = [
        json.loads(line)
        for line in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert any(
        e["event"] == "check_failed" and "timed out" in e.get("reason", "")
        for e in events
    )


def test_retry_prompt_names_the_previous_violations(tmp_path: Path) -> None:
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        if len(prompts) == 1:
            return json.dumps({"summary": "missing paths"})
        return _valid_output()

    runner = _runner(tmp_path, ask, max_retries=1)
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "needs_review"
    assert len(prompts) == 2
    assert "changed_paths" in prompts[1]

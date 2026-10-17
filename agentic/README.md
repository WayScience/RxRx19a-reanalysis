# Agentic Workflow Harness

Runs tickets through a small, testable loop: a stage follows a written
procedure, a check runs a real command, a gate holds the run until the
checks pass, and the result lands in a terminal action a person can
review. Every step is journaled, so an interrupted run resumes where it
stopped instead of starting over.

## The pieces

| Path | What it does |
| --- | --- |
| `src/rerx/agentic/model.py` | Workflow documents and validation |
| `src/rerx/agentic/predicates.py` | How a gate judges a run |
| `src/rerx/agentic/skills.py` | Skill loading and output contracts |
| `src/rerx/agentic/journal.py` | The JSONL event journal and replay |
| `src/rerx/agentic/runner.py` | The walk over the graph |
| `src/rerx/agentic/responders.py` | The responder protocol |
| `src/rerx/agentic/cli.py` | The `rerx agent` commands |
| `workflows/default.json` | The shipped loop |
| `skills/<name>/SKILL.md` | Procedure skills |

## The shipped loop

`workflows/default.json` runs a ticket through triage, a review stage,
the repository's test suite, gates that loop failed work back for another
attempt, and a terminal that hands the result to a person.

```json
{
  "workflow_version": "1.0",
  "start": "triage",
  "nodes": [
    {"id": "triage", "type": "llm", "skill": "triage", "max_retries": 1},
    {"id": "review", "type": "llm", "skill": "review", "max_retries": 0},
    {"id": "review-gate", "type": "flow", "kind": "gate", "predicate": {"kind": "output", "node": "review", "field": "verdict", "equals": "pass"}},
    {"id": "checks", "type": "infra", "command": ["uv", "run", "--frozen", "pytest", "-q"]},
    {"id": "gate", "type": "flow", "kind": "gate", "predicate": {"kind": "checks", "status": "passed"}},
    {"id": "done", "type": "term", "action": "needs_review"}
  ],
  "edges": [
    {"from": "triage", "to": "review"},
    {"from": "review", "to": "review-gate"},
    {"from": "review-gate", "to": "checks"},
    {"from": "checks", "to": "gate"},
    {"from": "gate", "to": "done"},
    {"from": "gate", "to": "triage", "kind": "loop"},
    {"from": "review-gate", "to": "triage", "kind": "loop"}
  ]
}
```

The review stage reads the change with the review skill's rules: findings
need a file, a line, and what breaks; no repeats of findings already
reported; judge the change, not the style. The review gate holds the run
until the review's verdict is `pass`, so a run cannot finish with an open
finding. Both gates loop back to triage, bounded by its `max_retries` —
the loop's round cap.

## Running a ticket

A responder is any command that reads the prompt on stdin and writes
its response to stdout. Point it at a script that wraps a model backend,
or use a stub for testing.

```bash
# validate a document
uv run --frozen rerx agent validate agentic/workflows/default.json

# run the example ticket
uv run --frozen rerx agent run \
  --workflow agentic/workflows/default.json \
  --skills agentic/skills \
  --responder path/to/responder \
  --ticket agentic/tickets/example.json \
  --workdir . \
  --journal path/to/journal.jsonl
```

The command prints the outcome and exits non-zero when the run fails.
Re-running with the same journal resumes: completed stages are skipped,
and an interrupted stage restarts with its next attempt.

The journal is one JSON event per line, in the order the run wrote them:

```json
{"event": "node_started", "node": "triage", "attempt": 1}
{"event": "node_completed", "node": "triage", "attempt": 1}
{"event": "check_passed", "exit_code": 0, "node": "checks"}
{"event": "gate_passed", "node": "gate"}
{"event": "term_reached", "action": "needs_review", "node": "done"}
```

## Writing a skill

A skill is a directory with a `SKILL.md`: the procedure a model stage
follows, plus an output contract the harness checks responses against
before accepting them. The contract is a fenced ```json block under an
`## Output contract` heading.

```markdown
# Plan

Plan the change a ticket asks for.

## Steps

1. Read the ticket.
1. Name the exact files the change will touch.

## Output contract

```json
{
  "type": "object",
  "required": ["summary", "changed_paths"],
  "properties": {
    "summary": {"type": "string"},
    "changed_paths": {"type": "array", "items": {"type": "string"}}
  },
  "additionalProperties": false
}
```
```

A response that violates the contract is retried with the violations
named, while the stage's `max_retries` allows.

## Authoring rules

- Every node must be reachable from `start`, and every node must be able
  to reach a term node.
- A gate must have a `loop` edge to an earlier stage. The loop target's
  `max_retries` bounds how many times the gate can send work back.
- A term node ends the run with one of `open_pr`, `needs_review`, or
  `failed`.
- Documents are closed: a field the schema does not know is an error.
- Predicates are flat: `checks`, `effort`, `labels`, `paths`, or
  `output` (route on a prior stage's validated output — for example, a
  review stage's `verdict`).

## Verification

```bash
uv run --frozen pytest tests/test_agentic_*.py -q
uv run --frozen rerx agent validate agentic/workflows/default.json
```

Every stage boundary writes to the journal before the work happens, so a
crash leaves a position replay can fold and continue from. The loop
cannot run forever: the guard caps a walk at 1000 steps, and a gate
exhausts its target's attempts before failing the run.
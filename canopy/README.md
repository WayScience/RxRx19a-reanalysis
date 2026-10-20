# Canopy

Canopy is a reusable workflow harness. It runs tickets through a small,
testable loop: a stage follows a written procedure, a check runs a real
command, a gate holds the run until the checks pass, and the result lands
in a terminal action a person can review. Every step is journaled, so an
interrupted run resumes where it stopped instead of starting over.

A ticket is the request a run carries: a short description of the work
(usually a project issue), optional labels, and an optional effort
estimate (`xs`, `s`, `m`, `l`, `xl`). The harness is project-agnostic:
workflow documents, skills, and the responder command are per-project.

## The pieces

| Path                       | What it does                       |
| -------------------------- | ---------------------------------- |
| `src/canopy/model.py`      | Workflow documents and validation  |
| `src/canopy/predicates.py` | How a gate judges a run            |
| `src/canopy/skills.py`     | Skill loading and output contracts |
| `src/canopy/journal.py`    | The JSONL event journal and replay |
| `src/canopy/runner.py`     | The walk over the graph            |
| `src/canopy/responders.py` | The responder protocol             |
| `src/canopy/cli.py`        | The `rerx agent` command mount     |
| `workflows/default.json`   | The shipped loop                   |
| `skills/<name>/SKILL.md`   | Procedure skills                   |

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
    {"id": "checks", "type": "infra", "command": ["uv", "run", "--frozen", "pytest", "-q", "--no-cov"]},
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

The review stage reads the change with the review skill's rules: every
finding names a file, a line, and what breaks; a re-reported finding
from an earlier round must say why it still stands; judge the change,
not the style. The review gate holds the run until the review's verdict
is `pass`, so a run cannot finish with an open finding. Both gates loop
back to triage, bounded by its `max_retries` — the loop's round cap.

## Running a ticket

A responder is any command that reads the prompt on stdin and writes
its response to stdout. Point it at a script that wraps a model backend,
or use a stub for testing.

```bash
# validate a document
uv run --frozen rerx agent validate canopy/workflows/default.json

# run the example ticket
uv run --frozen rerx agent run \
  --workflow canopy/workflows/default.json \
  --skills canopy/skills \
  --responder path/to/responder \
  --ticket canopy/tickets/example.json \
  --workdir . \
  --journal path/to/journal.jsonl
```

The command prints the outcome and exits non-zero when the run fails.
Re-running with the same journal resumes: completed stages are skipped,
and an interrupted stage restarts with its next attempt.

The journal is one JSON event per line, in the order the run wrote them:

```json
{"event": "run_started", "node": "triage"}
{"event": "node_started", "node": "triage", "attempt": 1}
{"event": "node_completed", "node": "triage", "attempt": 1, "output": {"category": "docs", "summary": "fix the docs", "changed_paths": ["docs/x.md"]}}
{"event": "node_started", "node": "checks", "attempt": 1}
{"event": "check_passed", "exit_code": 0, "node": "checks"}
{"event": "node_completed", "node": "checks", "passed": true, "attempt": 1}
{"event": "gate_passed", "node": "gate"}
{"event": "term_reached", "action": "needs_review", "node": "done"}
```

A failed run ends with a `run_failed` event carrying the reason; both
that and `term_reached` are terminal, so re-running with the same
journal returns the result without redoing stages.

## Writing a skill

A skill is a directory with a `SKILL.md`: the procedure a model stage
follows, plus an output contract the harness checks responses against
before accepting them. The contract is a fenced \`\`\`json block under an
`## Output contract` heading.

````markdown
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
````

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

## Reusing Canopy in another project

The `src/canopy` package has no rerx imports and mounts under any
Python CLI. Copy the package, add one line that mounts `AgentCLI`, and
write the project's own workflow document, skills, and responder. The
`rerx agent` command here is one example of that mount.

## Verification

```bash
uv run --frozen pytest tests/test_canopy_*.py -q
uv run --frozen rerx agent validate canopy/workflows/default.json
```

Every stage boundary writes to the journal before the work happens, so a
crash leaves a position replay can fold and continue from. The loop
cannot run forever: the guard caps a walk at 1000 steps, and a gate
exhausts its target's attempts before failing the run.

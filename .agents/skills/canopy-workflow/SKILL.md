______________________________________________________________________

## name: canopy-workflow description: 'Runs tickets through the Canopy workflow harness: validate, run, resume. Use when operating the loop under canopy/ or adding skills or workflows.'

# Canopy Workflow

## Overview

Canopy is a reusable workflow harness. It runs one ticket — the request a
run carries, usually a project issue with optional labels and an effort
estimate — through a JSON workflow document: model stages follow
procedure skills under `canopy/skills/`, check stages run real commands,
gates loop failed work back bounded by `max_retries`, and a term node
decides the outcome. Every stage boundary is journaled, so an
interrupted run resumes where it stopped.

## When to use

- Running a ticket through a workflow
- Adding or editing a procedure skill
- Authoring or changing a workflow document
- Resuming an interrupted run

## Pieces

- `src/canopy/model.py` — the document model and validation
- `src/canopy/predicates.py` — how a gate judges run state
- `src/canopy/skills.py` — skill loading and output contracts
- `src/canopy/journal.py` — the JSONL event journal and replay
- `src/canopy/runner.py` — the walk itself
- `src/canopy/responders.py` — the responder protocol
- `canopy/workflows/default.json` — the shipped loop
- `canopy/skills/<name>/SKILL.md` — procedure skills

## Operating the loop

1. Validate a document before running:
   `uv run --frozen rerx agent validate canopy/workflows/default.json`
1. Run a ticket:
   `uv run --frozen rerx agent run --workflow canopy/workflows/default.json --skills canopy/skills --responder <command> --ticket canopy/tickets/example.json --workdir . --journal <journal path>`
1. The responder command reads the prompt on stdin, writes its response
   to stdout. Any command that wraps a model backend works.
1. Resume: re-run the same command with the same journal path.
1. Read a run's history: the journal is one JSON event per line.

The `rerx agent` command mounts Canopy under this project's CLI; the
`canopy` package itself has no rerx imports and can be lifted to another
project as-is.

## Authoring rules

- A skill's output contract lives under an `## Output contract` heading
  in one fenced \`\`\`json block; the harness validates responses against
  it before accepting them.
- Every node must be reachable from start and able to reach a term.
- A gate must have a loop edge to an earlier stage, and a loop target's
  `max_retries` bounds the loop.
- Keep documents closed: unknown fields are validation errors.

## Verification

- `uv run --frozen pytest tests/test_canopy_*.py -q`
- `uv run --frozen rerx agent validate canopy/workflows/default.json`

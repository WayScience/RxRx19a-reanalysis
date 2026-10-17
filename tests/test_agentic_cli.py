"""
Tests for the agentic CLI commands (agentic harness, CLI slice).
"""

import json
from pathlib import Path

import pytest

from rerx.agentic.cli import AgentCLI


def test_validate_reports_a_valid_document(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    document = {
        "workflow_version": "1.0",
        "start": "plan",
        "nodes": [
            {"id": "plan", "type": "llm", "skill": "plan", "max_retries": 1},
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [{"from": "plan", "to": "done"}],
    }
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    cli = AgentCLI()
    cli.validate(str(path))
    captured = capsys.readouterr()
    assert "valid" in captured.out


def test_validate_reports_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    document = {"workflow_version": "9.9", "start": "nope", "nodes": [], "edges": []}
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    cli = AgentCLI()
    try:
        cli.validate(str(path))
        raised = False
    except SystemExit:
        raised = True
    assert raised
    captured = capsys.readouterr()
    assert "invalid" in captured.err or "invalid" in captured.out


def test_run_walks_default_style_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    skills = tmp_path / "skills"
    skill_dir = skills / "echo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Echo\n\nReply with the ticket JSON verbatim.\n",
        encoding="utf-8",
    )
    document = {
        "workflow_version": "1.0",
        "start": "echo",
        "nodes": [
            {"id": "echo", "type": "llm", "skill": "echo", "max_retries": 0},
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [{"from": "echo", "to": "done"}],
    }
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps(document), encoding="utf-8")
    responder = tmp_path / "responder.sh"
    responder.write_text(
        '#!/bin/sh\n'
        'cat\n',
        encoding="utf-8",
    )
    responder.chmod(0o755)
    ticket = tmp_path / "ticket.json"
    ticket.write_text('{"title": "t"}\n', encoding="utf-8")
    journal = tmp_path / "journal.jsonl"
    cli = AgentCLI()
    cli.run(
        workflow=str(workflow),
        skills=str(skills),
        responder=str(responder),
        ticket=str(ticket),
        workdir=str(tmp_path),
        journal=str(journal),
    )
    captured = capsys.readouterr()
    assert "needs_review" in captured.out
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line]
    assert events[-1]["event"] == "term_reached"


def test_resume_continues_from_journal(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    # same setup as run, but the journal already holds the llm node's
    # completion: the responder must not be invoked again
    skills = tmp_path / "skills"
    skill_dir = skills / "echo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Echo\n\nReply with the ticket JSON verbatim.\n",
        encoding="utf-8",
    )
    document = {
        "workflow_version": "1.0",
        "start": "echo",
        "nodes": [
            {"id": "echo", "type": "llm", "skill": "echo", "max_retries": 0},
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [{"from": "echo", "to": "done"}],
    }
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps(document), encoding="utf-8")
    responder = tmp_path / "responder.sh"
    responder.write_text('#!/bin/sh\necho "not json"\n', encoding="utf-8")
    responder.chmod(0o755)
    ticket = tmp_path / "ticket.json"
    ticket.write_text('{"title": "t"}\n', encoding="utf-8")
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps({"event": "node_started", "node": "echo", "attempt": 1}) + "\n"
        + json.dumps({"event": "node_completed", "node": "echo", "attempt": 1}) + "\n",
        encoding="utf-8",
    )
    cli = AgentCLI()
    cli.run(
        workflow=str(workflow),
        skills=str(skills),
        responder=str(responder),
        ticket=str(ticket),
        workdir=str(tmp_path),
        journal=str(journal),
    )
    captured = capsys.readouterr()
    assert "needs_review" in captured.out


def test_run_requires_existing_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    import pytest

    cli = AgentCLI()
    with pytest.raises(SystemExit):
        cli.run(
            workflow=str(tmp_path / "missing.json"),
            skills=str(tmp_path),
            responder="true",
            ticket=str(tmp_path / "ticket.json"),
            workdir=str(tmp_path),
            journal=str(tmp_path / "journal.jsonl"),
        )
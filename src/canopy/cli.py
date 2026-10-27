"""
Agentic harness CLI: validate documents and run tickets.

A thin mount over :mod:`canopy.runner` — everything real happens
there and in tests; this module only parses arguments, wires the
responder, and prints the outcome.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from canopy.model import load_workflow, validate_document
from canopy.responders import CommandResponder
from canopy.runner import AgenticRunner, RunResult


class AgentCLI:
    """
    Workflow validation and ticket runs.
    """

    def validate(self, workflow: str) -> None:
        """
        Validate a workflow document and print the verdict.

        Args:
            workflow (str):
                Path to the workflow JSON document.
        """
        path = Path(workflow)
        if not path.is_file():
            print(f"no such workflow: {workflow}", file=sys.stderr)
            raise SystemExit(1)
        document = json.loads(path.read_text(encoding="utf-8"))
        errors = validate_document(document)
        if errors:
            print(f"invalid workflow document ({len(errors)} problems):")
            for error in errors:
                print(f"- {error}")
            raise SystemExit(1)
        print(f"valid workflow document: {workflow}")

    def run(  # noqa: PLR0913, PLR0917
        self,
        workflow: str,
        skills: str,
        responder: str,
        ticket: str,
        workdir: str,
        journal: str,
    ) -> None:
        """
        Run one ticket through a workflow.

        The responder is a command that reads the prompt on stdin and
        writes its response to stdout. Re-running with the same journal
        resumes the run.

        Args:
            workflow (str):
                Path to the workflow JSON document.
            skills (str):
                Directory of skill directories, each holding SKILL.md.
            responder (str):
                The responder command's path, or a command name on PATH.
            ticket (str):
                Path to the ticket JSON file.
            workdir (str):
                Directory check commands run in.
            journal (str):
                Path to the run's JSONL journal.
        """
        workflow_path = Path(workflow)
        if not workflow_path.is_file():
            print(f"no such workflow: {workflow}", file=sys.stderr)
            raise SystemExit(1)
        ticket_path = Path(ticket)
        if not ticket_path.is_file():
            print(f"no such ticket: {ticket}", file=sys.stderr)
            raise SystemExit(1)
        parsed_ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
        responder_path = shutil.which(responder) or responder
        ask = CommandResponder(command=(str(responder_path),))
        runner = AgenticRunner(
            workflow=load_workflow(workflow_path),
            skills_dir=Path(skills),
            ask=ask,
            workdir=Path(workdir),
            journal_path=Path(journal),
        )
        result = runner.run(ticket=parsed_ticket)
        _print_result(result)
        if result.outcome == "failed":
            raise SystemExit(1)


def _print_result(result: RunResult) -> None:
    """
    Print a run's outcome and the evidence a person reads to judge it.
    """
    print(f"outcome: {result.outcome}")
    if result.node is not None:
        print(f"term: {result.node}")
    if result.failed_reason is not None:
        print(f"reason: {result.failed_reason}")
    if result.changed_paths:
        print("changed paths:")
        for path in result.changed_paths:
            print(f"- {path}")
    for node_id, output in sorted(result.outputs.items()):
        if "report" in output:
            print(f"report ({node_id}):\n{output['report']}")
        if "verdict" in output:
            print(f"review verdict ({node_id}): {output['verdict']}")
            for finding in output.get("findings", []):
                print(
                    f"- {finding.get('file')}:{finding.get('line')}: "
                    f"{finding.get('problem')}"
                )

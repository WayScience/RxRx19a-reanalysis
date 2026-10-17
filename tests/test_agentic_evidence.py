"""
Tests for the human-facing evidence in run results and CLI output
(agentic harness, evidence slice).
"""

import json
from pathlib import Path

from rerx.agentic.model import load_workflow
from rerx.agentic.runner import AgenticRunner


def _skills(tmp_path: Path) -> Path:
    skills = tmp_path / "skills"
    for name, body in {
        "triage": '{"type": "object", "required": ["category", "changed_paths"], "properties": {"category": {"type": "string"}, "changed_paths": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": false}',
        "review": '{"type": "object", "required": ["verdict", "findings", "changed_paths"], "properties": {"verdict": {"type": "string", "enum": ["pass", "fail"]}, "findings": {"type": "array", "items": {"type": "object"}}, "changed_paths": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": false}',
    }.items():
        skill = skills / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"# {name}\n\nDo the thing.\n\n## Output contract\n\n```json\n{body}\n```\n",
            encoding="utf-8",
        )
    return skills


def test_run_result_carries_evidence_for_the_human(tmp_path: Path) -> None:
    document = {
        "workflow_version": "1.0",
        "start": "triage",
        "nodes": [
            {"id": "triage", "type": "llm", "skill": "triage", "max_retries": 0},
            {"id": "review", "type": "llm", "skill": "review", "max_retries": 0},
            {"id": "done", "type": "term", "action": "needs_review"},
        ],
        "edges": [
            {"from": "triage", "to": "review"},
            {"from": "review", "to": "done"},
        ],
    }
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    responses = iter(
        [
            json.dumps(
                {"category": "bug", "changed_paths": ["src/rerx/bug.py"]}
            ),
            json.dumps(
                {
                    "verdict": "fail",
                    "findings": [
                        {
                            "file": "src/rerx/bug.py",
                            "line": 10,
                            "problem": "off-by-one",
                        }
                    ],
                    "changed_paths": [],
                }
            ),
        ]
    )
    runner = AgenticRunner(
        workflow=load_workflow(path),
        skills_dir=_skills(tmp_path),
        ask=lambda prompt: next(responses),
        workdir=tmp_path,
        journal_path=tmp_path / "j.jsonl",
    )
    result = runner.run(ticket={"title": "t"})
    assert result.outcome == "needs_review"
    assert result.changed_paths == ["src/rerx/bug.py"]
    assert result.outputs["review"]["verdict"] == "fail"
    assert result.outputs["review"]["findings"][0]["file"] == "src/rerx/bug.py"
    assert result.outputs["triage"]["category"] == "bug"
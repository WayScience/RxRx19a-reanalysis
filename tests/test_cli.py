"""
Tests for the RunsCLI (plan.md section 26 cleanup commands).
"""

import subprocess
from pathlib import Path

import pytest

from rerx.cli import RunsCLI
from rerx.runs import PROTECTED, RUNNING, make_run_dir, make_run_id


def _env(tmp_path: Path) -> dict[str, str]:
    peta = tmp_path / "peta"
    peta.mkdir()
    return {
        "RERX_SOURCE_ROOT": str(tmp_path / "source"),
        "RERX_PETA_ROOT": str(peta),
        "RERX_MIRROR_ROOT": str(tmp_path / "mirror"),
    }


def _make_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str = "pilot"
) -> tuple[str, Path]:
    env = _env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    run_id = make_run_id(scope=scope, commit="abc1234")
    run_dir = make_run_dir(Path(env["RERX_PETA_ROOT"]) / "datasets", run_id)
    return run_id, run_dir


def test_runs_list_prints_nothing_found_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    RunsCLI().list()
    assert "no runs found" in capsys.readouterr().out


def test_runs_list_shows_created_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    run_id, _ = _make_run(tmp_path, monkeypatch)
    RunsCLI().list()
    out = capsys.readouterr().out
    assert run_id in out


def test_runs_inspect_reports_running_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    run_id, run_dir = _make_run(tmp_path, monkeypatch)
    (run_dir / RUNNING).write_text("written_at_utc=now\n")
    RunsCLI().inspect(run_id)
    out = capsys.readouterr().out
    assert "status: running" in out


def test_runs_inspect_rejects_missing_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit):
        RunsCLI().inspect("rxrx19a-pilot-20260101T000000Z-gabc1234")


def test_runs_delete_dry_run_does_not_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    run_id, run_dir = _make_run(tmp_path, monkeypatch)
    RunsCLI().delete(run_id)
    assert run_dir.exists()
    assert "would delete" in capsys.readouterr().out


def test_runs_delete_requires_yes_even_without_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, run_dir = _make_run(tmp_path, monkeypatch)
    RunsCLI().delete(run_id, dry_run=False, yes=False)
    assert run_dir.exists()


def test_runs_delete_removes_with_dry_run_false_and_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, run_dir = _make_run(tmp_path, monkeypatch)
    RunsCLI().delete(run_id, dry_run=False, yes=True)
    assert not run_dir.exists()


def test_runs_delete_refuses_protected_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    run_id, run_dir = _make_run(tmp_path, monkeypatch)
    (run_dir / PROTECTED).write_text("written_at_utc=now\n")
    with pytest.raises(SystemExit):
        RunsCLI().delete(run_id, dry_run=False, yes=True)
    assert run_dir.exists()
    assert "refusing to delete protected run" in capsys.readouterr().err


def test_runs_protect_writes_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, run_dir = _make_run(tmp_path, monkeypatch)
    RunsCLI().protect(run_id)
    assert (run_dir / PROTECTED).exists()


def test_runs_delete_rejects_malformed_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError):
        RunsCLI().delete("not-a-real-run-id")


def test_show_message_cli() -> None:
    """Run the installed CLI entry point with a message."""
    output = subprocess.run(
        ["uv", "run", "--frozen", "rerx", "show_message", "--message=Hello terminal!"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Hello terminal!" in output.stdout

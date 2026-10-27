"""Cheap, flushed process-memory diagnostics."""

import io
from types import SimpleNamespace

import pytest

from rerx import diagnostics


def test_log_stage_public_api(capsys, monkeypatch) -> None:
    monkeypatch.setattr(diagnostics, "memory_mib", lambda: (12.5, 20.0))
    diagnostics.log_stage("plate=25 stage=start", rows=30, columns=8)
    output = capsys.readouterr().out
    assert "plate=25 stage=start rows=30 columns=8" in output
    assert "rss_mib=12.5 peak_rss_mib=20.0" in output


def test_stage_flushes_and_does_not_suppress_errors(monkeypatch) -> None:
    class Output(io.StringIO):
        flushes = 0

        def flush(self):
            self.flushes += 1
            super().flush()

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    with (
        pytest.raises(ValueError, match="original"),
        diagnostics.stage(
            "probe", experiment="e", plate="p", profiler="cp", shape=(2, 3)
        ) as progress,
    ):
        assert output.flushes == 1
        assert "event=start" in output.getvalue()
        progress.shape = (1, 4)
        raise ValueError("original")
    assert output.flushes == 2
    assert "event=failed" in output.getvalue()
    assert "rows=1 cols=4" in output.getvalue()
    assert "error=ValueError" in output.getvalue()


@pytest.mark.parametrize("platform,peak", [("linux", 2048), ("darwin", 2097152)])
def test_memory_units_and_linux_current(monkeypatch, platform, peak) -> None:
    monkeypatch.setattr(diagnostics.sys, "platform", platform)
    monkeypatch.setattr(
        diagnostics,
        "resource",
        SimpleNamespace(
            RUSAGE_SELF=0, getrusage=lambda _: SimpleNamespace(ru_maxrss=peak)
        ),
    )
    monkeypatch.setattr(
        diagnostics.Path, "read_text", lambda *a, **k: "VmRSS:\t1024 kB\n"
    )
    current, high_water = diagnostics.memory_mib()
    assert high_water == 2.0
    assert current == (1.0 if platform == "linux" else None)


def test_memory_falls_back_without_proc_or_resource(monkeypatch) -> None:
    def unavailable(*_args: object, **_kwargs: object):
        raise OSError("not available")

    monkeypatch.setattr(diagnostics.sys, "platform", "linux")
    monkeypatch.setattr(diagnostics.Path, "read_text", unavailable)
    monkeypatch.setattr(diagnostics, "resource", None)
    assert diagnostics.memory_mib() == (None, None)


def test_memory_real_process() -> None:
    _, peak = diagnostics.memory_mib()
    assert peak is not None and peak > 0

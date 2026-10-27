"""
Tests for the shipped experiment specs (experiments/*/spec.yaml).
"""

from pathlib import Path

from rerx.spec import load_spec, validate_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS = REPO_ROOT / "experiments"


def test_every_experiment_spec_is_valid() -> None:
    specs = list(EXPERIMENTS.glob("*/spec.yaml"))
    assert specs, "expected at least one experiment spec"
    for path in specs:
        errors = validate_spec(load_spec(path))
        assert errors == [], f"{path}: {errors}"


def test_jpeg_crop_quality_spec_covers_both_encodings() -> None:
    spec = load_spec(EXPERIMENTS / "jpeg-crop-quality" / "spec.yaml")
    options = spec["decisions"]["crop_encoding"]["options"]
    assert set(options) == {"jpeg_q95", "lossless"}
    universes = spec["universes"]
    assert {u["decisions"]["crop_encoding"] for u in universes.values()} == {
        "jpeg_q95",
        "lossless",
    }
    # every output names the decision it is shaped by
    for output in spec["outputs"]:
        assert "crop_encoding" in output["decisions"]

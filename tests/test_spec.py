"""
Tests for experiment specs: decisions, rationale, universes, and
outputs naming decisions (astra-style recording, plan.md section 14).
"""

from rerx.spec import validate_spec


def _spec() -> dict:
    return {
        "version": "1.0",
        "name": "JPEG crop quality comparison",
        "decisions": {
            "crop_encoding": {
                "label": "Crop encoding",
                "rationale": "JPEG 95 saves space; lossless avoids compression artifacts.",
                "default": "jpeg_q95",
                "options": {
                    "jpeg_q95": {"label": "JPEG quality 95"},
                    "lossless": {"label": "Lossless in-memory PNG"},
                },
            }
        },
        "outputs": [
            {
                "id": "embedding_delta",
                "type": "metric",
                "decisions": ["crop_encoding"],
                "description": "Mean MorphEm embedding distance between encodings",
            }
        ],
        "universes": {
            "jpeg": {"decisions": {"crop_encoding": "jpeg_q95"}},
            "lossless": {"decisions": {"crop_encoding": "lossless"}},
        },
    }


def test_validate_spec_accepts_minimal_spec() -> None:
    assert validate_spec(_spec()) == []


def test_validate_spec_rejects_unknown_version() -> None:
    spec = _spec()
    spec["version"] = "9.9"
    errors = validate_spec(spec)
    assert any("version" in error for error in errors)


def test_validate_spec_requires_decisions_with_rationale() -> None:
    spec = _spec()
    del spec["decisions"]["crop_encoding"]["rationale"]
    errors = validate_spec(spec)
    assert any("rationale" in error for error in errors)


def test_validate_spec_rejects_decision_without_options() -> None:
    spec = _spec()
    spec["decisions"]["crop_encoding"]["options"] = {}
    errors = validate_spec(spec)
    assert any("options" in error for error in errors)


def test_validate_spec_rejects_default_not_in_options() -> None:
    spec = _spec()
    spec["decisions"]["crop_encoding"]["default"] = "png_q100"
    errors = validate_spec(spec)
    assert any("default" in error for error in errors)


def test_validate_spec_requires_outputs_name_decisions() -> None:
    spec = _spec()
    del spec["outputs"][0]["decisions"]
    errors = validate_spec(spec)
    assert any("decisions" in error for error in errors)


def test_validate_spec_rejects_output_naming_unknown_decision() -> None:
    spec = _spec()
    spec["outputs"][0]["decisions"] = ["ghost_decision"]
    errors = validate_spec(spec)
    assert any("ghost_decision" in error for error in errors)


def test_validate_spec_requires_universe_picks_known_decisions() -> None:
    spec = _spec()
    spec["universes"]["jpeg"]["decisions"]["ghost"] = "x"
    errors = validate_spec(spec)
    assert any("ghost" in error for error in errors)


def test_validate_spec_rejects_universe_picking_unknown_option() -> None:
    spec = _spec()
    spec["universes"]["jpeg"]["decisions"]["crop_encoding"] = "png_q100"
    errors = validate_spec(spec)
    assert any("png_q100" in error for error in errors)


def test_validate_spec_rejects_unknown_top_level_key() -> None:
    spec = _spec()
    spec["surprise"] = True
    errors = validate_spec(spec)
    assert any("surprise" in error for error in errors)


def test_validate_spec_accepts_universe_subset_of_decisions() -> None:
    # a universe may leave a decision at its default by omitting it
    spec = _spec()
    spec["decisions"]["seed"] = {
        "label": "Seed",
        "rationale": "Reproducibility.",
        "default": "42",
        "options": {"42": {"label": "42"}},
    }
    spec["universes"]["jpeg"]["decisions"]["seed"] = "42"
    spec["universes"]["lossless"]["decisions"]["seed"] = "42"
    assert validate_spec(spec) == []


def test_validate_spec_rejects_malformed_document() -> None:
    # the validator never raises on malformed input: it reports
    errors = validate_spec("not a dict")
    assert errors != []
    errors = validate_spec(123)
    assert errors != []
    errors = validate_spec(None)
    assert errors != []
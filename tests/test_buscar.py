"""
Tests for the buscar module.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rerx.buscar import (
    CONTROL_PERTURBATION,
    REF_STATE,
    TARGET_STATE,
    BuscarConfig,
    PlateBuscarResult,
    build_buscar_signatures,
    calculate_scores_summary,
    morphological_features,
    run_buscar_for_plate,
    write_buscar_outputs,
)


def _plate_profiles() -> pd.DataFrame:
    """
    Small synthetic plate: mock/active_untreated controls plus one
    partially-reversing treatment, with an off-signature feature that
    does not move between mock and active_untreated at all.
    """
    rng = np.random.default_rng(0)
    n = 40

    def make(perturbation: str, shift_a: float, shift_b: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Metadata_perturbation": [perturbation] * n,
                "Cells_AreaShape_Area": rng.normal(shift_a, 1.0, n),
                "Cells_Intensity_MeanIntensity_DNA": rng.normal(shift_b, 1.0, n),
                "Cells_AreaShape_Perimeter": rng.normal(0.0, 1.0, n),
            }
        )

    mock = make(TARGET_STATE, 0.0, 0.0)
    disease = make(REF_STATE, 5.0, 5.0)
    treated = make("drugA__1.0", 1.0, 1.0)
    return pd.concat([mock, disease, treated], ignore_index=True)


def test_morphological_features_sorted_and_prefixed() -> None:
    import polars as pl

    df = pl.DataFrame(
        {
            "Metadata_perturbation": ["mock"],
            "Cells_AreaShape_Area": [1.0],
            "Nuclei_Intensity_MeanIntensity_DNA": [2.0],
        }
    )
    feats = morphological_features(df)
    assert feats == [
        "Cells_AreaShape_Area",
        "Nuclei_Intensity_MeanIntensity_DNA",
    ]


def test_build_buscar_signatures_separates_on_and_off_features() -> None:
    import polars as pl

    profiles = pl.from_pandas(_plate_profiles())
    on, off, ambiguous = build_buscar_signatures(profiles)

    assert set(on) == {"Cells_AreaShape_Area", "Cells_Intensity_MeanIntensity_DNA"}
    assert "Cells_AreaShape_Perimeter" in off
    assert ambiguous == []


def test_build_buscar_signatures_missing_reference_raises() -> None:
    import polars as pl

    profiles = pl.from_pandas(_plate_profiles()).filter(
        pl.col("Metadata_perturbation") != REF_STATE
    )
    with pytest.raises(ValueError, match="no reference rows"):
        build_buscar_signatures(profiles)


def test_calculate_scores_summary_reversal_is_between_target_and_reference() -> None:
    import polars as pl

    profiles = pl.from_pandas(_plate_profiles())
    on, off, _ = build_buscar_signatures(profiles)
    scores = calculate_scores_summary(profiles, on, off)

    scores_df = scores.to_pandas().set_index("perturbation")
    # Reference distance is normalized to 1.0.
    assert scores_df.loc[REF_STATE, "on_buscar_scores"] == pytest.approx(1.0)
    # The partially-reversed treatment scores strictly between full
    # reversal (would be ~0, matching target) and no reversal (1.0).
    treated_score = scores_df.loc["drugA__1.0", "on_buscar_scores"]
    assert 0.0 < treated_score < 1.0
    # The off-signature feature never moved, so no off-target effect.
    assert scores_df.loc["drugA__1.0", "off_buscar_scores"] == pytest.approx(0.0)


def test_write_buscar_outputs_writes_parquet_and_summary(tmp_path: Path) -> None:
    import polars as pl

    profiles = pl.from_pandas(_plate_profiles())
    on, off, ambiguous = build_buscar_signatures(profiles)
    scores = calculate_scores_summary(profiles, on, off)
    dest_dir = tmp_path / "buscar" / "cellprofiler"

    sig_path, scores_path = write_buscar_outputs(
        (on, off, ambiguous), scores, dest_dir, profiler="cellprofiler"
    )

    assert sig_path.is_file()
    assert scores_path.is_file()
    summary_path = dest_dir / "cellprofiler_summary.json"
    assert summary_path.is_file()
    sig_df = pd.read_parquet(sig_path)
    assert set(sig_df["signature_type"]) <= {"on", "off", "ambiguous"}


def test_run_buscar_for_plate_returns_result_and_writes_files(tmp_path: Path) -> None:
    profiles = _plate_profiles()
    dest_dir = tmp_path / "buscar" / "cellprofiler"

    result = run_buscar_for_plate(profiles, dest_dir, experiment="HRCE-1", plate="25")

    assert isinstance(result, PlateBuscarResult)
    assert result.experiment == "HRCE-1"
    assert result.plate == "25"
    assert result.signatures_path.is_file()
    assert result.scores_path.is_file()
    assert isinstance(result.scores, pd.DataFrame)
    assert set(result.scores["perturbation"]) == {REF_STATE, "drugA__1.0"}


def test_buscar_config_defaults_match_rxrx_control_types() -> None:
    cfg = BuscarConfig()
    assert cfg.target_state == "mock"
    assert cfg.ref_state == "active_untreated"
    assert cfg.perturbation_col == "Metadata_perturbation"


def test_control_perturbation_constant() -> None:
    assert CONTROL_PERTURBATION == "control"

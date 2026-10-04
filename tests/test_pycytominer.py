"""
Tests for the pycytominer module.
"""

import numpy as np
import pandas as pd
import pytest

from rerx.pycytominer import (
    BUSCAR_STATE_OTHER,
    DEFAULT_FEATURE_SELECT_OPERATIONS,
    PYCYTOMINER_CONTROL_REFERENCE,
    PYCYTOMINER_CONTROL_SAMPLE,
    RXRX_CONTROL_ACTIVE_UNTREATED,
    RXRX_CONTROL_MOCK,
    RXRX_CONTROL_TREATED,
    RXRX_CONTROL_UV,
    add_control_columns,
    add_perturbation_column,
    annotate_profiles,
    buscar_state,
    drop_flagged_outliers,
    flag_outliers,
    normalization_samples_query,
    pycytominer_control_type,
    rename_image_metadata_columns,
    rxrx_control_type,
)


def test_rename_image_metadata_columns() -> None:
    df = pd.DataFrame(
        {
            "Image_Metadata_Experiment": ["HRCE-1"],
            "Image_Metadata_Plate": ["25"],
            "Image_Metadata_Well": ["A01"],
            "Image_Metadata_Site": [1],
            "Cells_AreaShape_Area": [100.0],
        }
    )
    renamed = rename_image_metadata_columns(df)
    assert "Metadata_Experiment" in renamed.columns
    assert "Metadata_Plate" in renamed.columns
    assert "Metadata_Well" in renamed.columns
    assert "Metadata_Site" in renamed.columns
    assert "Image_Metadata_Experiment" not in renamed.columns
    assert "Cells_AreaShape_Area" in renamed.columns


def test_rename_image_metadata_columns_missing_is_noop() -> None:
    # MorphEm profiles have no Image_Metadata_* columns at all.
    df = pd.DataFrame({"embedding_0": [1.0], "embedding_1": [2.0]})
    renamed = rename_image_metadata_columns(df)
    assert list(renamed.columns) == list(df.columns)


@pytest.mark.parametrize(
    ("disease_condition", "treatment", "expected"),
    [
        ("Mock", None, RXRX_CONTROL_MOCK),
        ("UV Inactivated SARS-CoV-2", None, RXRX_CONTROL_UV),
        ("Active SARS-CoV-2", None, RXRX_CONTROL_ACTIVE_UNTREATED),
        ("Active SARS-CoV-2", "", RXRX_CONTROL_ACTIVE_UNTREATED),
        ("Active SARS-CoV-2", "Remdesivir (GS-5734)", RXRX_CONTROL_TREATED),
    ],
)
def test_rxrx_control_type(disease_condition: str, treatment, expected: str) -> None:
    row = pd.Series({"disease_condition": disease_condition, "treatment": treatment})
    assert rxrx_control_type(row) == expected


def test_pycytominer_control_type_mock_is_reference() -> None:
    assert pycytominer_control_type(RXRX_CONTROL_MOCK) == PYCYTOMINER_CONTROL_REFERENCE


@pytest.mark.parametrize(
    "rxrx",
    [RXRX_CONTROL_UV, RXRX_CONTROL_ACTIVE_UNTREATED, RXRX_CONTROL_TREATED],
)
def test_pycytominer_control_type_others_are_sample(rxrx: str) -> None:
    assert pycytominer_control_type(rxrx) == PYCYTOMINER_CONTROL_SAMPLE


def test_buscar_state_mock_is_healthy() -> None:
    from rerx.metadata import HEALTHY_STATE

    assert buscar_state(RXRX_CONTROL_MOCK) == HEALTHY_STATE


@pytest.mark.parametrize(
    "rxrx",
    [RXRX_CONTROL_UV, RXRX_CONTROL_ACTIVE_UNTREATED, RXRX_CONTROL_TREATED],
)
def test_buscar_state_challenged_is_disease(rxrx: str) -> None:
    from rerx.metadata import DISEASE_STATE

    assert buscar_state(rxrx) == DISEASE_STATE


def test_buscar_state_unknown_is_other() -> None:
    assert buscar_state("something_else") == BUSCAR_STATE_OTHER


def test_add_control_columns_three_separate_meanings() -> None:
    df = pd.DataFrame(
        {
            "disease_condition": ["Mock", "Active SARS-CoV-2"],
            "treatment": [None, "Remdesivir (GS-5734)"],
        }
    )
    out = add_control_columns(df)
    assert "Metadata_rxrx_control_type" in out.columns
    assert "Metadata_pycytominer_control_type" in out.columns
    assert "Metadata_buscar_state" in out.columns
    assert out["Metadata_rxrx_control_type"].tolist() == [
        RXRX_CONTROL_MOCK,
        RXRX_CONTROL_TREATED,
    ]
    assert out["Metadata_pycytominer_control_type"].tolist() == [
        PYCYTOMINER_CONTROL_REFERENCE,
        PYCYTOMINER_CONTROL_SAMPLE,
    ]


def test_add_control_columns_missing_columns_raises() -> None:
    df = pd.DataFrame({"foo": [1]})
    with pytest.raises(ValueError, match="disease_condition"):
        add_control_columns(df)


def test_add_control_columns_works_with_prefixed_names() -> None:
    df = pd.DataFrame(
        {
            "Metadata_disease_condition": ["Mock"],
            "Metadata_treatment": [None],
        }
    )
    out = add_control_columns(df)
    assert out["Metadata_rxrx_control_type"].tolist() == [RXRX_CONTROL_MOCK]


def test_normalization_samples_query() -> None:
    query = normalization_samples_query()
    assert query == "Metadata_pycytominer_control_type == 'control'"


def _sample_cp_profiles() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Metadata_cell_id": ["a", "b", "c"],
            "Image_Metadata_Experiment": ["HRCE-1"] * 3,
            "Image_Metadata_Plate": ["25"] * 3,
            "Image_Metadata_Well": ["A01", "A02", "A02"],
            "Image_Metadata_Site": [1, 1, 1],
            "Cells_AreaShape_Area": [100.0, 110.0, 120.0],
        }
    )


def _sample_site_metadata() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "experiment": ["HRCE-1", "HRCE-1"],
            "plate": ["25", "25"],
            "well": ["A01", "A02"],
            "site": [1, 1],
            "disease_condition": ["Mock", "Active SARS-CoV-2"],
            "treatment": [None, "Remdesivir (GS-5734)"],
        }
    )


def test_annotate_profiles_joins_and_adds_controls() -> None:
    profiles = _sample_cp_profiles()
    site_metadata = _sample_site_metadata()

    annotated = annotate_profiles(profiles, site_metadata)

    assert len(annotated) == 3
    assert "Metadata_Well" in annotated.columns
    assert "Metadata_rxrx_control_type" in annotated.columns
    a01_rows = annotated[annotated["Metadata_Well"] == "A01"]
    assert (a01_rows["Metadata_rxrx_control_type"] == RXRX_CONTROL_MOCK).all()
    a02_rows = annotated[annotated["Metadata_Well"] == "A02"]
    assert (a02_rows["Metadata_rxrx_control_type"] == RXRX_CONTROL_TREATED).all()


def test_annotate_profiles_missing_join_columns_raises() -> None:
    profiles = pd.DataFrame({"Cells_AreaShape_Area": [1.0]})
    site_metadata = _sample_site_metadata()
    with pytest.raises(ValueError, match="missing join columns"):
        annotate_profiles(profiles, site_metadata)


def test_default_feature_select_operations() -> None:
    assert DEFAULT_FEATURE_SELECT_OPERATIONS == [
        "variance_threshold",
        "correlation_threshold",
        "blocklist",
        "drop_na_columns",
    ]


def test_add_perturbation_column_treatment_and_concentration() -> None:
    df = pd.DataFrame(
        {
            "Metadata_treatment": [
                "Remdesivir (GS-5734)",
                "Remdesivir (GS-5734)",
                None,
            ],
            "Metadata_treatment_conc": ["1.0", "2.0", None],
        }
    )
    out = add_perturbation_column(df)
    assert out["Metadata_perturbation"].tolist() == [
        "Remdesivir (GS-5734)__1.0",
        "Remdesivir (GS-5734)__2.0",
        "control",
    ]


def test_add_perturbation_column_untreated_is_control() -> None:
    df = pd.DataFrame(
        {"Metadata_treatment": ["", None], "Metadata_treatment_conc": ["", None]}
    )
    out = add_perturbation_column(df)
    assert out["Metadata_perturbation"].tolist() == ["control", "control"]


def test_add_perturbation_column_uses_rxrx_control_type_when_present() -> None:
    # mock/uv/active_untreated arms are all "untreated" but should stay
    # distinguishable in the buscar score table (plan.md section 19).
    df = pd.DataFrame(
        {
            "Metadata_treatment": ["", "", "", "Remdesivir (GS-5734)"],
            "Metadata_treatment_conc": ["", "", "", "1.0"],
            "Metadata_rxrx_control_type": [
                "mock",
                "uv",
                "active_untreated",
                "treated",
            ],
        }
    )
    out = add_perturbation_column(df)
    assert out["Metadata_perturbation"].tolist() == [
        "mock",
        "uv",
        "active_untreated",
        "Remdesivir (GS-5734)__1.0",
    ]


def test_add_perturbation_column_missing_columns_raises() -> None:
    df = pd.DataFrame({"foo": [1]})
    with pytest.raises(ValueError, match="Metadata_treatment"):
        add_perturbation_column(df)


def test_features_argument_cellprofiler_profiles_infer() -> None:
    from rerx.pycytominer import _features_argument

    assert _features_argument(_sample_cp_profiles()) == "infer"


def test_features_argument_morphem_profiles_explicit() -> None:
    from rerx.pycytominer import _features_argument

    profiles = pd.DataFrame(
        {
            "Metadata_cell_id": ["a", "b"],
            "Morphem_red_0": [0.1, 0.2],
            "Morphem_red_1": [0.3, 0.4],
        }
    )
    features = _features_argument(profiles)
    assert features == ["Morphem_red_0", "Morphem_red_1"]


def test_features_argument_no_features_defaults_infer() -> None:
    from rerx.pycytominer import _features_argument

    profiles = pd.DataFrame({"Metadata_cell_id": ["a"]})
    assert _features_argument(profiles) == "infer"


def test_rxrx_control_type_handles_missing_value_sentinels() -> None:
    # Real RxRx19a metadata mixes None, NaN, pd.NA, and "" for
    # "no treatment"; every sentinel must read as untreated, not raise
    # (pd.NA == "" is pd.NA, so truthiness and == "" checks are unsafe).
    for missing in (pd.NA, float("nan"), "", None):
        row = pd.Series(
            {"disease_condition": "Active SARS-CoV-2", "treatment": missing}
        )
        assert rxrx_control_type(row) == RXRX_CONTROL_ACTIVE_UNTREATED


def test_rxrx_control_type_falls_back_to_prefixed_when_plain_missing() -> None:
    # A missing (pd.NA) plain column must fall back to the
    # Metadata_-prefixed one instead of raising on truthiness.
    row = pd.Series(
        {
            "disease_condition": pd.NA,
            "treatment": pd.NA,
            "Metadata_disease_condition": "Mock",
            "Metadata_treatment": None,
        }
    )
    assert rxrx_control_type(row) == RXRX_CONTROL_MOCK


def test_add_perturbation_column_missing_conc_sentinels() -> None:
    # pd.NA / NaN concentrations must read as "no concentration", not
    # leak str(<NA>) into the perturbation label.
    df = pd.DataFrame(
        {
            "Metadata_treatment": [
                "Remdesivir (GS-5734)",
                "Remdesivir (GS-5734)",
            ],
            "Metadata_treatment_conc": [pd.NA, float("nan")],
        }
    )
    out = add_perturbation_column(df)
    assert out["Metadata_perturbation"].tolist() == [
        "Remdesivir (GS-5734)",
        "Remdesivir (GS-5734)",
    ]


def _nuclei_profiles(n_normal: int = 50, n_large: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    normal_area = rng.normal(100.0, 2.0, size=n_normal)
    normal_form = rng.normal(0.8, 0.01, size=n_normal)
    # large_nuclei requires BOTH large area AND low formfactor
    # (cosmicqc ANDs every feature condition within a threshold set).
    large_area = rng.normal(2000.0, 2.0, size=n_large)
    large_form = rng.normal(0.2, 0.01, size=n_large)
    return pd.DataFrame(
        {
            "Metadata_cell_id": [f"c{i}" for i in range(n_normal + n_large)],
            "Nuclei_AreaShape_Area": np.concatenate([normal_area, large_area]),
            "Nuclei_AreaShape_FormFactor": np.concatenate([normal_form, large_form]),
            "Nuclei_AreaShape_Eccentricity": rng.normal(
                0.5, 0.05, size=n_normal + n_large
            ),
            "Cells_AreaShape_Area": rng.normal(200.0, 10.0, size=n_normal + n_large),
        }
    )


def test_flag_outliers_adds_metadata_columns_without_dropping_rows() -> None:
    profiles = _nuclei_profiles()
    flagged = flag_outliers(profiles)
    assert len(flagged) == len(profiles)
    outlier_cols = [c for c in flagged.columns if c.startswith("Metadata_cqc_")]
    assert outlier_cols
    assert all(c.endswith("_is_outlier") for c in outlier_cols)
    # Must be a plain DataFrame (not cosmicqc's CytoDataFrame/CytoTable
    # wrapper type) so downstream pandas ops behave normally.
    assert type(flagged) is pd.DataFrame


def test_flag_outliers_flags_large_nuclei() -> None:
    profiles = _nuclei_profiles()
    flagged = flag_outliers(profiles)
    # The last n_large rows were constructed well outside the normal
    # nuclei area range, so at least one outlier column must catch them.
    any_outlier = flagged[
        [c for c in flagged.columns if c.endswith("_is_outlier")]
    ].any(axis=1)
    assert any_outlier.tail(3).all()
    assert not any_outlier.head(50).any()


def test_flag_outliers_missing_nuclei_columns_is_a_noop() -> None:
    # MorphEm profiles have no Nuclei_AreaShape_* columns at all; the
    # default nuclei QC thresholds don't apply, so this must pass
    # through unchanged rather than raising.
    profiles = pd.DataFrame(
        {
            "Metadata_cell_id": ["a", "b"],
            "Morphem_feature_0": [0.1, 0.2],
        }
    )
    flagged = flag_outliers(profiles)
    pd.testing.assert_frame_equal(flagged, profiles)


def test_drop_flagged_outliers_removes_only_flagged_rows() -> None:
    profiles = _nuclei_profiles()
    flagged = flag_outliers(profiles)
    before = len(flagged)
    filtered = drop_flagged_outliers(flagged)
    assert len(filtered) < before
    # The cqc flag columns themselves are dropped from the output -- they
    # are QC bookkeeping, not morphology features, and must not leak into
    # normalize/select_features.
    assert not any(c.startswith("Metadata_cqc_") for c in filtered.columns)


def test_drop_flagged_outliers_noop_when_no_flag_columns() -> None:
    profiles = pd.DataFrame({"Metadata_cell_id": ["a", "b"], "x": [1, 2]})
    filtered = drop_flagged_outliers(profiles)
    pd.testing.assert_frame_equal(filtered, profiles)

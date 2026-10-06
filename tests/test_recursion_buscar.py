"""
Tests for Recursion site embeddings as a buscar feature space.

The published embeddings (a variant of DenseNet-161; biorxiv
2020.08.02.233064) are site-level, one row per imaged field. These tests
cover the small profile-builder that annotates them with buscar metadata
so ``run_buscar_for_plate`` can score them like any other profiler.
"""

from __future__ import annotations

import pandas as pd
import pytest

from rerx.embeddings import build_recursion_buscar_profiles


def _embeddings(n: int = 6) -> pd.DataFrame:
    """Synthetic site embeddings shaped like the real table."""
    rows = {
        "site_id": [f"HRCE-1_25_A{i:02d}_1" for i in range(1, n + 1)],
        "feature_0": [0.1 * i for i in range(n)],
        "feature_1": [0.2 * i for i in range(n)],
    }
    return pd.DataFrame(rows)


def _selection(n: int = 6) -> pd.DataFrame:
    """Selection rows matching the embeddings' site_ids."""
    rows = {
        "site_id": [f"HRCE-1_25_A{i:02d}_1" for i in range(1, n + 1)],
        "experiment": ["HRCE-1"] * n,
        "plate": ["25"] * n,
        "well": [f"A{i:02d}" for i in range(1, n + 1)],
        "disease_condition": ["Mock"] * 2 + ["Active SARS-CoV-2"] * (n - 2),
        "treatment": ["", "", "", "Remdesivir (GS-5734)", "", ""][:n],
        "treatment_conc": ["", "", "", "0.1", "", ""][:n],
    }
    return pd.DataFrame(rows)


def test_build_profiles_annotates_embeddings_with_buscar_metadata() -> None:
    profiles = build_recursion_buscar_profiles(
        embeddings=_embeddings(),
        selection=_selection(),
    )
    assert "Metadata_perturbation" in profiles.columns
    assert "Metadata_buscar_state" in profiles.columns
    # buscar discovers features by prefix: feature_* becomes Recursion_*.
    assert "Recursion_0" in profiles.columns
    assert "feature_0" not in profiles.columns
    # Identity columns carry through for plate partitioning.
    assert "experiment" in profiles.columns
    assert "plate" in profiles.columns
    assert "well" in profiles.columns
    assert len(profiles) == 6
    assert list(profiles["Metadata_perturbation"].unique()) == [
        "mock",
        "active_untreated",
        "Remdesivir (GS-5734)__0.1",
    ]


def test_build_profiles_inner_joins_on_site_id() -> None:
    embeddings = _embeddings(6)
    selection = _selection(4)  # fewer rows -> inner join keeps 4
    profiles = build_recursion_buscar_profiles(
        embeddings=embeddings, selection=selection
    )
    assert len(profiles) == 4
    # Embedding rows with no selection match are dropped.
    assert set(profiles["site_id"]) == set(selection["site_id"])


def test_build_profiles_rejects_empty_selection() -> None:
    with pytest.raises(ValueError, match="selection"):
        build_recursion_buscar_profiles(
            embeddings=_embeddings(), selection=pd.DataFrame()
        )


def test_build_profiles_perturbation_matches_pycytominer_rules() -> None:
    profiles = build_recursion_buscar_profiles(
        embeddings=_embeddings(), selection=_selection()
    )
    state_by_perturbation = profiles.set_index("Metadata_perturbation")[
        "Metadata_buscar_state"
    ].to_dict()
    assert state_by_perturbation["mock"] == "Mock"
    assert state_by_perturbation["active_untreated"] == "Active SARS-CoV-2"
    assert state_by_perturbation["Remdesivir (GS-5734)__0.1"] == "Active SARS-CoV-2"

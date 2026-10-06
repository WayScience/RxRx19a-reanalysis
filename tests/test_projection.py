"""
Tests for Recursion-style on/off-perturbation projection scoring.

Implements the scoring method described in Cuccarese et al. 2020
(biorxiv 2020.08.02.233064): compute the vector between the barycenters
of the untreated and perturbed conditions, decompose each embedding
into the signed scalar projection (on-perturbation score) and the
scalar rejection (off-perturbation score), and normalize so the mean
on-score is 0 for untreated and 1 for perturbed. Separation is
assessed with a Z-factor between the two control populations.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rerx.projection import (
    control_separation_zfactor,
    projection_scores,
)


def _profiles(seed: int = 0) -> pd.DataFrame:
    """Synthetic profiles: mock at origin, active far along x, treated halfway.

    Features f0..f3. The signal lives entirely in f0; f1..f3 carry only
    noise so off-scores are positive but small.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for perturbation, n, mean in (
        ("mock", 20, 0.0),
        ("active_untreated", 20, 4.0),
        ("Remdesivir (GS-5734)__0.1", 20, 2.0),
    ):
        for _ in range(n):
            rows.append(
                {
                    "Metadata_perturbation": perturbation,
                    "f0": mean + rng.normal(0, 0.2),
                    "f1": rng.normal(0, 0.1),
                    "f2": rng.normal(0, 0.1),
                    "f3": rng.normal(0, 0.1),
                }
            )
    return pd.DataFrame(rows)


def test_projection_scores_normalizes_controls_to_zero_and_one() -> None:
    scores = projection_scores(_profiles())

    by_pert = scores.set_index("perturbation")["mean_on_perturbation_score"]
    # Paper: mean on-score is 0 for untreated, 1 for perturbed.
    assert by_pert["mock"] == pytest.approx(0.0, abs=1e-9)
    assert by_pert["active_untreated"] == pytest.approx(1.0, abs=1e-9)
    # The halfway-reverted treated arm lands between the controls.
    assert 0.3 < by_pert["Remdesivir (GS-5734)__0.1"] < 0.7


def test_projection_scores_off_positive_for_noisy_features() -> None:
    scores = projection_scores(_profiles())
    off = scores.set_index("perturbation")["mean_off_perturbation_score"]
    # Points carry orthogonal noise, so every arm has a positive
    # off-score; the signal axis is shared, so off stays small.
    assert (off > 0).all()
    assert (off < 1.0).all()


def test_projection_scores_output_schema() -> None:
    scores = projection_scores(_profiles())
    assert list(scores.columns) == [
        "perturbation",
        "cell_count",
        "mean_on_perturbation_score",
        "mean_off_perturbation_score",
    ]
    # One row per perturbation, sorted for stable output.
    assert len(scores) == 3
    assert list(scores["perturbation"]) == sorted(scores["perturbation"])


def test_projection_scores_rejects_missing_controls() -> None:
    profiles = _profiles()
    profiles = profiles[profiles["Metadata_perturbation"] != "active_untreated"]
    with pytest.raises(ValueError, match="active_untreated"):
        projection_scores(profiles)


def test_projection_scores_default_feature_cols_skip_non_numeric() -> None:
    profiles = _profiles()
    profiles["Metadata_note"] = "ignored"
    # A non-Metadata_, non-numeric column must not be treated as a
    # feature (it would break the float64 cast in _feature_matrix).
    profiles["label"] = "not a number"
    scores = projection_scores(profiles)
    assert len(scores) == 3


def test_control_separation_zfactor() -> None:
    # Well-separated controls -> high Z-factor; identical -> <= 0.
    separated = control_separation_zfactor(
        on_scores_control=np.zeros(50), on_scores_perturbed=np.ones(50)
    )
    assert separated > 0.5

    same = control_separation_zfactor(
        on_scores_control=np.zeros(50), on_scores_perturbed=np.zeros(50)
    )
    assert same <= 0.0

"""
Tests for the finalize module (per-plate batch orchestration).
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from rerx.finalize import PlateFinalizeResult, finalize_plate


def _plate_raw_profiles(well_prefix: str = "A") -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n_cells_per_well = 10
    rows = []
    wells = [
        (f"{well_prefix}01", "Mock", None, None),
        (f"{well_prefix}02", "Active SARS-CoV-2", None, None),
        (f"{well_prefix}03", "Active SARS-CoV-2", "Remdesivir (GS-5734)", "1.0"),
    ]
    for well, disease, treatment, conc in wells:
        shift = 0.0 if disease == "Mock" else 5.0
        for cell_idx in range(n_cells_per_well):
            rows.append(
                {
                    "Metadata_cell_id": f"{well}_c{cell_idx}",
                    "Image_Metadata_Experiment": "HRCE-1",
                    "Image_Metadata_Plate": "25",
                    "Image_Metadata_Well": well,
                    "Image_Metadata_Site": 1,
                    "Cells_AreaShape_Area": rng.normal(shift, 1.0),
                    "Cells_Intensity_MeanIntensity_DNA": rng.normal(shift, 1.0),
                    # Never separates mock vs. disease -- an off-signature
                    # feature, matching real profiles where most of the
                    # ~1500 features don't move for a given perturbation.
                    "Cells_AreaShape_Perimeter": rng.normal(0.0, 1.0),
                    "_disease_condition": disease,
                    "_treatment": treatment,
                    "_treatment_conc": conc,
                }
            )
    df = pd.DataFrame(rows)
    return df


def _site_metadata() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "experiment": "HRCE-1",
                "plate": "25",
                "well": "A01",
                "site": 1,
                "disease_condition": "Mock",
                "treatment": None,
                "treatment_conc": None,
            },
            {
                "experiment": "HRCE-1",
                "plate": "25",
                "well": "A02",
                "site": 1,
                "disease_condition": "Active SARS-CoV-2",
                "treatment": None,
                "treatment_conc": None,
            },
            {
                "experiment": "HRCE-1",
                "plate": "25",
                "well": "A03",
                "site": 1,
                "disease_condition": "Active SARS-CoV-2",
                "treatment": "Remdesivir (GS-5734)",
                "treatment_conc": "1.0",
            },
        ]
    )


def test_finalize_plate_writes_all_stages(tmp_path: Path) -> None:
    raw = _plate_raw_profiles().drop(
        columns=["_disease_condition", "_treatment", "_treatment_conc"]
    )
    site_metadata = _site_metadata()
    run_dir = tmp_path / "run"

    result = finalize_plate(
        raw_profiles=raw,
        site_metadata=site_metadata,
        run_dir=run_dir,
        experiment="HRCE-1",
        plate="25",
    )

    assert isinstance(result, PlateFinalizeResult)
    assert result.normalized_path.is_file()
    assert result.feature_selected_path.is_file()
    assert result.buscar is not None
    assert result.buscar.signatures_path.is_file()
    assert result.buscar.scores_path.is_file()
    assert "Metadata_perturbation" in result.normalized.columns
    assert result.control_separation is not None


def test_finalize_plate_control_separation_flags_bad_plate(tmp_path: Path) -> None:
    # Same distribution for every well -> mock and disease controls
    # overlap, so buscar has no on-signature and is skipped rather than
    # crashing.
    rng = np.random.default_rng(1)
    rows = []
    for well, disease in [("A01", "Mock"), ("A02", "Active SARS-CoV-2")]:
        for cell_idx in range(30):
            rows.append(
                {
                    "Metadata_cell_id": f"{well}_c{cell_idx}",
                    "Image_Metadata_Experiment": "HRCE-1",
                    "Image_Metadata_Plate": "25",
                    "Image_Metadata_Well": well,
                    "Image_Metadata_Site": 1,
                    "Cells_AreaShape_Area": rng.normal(0.0, 1.0),
                }
            )
    raw = pd.DataFrame(rows)
    site_metadata = pd.DataFrame(
        [
            {
                "experiment": "HRCE-1",
                "plate": "25",
                "well": "A01",
                "site": 1,
                "disease_condition": "Mock",
                "treatment": None,
                "treatment_conc": None,
            },
            {
                "experiment": "HRCE-1",
                "plate": "25",
                "well": "A02",
                "site": 1,
                "disease_condition": "Active SARS-CoV-2",
                "treatment": None,
                "treatment_conc": None,
            },
        ]
    )
    run_dir = tmp_path / "run"

    result = finalize_plate(
        raw_profiles=raw,
        site_metadata=site_metadata,
        run_dir=run_dir,
        experiment="HRCE-1",
        plate="25",
    )

    assert result.control_separation is not None
    assert not result.control_separation.passed
    assert result.buscar is None
    assert result.buscar_skipped_reason is not None


def test_finalize_plate_buscar_keeps_profiler_label(tmp_path: Path) -> None:
    # buscar outputs must carry the profiler the plate was finalized
    # with (summary filename + "profiler" field), not the default label.
    raw = _plate_raw_profiles().drop(
        columns=["_disease_condition", "_treatment", "_treatment_conc"]
    )
    result = finalize_plate(
        raw_profiles=raw,
        site_metadata=_site_metadata(),
        run_dir=tmp_path / "run",
        experiment="HRCE-1",
        plate="25",
        profiler="morphem",
    )
    assert result.buscar is not None
    summary_path = (
        tmp_path
        / "run"
        / "buscar"
        / "morphem"
        / "experiment=HRCE-1"
        / "plate=25"
        / "morphem_summary.json"
    )
    assert summary_path.is_file()
    assert json.loads(summary_path.read_text())["profiler"] == "morphem"


def test_finalize_plate_flags_and_drops_cosmicqc_outliers(tmp_path: Path) -> None:
    # A plate whose raw profiles include Nuclei_AreaShape_* columns (the
    # real CellProfiler case) must have coSMicQC's default outlier
    # checks run and the flagged cells dropped before normalization.
    rng = np.random.default_rng(2)
    rows = []
    for well, disease in [("A01", "Mock"), ("A02", "Active SARS-CoV-2")]:
        for cell_idx in range(30):
            # One deliberately huge+low-formfactor nucleus per well to
            # trip the "large_nuclei" default threshold set.
            is_outlier = cell_idx == 0
            rows.append(
                {
                    "Metadata_cell_id": f"{well}_c{cell_idx}",
                    "Image_Metadata_Experiment": "HRCE-1",
                    "Image_Metadata_Plate": "25",
                    "Image_Metadata_Well": well,
                    "Image_Metadata_Site": 1,
                    "Cells_AreaShape_Area": rng.normal(0.0, 1.0),
                    "Nuclei_AreaShape_Area": (
                        2000.0 if is_outlier else rng.normal(100.0, 2.0)
                    ),
                    "Nuclei_AreaShape_FormFactor": (
                        0.2 if is_outlier else rng.normal(0.8, 0.01)
                    ),
                    "Nuclei_AreaShape_Eccentricity": rng.normal(0.5, 0.05),
                }
            )
    raw = pd.DataFrame(rows)
    site_metadata = pd.DataFrame(
        [
            {
                "experiment": "HRCE-1",
                "plate": "25",
                "well": "A01",
                "site": 1,
                "disease_condition": "Mock",
                "treatment": None,
                "treatment_conc": None,
            },
            {
                "experiment": "HRCE-1",
                "plate": "25",
                "well": "A02",
                "site": 1,
                "disease_condition": "Active SARS-CoV-2",
                "treatment": None,
                "treatment_conc": None,
            },
        ]
    )

    result = finalize_plate(
        raw_profiles=raw,
        site_metadata=site_metadata,
        run_dir=tmp_path / "run",
        experiment="HRCE-1",
        plate="25",
        run_buscar=False,
    )

    assert result.n_cells_flagged_outlier >= 2
    assert len(result.normalized) == len(raw) - result.n_cells_flagged_outlier
    assert not any(c.startswith("Metadata_cqc_") for c in result.normalized.columns)

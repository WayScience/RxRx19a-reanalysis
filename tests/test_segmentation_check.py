"""
Tests for rerx.segmentation_check: dataset-agnostic pre-step logic for
picking a representative sample of sites and scoring whether they need a
threshold-strategy recheck.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rerx.segmentation_check import (
    ContrastStats,
    build_threshold_variant,
    compare_counts,
    flag_dim_wells,
    image_contrast_stats,
    pick_sample_sites,
)


def _row(well: str, site: int, plate: str, disease: str) -> dict:
    return {
        "site_id": f"HRCE-1_{plate}_{well}_{site}",
        "well_id": f"HRCE-1_{plate}_{well}",
        "cell_type": "HRCE",
        "experiment": "HRCE-1",
        "plate": plate,
        "well": well,
        "site": site,
        "disease_condition": disease,
        "treatment": None,
        "treatment_conc": None,
    }


def _metadata_for_plate(plate: str) -> pd.DataFrame:
    rows = []
    for well, disease in [
        ("AA02", "Active SARS-CoV-2"),
        ("AA03", "Active SARS-CoV-2"),
        ("AA08", "UV Inactivated SARS-CoV-2"),
        ("AA16", "UV Inactivated SARS-CoV-2"),
        ("E08", "Mock"),
        ("E16", "Mock"),
        ("E99", "Mock"),  # extra mock well to test "2 wells per condition"
    ]:
        for site in range(1, 5):
            rows.append(_row(well, site, plate, disease))
    return pd.DataFrame(rows)


def test_pick_sample_sites_selects_two_wells_per_condition_all_sites() -> None:
    metadata = _metadata_for_plate("1")
    sample = pick_sample_sites(metadata, wells_per_condition=2, seed=0)

    assert len(sample) == 2 * 3 * 4  # 2 wells x 3 conditions x 4 sites
    # Every selected well contributes all 4 of its sites.
    counts = sample.groupby("well")["site"].nunique()
    assert (counts == 4).all()
    # Exactly 2 distinct wells per condition.
    wells_per_condition = sample.groupby("disease_condition")["well"].nunique()
    assert (wells_per_condition == 2).all()


def test_pick_sample_sites_is_deterministic_for_a_fixed_seed() -> None:
    metadata = _metadata_for_plate("1")
    first = pick_sample_sites(metadata, wells_per_condition=2, seed=42)
    second = pick_sample_sites(metadata, wells_per_condition=2, seed=42)
    pd.testing.assert_frame_equal(
        first.reset_index(drop=True), second.reset_index(drop=True)
    )


def test_pick_sample_sites_raises_when_a_condition_has_too_few_wells() -> None:
    metadata = _metadata_for_plate("1")
    # Ask for more wells per condition than any condition actually has.
    with pytest.raises(ValueError, match="distinct wells, need 5"):
        pick_sample_sites(metadata, wells_per_condition=5, seed=0)


def test_image_contrast_stats_reports_std_and_peak_intensity() -> None:
    dim = np.zeros((32, 32), dtype=np.uint8)
    dim[0, 0] = 40  # a tiny dim speck, like a low-contrast nucleus field
    bright = np.zeros((32, 32), dtype=np.uint8)
    bright[0, 0] = 200

    dim_stats = image_contrast_stats(dim)
    bright_stats = image_contrast_stats(bright)

    assert isinstance(dim_stats, ContrastStats)
    assert dim_stats.max_intensity == 40
    assert bright_stats.max_intensity == 200
    assert bright_stats.std > dim_stats.std


def test_image_contrast_stats_flags_dim_fields_below_a_threshold() -> None:
    dim = np.zeros((32, 32), dtype=np.uint8)
    dim[0, 0] = 40
    bright = np.zeros((32, 32), dtype=np.uint8)
    bright[0, 0] = 200

    assert image_contrast_stats(dim).is_dim(max_intensity_threshold=100) is True
    assert image_contrast_stats(bright).is_dim(max_intensity_threshold=100) is False


def test_compare_counts_computes_mean_absolute_percent_error() -> None:
    # Reference (Cellpose) counts vs. a candidate CellProfiler method's
    # counts, keyed by site. One site matches exactly, one is off by 50%.
    reference = {"AA02_1": 100, "AA08_1": 100}
    candidate = {"AA02_1": 100, "AA08_1": 150}

    result = compare_counts(reference, candidate)

    assert result.mean_abs_pct_error == pytest.approx(25.0)
    assert result.per_site["AA02_1"] == pytest.approx(0.0)
    assert result.per_site["AA08_1"] == pytest.approx(50.0)


def test_compare_counts_raises_on_mismatched_site_keys() -> None:
    reference = {"AA02_1": 100}
    candidate = {"AA08_1": 100}
    with pytest.raises(ValueError, match="site keys"):
        compare_counts(reference, candidate)


_SAMPLE_CPPIPE = """\
CellProfiler Pipeline: http://www.cellprofiler.org
Version:5
DateRevision:424
GitHash:
ModuleCount:2
HasImagePlaneDetails:False

Images:[module_num:1|svn_version:'Unknown'|variable_revision_number:2|show_window:False|notes:[]|batch_state:array([], dtype=uint8)|enabled:True|wants_pause:False]
    :
    Filter images?:Images only

IdentifyPrimaryObjects:[module_num:2|svn_version:'Unknown'|variable_revision_number:15|show_window:False|notes:[]|batch_state:array([], dtype=uint8)|enabled:True|wants_pause:False]
    Select the input image:DNA
    Name the primary objects to be identified:Nuclei
    Threshold strategy:Global
    Thresholding method:Otsu
    Threshold correction factor:1.0
"""


def test_build_threshold_variant_changes_only_the_named_setting() -> None:
    variant = build_threshold_variant(
        _SAMPLE_CPPIPE, setting="Thresholding method", value="Robust Background"
    )

    assert "Thresholding method:Robust Background" in variant
    assert "Thresholding method:Otsu" not in variant
    # Every other line is untouched.
    original_lines = _SAMPLE_CPPIPE.splitlines()
    variant_lines = variant.splitlines()
    assert len(original_lines) == len(variant_lines)
    changed = [
        (a, b) for a, b in zip(original_lines, variant_lines, strict=True) if a != b
    ]
    assert changed == [
        ("    Thresholding method:Otsu", "    Thresholding method:Robust Background")
    ]


def test_build_threshold_variant_raises_when_setting_not_found() -> None:
    with pytest.raises(ValueError, match="not found"):
        build_threshold_variant(
            _SAMPLE_CPPIPE, setting="No Such Setting", value="x"
        )


def test_flag_dim_wells_flags_a_well_whose_intensity_is_a_plate_outlier() -> None:
    # 5 wells on one plate: one (AA08) is clearly dimmer than the rest,
    # matching the real Plate 25 AA08/E08 finding (max intensity ~0.17-0.26
    # vs. ~0.40-0.57 for normal wells).
    quality = pd.DataFrame(
        [
            {"well": "AA02", "site": 1, "max_intensity": 0.46},
            {"well": "AA02", "site": 2, "max_intensity": 0.57},
            {"well": "AA09", "site": 1, "max_intensity": 0.44},
            {"well": "AA09", "site": 2, "max_intensity": 0.40},
            {"well": "AA16", "site": 1, "max_intensity": 0.48},
            {"well": "AA16", "site": 2, "max_intensity": 0.50},
            {"well": "AA08", "site": 1, "max_intensity": 0.26},
            {"well": "AA08", "site": 2, "max_intensity": 0.17},
        ]
    )

    flagged = flag_dim_wells(quality, metric_col="max_intensity")

    assert flagged == ["AA08"]


def test_flag_dim_wells_flags_nothing_when_all_wells_are_similar() -> None:
    quality = pd.DataFrame(
        [
            {"well": "AA02", "site": 1, "max_intensity": 0.46},
            {"well": "AA02", "site": 2, "max_intensity": 0.48},
            {"well": "AA09", "site": 1, "max_intensity": 0.44},
            {"well": "AA09", "site": 2, "max_intensity": 0.45},
        ]
    )

    assert flag_dim_wells(quality, metric_col="max_intensity") == []


def test_flag_dim_wells_raises_on_missing_column() -> None:
    quality = pd.DataFrame([{"well": "AA02", "site": 1}])
    with pytest.raises(ValueError, match="max_intensity"):
        flag_dim_wells(quality, metric_col="max_intensity")

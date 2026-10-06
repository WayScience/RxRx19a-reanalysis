"""
Tests for rerx.qc_notebook.
"""

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rerx.crops import CROP_SCHEMA_COLUMNS, encode_jpeg
from rerx.qc_notebook import (
    DEFAULT_N_SAMPLES,
    build_crop_review_notebook,
    notebook_source,
)


def _sample_crop_row(cell_id: str, well: str, site: int) -> dict:
    arr = np.full((16, 16), 128, dtype=np.uint8)
    row = {
        "Metadata_cell_id": cell_id,
        "Metadata_site_id": f"HRCE-1_25_{well}_{site}",
        "Metadata_experiment": "HRCE-1",
        "Metadata_plate": "25",
        "Metadata_well": well,
        "Metadata_site": site,
        "Metadata_object_number": 1,
        "Metadata_center_x": 8.0,
        "Metadata_center_y": 8.0,
        "crop_width": 16,
        "crop_height": 16,
        "jpeg_quality": 95,
    }
    for channel in range(1, 6):
        row[f"crop_w{channel}_jpeg"] = encode_jpeg(arr, quality=95)
    return row


def _write_crop_shard(path: Path, n_cells: int = 5) -> None:
    rows = [
        _sample_crop_row(f"cell-{i}", well="A01", site=(i % 2) + 1)
        for i in range(n_cells)
    ]
    df = pd.DataFrame(rows, columns=pd.Index(CROP_SCHEMA_COLUMNS))
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def test_notebook_source_embeds_run_dir_and_params(tmp_path: Path) -> None:
    source = notebook_source(run_dir=tmp_path, n_samples=7, seed=3)
    assert str(tmp_path) in source
    assert "n_samples = 7" in source
    assert "seed = 3" in source
    assert "CytoDataFrame" in source


def test_notebook_source_is_valid_python(tmp_path: Path) -> None:
    source = notebook_source(run_dir=tmp_path, n_samples=DEFAULT_N_SAMPLES, seed=0)
    ast.parse(source)  # raises SyntaxError if malformed


def test_notebook_source_is_jupytext_light_format(tmp_path: Path) -> None:
    source = notebook_source(run_dir=tmp_path, n_samples=5, seed=0)
    assert source.startswith("# %%")
    assert "# %% [markdown]" in source


def test_build_crop_review_notebook_writes_expected_path(tmp_path: Path) -> None:
    _write_crop_shard(tmp_path / "crops" / "cells" / "shard0.parquet")
    out = build_crop_review_notebook(run_dir=tmp_path)
    assert out == tmp_path / "qc" / "images" / "crop_spot_check.py"
    assert out.is_file()
    assert "CytoDataFrame" in out.read_text()


def test_build_crop_review_notebook_custom_out_path(tmp_path: Path) -> None:
    _write_crop_shard(tmp_path / "crops" / "cells" / "shard0.parquet")
    custom = tmp_path / "somewhere" / "spot_check.py"
    out = build_crop_review_notebook(run_dir=tmp_path, out_path=custom)
    assert out == custom
    assert out.is_file()


def test_generated_notebook_runs_against_real_crop_shards(tmp_path: Path) -> None:
    """Runtime verification: the generated script actually executes (as a
    plain .py, no jupytext/nbformat needed) against real crop parquet shards
    and builds a CytoDataFrame without error."""
    run_dir = tmp_path / "run"
    _write_crop_shard(run_dir / "crops" / "cells" / "shard0.parquet", n_cells=6)
    _write_crop_shard(run_dir / "crops" / "cells" / "shard1.parquet", n_cells=4)

    out = build_crop_review_notebook(run_dir=run_dir, n_samples=5, seed=1)

    namespace: dict = {}
    exec(compile(out.read_text(), str(out), "exec"), namespace)

    assert namespace["len_crops"] == 10
    assert namespace["len_sample"] == 5
    cdf = namespace["cdf"]
    assert len(cdf) == 5


def test_build_crop_review_notebook_raises_without_crops(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no crop shards"):
        build_crop_review_notebook(run_dir=tmp_path)

"""Regression check for the segmentation notebook's fixed image widths."""

import re
import runpy
from pathlib import Path

import pandas as pd
from IPython import display as ipython_display
from PIL import Image

from scripts.segmentation_cytodataframe_compare import build_notebook


def test_segmentation_notebook_keeps_image_widths(tmp_path: Path, monkeypatch) -> None:
    image_dir = tmp_path / "overlays"
    image_dir.mkdir()
    for filename in ("short.tiff", "long.tiff"):
        Image.new("L", (32, 32), 128).save(image_dir / filename)

    metadata_path = tmp_path / "comparison_meta.json"
    pd.DataFrame(
        {
            "Image_FileName_A": ["short.tiff"],
            "Image_FileName_Long_Method_Name_For_Segmentation": ["long.tiff"],
        }
    ).to_json(metadata_path, orient="records")
    notebook = build_notebook(
        metadata_path, ["AA02"], ["A", "long method"], tmp_path / "comparison.py"
    )

    outputs = []
    monkeypatch.setattr(ipython_display, "display", outputs.append)
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(notebook))

    assert len(outputs) == 1
    styles = re.findall(r'<img[^>]*style="([^"]+)"', outputs[0].data)
    assert len(styles) == 2
    for style in styles:
        properties = dict(part.split(":", 1) for part in style.split(";") if part)
        assert properties["width"] == "420px"
        assert properties["min-width"] == "420px"
        assert properties["max-width"] == "none"

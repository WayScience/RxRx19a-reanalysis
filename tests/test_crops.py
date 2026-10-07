"""
Tests for the crops module.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from rerx.crops import (
    BACKGROUND_VALUE,
    CROP_SCHEMA_COLUMNS,
    SiteCropInputs,
    crop_and_mask_channel,
    crop_box,
    crop_site_cells,
    decode_jpeg,
    encode_jpeg,
    load_mask,
    load_site_crop_inputs,
    validate_crops_join_one_to_one,
    write_crops_parquet,
)
from rerx.metadata import ImageSetID


def test_crop_box_centered_within_bounds() -> None:
    left, top, right, bottom = crop_box(
        center_x=100,
        center_y=100,
        crop_size=20,
        image_width=512,
        image_height=512,
    )
    assert (left, top, right, bottom) == (90, 90, 110, 110)


def test_crop_box_clamped_at_top_left_edge() -> None:
    left, top, right, bottom = crop_box(
        center_x=5,
        center_y=5,
        crop_size=20,
        image_width=512,
        image_height=512,
    )
    assert left == 0
    assert top == 0
    assert right == 15  # ideal right (5-10+20=15) unaffected by left clamp
    assert bottom == 15


def test_crop_box_clamped_at_bottom_right_edge() -> None:
    _, _, right, bottom = crop_box(
        center_x=508,
        center_y=508,
        crop_size=20,
        image_width=512,
        image_height=512,
    )
    assert right == 512
    assert bottom == 512


def test_crop_and_mask_channel_shape_and_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = np.full((50, 50), 200, dtype=np.uint8)
    mask = np.zeros((50, 50), dtype=np.int32)
    mask[20:30, 20:30] = 1  # object 1 occupies a 10x10 block centered at (25,25)

    crop = crop_and_mask_channel(
        channel,
        mask,
        object_number=1,
        center_x=25,
        center_y=25,
        crop_size=20,
    )
    assert crop.shape == (20, 20)
    assert crop.dtype == channel.dtype
    # Center of the crop (inside the object) keeps the channel value.
    assert crop[10, 10] == 200
    # A corner far from the object is background.
    assert crop[0, 0] == BACKGROUND_VALUE


def test_crop_and_mask_channel_masks_other_objects() -> None:
    channel = np.full((50, 50), 200, dtype=np.uint8)
    mask = np.zeros((50, 50), dtype=np.int32)
    mask[20:30, 20:30] = 2  # a DIFFERENT object occupies this region

    crop = crop_and_mask_channel(
        channel,
        mask,
        object_number=1,
        center_x=25,
        center_y=25,
        crop_size=20,
    )
    # Nothing belongs to object 1, so the whole crop is background.
    assert np.all(crop == BACKGROUND_VALUE)


def test_crop_and_mask_channel_pads_at_image_boundary() -> None:
    channel = np.full((50, 50), 200, dtype=np.uint8)
    mask = np.zeros((50, 50), dtype=np.int32)
    mask[0:10, 0:10] = 1

    crop = crop_and_mask_channel(
        channel,
        mask,
        object_number=1,
        center_x=2,
        center_y=2,
        crop_size=20,
    )
    assert crop.shape == (20, 20)
    # Padded region (outside the original image) is background.
    assert crop[19, 19] == BACKGROUND_VALUE


def test_crop_and_mask_channel_shape_mismatch_raises() -> None:
    channel = np.zeros((50, 50), dtype=np.uint8)
    mask = np.zeros((40, 40), dtype=np.int32)
    with pytest.raises(ValueError, match="shape"):
        crop_and_mask_channel(channel, mask, 1, 10, 10, crop_size=10)


def test_encode_decode_jpeg_roundtrip() -> None:
    arr = np.random.default_rng(0).integers(0, 255, (32, 32), dtype=np.uint8)
    data = encode_jpeg(arr, quality=95)
    assert isinstance(data, bytes)
    assert len(data) > 0
    decoded = decode_jpeg(data)
    assert decoded.shape == (32, 32)
    # JPEG is lossy; values should be close, not necessarily identical.
    assert np.abs(decoded.astype(int) - arr.astype(int)).mean() < 20


def test_load_mask(tmp_path: Path) -> None:
    arr = np.array([[0, 1], [2, 0]], dtype=np.int32)
    path = tmp_path / "mask.tiff"
    Image.fromarray(arr, mode="I").save(path)
    loaded = load_mask(path)
    assert loaded.dtype == np.int32
    np.testing.assert_array_equal(loaded, arr)


def _write_channel_png(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.full((64, 64), value, dtype=np.uint8)
    Image.fromarray(arr, mode="L").save(path)


def test_load_site_crop_inputs(tmp_path: Path) -> None:
    ids = ImageSetID(experiment="HRCE-1", plate="25", well="A01", site=1)
    images_dir = tmp_path / "images"
    for channel in (1, 2, 3, 4, 5):
        _write_channel_png(images_dir / ids.image_path(channel), value=channel * 10)
    mask_path = tmp_path / "A01_s1_w1_MaskCells.tiff"
    mask_arr = np.zeros((64, 64), dtype=np.int32)
    mask_arr[10:20, 10:20] = 1
    Image.fromarray(mask_arr, mode="I").save(mask_path)

    inputs = load_site_crop_inputs(ids, images_dir, mask_path)

    assert isinstance(inputs, SiteCropInputs)
    assert set(inputs.channel_images.keys()) == {1, 2, 3, 4, 5}
    assert inputs.channel_images[1][0, 0] == 10
    assert inputs.cell_mask.shape == (64, 64)


def test_crop_site_cells_produces_expected_schema(tmp_path: Path) -> None:
    ids = ImageSetID(experiment="HRCE-1", plate="25", well="A01", site=1)
    images_dir = tmp_path / "images"
    for channel in (1, 2, 3, 4, 5):
        _write_channel_png(images_dir / ids.image_path(channel), value=100 + channel)
    mask_path = tmp_path / "A01_s1_w1_MaskCells.tiff"
    mask_arr = np.zeros((64, 64), dtype=np.int32)
    mask_arr[10:20, 10:20] = 1
    mask_arr[30:40, 30:40] = 2
    Image.fromarray(mask_arr, mode="I").save(mask_path)
    inputs = load_site_crop_inputs(ids, images_dir, mask_path)

    cell_rows = pd.DataFrame(
        {
            "Metadata_cell_id": ["cell-1", "cell-2"],
            "Cells_Number_Object_Number": [1, 2],
            "Cells_AreaShape_Center_X": [15.0, 35.0],
            "Cells_AreaShape_Center_Y": [15.0, 35.0],
        }
    )

    crops = crop_site_cells(inputs, cell_rows, crop_size=16, jpeg_quality=90)

    assert list(crops.columns) == CROP_SCHEMA_COLUMNS
    assert len(crops) == 2
    assert (crops["crop_width"] == 16).all()
    assert (crops["jpeg_quality"] == 90).all()
    assert crops.loc[0, "Metadata_experiment"] == "HRCE-1"
    assert crops.loc[0, "Metadata_well"] == "A01"
    for channel in (1, 2, 3, 4, 5):
        col = f"crop_w{channel}_jpeg"
        assert col in crops.columns
        assert all(isinstance(v, (bytes, bytearray)) for v in crops[col])


def test_crop_site_cells_missing_columns_raises(tmp_path: Path) -> None:
    ids = ImageSetID(experiment="HRCE-1", plate="25", well="A01", site=1)
    images_dir = tmp_path / "images"
    for channel in (1, 2, 3, 4, 5):
        _write_channel_png(images_dir / ids.image_path(channel), value=100)
    mask_path = tmp_path / "mask.tiff"
    Image.fromarray(np.zeros((64, 64), dtype=np.int32), mode="I").save(mask_path)
    inputs = load_site_crop_inputs(ids, images_dir, mask_path)

    bad_rows = pd.DataFrame({"Metadata_cell_id": ["cell-1"]})
    with pytest.raises(ValueError, match="missing required columns"):
        crop_site_cells(inputs, bad_rows)


def test_write_crops_parquet_atomic(tmp_path: Path) -> None:
    crops = pd.DataFrame(
        {
            "Metadata_cell_id": ["a", "b"],
            "crop_w1_jpeg": [b"x", b"y"],
        }
    )
    dest = tmp_path / "crops" / "cells.parquet"
    result = write_crops_parquet(crops, dest)
    assert result == dest
    assert dest.exists()
    assert not dest.with_suffix(".parquet.tmp").exists()
    round_trip = pd.read_parquet(dest)
    assert round_trip["Metadata_cell_id"].tolist() == ["a", "b"]


def test_validate_crops_join_one_to_one_passes() -> None:
    crops = pd.DataFrame({"Metadata_cell_id": ["a", "b"]})
    profiles = pd.DataFrame({"Metadata_cell_id": ["a", "b"]})
    validate_crops_join_one_to_one(crops, profiles)  # must not raise


def test_validate_crops_join_one_to_one_detects_duplicate_crops() -> None:
    crops = pd.DataFrame({"Metadata_cell_id": ["a", "a"]})
    profiles = pd.DataFrame({"Metadata_cell_id": ["a", "b"]})
    with pytest.raises(ValueError, match="crops has duplicate"):
        validate_crops_join_one_to_one(crops, profiles)


def test_validate_crops_join_one_to_one_detects_mismatch() -> None:
    crops = pd.DataFrame({"Metadata_cell_id": ["a", "c"]})
    profiles = pd.DataFrame({"Metadata_cell_id": ["a", "b"]})
    with pytest.raises(ValueError, match="do not join one-to-one"):
        validate_crops_join_one_to_one(crops, profiles)

"""
Tests for rerx.morphem: MorphEm embedding of crop shards.

The host environment has no torch (by design -- torch lives only in
containers/morphem.def), so every inference test uses a stub model that
satisfies the ``forward_features`` protocol MorphEm requires. The lazy
imports mean importing rerx.morphem never touches torch.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from rerx import morphem


def _jpeg_crop(size: int = 96, value: int = 128) -> bytes:
    """One grayscale JPEG crop at a fixed pixel value."""
    img = Image.fromarray(np.full((size, size), value, dtype=np.uint8), mode="L")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _crop_rows(n: int = 3, size: int = 96) -> pd.DataFrame:
    """Minimal crop-shard rows matching rerx.crops schema."""
    rows = {
        "Metadata_experiment": ["HRCE-1"] * n,
        "Metadata_plate": ["25"] * n,
        "Metadata_well": ["A03"] * n,
        "Metadata_site": ["1"] * n,
        "Metadata_cell_id": [f"cell_{i}" for i in range(n)],
        "crop_width": [size] * n,
        "crop_height": [size] * n,
    }
    for c in (1, 2, 3, 4, 5):
        rows[f"crop_w{c}_jpeg"] = [_jpeg_crop() for _ in range(n)]
    return pd.DataFrame(rows)


class _StubModel:
    """Torch-free stand-in for MorphEm's forward_features protocol."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def to(self, device: str) -> _StubModel:
        self.device = device
        return self

    def eval(self) -> _StubModel:
        return self

    def forward_features(self, x: object) -> dict[str, np.ndarray]:
        # x: (batch, 1, 224, 224) after the transform... but the stub
        # bypasses the real transform (which needs torchvision), so we
        # only exercise shape bookkeeping here.
        arr = np.asarray(getattr(x, "shape", None) or (1,))
        batch = arr[0] if len(arr) else 1
        return {"x_norm_clstoken": np.zeros((batch, self.dim), dtype=np.float32)}


def _stub_embed_fn(
    model: object, images: object, device: str, batch_size: int
) -> tuple[float, np.ndarray]:
    """Torch-free stand-in for embed_crops_batched."""
    del model, device, batch_size
    arr = np.asarray(images)
    n = arr.shape[0]
    features = np.zeros((n, 5 * 384), dtype=np.float32)
    return 0.0, features


def test_morphem_feature_names_layout() -> None:
    names = morphem.morphem_feature_names()
    assert len(names) == 5 * 384
    assert names[0] == "Morphem_c1_d0"
    assert names[383] == "Morphem_c1_d383"
    assert names[384] == "Morphem_c2_d0"
    assert names[-1] == "Morphem_c5_d383"


def test_morphem_constants() -> None:
    assert morphem.MORPHEM_MODEL_ID == "CaicedoLab/MorphEm"
    # Pinned commit SHA (not a moving branch): build-time and inference
    # model code must resolve to the identical revision.
    assert morphem.MORPHEM_MODEL_REVISION == (
        "0e8d58787421f83f975634d72420d85c5dfc9c2c"
    )
    assert morphem.MORPHEM_TRANSFORMERS_VERSION == "4.46.3"
    assert morphem.MORPHEM_CHANNEL_COLS == [
        "crop_w1_jpeg",
        "crop_w2_jpeg",
        "crop_w3_jpeg",
        "crop_w4_jpeg",
        "crop_w5_jpeg",
    ]
    assert morphem.MORPHEM_DEFAULT_BATCH_SIZE == 32
    assert morphem.MORPHEM_IMAGE_SIZE == 224


def test_write_morphem_profiles_writes_metadata_and_features(
    tmp_path: Path,
) -> None:
    crops = _crop_rows(3)
    features = np.arange(3 * 5 * 384, dtype=np.float32).reshape(3, 5 * 384)
    dest = tmp_path / "profiles" / "morphem" / "raw" / "shard.parquet"

    profiles, path = morphem.write_morphem_profiles(crops, features, dest)

    assert path == dest
    assert dest.is_file()
    assert len(profiles) == 3
    # Metadata passthrough, crop pixels dropped.
    assert "Metadata_cell_id" in profiles.columns
    assert "crop_w1_jpeg" not in profiles.columns
    # Feature columns: exact layout.
    assert list(profiles.columns) == [
        *[c for c in crops.columns if c.startswith("Metadata_")],
        *morphem.morphem_feature_names(),
    ]
    # Values landed in order.
    np.testing.assert_array_equal(
        profiles[morphem.morphem_feature_names()].to_numpy(), features
    )
    # Round-trips through parquet.
    back = morphem.read_morphem_profiles(dest)
    assert list(back.columns) == list(profiles.columns)
    assert len(back) == 3


def test_write_morphem_profiles_rejects_row_mismatch(tmp_path: Path) -> None:
    crops = _crop_rows(3)
    features = np.zeros((2, 5 * 384), dtype=np.float32)
    with pytest.raises(ValueError, match="does not match"):
        morphem.write_morphem_profiles(crops, features, tmp_path / "bad.parquet")


def test_write_morphem_profiles_rejects_bad_channel_count(
    tmp_path: Path,
) -> None:
    crops = _crop_rows(2)
    features = np.zeros((2, 383), dtype=np.float32)  # not divisible by 5
    with pytest.raises(ValueError, match="divisible"):
        morphem.write_morphem_profiles(crops, features, tmp_path / "bad.parquet")


def test_embed_shard_with_stub_model(tmp_path: Path) -> None:
    """Full shard embed path with a stubbed model (no torch needed)."""
    crops = _crop_rows(4)
    dest = tmp_path / "shard.parquet"

    result = morphem.embed_shard(
        crops,
        shard_id="HRCE-1-Plate25-0000",
        dest=dest,
        model=_StubModel(),
        device="cpu",
        load=False,
        images=np.zeros((4, 5, 96, 96), dtype=np.float32),
        embed_fn=_stub_embed_fn,
    )

    assert result.shard_id == "HRCE-1-Plate25-0000"
    assert result.cell_count == 4
    assert result.feature_count == 5 * 384
    assert result.device == "cpu"
    assert result.profiles_path == dest
    assert dest.is_file()
    # 4 rows x 1,920 features, zeros from the stub.
    read_back = morphem.read_morphem_profiles(dest)
    assert read_back.shape == (4, 5 * 384 + 5)  # 5 Metadata columns
    assert read_back.filter(regex="^Morphem_").to_numpy().max() == 0


def test_embed_shard_empty_crops(tmp_path: Path) -> None:
    """Empty crop shard short-circuits without touching the model."""
    crops = _crop_rows(0)
    dest = tmp_path / "empty.parquet"
    result = morphem.embed_shard(
        crops,
        shard_id="shard-empty",
        dest=dest,
        model=_StubModel(),
        device="cpu",
        load=False,
    )
    assert result.cell_count == 0
    assert result.feature_count == 0
    assert dest.is_file()
    assert len(morphem.read_morphem_profiles(dest)) == 0


def test_embed_shard_requires_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no model"):
        morphem.embed_shard(
            _crop_rows(1),
            shard_id="x",
            dest=tmp_path / "x.parquet",
            model=None,
            load=False,
        )


def test_morphem_profile_count(tmp_path: Path) -> None:
    raw = tmp_path / "profiles" / "morphem" / "raw"
    assert morphem.morphem_profile_count(raw) == 0
    for shard in ("a", "b", "c"):
        morphem.write_morphem_profiles(
            _crop_rows(1),
            np.zeros((1, 5 * 384), dtype=np.float32),
            raw / f"{shard}.parquet",
        )
    assert morphem.morphem_profile_count(raw) == 3


def _torch_available() -> bool:
    try:
        import torch  # ty: ignore[unresolved-import]  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(_torch_available(), reason="torch installed; container-only test")
def test_crops_to_tensor_import_error_without_torch() -> None:
    """On the torch-free host, crops_to_tensor must raise ImportError
    (torch is only installed inside containers/morphem.def)."""
    crops = _crop_rows(1)
    with pytest.raises(ImportError):
        morphem.crops_to_tensor(crops)

"""
MorphEm embedding profiles for ReRx crop shards.

Implements plan.md section 15's MorphEm stage around the REAL interface,
verified in spikes/001-morphem-cpu-vs-gpu: MorphEm is the HuggingFace
``CaicedoLab/MorphEm`` ViT-Small vision transformer (AutoModel,
``trust_remote_code=True``) applied to OUR per-cell crop Parquet shards
using the model card's "Bag of Channels" reference inference -- each of
the 5 channel crops (96x96 JPEG, background zeroed) runs through the
ViT separately at 224x224, and the 5 per-channel embeddings are
concatenated into one 1,920-dim profile per cell (5 x 384).

This module replaces the original scaffolding, which was written against
a GUESSED container interface (a config-driven tarball that does not
exist). Everything below was exercised for real in the spike: the
SaturationNoiseInjector / PerImageNormalize transforms are verbatim from
the model card, ``transformers==4.46.3`` is REQUIRED (5.x breaks
``trust_remote_code`` loading with ``all_tied_weights_keys``
AttributeError), and CPU inference is fully supported (the model card's
own reference uses ``cuda if available else cpu``).

Torch/transformers are NOT project dependencies: they live only inside
containers/morphem.def. This module therefore imports them lazily inside
the functions that actually run inference (so the host library, tests,
and every other pipeline stage stay torch-free), and
:func:`embed_crops_batched` accepts any object with a compatible
``forward_features`` method, keeping unit tests free of torch entirely.

Per-shard scalability mirrors the rest of the pipeline: one crop shard
in, one MorphEm profile Parquet out (profiles/morphem/raw/<shard>.parquet),
registered later by the DuckLake catalog builder like every other table.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# HuggingFace identifiers for MorphEm (verified in the spike).
# Revision is pinned to a commit SHA (the `main` branch HEAD at spike
# time, 2026-05-21), not a moving branch: the container bakes the model
# at build time and inference must load the identical code/weights.
MORPHEM_MODEL_ID = "CaicedoLab/MorphEm"
MORPHEM_MODEL_REVISION = "0e8d58787421f83f975634d72420d85c5dfc9c2c"

# transformers pin required by MorphEm's trust_remote_code custom model:
# 5.x raises AttributeError on all_tied_weights_keys (verified in the
# spike; the container bakes this exact version in).
MORPHEM_TRANSFORMERS_VERSION = "4.46.3"

# Crop shard channel columns (see rerx.crops.CROP_SCHEMA_COLUMNS).
MORPHEM_CHANNEL_COLS = [f"crop_w{i}_jpeg" for i in (1, 2, 3, 4, 5)]

# Channels per crop (RxRx19a w1..w5) and per-channel ViT-Small dim.
MORPHEM_NUM_CHANNELS = 5
MORPHEM_EMBEDDING_DIM = 384

# Model card preprocessing target resolution.
MORPHEM_IMAGE_SIZE = 224

# Model card transform constants.
MORPHEM_SATURATED_PIXEL_VALUE = 255
MORPHEM_TENSOR_CHW_RANK = 3

# A features array from embed_crops_batched is always (N, 5 * D).
FEATURE_ARRAY_RANK = 2

# Default per-device batch size; the spike found 16/32/64 all stable on
# CPU and MPS with batch 32 the sweet spot.
MORPHEM_DEFAULT_BATCH_SIZE = 32

# Baked model directory inside containers/morphem.def. Runtime jobs use
# local pinned weights without downloading them again. Overridable because
# load_model() prefers this env var when set.
MORPHEM_MODEL_DIR_ENV = "MORPHEM_MODEL_DIR"

# Column layout of a MorphEm profile Parquet: metadata passthrough plus
# Morphem_c{channel}_d{dim} feature columns (5 channels x 384 dims).
MORPHEM_FEATURE_PREFIX = "Morphem_"


def morphem_feature_names(embedding_dim: int = MORPHEM_EMBEDDING_DIM) -> list[str]:
    """
    Column names for a MorphEm profile's concatenated features.

    Parameters
    ----------
    embedding_dim : int
        Per-channel ViT embedding dimensionality (MorphEm's ViT-Small
        is 384; measured live in the spike).

    Returns
    -------
    list[str]
        ``Morphem_c1_d0`` ... ``Morphem_c5_d{embedding_dim-1}`` (5
        channels x ``embedding_dim`` columns, matching the model
        card's reference concatenation order).
    """
    names: list[str] = []
    for channel in (1, 2, 3, 4, 5):
        names.extend(
            f"{MORPHEM_FEATURE_PREFIX}c{channel}_d{dim}" for dim in range(embedding_dim)
        )
    return names


def load_model() -> Any:  # noqa: ANN401
    """
    Load the MorphEm ViT-Small from HuggingFace (real torch import).

    Returns
    -------
    Any
        The loaded model (a torch Module with ``forward_features``).
        The caller must call ``.to(device).eval()`` itself, matching the
        model card's reference usage.
    """
    import os

    from transformers import AutoModel  # ty: ignore[unresolved-import]

    model_dir = os.environ.get(MORPHEM_MODEL_DIR_ENV)
    if model_dir:
        # Baked model inside containers/morphem.def (no runtime download).
        # revision is a hub-only kwarg; local dirs load without it.
        return AutoModel.from_pretrained(
            model_dir,
            trust_remote_code=True,
        )
    return AutoModel.from_pretrained(
        MORPHEM_MODEL_ID,
        revision=MORPHEM_MODEL_REVISION,
        trust_remote_code=True,
    )


def _build_transform() -> Any:  # noqa: ANN401
    """
    The model card's exact preprocessing pipeline (lazy torchvision import).

    Returns
    -------
    Any
        A composed transform implementing, in order:
        SaturationNoiseInjector (uniform noise on saturated pixels, first
        channel only), PerImageNormalize (instance norm per image), and a
        resize to 224x224 with antialias.
    """
    import torch  # ty: ignore[unresolved-import]
    from torch import nn  # ty: ignore[unresolved-import]
    from torchvision import transforms as v2  # ty: ignore[unresolved-import]

    saturated = MORPHEM_SATURATED_PIXEL_VALUE

    class SaturationNoiseInjector(nn.Module):
        """Exact transform from the MorphEm model card."""

        def __init__(self, low: int = 200, high: int = saturated) -> None:
            super().__init__()
            self.low = low
            self.high = high

        def forward(self, x: Any) -> Any:  # noqa: ANN401
            channel = x[0].clone()
            noise = torch.empty_like(channel).uniform_(self.low, self.high)
            mask = (channel == saturated).float()
            noise_masked = noise * mask
            channel[channel == saturated] = 0
            channel = channel + noise_masked
            x[0] = channel
            return x

    class PerImageNormalize(nn.Module):
        """Exact transform from the MorphEm model card."""

        def __init__(self, eps: float = 1e-7) -> None:
            super().__init__()
            self.eps = eps
            self.instance_norm = nn.InstanceNorm2d(
                num_features=1,
                affine=False,
                track_running_stats=False,
                eps=self.eps,
            )

        def forward(self, x: Any) -> Any:  # noqa: ANN401
            if x.dim() == MORPHEM_TENSOR_CHW_RANK:
                x = x.unsqueeze(0)
            x = self.instance_norm(x)
            if x.shape[0] == 1:
                x = x.squeeze(0)
            return x

    return v2.Compose(
        [
            SaturationNoiseInjector(),
            PerImageNormalize(),
            v2.Resize(size=(MORPHEM_IMAGE_SIZE, MORPHEM_IMAGE_SIZE), antialias=True),
        ]
    )


def crops_to_tensor(crops: pd.DataFrame) -> Any:  # noqa: ANN401
    """
    Decode a crop shard's 5-channel JPEG columns into a stacked tensor.

    Parameters
    ----------
    crops : pd.DataFrame
        Crop rows with ``crop_w1_jpeg`` .. ``crop_w5_jpeg`` binary
        columns and ``crop_width`` / ``crop_height`` (see
        :data:`rerx.crops.CROP_SCHEMA_COLUMNS`).

    Returns
    -------
    Any
        ``(N, 5, H, W)`` float32 torch tensor, channels stacked in
        order (w1..w5) to match MorphEm's Bag-of-Channels input
        convention.

    Raises
    ------
    ImportError
        If torch/PIL are not installed (host environments never call
        this; it runs inside containers/morphem.def).
    """
    import torch  # ty: ignore[unresolved-import]
    from PIL import Image

    n = len(crops)
    if n == 0:
        return torch.zeros((0, MORPHEM_NUM_CHANNELS, 0, 0), dtype=torch.float32)
    h = int(crops["crop_height"].iloc[0])
    w = int(crops["crop_width"].iloc[0])
    out = np.zeros((n, MORPHEM_NUM_CHANNELS, h, w), dtype=np.float32)
    for i, row in enumerate(crops[MORPHEM_CHANNEL_COLS].itertuples(index=False)):
        for c, jpeg_bytes in enumerate(row):
            img = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
            out[i, c] = np.array(img, dtype=np.float32)
    return torch.from_numpy(out)


@dataclass(frozen=True)
class MorphemEmbedResult:
    """
    Outcome of embedding one crop shard with MorphEm.

    Attributes
    ----------
    shard_id : str
        Shard identifier (e.g. ``HRCE-1-Plate25-0000``).
    profiles : pd.DataFrame
        One row per cell: the crop shard's Metadata columns plus
        ``Morphem_c{channel}_d{dim}`` feature columns.
    profiles_path : Path
        Where the profiles Parquet was written.
    cell_count : int
        Number of cells embedded.
    feature_count : int
        Number of feature columns written (5 x per-channel dim).
    elapsed_seconds : float
        Wall time of the timed inference loop (after warmup).
    device : str
        Device inference ran on (``"cpu"``, ``"cuda"``, ``"mps"``).
    """

    shard_id: str
    profiles: pd.DataFrame
    profiles_path: Path
    cell_count: int
    feature_count: int
    elapsed_seconds: float
    device: str


def _select_device() -> str:
    """Pick the best available torch device (cuda > mps > cpu)."""
    import torch  # ty: ignore[unresolved-import]

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def embed_crops_batched(
    model: Any,  # noqa: ANN401
    images: Any,  # noqa: ANN401
    device: str,
    batch_size: int = MORPHEM_DEFAULT_BATCH_SIZE,
    warmup: bool = True,
) -> tuple[float, np.ndarray]:
    """
    Run MorphEm's Bag-of-Channels inference over a stacked crop tensor.

    This is the production port of the spike's proven
    ``run_inference`` (spikes/001-morphem-cpu-vs-gpu/benchmark.py): same
    transforms, same per-channel loop, same warmup-before-timing
    discipline.

    Parameters
    ----------
    model : Any
        MorphEm model with a ``forward_features`` method (does not
        need to be pre-moved to ``device`` or switched to eval mode;
        this function does both, matching the model card reference).
    images : Any
        ``(N, 5, H, W)`` torch tensor (see :func:`crops_to_tensor`).
    device : str
        Torch device name (``"cpu"``, ``"cuda"``, ``"mps"``).
    batch_size : int
        Cells per forward pass batch. The spike found 16/32/64 stable.
    warmup : bool
        Run one discarded batch first. Required for fair GPU/MPS
        timing (first-call kernel compile + memory allocation) and
        harmless on CPU.

    Returns
    -------
    tuple[float, np.ndarray]
        Wall seconds of the timed loop, and an ``(N, 5 * D)`` float32
        array (per-channel embeddings concatenated channel-major).
    """
    import time

    import torch  # ty: ignore[unresolved-import]

    transform = _build_transform()
    n = images.shape[0]
    n_channels = images.shape[1]
    model = model.to(device).eval()

    def forward_batch(batch: Any) -> list[np.ndarray]:  # noqa: ANN401
        batch = batch.to(device)
        feats = []
        for c in range(n_channels):
            single_channel = batch[:, c, :, :]
            single_channel = transform(single_channel).unsqueeze(1)
            output = model.forward_features(single_channel)
            feats.append(output["x_norm_clstoken"].cpu().detach().numpy())
        return feats

    if warmup and n > 0:
        with torch.no_grad():
            forward_batch(images[: min(batch_size, n)])
        if device == "mps":
            torch.mps.synchronize()

    all_feats: list[list[np.ndarray]] = [[] for _ in range(n_channels)]
    t0 = time.perf_counter()
    with torch.no_grad():
        for start in range(0, n, batch_size):
            batch_feats = forward_batch(images[start : start + batch_size])
            for c, feat in enumerate(batch_feats):
                all_feats[c].append(feat)
    if device == "mps":
        torch.mps.synchronize()
    elapsed = time.perf_counter() - t0

    features = np.concatenate(
        [np.concatenate(chan_batches, axis=0) for chan_batches in all_feats],
        axis=1,
    )
    return elapsed, features


def write_morphem_profiles(
    crops: pd.DataFrame,
    features: np.ndarray,
    dest: Path,
) -> tuple[pd.DataFrame, Path]:
    """
    Write one shard's MorphEm profiles Parquet (metadata + features).

    Parameters
    ----------
    crops : pd.DataFrame
        The crop shard's rows, whose Metadata columns are passed
        through unchanged to the output.
    features : np.ndarray
        ``(N, 5 * D)`` array from :func:`embed_crops_batched`.
    dest : Path
        Destination Parquet path (profiles/morphem/raw/<shard>.parquet).

    Returns
    -------
    tuple[pd.DataFrame, Path]
        The written DataFrame and its path.

    Raises
    ------
    ValueError
        If ``features`` is not 2-D, its row count does not match
        ``crops``, or its column count is not divisible by the 5
        channels.
    """
    if len(features.shape) != FEATURE_ARRAY_RANK or features.shape[0] != len(crops):
        raise ValueError(
            f"features shape {features.shape} does not match {len(crops)} crop rows"
        )
    if features.shape[1] % MORPHEM_NUM_CHANNELS:
        raise ValueError(
            f"feature count {features.shape[1]} is not divisible by 5 channels"
        )
    embedding_dim = features.shape[1] // MORPHEM_NUM_CHANNELS
    metadata_cols = [c for c in crops.columns if c.startswith("Metadata_")]
    profiles = crops[metadata_cols].copy().reset_index(drop=True)
    feature_frame = pd.DataFrame(
        features,
        columns=pd.Index(morphem_feature_names(embedding_dim)),
        index=profiles.index,
    )
    profiles = pd.concat([profiles, feature_frame], axis=1)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    profiles.to_parquet(dest, index=False)
    return profiles, dest


def embed_shard(  # noqa: PLR0913
    crops: pd.DataFrame,
    shard_id: str,
    dest: Path,
    model: Any = None,  # noqa: ANN401
    device: str | None = None,
    batch_size: int = MORPHEM_DEFAULT_BATCH_SIZE,
    load: bool = True,
    images: Any = None,  # noqa: ANN401
    embed_fn: Any = None,  # noqa: ANN401
) -> MorphemEmbedResult:
    """
    Embed one crop shard end-to-end: decode, infer, write profiles.

    Parameters
    ----------
    crops : pd.DataFrame
        One shard's crop rows (see :func:`rerx.crops.write_crops_parquet`).
    shard_id : str
        Shard identifier for the result record.
    dest : Path
        Destination profiles Parquet (profiles/morphem/raw/<shard>.parquet).
    model : Any
        A MorphEm model, or ``None`` to load from HuggingFace when
        ``load=True`` (pass a stub from tests to avoid torch entirely).
    device : str | None
        Torch device. ``None`` auto-selects (cuda > mps > cpu).
    batch_size : int
        Cells per batch (see :func:`embed_crops_batched`).
    load : bool
        When True (and ``model is None``), call :func:`load_model`.
    images : Any
        Pre-decoded image tensor, bypassing :func:`crops_to_tensor`.
        Tests pass array-likes so torch is never required on the host.
    embed_fn : Any
        Override for :func:`embed_crops_batched` (tests pass a torch-free
        stub). Signature: ``(model, images, device, batch_size) ->
        (elapsed, features)``.

    Returns
    -------
    MorphemEmbedResult
        Profiles, paths, and run statistics.

    Raises
    ------
    ValueError
        If no model is provided and loading is disabled.
    """
    if model is None and load:
        model = load_model()
    if model is None:
        raise ValueError("no model provided and load=False")

    dev = device or _select_device()
    empty = len(crops) == 0
    if empty:
        # Empty shards skip decoding/inference entirely.
        profiles, path = write_morphem_profiles(
            crops,
            np.zeros((0, MORPHEM_NUM_CHANNELS * MORPHEM_EMBEDDING_DIM)),
            dest,
        )
        return MorphemEmbedResult(
            shard_id=shard_id,
            profiles=profiles,
            profiles_path=path,
            cell_count=0,
            feature_count=0,
            elapsed_seconds=0.0,
            device=dev,
        )
    elapsed, features = (
        embed_fn(model, images, dev, batch_size)
        if embed_fn is not None
        else embed_crops_batched(
            model,
            images if images is not None else crops_to_tensor(crops),
            dev,
            batch_size=batch_size,
        )
    )
    profiles, path = write_morphem_profiles(crops, features, dest)
    return MorphemEmbedResult(
        shard_id=shard_id,
        profiles=profiles,
        profiles_path=path,
        cell_count=len(profiles),
        feature_count=features.shape[1],
        elapsed_seconds=elapsed,
        device=dev,
    )


def read_morphem_profiles(path: Path) -> pd.DataFrame:
    """
    Read back a shard's MorphEm profiles Parquet.

    Parameters
    ----------
    path : Path
        Parquet path written by :func:`write_morphem_profiles`.

    Returns
    -------
    pd.DataFrame
        The shard's MorphEm profiles.
    """
    return pd.read_parquet(path)


def morphem_profile_count(embed_root: Path) -> int:
    """
    Count MorphEm profile shards produced under a raw profiles root.

    Parameters
    ----------
    embed_root : Path
        Directory holding ``<shard>.parquet`` files (e.g.
        ``profiles/morphem/raw``).

    Returns
    -------
    int
        Number of profile Parquet files found.
    """
    root = Path(embed_root)
    return len(sorted(root.glob("*.parquet")))

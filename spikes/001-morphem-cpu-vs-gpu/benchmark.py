"""
Spike 001: MorphEm CPU vs GPU (MPS) throughput on real pilot crop data.

Loads N real cell crops from the ReRx pilot's crops/cells parquet
(5-channel JPEG crops, 96x96, HRCE-1 Plate 25), runs MorphEm's exact
reference "Bag of Channels" inference (5 channels x N cells forward
passes) on cpu and mps, and reports throughput + wall time for each.

Usage:
    .venv/bin/python benchmark.py [n_cells] [batch_size]
"""

import io
import sys
import time
from typing import Any

import numpy as np
import pandas as pd
import torch
from PIL import Image
from torch import nn
from torchvision import transforms as v2
from transformers import AutoModel

CHANNEL_COLS = [f"crop_w{i}_jpeg" for i in (1, 2, 3, 4, 5)]

# Model-card constants (SaturationNoiseInjector / PerImageNormalize).
SATURATED_PIXEL_VALUE = 255
CHANNEL_FIRST_DIM = 3
ARG_RUN_COUNT = 2


class SaturationNoiseInjector(nn.Module):
    """Exact transform from the MorphEm model card."""

    def __init__(self, low: float = 200, high: float = 255) -> None:
        super().__init__()
        self.low = low
        self.high = high

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        channel = x[0].clone()
        noise = torch.empty_like(channel).uniform_(self.low, self.high)
        mask = (channel == SATURATED_PIXEL_VALUE).float()
        noise_masked = noise * mask
        channel[channel == SATURATED_PIXEL_VALUE] = 0
        channel = channel + noise_masked
        x[0] = channel
        return x


class PerImageNormalize(nn.Module):
    """Exact transform from the MorphEm model card."""

    def __init__(self, eps: float = 1e-7) -> None:
        super().__init__()
        self.eps = eps
        self.instance_norm = nn.InstanceNorm2d(
            num_features=1, affine=False, track_running_stats=False, eps=self.eps
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == CHANNEL_FIRST_DIM:
            x = x.unsqueeze(0)
        x = self.instance_norm(x)
        if x.shape[0] == 1:
            x = x.squeeze(0)
        return x


def load_crops(parquet_path: str, n_cells: int) -> torch.Tensor:
    """
    Load N real cell crops as a (N, 5, 96, 96) float32 tensor.

    Each of the 5 channel JPEG columns is decoded to a single-channel
    grayscale array; channels are stacked to match MorphEm's
    (N, C, H, W) input convention for the "Bag of Channels" loop.
    """
    df = pd.read_parquet(parquet_path).head(n_cells)
    n = len(df)
    h, w = int(df.iloc[0]["crop_height"]), int(df.iloc[0]["crop_width"])
    out = np.zeros((n, 5, h, w), dtype=np.float32)
    for i, row in enumerate(df.itertuples()):
        for c, col in enumerate(CHANNEL_COLS):
            jpeg_bytes = getattr(row, col)
            img = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
            out[i, c] = np.array(img, dtype=np.float32)
    return torch.from_numpy(out)


def run_inference(
    model: Any,  # noqa: ANN401
    images: torch.Tensor,
    device: str,
    batch_size: int,
    warmup: bool = True,
) -> tuple[float, np.ndarray]:
    """
    Run MorphEm's Bag-of-Channels inference over `images`, batched.

    Returns (wall_seconds, features) where features has shape
    (N, n_channels * embedding_dim), matching the model card's
    reference concatenation.

    Parameters
    ----------
    warmup : bool
        Run one small batch first and discard its timing. GPU/MPS
        backends pay a one-time kernel-compile and memory-allocation
        cost on the first call; without a warmup, that fixed cost gets
        folded into the timed run and unfairly penalizes the GPU on
        small sample sizes.
    """
    transform = v2.Compose(
        [
            SaturationNoiseInjector(),
            PerImageNormalize(),
            v2.Resize(size=(224, 224), antialias=True),
        ]
    )
    n = images.shape[0]
    n_channels = images.shape[1]
    model = model.to(device).eval()

    def forward_batch(batch: torch.Tensor) -> list[np.ndarray]:
        batch = batch.to(device)
        feats = []
        for c in range(n_channels):
            single_channel = batch[:, c, :, :]
            single_channel = transform(single_channel).unsqueeze(1)
            output = model.forward_features(single_channel)
            feats.append(output["x_norm_clstoken"].cpu().detach().numpy())
        return feats

    if warmup:
        with torch.no_grad():
            forward_batch(images[: min(batch_size, n)])
        if device == "mps":
            torch.mps.synchronize()

    all_feats = [[] for _ in range(n_channels)]
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
        [np.concatenate(chan_batches, axis=0) for chan_batches in all_feats], axis=1
    )
    return elapsed, features


def main() -> None:
    n_cells = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    batch_size = int(sys.argv[ARG_RUN_COUNT]) if len(sys.argv) > ARG_RUN_COUNT else 32

    print(f"Loading {n_cells} real pilot crops (5 channels each)...")
    t0 = time.perf_counter()
    images = load_crops("sample_crops.parquet", n_cells)
    print(f"  loaded {images.shape} in {time.perf_counter() - t0:.2f}s")

    print("Loading MorphEm (CaicedoLab/MorphEm)...")
    model = AutoModel.from_pretrained("CaicedoLab/MorphEm", trust_remote_code=True)

    results = {}
    devices = ["cpu"]
    if torch.backends.mps.is_available():
        devices.append("mps")
    else:
        print("MPS not available on this machine; skipping GPU run.")

    for device in devices:
        print(f"\n--- Running on {device} (batch_size={batch_size}) ---")
        elapsed, features = run_inference(model, images, device, batch_size)
        # Throughput reflects the rows actually loaded (load_crops may
        # return fewer than the requested n_cells).
        loaded_cells = images.shape[0]
        cells_per_sec = loaded_cells / elapsed
        results[device] = {
            "elapsed_s": elapsed,
            "cells_per_sec": cells_per_sec,
            "feature_shape": features.shape,
        }
        print(f"  {loaded_cells} cells x 5 channels in {elapsed:.2f}s")
        print(f"  {cells_per_sec:.2f} cells/sec")
        print(f"  output feature shape: {features.shape}")

    print("\n=== Summary ===")
    for device, r in results.items():
        print(
            f"{device:>4}: {r['elapsed_s']:7.2f}s  "
            f"{r['cells_per_sec']:7.2f} cells/sec  "
            f"features={r['feature_shape']}"
        )
    if "cpu" in results and "mps" in results:
        speedup = results["cpu"]["elapsed_s"] / results["mps"]["elapsed_s"]
        print(f"\nMPS speedup over CPU: {speedup:.2f}x")

        # Extrapolate to the full pilot (33,330 cells).
        full_pilot_cells = 33330
        for device, r in results.items():
            est_seconds = full_pilot_cells / r["cells_per_sec"]
            print(
                f"Estimated full pilot ({full_pilot_cells} cells) on {device}: "
                f"{est_seconds / 60:.1f} minutes"
            )


if __name__ == "__main__":
    main()

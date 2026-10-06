# Spike 001: MorphEm CPU vs GPU (MPS) on real pilot crops

Question: can MorphEm run on CPU, and how much does a GPU actually help?

## What we did

We loaded 200 real cell crops from the pilot run (HRCE-1 Plate 25, 96x96,
5-channel JPEG crops from `crops/cells/HRCE-1-Plate25-0000.parquet`) and ran
MorphEm's exact reference "Bag of Channels" inference on them — each of the 5
channels goes through the model separately, so each cell means 5 forward
passes at 224x224. We timed the same run on CPU and on this Mac's GPU (M4 Pro,
MPS backend), with a warmup batch so the GPU's one-time setup cost did not
count against it.

## What we found

| Device             | Throughput      | Full pilot (33,330 cells) |
| ------------------ | --------------- | ------------------------- |
| CPU (M4 Pro)       | ~29.6 cells/sec | ~19 minutes               |
| GPU / MPS (M4 Pro) | ~73.5 cells/sec | ~7.6 minutes              |

- **MorphEm runs fine on CPU.** This matches the model's own reference code,
  which falls back to CPU when no CUDA device is found.
- **The GPU is ~2.5x faster**, and the number was stable across batch sizes
  (16, 32, 64) and sample sizes. This Mac's GPU is plenty.
- **CPU and GPU agree numerically.** Max absolute difference between the two
  was 4.4e-5 on features with a standard deviation of ~3.0 — the same
  embeddings for any downstream purpose.
- Without the warmup pass, the GPU looked *slower* than CPU (0.67x) on a
  20-cell run. That was first-call setup cost, not real throughput. Small
  benchmark runs need a warmup before you believe them.

## Version gotcha

MorphEm loads through `trust_remote_code=True`, and the current
`transformers` release (5.17.0) breaks that path with
`AttributeError: all_tied_weights_keys`. Pin `transformers==4.46.3` (or
similar 4.x) when building the MorphEm container.

## What this means for the pipeline

- For a pilot of this size, CPU is entirely practical: ~19 minutes of
  single-node work. The "needs a GPU" instinct comes from full-dataset scale,
  not from the model itself.
- On Alpine, MorphEm can run in the same `acpu` CPU partition as everything
  else. No separate GPU allocation is needed for the pilot.
- If we scale to the full RxRx19a dataset (~1.2M sites, far more cells),
  a 2.5x speedup starts to matter, and the existing `morphem_command(gpus=...)`
  switch (adds `--nv` for Apptainer) is already there for that case.

## Reproduce

```
.venv/bin/python benchmark.py 200 32
```

Artifacts: `benchmark.py`, `sample_crops.parquet` (4,994 real pilot cells).

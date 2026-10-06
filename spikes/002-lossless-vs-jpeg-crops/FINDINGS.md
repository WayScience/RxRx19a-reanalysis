# Spike 002: lossless vs JPEG-95 crop encoding for MorphEm

plan.md section 14: "Start with high-quality JPEG, such as quality 95 or
higher, but validate the choice before freezing it. For the pilot,
compare MorphEm embeddings from lossless in-memory crops against
embeddings from stored JPEG crops. Keep the JPEG setting only if the
difference is acceptable for this analysis."

## What we did

We re-cropped cells from the staged source 5-channel PNGs using the
exact production crop path (`rerx.crops.crop_and_mask_channel` — same
masking, same boundary padding), then encoded the same pixels two ways:

- **lossless**: PNG-encoded source pixels (plan.md's "lossless
  in-memory crops" side)
- **jpeg95**: JPEG quality 95 (the production encoding)

A sanity check first verified our re-crop reproduces the stored
production crops: 0 gross mismatches (mean abs pixel difference > 10)
across every cell and channel — so the lossless side really is the same
pixels the pipeline cropped, not a different crop.

Both sets went through the baked-in MorphEm model (Bag of Channels,
inside `morphem.sif`, CPU), and we compared the two embedding matrices
on matched cells.

## What we found

(2-site run, 270 cells; 8-site run below)

| Metric                            | 2 sites (270 cells) |
| --------------------------------- | ------------------- |
| mean abs embedding difference     | 0.41 (feature std ~3) |
| p99 abs difference                | 2.40                |
| max abs difference                | 7.44                |
| mean rel diff (vs feature std)    | 0.24                |
| mean per-feature Pearson r (across cells) | 0.959        |

Embeddings from JPEG-95 crops track lossless crops closely but not
identically: per-feature Pearson ~0.96 across cells, typical difference
about a quarter of the feature's own standard deviation.

## What this means

(pending the 8-site run before finalizing)

## How to rerun

```bash
sbatch spike002.sbatch   # on Alpine: step 1 host venv, step 2 in morphem.sif
```

Results land in `/rerx/spike002/results.json` (bound to
`/scratch/alpine/dabu57888@xsede.org/rerx/spike002/`) and crop shards
in the run tree under `baseline/spike002_lossless_vs_jpeg/`.
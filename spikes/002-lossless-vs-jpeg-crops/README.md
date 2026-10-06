# Spike 002: lossless vs JPEG-95 crop encoding for MorphEm

plan.md section 14 requires validating the crop encoding choice before
freezing it: compare MorphEm embeddings computed from lossless in-memory
crops against embeddings from the stored JPEG-95 crops (what the
pipeline uses today). Keep the JPEG setting only if the difference is
acceptable.

## What we did

(see FINDINGS.md after the run)

## How to rerun

Everything runs on Alpine in the morphem container (torch lives there,
not in the host venv):

1. `prepare_lossless_jpeg_crops.py` (host venv, pandas/PIL only):
   re-crops a sample of cells from the source 5-channel PNGs using the
   exact production crop path (rerx.crops.crop_and_mask_channel), then
   writes TWO matched crop shards: the lossless pixels (PNG-encoded
   bytes, then decoded losslessly) and the JPEG-95 encoding of those
   same pixels.
2. `embed_and_compare.py` (inside morphem.sif): embeds both shards with
   the baked-in MorphEm model and compares the two embedding matrices
   (per-feature and per-cell differences, correlation).

```bash
sbatch spike002.sbatch
```
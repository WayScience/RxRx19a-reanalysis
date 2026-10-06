# Segmentation configuration check (pre-pipeline step)

Before committing to CellProfiler's nuclei-thresholding settings for a new
dataset (or a new plate within a dataset), run this check. It answers one
question: **does this data have dim/low-contrast fields that make
CellProfiler's default thresholding over-segment, and if so, which
thresholding setting fixes it?**

This was built after a real finding on RxRx19a's pilot plate (HRCE-1
Plate 25): two wells (AA08, E08) were measurably dimmer than the rest of
the plate, and CellProfiler's default `Otsu` threshold over-segmented them
3-7x relative to a Cellpose reference count, while every other well on
the same plate matched Cellpose within ±25%. `Robust Background`
thresholding fixed it (mean absolute percent error vs. Cellpose dropped
from 107% to 13% on that plate) without under-segmenting the wells that
were already fine. See `plan.md`'s segmentation review section for the
full numbers.

## Why this is a pre-step, not a one-off

CellProfiler's default `Otsu` threshold assumes roughly uniform
brightness across fields. Real microscopy datasets sometimes don't have
that -- a dim well can come from focus, staining, or acquisition
differences that have nothing to do with the biology. The only way to
catch it is to compare CellProfiler's object counts against a
threshold-independent reference (Cellpose, used here) on a representative
sample, *before* running the full dataset. Skipping this risks silently
inflated cell counts in specific wells, which will distort every
downstream per-well statistic.

## The procedure

1. **Pick a representative sample.** Use
   `rerx.segmentation_check.pick_sample_sites()` to select a small,
   stratified sample (a fixed number of wells per disease condition, all
   sites from each chosen well) from one plate's metadata. 2 wells per
   condition x however many conditions is enough to span the plate's
   conditions without downloading the whole thing.

2. **Download just those sites' images.**
   `scripts/segmentation_cellpose_check.py download-images --sites ...`
   pulls the w1 (nuclei channel) PNGs for the sampled sites. For the
   CellProfiler side you additionally need all 5 channels per site (w1-w5)
   since `NamesAndTypes` expects the full channel set -- download those
   the same way `metadata.ImageSetID.image_url()` builds URLs for each
   channel.

3. **Run Cellpose as the reference.**
   `scripts/segmentation_cellpose_check.py run-cellpose --images-dir ...
   --out-dir ...` runs Cellpose's nuclei model (auto-estimated diameter)
   on the sampled w1 images. Cellpose doesn't use CellProfiler's
   threshold-based approach at all, so it's a reasonable independent
   reference -- not ground truth, but a different-enough method that
   systematic disagreement is a real signal, not a shared blind spot.
   This step is slow (minutes per image on CPU); run it as a background
   process or a Slurm job, not inline.

4. **Build threshold variants of the production pipeline.**
   `rerx.segmentation_check.build_threshold_variant(cppipe_text, setting,
   value)` takes the real pipeline's `.cppipe` text and swaps one setting
   value, leaving every other line untouched (verify with `diff` against
   the original -- it should differ by exactly one line). Build at least:
   - baseline (unchanged, whatever the pipeline currently uses)
   - `Thresholding method` -> `Robust Background`
   - `Threshold correction factor` -> a higher value, e.g. `1.3`
   - `Threshold strategy` -> `Adaptive`

   CellProfiler's setting values are exact strings -- `"Robust
   Background"` needs the space; `"RobustBackground"` crashes with
   "Invalid thresholding settings". When in doubt, check
   `cellprofiler/modules/threshold.py` in the CellProfiler install for the
   valid value list.

   Trim each variant pipeline down to just the modules needed to get a
   nuclei mask out (Images, Metadata, NamesAndTypes, Groups,
   IdentifyPrimaryObjects, ConvertObjectsToImage, SaveImages) -- dropping
   the measurement modules makes each run much faster since you only need
   object counts for this check, not full feature extraction.

5. **Run each variant against the same sampled images**, same way the
   production pipeline runs (Apptainer on an Alpine compute node via a
   Slurm job -- bare SSH/login-shell `apptainer` calls fail, this has to
   be a real job).

6. **Count objects per site per variant** from the saved label-mask
   TIFFs (count distinct nonzero pixel values), and compare each variant
   against the Cellpose reference counts with
   `rerx.segmentation_check.compare_counts()`. It raises if the two
   count dictionaries don't cover the same sites, and reports both a
   per-site percent error and the mean absolute percent error across the
   sample -- use the mean to pick a variant, and look at the biggest
   per-site misses to make sure it isn't just averaging out an anomaly.

7. **Sanity-check object sizes, not just counts.** A variant that
   under-segments (merges real nuclei, or threshold too strict and drops
   them) can coincidentally produce a count close to the reference while
   being wrong in a different way. Check that the surviving objects'
   median size in the "fixed" wells is in the same range as the
   already-fine wells, not systematically smaller.

8. **Check contrast, not just the pass/fail number.** Use
   `rerx.segmentation_check.image_contrast_stats()` /
   `ContrastStats.is_dim()` on each sampled image to confirm the pattern:
   does the winning variant's improvement track with per-image contrast
   (dimmer fields improve more, brighter fields barely change)? If yes,
   that's evidence the fix addresses a real brightness-driven failure
   mode rather than an artifact of this particular sample.

9. **Apply and document.** If a variant clearly wins, apply that one
   setting change to the production `.cppipe` (not the whole pipeline --
   just the setting that was actually tested), note why in the module's
   `notes:` field, and record the before/after numbers in `plan.md`. Keep
   the baseline run as the fallback if a future dataset doesn't show this
   problem at all.

10. **Re-validate on more plates before trusting it dataset-wide.** A
    fix found on one plate's sample may not generalize -- different
    plates can have different acquisition conditions. Re-run steps 1-8 (skip
    step 4, reuse the already-built variant pipelines) on a couple of
    other plates. If the pattern holds (contrast-correlated improvement,
    no erratic blow-ups, no under-segmentation), it's dataset-wide. If a
    plate shows a well as extreme as the original finding, repeat the
    full Cellpose comparison on that plate specifically before trusting
    counts from it.

## What's reusable code vs. per-run orchestration

- `src/rerx/segmentation_check.py` -- pure, unit-tested functions:
  `pick_sample_sites`, `image_contrast_stats`/`ContrastStats`,
  `build_threshold_variant`, `compare_counts`/`CountComparison`. These
  don't touch the network, Cellpose, or CellProfiler, so they're fast to
  test and safe to reuse for any dataset.
- `scripts/segmentation_cellpose_check.py` -- the orchestration CLI
  (download images, run Cellpose, compare masks). Add a Slurm-job wrapper
  per dataset for the CellProfiler threshold-variant runs the same way
  `scripts/alpine_launch.sh` launches the main pipeline.
- Everything after mask generation (counting, size sanity-check, contrast
  correlation) is a handful of lines of `numpy`/`tifffile` glue -- keep it
  in a per-check scratch script rather than the package, since the exact
  comparison (counts vs. a saved reference, which variants, which plates)
  changes per investigation; the stable, reusable parts are listed above.

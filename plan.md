# ReRx plan

Build a boring, fast, reproducible RxRx19a pipeline.

Primary outputs:

- Single-cell CellProfiler features.
- Single-cell MorphEm embeddings.
- Pycytominer-ready profiles.
- buscar-ready profiles and scores.
- A matched site-level comparison of Recursion RxRx19a embeddings and MorphEm embeddings.
- Cell crops stored as high-quality JPEG bytes in Parquet.
- Plain Parquet datasets that work without DuckDB or DuckLake.
- Optional Frozen DuckLake catalogs over completed Parquet datasets.

Start with a pilot. Run the full dataset only after the pilot passes validation.

## 1. Main question

Use RxRx19a to ask:

> Which treatments move SARS-CoV-2-infected cells toward the mock-cell morphology while limiting unrelated morphology changes?

For buscar:

- Target: mock HRCE cells.
- Reference: viral HRCE control cells.
- Perturbation: viral HRCE cells plus compound and concentration.
- Irradiated-virus cells: extra control, not the primary target.

Run buscar separately on CellProfiler and MorphEm feature spaces first. Do not concatenate feature spaces until each works alone.

## 2. Design rules

Keep this boring.

1. Parquet is the durable source of truth for derived tabular data.
1. Each production run gets a new timestamped directory.
1. Never overwrite a prior dataset run.
1. Mark runs as `pilot` or `full` in the directory name and metadata.
1. Workers never write concurrently to one SQLite, DuckDB, or DuckLake database.
1. Workers write isolated shards, validate them, then publish them.
1. Use SQLite only as a temporary CellProfiler interchange format.
1. Keep crop data in its own Parquet dataset.
1. Keep CellProfiler and MorphEm features in separate datasets.
1. Use DuckLake only as a derived catalog over completed Parquet files.
1. Keep storage paths and allocation names out of committed configuration.
1. Prefer a few clear stages over clever abstractions.
1. Record versions, hashes, configuration, and row counts for every run.
1. Run heavy work through Slurm. Do not process images on login nodes or `Persistence1`.

## 3. Repository

Create a new `ReRx` repository from:

https://github.com/CU-DBMI/template-uv-python-research-software/

Use the pure-Python template. Keep the processing pipeline in package code and command-line tools. Use notebooks only for exploration and final analysis.

Suggested repository shape:

```text
ReRx/
├── AGENTS.md
├── plan.md
├── pyproject.toml
├── uv.lock
├── .agents/
│   └── skills/
│       ├── alpine/
│       │   └── SKILL.md
│       └── petalibrary/
│           └── SKILL.md
├── config/
│   ├── defaults.toml
│   ├── pilot.toml
│   └── full.toml
├── containers/
│   ├── cellprofiler.def
│   └── morphem.def
├── pipelines/
│   └── rxrx19a.cppipe
├── workflows/
│   ├── main.nf
│   └── nextflow.config
├── src/
│   └── rerx/
│       ├── cli.py
│       ├── manifest.py
│       ├── metadata.py
│       ├── cellprofiler.py
│       ├── cytotable.py
│       ├── crops.py
│       ├── morphem.py
│       ├── pycytominer.py
│       ├── buscar.py
│       ├── catalog.py
│       ├── validate.py
│       └── runs.py
├── tests/
└── notebooks/
```

Copy and pin the Alpine agent skill from:

https://github.com/WayScience/formascute/blob/main/.agents/skills/alpine/SKILL.md

Also copy the PetaLibrary skill because this project depends on PetaLibrary storage behavior:

https://github.com/WayScience/formascute/blob/main/.agents/skills/petalibrary/SKILL.md

Record the upstream commit used for each copied skill. Update the copies deliberately.

## 4. Storage configuration

Do not commit literal allocation or storage paths.

Use environment variables or a gitignored local configuration:

```text
RERX_SOURCE_ROOT=<RxRx19a source on Isilon>
RERX_PETA_ROOT=<PetaLibrary allocation>/ReRx
RERX_MIRROR_ROOT=<Isilon mirror>/ReRx
RERX_SCRATCH_ROOT=/scratch/alpine/$USER/ReRx
SLURM_ACCOUNT=<allocation>
```

The current source and destination paths can be set locally from the paths already known to the project.

Confirm that the PetaLibrary location is an Active tier visible from Alpine compute nodes. If not, stage compute inputs to Alpine scratch first.

## 5. Run IDs

Use UTC and a sortable timestamp.

```text
rxrx19a-pilot-YYYYMMDDTHHMMSSZ-g<gitsha>
rxrx19a-full-YYYYMMDDTHHMMSSZ-g<gitsha>
```

Example:

```text
rxrx19a-pilot-20260925T153000Z-g1a2b3c4
```

Every new run gets a new directory. A restart that creates new durable output gets a new run ID.

A run can contain these markers:

```text
_RUNNING
_FAILED
_SUCCESS
_PROTECTED
_MIRRORED
```

`_PROTECTED` means cleanup tools must refuse deletion unless the user explicitly overrides protection.

## 6. Durable dataset layout

Use the same relative layout on PetaLibrary and the Isilon mirror.

```text
$RERX_PETA_ROOT/
├── source-cache/
│   └── rxrx19a/
└── datasets/
    ├── rxrx19a-pilot-<timestamp>-g<sha>/
    └── rxrx19a-full-<timestamp>-g<sha>/
```

Each run:

```text
rxrx19a-<scope>-<timestamp>-g<sha>/
├── run.json
├── manifest/
│   ├── source_files.parquet
│   ├── image_sets.parquet
│   ├── selection.parquet
│   ├── outputs.parquet
│   └── files.parquet
├── metadata/
│   ├── rxrx19a.parquet
│   └── controls.parquet
├── profiles/
│   ├── cellprofiler/
│   │   ├── raw/
│   │   ├── annotated/
│   │   ├── normalized/
│   │   └── feature_selected/
│   └── morphem/
│       ├── raw/
│       ├── annotated/
│       └── normalized/
├── crops/
│   └── cells/
├── buscar/
│   ├── cellprofiler/
│   │   ├── signatures.parquet
│   │   └── scores.parquet
│   └── morphem/
│       ├── signatures.parquet
│       └── scores.parquet
├── baseline/
│   └── recursion_site_embeddings/
├── comparisons/
│   └── recursion_vs_morphem/
├── qc/
│   ├── summaries/
│   └── images/
├── catalog/
├── logs/
└── _SUCCESS
```

Keep temporary SQLite files, masks, Nextflow work directories, and other intermediates under Alpine scratch. Do not make them part of the final dataset unless the pilot shows a reason to keep them.

## 7. Stable identifiers

Never depend on row order.

Keep source IDs where available:

```text
Metadata_site_id
Metadata_well_id
Metadata_experiment
Metadata_plate
Metadata_well
Metadata_site
```

Create one stable cell ID after segmentation:

```text
Metadata_cell_id = <site_id>:<CellProfiler object number>
```

Also keep:

```text
Metadata_object_number
Metadata_center_x
Metadata_center_y
```

Use `Metadata_cell_id` as the primary join key across:

- CellProfiler profiles.
- Crop rows.
- MorphEm profiles.
- QC flags.

## 8. Stage 0: source audit

Do this before image processing.

Build a source manifest from RxRx19a metadata and files.

Validate:

- Every selected site has all expected channels.
- Paths resolve.
- `site_id` values are unique where expected.
- Metadata joins are one-to-one where expected.
- Cell type, disease condition, compound, concentration, experiment, plate, well, and site are present.
- File sizes are nonzero.

Write the source manifest to Parquet.

Do not copy the full source into every run.

If Alpine cannot read the source directly, maintain one shared `source-cache/rxrx19a` on compute-readable storage. Runs reference this cache through the source manifest.

## 9. Stage 1: pilot selection

Use HRCE for the first pilot.

Build the pilot from metadata, not hand-picked file paths.

Target roughly 24-48 wells. Keep all four sites for selected wells.

Include the smallest practical set of plates that contains:

- Mock controls.
- Viral controls.
- Irradiated-virus controls.
- A known active treatment if present.
- A known weak or inactive treatment if present.
- Several concentrations for at least two compounds.
- Replicate wells where available.

Prefer Remdesivir as a positive-control candidate if its wells fit the selection. Keep the selection logic data-driven so another compound can substitute if needed.

Write the exact selection to:

```text
manifest/selection.parquet
```

Never change a selection after a run starts.

## 10. Stage 2: Recursion site-embedding baseline

Treat the existing RxRx19a embeddings as first-class source data.

Source:

https://storage.googleapis.com/rxrx/RxRx19a/RxRx19a-DL-embeddings.zip

The Recursion file contains one 1,024-dimensional vector per `site_id`.

Download the ZIP once into the shared source cache. Record its size and SHA256. Do not download it separately for each run.

Convert `embeddings.csv` once to Parquet with stable names:

```text
Metadata_site_id
RxRx19aDL_0000
RxRx19aDL_0001
...
RxRx19aDL_1023
```

Join the embeddings to the canonical RxRx19a metadata by `site_id`. Validate one-to-one joins and missing sites.

For each pilot or full run, select the required rows from this canonical Parquet table using `manifest/selection.parquet`.

Use the Recursion embeddings to test:

- Metadata mapping.
- Mock versus viral separation.
- Irradiated-virus placement.
- Compound-dose grouping.
- Replicate consistency.
- Dose-response signal.

Do not treat this as the main buscar result. These embeddings are site-level. buscar is intended for distributions of single cells.

Store run-specific selected data under:

```text
baseline/recursion_site_embeddings/
```

## 11. Stage 3: CellProfiler container

Start from the headless container pattern here:

https://github.com/d33bs/demo-cellprofiler/tree/main/src/docker/sqlite_compartment_object_parent_foreign_keys

Use a Singularity definition instead of Docker on Alpine:

```text
containers/cellprofiler.def
```

Requirements:

- Pin the base image.
- Pin the CellProfiler version.
- Pin Python dependencies.
- Run headless.
- Include SQLite tools.
- Record the `.sif` SHA256 in `run.json`.
- Validate the image with `cellprofiler --version` on Alpine.

Keep the CellProfiler pipeline in Git:

```text
pipelines/rxrx19a.cppipe
```

Do not build scientific settings dynamically during a production run.

## 12. Stage 4: CellProfiler segmentation and measurements

Start with a standard, explainable segmentation approach.

Pilot plan:

- Detect nuclei from channel `w1` Hoechst.
- Identify whole-cell boundaries using a tested combination of the cell-body channels.
- Derive cytoplasm from cell minus nucleus.
- Measure intensity, size, shape, texture, and other standard CellProfiler morphology features.
- Measure relevant features in nuclei, cells, and cytoplasm.
- Export object relationships.

Freeze segmentation parameters after pilot visual QC.

Pilot segmentation review (real pilot-x8 data, 125,855 nuclei, HRCE-1 plate 25):
the configured nucleus diameter range (12-60 px) genuinely clips a small tail
of detected nuclei at both bounds (about 3.8% below 14 px, about 0.5% above
58 px) -- `Nuclei_AreaShape_Area` min/max in the real output match
`pi*(d/2)^2` for d=12 and d=60 almost exactly, confirming the range is an
active filter, not just documentation. The clipped tail is not strongly
enriched in `active_untreated` versus `mock`/`uv` wells, so it looks like
general segmentation noise (partial nuclei, debris, doublets at the
boundary) rather than clipped cytopathic/syncytial phenotype.

Cellpose cross-check (24 real pilot sites, 6 wells x 4 sites, 2 wells each
of active/uv/mock, `scripts/segmentation_cellpose_check.py`): CellProfiler
found 7,039 nuclei total across the 24 sites versus Cellpose's independent,
auto-diameter estimate of 3,360 -- about 2.1x more. But this is not a
uniform under/over-count: 16 of 24 sites (AA02, AA09, AA16, E16) have
CellProfiler counts within +/-25% of Cellpose's (125-200 vs Cellpose's
100-168), while 8 sites across two wells (AA08, E08) have CellProfiler
counts 3-7x Cellpose's (349-912 vs Cellpose's 120-161). The over-counted
wells have visibly lower-contrast w1 (Hoechst) images (std ~3.0-3.6,
p99 intensity ~16-19, max ~36-93) than the normal wells (std ~4.0-4.8,
p99 ~20-24, max ~57-145) -- same two conditions (uv, mock) have both a
normal-contrast well and a low-contrast, over-segmented well, so this
tracks with per-well image brightness/contrast, not perturbation. Most of
CellProfiler's excess objects in the bad wells are mid-range diameter
(17-33 px, not clipped at the 12 px floor), so this is not the same
clipping effect as above -- it looks like CellProfiler's intensity
threshold is splitting dim nuclei into multiple small fragments, or
picking up background texture as nuclei, in low-contrast fields. This is
a real segmentation-quality gap the correlation check above did not
catch (that check only looked at size-clipping, not spurious-object
over-counting). Recommendation: before a full run, review CellProfiler's
`IdentifyPrimaryObjects` thresholding settings (e.g. an adaptive or
per-image threshold method) against a handful of low-contrast fields, and
re-run this same Cellpose check after any threshold change to confirm the
spurious-object problem shrinks. Current diameter range (12-60 px) itself
is not the issue; thresholding sensitivity to per-field contrast is.

Threshold-strategy test (same 24 sites, re-ran `IdentifyPrimaryObjects`
with each of 3 settings changes isolated, compared object counts against
the saved Cellpose reference): no illumination-correction step exists in
`pipelines/rxrx19a.cppipe` today (confirmed: no `CorrectIllumination*`
module). Illumination correction (CellProfiler's own module, or BaSiC)
targets within-field shading/vignetting; what this plate showed is
whole-well brightness differences (AA08/E08 dim, AA02/AA09/AA16/E16
normal), which isn't the problem that tool fixes, so it was not tried.

Instead tested the threshold setting directly implicated by the earlier
finding:

- `Threshold strategy: Adaptive` (same Otsu method, computed per-window
  instead of per-image): made it worse (total 8,293 vs. baseline's 7,039;
  AA08/E08 sites went to 600-900+ objects, higher than baseline).
- `Threshold correction factor: 1.3` (require a brighter margin above
  the computed threshold): partial improvement (total 5,203, down from
  7,039) but AA08/E08 still ran 2-3x over Cellpose's counts.
- `Thresholding method: Robust Background` (assumes the image is mostly
  background, trims the brightest/dimmest 5% of pixels, thresholds at
  mean + 2 std of what remains, instead of Otsu's two/three-peak split):
  fixed it. Total count 3,754 vs. Cellpose's 3,360 (baseline was 7,039).
  Mean absolute per-site error vs. Cellpose dropped from 107% (baseline)
  to 13%; on just the two bad wells, from 302% to 27.5%. The remaining
  objects in AA08/E08 are normal-sized (median ~197 um^2, versus ~200-290
  um^2 in the unaffected wells under either method) -- not a handful of
  tiny fragments coincidentally summing to the right count, but objects
  that look like real nuclei. Good wells (AA02/AA09/AA16/E16) were
  essentially unchanged by the switch (good-well mean error 9.8% baseline
  vs. 6.2% Robust Background), so this is not a tradeoff against the
  wells that were already fine.

Recommendation: switch `IdentifyPrimaryObjects`'s nuclei thresholding
method from `Otsu` to `Robust Background` in `pipelines/rxrx19a.cppipe`
(keep `Threshold strategy: Global`, `Threshold correction factor: 1.0`;
only the one setting changes). This is a single pipeline-file edit, not a
new tool or dependency, and it stays within CellProfiler's own thresholding
options, so it scales to the full run the same way the rest of the
pipeline does -- no extra preprocessing stage, no illumination-correction
step needed. Re-validate on a fresh pilot subset once the pipeline change
lands (plan.md's existing Cellpose cross-check script,
`scripts/segmentation_cellpose_check.py`, already supports rerunning this
exact comparison).

Cross-plate re-validation with a real Cellpose reference (same 6-well/
24-site sampling pattern, applied to HRCE-1 Plate 1 and Plate 13, each
with its own 48-site Cellpose nuclei run -- not the "no reference, Otsu
vs. Robust Background compared only to each other" placeholder from the
first pass): neither plate had a site as extreme as Plate 25's AA08/E08
(worst case here was 226 objects on one site, versus 349-912 on Plate
25's bad wells), so this is a milder test of the same effect. Mean
absolute percent error vs. Cellpose across all 48 sites: Otsu 7.3%,
Robust Background 5.0% -- a real but modest improvement when averaged
over everything. Split by plate: Plate 1 was close either way (Otsu
4.8%, Robust Background 4.4%, total counts 3,665 vs. 3,622 against a
Cellpose total of 3,627); Plate 13 showed a clearer win (Otsu 9.8%,
Robust Background 5.5%, total counts 3,694 vs. 3,391 against a Cellpose
total of 3,445), concentrated in the dimmest sites found during the
earlier contrast scan (Plate 13's E16 wells, max pixel ~38-53): Otsu
overshot those sites by 36-51%, Robust Background's worst miss on the
same plate was 15%. Conclusion: Robust Background's improvement
generalizes in direction and does not introduce a new failure mode
(no under-segmentation -- see the size-sanity check above) on plates
that don't share Plate 25's severity of dim wells; it is a reasonable
default for the full run. If a future plate turns out to have a dim
well as extreme as Plate 25's, rerun
`scripts/segmentation_cellpose_check.py` against it for its own ground-
truth check the same way Plate 25, 1, and 13 were each validated here,
rather than assuming the Plate-25 numbers transfer directly. The full
reusable procedure (sample selection, threshold-variant pipeline
generation, Cellpose reference, count/contrast comparison) is written up
as a repeatable per-dataset pre-step in
`docs/src/segmentation-config-check.md`, backed by unit-tested functions
in `src/rerx/segmentation_check.py`.

Embedded production monitoring (so a future dim-well problem surfaces
automatically instead of needing a manual Cellpose investigation to
notice it): added CellProfiler's own `MeasureImageQuality` module to
`pipelines/rxrx19a.cppipe`, scoped to the DNA (w1 Hoechst) channel only
-- the channel used for nuclei segmentation and the one that showed the
Plate 25 AA08/E08 problem. Running it on all 5 channels hits SQLite's
`Per_Image` column limit in `ExportToDatabase` (confirmed: crashes with
"too many columns on Per_Image"); DNA-only avoids that. Verified on
Alpine against 6 real Plate 25 sites: adds ~0.2-0.4s per image
(negligible next to CellProfiler's own segmentation/measurement time --
the with-module run was not measurably slower than the baseline run in
the same job), and the resulting `Image_ImageQuality_MaxIntensity_DNA`
column correctly separates AA08 (0.17-0.26) from the normal wells
(0.40-0.57) end to end through `CytoTable`'s SQLite-to-Parquet
conversion (`RERX_JOINS` in `src/rerx/cytotable.py` extended to keep
`Image_ImageQuality_*` columns, which the base join dropped).
`rerx.validate.check_image_quality` (built on
`rerx.segmentation_check.flag_dim_wells`, a median/MAD outlier check
across a plate's wells) turns that column into a flagged-wells list,
wired into `PilotValidationReport`/`validate_pilot_run` as
`image_quality` alongside the other pilot exit-criteria checks. Cellpose
itself is not, and never will be, part of production -- it only served
as the one-time ground truth used to pick and validate Robust
Background; `MeasureImageQuality` is the ongoing, in-pipeline substitute
for noticing the next dim-well problem without rerunning that
investigation by hand. Needs several wells to resolve (a 2-3 well smoke
test won't reliably flag a known-dim well; a real pilot plate with 5+
wells per condition does, per the Plate 1/13/25 validation above).

Comparison against WayScience's `pediatric_cancer_atlas_profiling`
repo's CellProfiler pipelines (a similar Cell Painting assay, different
cell lines/stains): their nuclei `IdentifyPrimaryObjects` uses
`Thresholding method: Minimum Cross-Entropy`, not Otsu or Robust
Background -- tested as a third variant against the same 24 Plate 25
sites and the same saved Cellpose reference
(`reports/data/segmentation_cellpose_comparison.json`), using the
production pipeline (Robust Background default, `MeasureImageQuality`
now embedded) as the base and swapping only the nuclei threshold
method. Pooled mean absolute percent error vs. Cellpose across all 24
sites: Otsu 107.1%, Robust Background 13.3%, Minimum Cross-Entropy
29.8%. Per-well totals on the two known-bad wells (Cellpose reference in
parens): AA08 -- Otsu 2,364, Robust Background 739, Minimum
Cross-Entropy 1,052 (Cellpose 585); E08 -- Otsu 2,251, Robust Background
713, Minimum Cross-Entropy 915 (Cellpose 557). On the always-fine well
(AA02, Cellpose 537) all three were close (Otsu 581, Robust Background
586, Minimum Cross-Entropy 601) -- confirming (again) this is specifically
a dim-well over-segmentation problem, not a general threshold quality
difference. Visual overlay spot-check (DNA channel with mask boundaries
drawn on top) on AA08 site 1 matches the counts: Otsu's overlay is a
dense mesh of tiny fragmented regions covering nearly the whole dim
field; Robust Background's overlay shows clean individually-outlined
nuclei matching what's visible in the raw image; Minimum
Cross-Entropy's overlay is intermediate -- fewer large merged/fragmented
blobs than Otsu, but still visibly messier than Robust Background's
clean single-nucleus outlines, several two-three-nucleus clumps not
split. Robust Background remains the better choice for this pipeline;
Minimum Cross-Entropy is not a recommended change. (Overlay images and
the comparison script are scratch-only, not part of the committed repo,
matching how earlier threshold-variant investigations in this section
were also one-off scratch comparisons with only the numeric conclusion
kept in this file.)

Added a pixel-overlap (IoU) check on the 3 spot-check sites (AA08,
E08, AA02, same sites as the overlay images above) alongside the
count-only comparison, since two masks can agree on total count while
segmenting different regions. Reused the exact greedy best-IoU
matching logic already validated in
`scripts/segmentation_cellpose_check.py` (`_match_masks`,
`IOU_MATCH_THRESHOLD = 0.5`) against a freshly regenerated Cellpose
reference mask for each site (counts matched the saved reference
exactly: AA08 149, E08 132). Results confirm the count-based
conclusion and add detail it couldn't show: on the two bad wells, the
fraction of CellProfiler nuclei that actually land on a real Cellpose
nucleus (>=50% IoU) is Otsu 1-4%, Minimum Cross-Entropy 13-15%, Robust
Background 46-48% -- the gap is much larger by this measure than by
count alone, because Otsu's and Minimum Cross-Entropy's "right-ish"
counts on these wells are partly coincidental (lots of small spurious
fragments summing close to the true count, not matching real nuclei
positions). One honest nuance IoU surfaced that counts alone missed:
on the always-fine well AA02, Minimum Cross-Entropy's match rate (33%,
pixel IoU 0.36) actually edges out Robust Background's (18%, pixel IoU
0.30) -- Robust Background's AA02 object count was closer to
Cellpose's, but its object boundaries there overlap real nuclei
somewhat less precisely. This doesn't change the recommendation (the
bad-well advantage, where the real production problem lives, dwarfs
this small well-matched-well difference), but is reported as-is rather
than only showing the metric that favors the chosen method. Full
numbers and a side-by-side visual comparison (DNA channel + mask
boundary overlay, one column per method, per spot-check site) are in a
scratch HTML page (not committed, same reasoning as above).

Each Slurm task processes a moderate shard of nearby image sets. Batch by data locality.

Do not start with one job per image.

Tune shard size during the pilot. Prefer jobs that take tens of minutes instead of thousands of tiny jobs or multi-hour monoliths.

Each task writes temporary outputs to local or Alpine scratch:

```text
<scratch>/<run_id>/<task_id>/
```

CellProfiler writes SQLite per task shard. Use one table per object type and retain parent-child relationships.

Run SQLite integrity checks inspired by the demo repository before conversion.

## 13. Stage 5: CellProfiler to single-cell Parquet

Convert validated CellProfiler SQLite output to one row per cell.

Prefer CytoTable for compartment joins if the pilot confirms clean joins for this pipeline.

The final CellProfiler Parquet row must include:

- Stable metadata.
- Stable `Metadata_cell_id`.
- Cell measurements.
- Nucleus measurements.
- Cytoplasm measurements.

Use clear feature prefixes such as the standard CellProfiler/Cytomining names.

Write partitioned Parquet by experiment and plate.

Target reasonably large Parquet files. Avoid one Parquet file per site or cell.

After Parquet validation succeeds, temporary SQLite can be deleted from scratch.

## 14. Stage 6: cell crop Parquet

Generate crops while CellProfiler masks and source images are available.

Use one Parquet row per cell.

Suggested schema:

```text
Metadata_cell_id
Metadata_site_id
Metadata_experiment
Metadata_plate
Metadata_well
Metadata_site
Metadata_object_number
Metadata_center_x
Metadata_center_y
crop_width
crop_height
jpeg_quality
crop_w1_jpeg
crop_w2_jpeg
crop_w3_jpeg
crop_w4_jpeg
crop_w5_jpeg
```

Each `crop_w*_jpeg` column is Parquet `BINARY` containing one grayscale JPEG.

Crop rules:

- Center on the CellProfiler cell.
- Use the CellProfiler cell mask.
- Set pixels outside the cell mask to a fixed background value.
- Use the same crop geometry for all five channels.
- Pad deterministically when a crop reaches an image boundary.
- Use one fixed crop size after the pilot.
- Record JPEG quality in every row or the run manifest.

Start with high-quality JPEG, such as quality 95 or higher, but validate the choice before freezing it.

For the pilot, compare MorphEm embeddings from lossless in-memory crops against embeddings from stored JPEG crops. Keep the JPEG setting only if the difference is acceptable for this analysis.

Store crops separately from profile data:

```text
crops/cells/
```

This keeps profile scans fast and lets users ignore image bytes when they only need features.

## 15. Stage 7: MorphEm extraction

MorphEm source:

https://huggingface.co/CaicedoLab/MorphEm

MorphEm expects segmented images. It can process channels independently and combine channel embeddings for downstream analysis.

Create a separate pinned runtime:

```text
containers/morphem.def
```

Record:

- Model repository.
- Model revision or commit.
- Container hash.
- Preprocessing settings.
- Crop size.
- Resize method.
- Channel order.
- Output dimension.

Do not assume the embedding dimension in downstream code. Read it from the validated model output and write it into `run.json`.

Use `Metadata_cell_id` as the join key.

Write one row per cell.

For analysis convenience, write embedding dimensions as numeric feature columns with stable names:

```text
MorphEm_0000
MorphEm_0001
...
```

Keep the raw MorphEm dataset separate from CellProfiler features.

If GPU access is available, run MorphEm as a separate GPU workflow. Do not mix CPU CellProfiler jobs and GPU MorphEm jobs into one opaque Alpine configuration.

## 16. Stage 8: QC

QC is a dataset stage, not an afterthought.

CellProfiler QC:

- Images processed versus expected.
- Cells per site.
- Empty sites.
- Extreme cell sizes.
- Extreme intensity values.
- Missing values.
- Duplicate cell IDs.
- Failed compartment joins.

Crop QC:

- Crop rows equal single-cell rows.
- All JPEG fields decode.
- All five channels exist.
- Dimensions are consistent.
- Random visual samples look centered and masked correctly.

MorphEm QC:

- One embedding per `Metadata_cell_id`.
- Fixed output dimension.
- No NaN or infinite values.
- No duplicate cell IDs.

Store a small set of QC images and overlays under:

```text
qc/images/
```

Do not store huge diagnostic image collections.

## 17. Stage 8A: compare Recursion embeddings with MorphEm

Compare the two embedding systems at the same unit: one vector per RxRx19a site.

Do not compare raw embedding coordinates. The two models use different latent spaces and different dimensions. `RxRx19aDL_0000` has no direct relationship to `MorphEm_0000`.

Build a matched site table keyed by `Metadata_site_id`.

For MorphEm, aggregate single-cell embeddings within each site. Use the per-dimension median as the primary site representation. Also compute the mean as a sensitivity check. Keep the single-cell MorphEm data unchanged.

Write:

```text
comparisons/recursion_vs_morphem/
├── matched_sites.parquet
├── morphem_site_median.parquet
├── morphem_site_mean.parquet
├── metrics.parquet
└── figures/
```

Use the exact same sites and metadata labels for both representations. Normalize each feature space separately. Never normalize one model using statistics from the other model.

Primary comparison questions:

1. Do the two models organize the same sites similarly?
1. Which model better preserves the biological signals needed for the ReRx analysis?
1. Does the single-cell MorphEm representation add useful information beyond the existing site-level Recursion representation?

Use boring metrics first.

Geometry comparison:

- Spearman correlation between pairwise site-distance matrices.
- k-nearest-neighbor overlap for the same sites, for a few fixed `k` values.
- PCA plots for both representations using identical labels and site subsets.
- Optional UMAP or openTSNE only for visualization, not as the main quantitative comparison.

Biology comparison:

- Mock versus viral separation.
- Mock, viral, and irradiated-virus control structure.
- Replicate similarity within the same treatment and dose.
- Same-compound retrieval across replicate wells.
- Dose-response ordering for selected compounds.
- Known active versus weak/inactive treatment behavior.
- Plate and experiment sensitivity.

Use the same train/test or holdout splits for both feature spaces when a supervised metric is used. Prefer plate-aware holdouts so a model cannot win by learning plate identity.

Do not declare a winner from one visualization. Summarize metrics in one Parquet table with columns such as:

```text
representation
aggregation
metric
subset
value
seed
```

The primary representation labels are:

```text
recursion_site
morphem_site_median
morphem_site_mean
```

Keep buscar separate from this site-level comparison. buscar remains a single-cell analysis for CellProfiler and MorphEm. The Recursion embedding comparison is a baseline and representation benchmark.

## 18. Stage 9: coSMicQC and Pycytominer

Use this example as the starting pattern:

https://github.com/cytomining/coSMicQC/blob/main/docs/src/examples/jump_umap_analysis_with_cosmicqc.py

For CellProfiler profiles:

1. Annotate with RxRx19a metadata.
1. Add coSMicQC flags where the features and rules apply.
1. Preserve QC columns outside the morphology feature set.
1. Normalize using explicit control samples.
1. Run feature selection.

Start with the same simple feature-selection operations used in the reference example:

```text
variance_threshold
correlation_threshold
blocklist
drop_na_columns
```

Keep normalization configuration explicit and versioned.

Create control metadata with separate meanings:

```text
Metadata_rxrx_control_type
Metadata_pycytominer_control_type
Metadata_buscar_state
```

Do not overload one column with three concepts.

For MorphEm profiles:

- Annotate with the same metadata.
- Normalize embedding dimensions using the selected controls.
- Do not apply a CellProfiler-specific blocklist.
- Add extra feature selection only if the pilot shows a clear need.

Keep raw, annotated, normalized, and selected datasets separate.

## 19. Stage 10: buscar analysis

Run buscar after profile production passes QC.

Primary HRCE mapping:

```text
mock              = target
viral control     = reference
viral + drug+dose = perturbation
irradiated        = extra control
```

Create a stable perturbation identifier from treatment and concentration.

Example shape:

```text
Metadata_perturbation = <treatment>__<concentration>
```

Run the analysis twice:

1. CellProfiler feature space.
1. MorphEm feature space.

Store signatures and scores as Parquet.

Compare:

- Known active versus inactive treatments.
- Dose response.
- Replicate consistency.
- Plate consistency.
- Agreement between CellProfiler and MorphEm results.

Do not make the combined CellProfiler + MorphEm feature space the primary result until the separate analyses are understood.

## 20. Stage 11: Frozen DuckLake catalog

Parquet remains canonical.

After a run has `_SUCCESS`, build a read-only Frozen DuckLake catalog over its Parquet files.

Reference:

https://ducklake.select/2025/10/24/frozen-ducklake/

Use `ducklake_add_data_files()` so the catalog references files already created by the pipeline.

Do not route worker writes through DuckLake.

Suggested tables:

```text
metadata
cellprofiler_raw
cellprofiler_normalized
cellprofiler_feature_selected
crops
morphem_raw
morphem_normalized
recursion_site_embeddings
morphem_site_median
morphem_site_mean
embedding_comparison_metrics
buscar_cellprofiler_signatures
buscar_cellprofiler_scores
buscar_morphem_signatures
buscar_morphem_scores
```

Store the generated catalog under:

```text
catalog/
```

Treat it as disposable and rebuildable.

A DuckLake catalog can contain storage-specific file paths. After mirroring a run to another storage root, rebuild a catalog for that root instead of assuming the first catalog is portable.

Anyone must still be able to inspect the dataset with plain tools such as:

```sql
SELECT *
FROM read_parquet('profiles/cellprofiler/feature_selected/**/*.parquet');
```

## 21. Stage 12: publish and mirror

PetaLibrary is the primary durable output for Alpine runs.

Publish only validated shards into the run directory.

At completion:

1. Generate `manifest/files.parquet` with relative path, size, and checksum.
1. Write `run.json` with final row counts and versions.
1. Build the PetaLibrary DuckLake catalog.
1. Write `_SUCCESS` last.
1. Mirror the complete run to the Isilon `ReRx` destination.
1. Validate the mirror against `manifest/files.parquet`.
1. Build a mirror-local DuckLake catalog if wanted.
1. Write `_MIRRORED` after validation.

Use `rsync` or Globus based on the storage endpoints available at run time. Keep the sync command path-configurable.

Never sync partial production output into a directory that already represents a successful run.

## 22. Alpine execution model

Follow the Alpine skill.

For Nextflow-scale workflows:

- Run the workflow manager on `Persistence1`.
- Run compute tasks through Slurm.
- Use production `queueSize = 200` unless CURC guidance changes.
- Do not add `submitRateLimit` by default.
- Batch small work before throttling submissions.
- Set explicit memory, CPU, and walltime for every process.
- Keep CPU and GPU work in separate profiles or workflow runs.

Do not copy the Formascute resource numbers blindly. CellProfiler and MorphEm have different memory and runtime behavior. Measure them during the ReRx pilot.

Suggested workflow split:

```text
CPU workflow A
  manifest
  CellProfiler
  SQLite validation
  single-cell Parquet
  crop Parquet

GPU workflow B
  MorphEm

CPU workflow C
  QC
  Pycytominer
  buscar
  catalog
  publish
```

This split makes failures easier to understand and rerun.

## 23. Atomic output rule

Every worker follows the same pattern:

1. Create a task directory in scratch.
1. Process the shard there.
1. Validate output there.
1. Write Parquet to a temporary name.
1. Close the file.
1. Validate schema and row count.
1. Move the final shard into the durable run directory.
1. Record the shard in the output manifest.

Never leave half-written Parquet files in the durable dataset.

## 24. Parquet rules

Keep schemas stable within one run.

Use:

- ZSTD compression.
- Hive-style partitions for experiment and plate where useful.
- Files large enough to avoid a small-file problem.
- Stable column names.
- Metadata columns prefixed with `Metadata_`.
- Morphology features kept numeric.

Do not partition by high-cardinality fields such as cell ID, well, or compound.

## 25. Run metadata

`run.json` must include at least:

```text
run_id
scope
created_at_utc
git_commit
source_manifest_hash
configuration_hash
cellprofiler_version
cellprofiler_pipeline_hash
cellprofiler_container_hash
morphem_model_revision
morphem_container_hash
pycytominer_version
buscar_version
source_image_sets
processed_image_sets
cell_count
crop_count
morphem_profile_count
recursion_embedding_source_sha256
recursion_embedding_site_count
morphem_site_count
matched_embedding_site_count
status
```

Also record Slurm job IDs and key performance summaries in the logs or manifest.

## 26. Cleanup

Cleanup must work by run ID.

Provide commands like:

```text
rerx runs list
rerx runs inspect <run-id>
rerx runs delete <run-id> --dry-run
rerx runs delete <run-id> --yes
```

Rules:

- Require an exact run ID.
- Refuse to delete `_PROTECTED` runs by default.
- Never use wildcard deletion in normal commands.
- Delete Alpine scratch separately from durable datasets.
- A failed run can be removed as one directory.
- A bad run is never repaired in place. Start a new timestamped run.

## 27. Pilot exit criteria

Do not start the full dataset until these pass.

Technical:

- Source manifest is complete.
- Pilot is reproducible from `selection.parquet`.
- CellProfiler container runs on Alpine.
- CellProfiler processes every pilot shard.
- SQLite integrity checks pass.
- Parquet schema is identical across shards.
- Cell IDs are unique.
- Crop rows join one-to-one with CellProfiler cells.
- JPEG crops decode.
- Random crop and segmentation QC looks correct.
- MorphEm produces one valid embedding per cell.
- MorphEm site aggregates join one-to-one with selected Recursion site embeddings.
- Recursion-versus-MorphEm comparison metrics run on the exact same site subset.
- Pycytominer can annotate and normalize the pilot.
- buscar can score the pilot.
- Frozen DuckLake queries the completed Parquet data.
- PetaLibrary-to-Isilon mirror validates.

Performance:

- Record wall time per image set.
- Record cells per second.
- Record crop generation rate.
- Record MorphEm cells per second.
- Record peak RSS and CPU/GPU use.
- Choose batch sizes from these measurements.
- Choose Slurm memory and walltime from these measurements.

Scientific sanity:

- Mock and viral controls are distinguishable in at least one feature representation.
- Replicate controls behave consistently enough to continue.
- Known treatment behavior is inspected, but biological expectations do not override technical QC.

## 28. Full run

After the pilot passes:

1. Freeze the pipeline configuration.
1. Tag the repository commit used for production.
1. Create a new `full` run ID.
1. Build the complete source manifest.
1. Stage source data if Alpine cannot access it directly.
1. Run CPU workflow A.
1. Validate all CellProfiler and crop partitions.
1. Run MorphEm workflow B.
1. Validate MorphEm partitions.
1. Build matched MorphEm site aggregates and compare them with Recursion site embeddings.
1. Run CPU workflow C.
1. Freeze the DuckLake catalog.
1. Write `_SUCCESS`.
1. Mirror to Isilon.
1. Validate the mirror.
1. Protect the run when it becomes a referenced analysis dataset.

Process all RxRx19a images for the full feature dataset. Keep the primary buscar analysis focused on HRCE first. Treat Vero as a secondary analysis unless the scientific question changes.

## 29. First implementation milestones

### Milestone 1: repository and metadata

- Create repo from the CU DBMI template.
- Download, checksum, and convert the existing RxRx19a site embeddings to canonical Parquet.
- Add pinned Alpine and PetaLibrary skills.
- Add storage configuration model.
- Add run-ID creation.
- Build RxRx19a source manifest.
- Build pilot selection.

### Milestone 2: one image set

- Build Singularity CellProfiler image.
- Process one five-channel site.
- Export SQLite.
- Convert to one-cell-per-row Parquet.
- Create masked crops.
- Store crops in Parquet.
- Run MorphEm on those cells.

### Milestone 3: pilot

- Run 24-48 wells.
- Aggregate MorphEm cells to matched site-level profiles.
- Compare Recursion and MorphEm representations on identical sites.
- Add QC.
- Add Pycytominer.
- Add buscar.
- Measure resources and runtime.

### Milestone 4: storage and publication

- Add immutable run publication.
- Add Frozen DuckLake generation.
- Add file manifest and checksums.
- Add PetaLibrary-to-Isilon mirror validation.
- Add safe cleanup commands.

### Milestone 5: full RxRx19a

- Freeze versions and configuration.
- Run all images.
- Publish full dataset.
- Run HRCE buscar analysis.

## 30. Things not to do yet

Do not add these until the basic pipeline works:

- Distributed databases.
- Custom storage formats.
- A service layer.
- A web application.
- Automatic parameter tuning.
- CellProfiler + MorphEm feature concatenation as the main analysis.
- Multiple orchestration frameworks.
- Complex cross-run caching.

Plain files, stable IDs, Parquet, Singularity, Slurm, and small Python tools are enough.

## 31. References

- ReRx project template: https://github.com/CU-DBMI/template-uv-python-research-software/
- buscar: https://github.com/WayScience/buscar
- RxRx19a dataset docs: https://github.com/recursionpharma/rxrx-datasets/tree/trunk/rxrx19a
- RxRx19a provided embeddings: https://storage.googleapis.com/rxrx/RxRx19a/RxRx19a-DL-embeddings.zip
- Alpine skill: https://github.com/WayScience/formascute/blob/main/.agents/skills/alpine/SKILL.md
- PetaLibrary skill: https://github.com/WayScience/formascute/blob/main/.agents/skills/petalibrary/SKILL.md
- CellProfiler container/SQLite example: https://github.com/d33bs/demo-cellprofiler/tree/main/src/docker/sqlite_compartment_object_parent_foreign_keys
- Pycytominer/coSMicQC example: https://github.com/cytomining/coSMicQC/blob/main/docs/src/examples/jump_umap_analysis_with_cosmicqc.py
- MorphEm: https://huggingface.co/CaicedoLab/MorphEm
- CHAMMI-75: https://github.com/CaicedoLab/CHAMMI-75
- Frozen DuckLake: https://ducklake.select/2025/10/24/frozen-ducklake/
- Caveman style reference: https://github.com/juliusbrussee/caveman

# RxRx19a reanalysis

This repository reanalyzes RxRx19a images to ask whether treatments move
SARS-CoV-2-infected cells toward a healthy appearance.
The pipeline is specific to RxRx19a, though some parts can be reused with
other datasets.

## What this pipeline does

The pipeline turns RxRx19a microscopy images into single-cell morphology
profiles.
It then uses buscar to score how much each treatment moves infected cells
toward the healthy state.

The pipeline has eight stages, in order:

1. **Metadata and selection** — download the RxRx19a metadata, then pick
   the wells for a run (a small pilot subset, or the full dataset).
1. **CellProfiler** — segment cells and measure per-cell features from
   the five-channel images. Runs in a container (Docker locally,
   Apptainer on Alpine).
1. **CytoTable** — join CellProfiler's per-compartment SQLite tables
   (Nuclei, Cells, Cytoplasm) into one row per cell, and add a stable
   `Metadata_cell_id`.
1. **Crops** — cut a JPEG crop of each cell from each of the five
   channels, for visual QC and embedding models.
1. **MorphEm** — run the MorphEm vision transformer over each cell's
   five-channel crop to get a 1,920-number deep-learning feature
   vector per cell (5 channels × 384 embedding dims). Runs in its own
   Apptainer container, on CPU.
1. **Finalize (per plate, per profiler)** — annotate, normalize, and
   select features with Pycytominer for both CellProfiler and MorphEm
   profiles, run a biological QC gate, and run buscar reversal
   scoring. See "Why the pipeline batches by plate" below.
1. **Fuse** — join the two feature sets per cell on `Metadata_cell_id`
   into one combined profile (`rerx.fuse`), written under
   `profiles/fused/` with a `fusion.json` sidecar.
1. **Catalog** — build a read-only DuckLake catalog over the finished
   Parquet files, so anyone can query the run with plain SQL.

Finalize also writes a human crop spot-check notebook
(`qc/images/crop_spot_check.py`, built by `rerx.qc_notebook` — a small,
seeded-random sample of per-cell crops rendered with
[`cytodataframe`](https://github.com/cytomining/CytoDataFrame) so a person
can open it in Jupyter and visually confirm segmentation looks centered
and masked correctly). It is a convenience output of every run, not a
pipeline stage with its own data to pass downstream.

The diagram below shows the same eight stages as data flowing left to
right, with the QC checks placed where they actually run, and the
pilot analysis reports that read the finished catalog at the end:

```mermaid
flowchart TD
    A["1 Metadata and selection<br/>download RxRx19a metadata,<br/>pick pilot or full wells"] --> B
    B["2 CellProfiler<br/>segment cells, measure features<br/>(container: Docker/Apptainer)"] --> BQC{{"QC: SQLite integrity check<br/>check_sqlite_integrity"}}
    BQC --> C["3 CytoTable<br/>join Nuclei/Cells/Cytoplasm<br/>into one row per cell"]
    C --> D["4 Crops<br/>per-cell 5-channel JPEG crops"]
    D --> DQC{{"QC: crop decode check<br/>check_crops_decode"}}
    DQC --> E["5 MorphEm<br/>deep-learning embedding<br/>per cell crop (CPU container)"]
    C --> F
    E --> F["6 Finalize (per plate, per profiler)<br/>annotate + add perturbation"]
    F --> FQC1{{"QC: coSMicQC outlier flag + drop<br/>flag_outliers (before normalizing)"}}
    FQC1 --> F2["normalize + select features"]
    F2 --> FQC2{{"QC: control separation gate<br/>check_control_separation<br/>(mock vs. disease, Cohen's d)"}}
    FQC2 -- pass --> FB["buscar reversal scoring<br/>on-score / off-score per treatment"]
    FQC2 -- fail --> FSkip["buscar skipped,<br/>reason logged"]
    D -. sampled .-> QCN{{"QC: human crop spot-check<br/>qc/images/crop_spot_check.py<br/>(cytodataframe, manual review)"}}
    FB --> G
    FSkip --> G["7 Fuse<br/>join CellProfiler + MorphEm<br/>per cell on Metadata_cell_id"]
    G --> H["8 Catalog<br/>DuckLake over the finished Parquet"]
    H --> R["Analysis and reports<br/>(reports/, pilot scale)"]
    R --> R1["phenotypic_overview.html<br/>CellProfiler vs. MorphEm vs.<br/>Recursion embeddings"]
    R --> R2["buscar_reversal.html<br/>on/off reversal scores<br/>per treatment"]
    R --> R3["pipeline_run.html<br/>run health: timing,<br/>QC pass/fail, flag rates"]
    R --> R4["segmentation_check.html<br/>Cellpose cross-check:<br/>why Robust Background"]
    R --> R5["decisions.html<br/>other component decisions,<br/>options tried, outcomes"]
```

Two runners drive this pipeline:

- Local, small runs: `tests/test_pilot_e2e.py` (an opt-in end-to-end
  test) shows the full call sequence against a real CellProfiler
  container.
- Alpine HPC, full-scale runs: `workflows/main.nf` (Nextflow, Slurm
  executor) calls `scripts/rerx_tasks.py`, a thin command-line driver
  that wraps the same library functions under `src/rerx/`.

## Why the pipeline batches by plate

Each plate is its own biological batch: it has its own mock (healthy) and
disease (active, untreated) control wells, and normalization must
compare cells against controls from the *same* plate, not a different
one.

This has three effects on the code:

- `rerx.cytotable.plate_partitions` groups cell profiles by
  `(experiment, plate)`. Every downstream step consumes one plate's
  group at a time.
- `rerx.finalize.finalize_plate` runs annotate, normalize,
  feature-select, the control-separation QC gate, and buscar for one
  plate. It never holds more than one plate's cells in memory.
- A full multi-plate run (thousands of plates) processes plate by
  plate. Memory use stays flat as the run scales, instead of growing
  with the whole dataset.

If a future project has a different batching unit (for example, per
96-well plate barcode, or per acquisition day), replace
`plate_partitions`'s group key. The rest of `finalize_plate` does not
change.

## The biological QC gate

A technically clean run (every SQLite integrity check passes, every
crop decodes) can still carry bad biology — a failed stain, or a dead
plate where nothing grew. `rerx.validate.check_control_separation`
catches this case.

The check compares the mock and disease control populations on every
morphology feature, using Cohen's d effect size. If the median absolute
effect size falls below a threshold (0.5 by default), the check fails
for that plate.

This check runs before buscar, not after, for a concrete reason: buscar
itself raises an error when there is no separating feature between mock
and disease (division by zero inside its Earth Mover's Distance
calculation). `finalize_plate` checks first, then skips buscar with a
clear reason logged, instead of letting the whole run crash on one bad
plate.

## buscar reversal scoring

Here is the whole comparison: RxRx19a provides healthy, infected, and
infected-plus-treatment wells. buscar learns what infection changes from
the first two groups, then scores each drug and dose against them. It
runs separately for each plate and feature set (CellProfiler or MorphEm)
after the control-separation QC check passes.

```mermaid
flowchart TD
    H["RxRx19a mock<br/>healthy = buscar target"]
    I["RxRx19a active_untreated<br/>infected = buscar reference"]
    H --> S["1. Learn infection-related<br/>and other cell features"]
    I --> S
    S --> B["2. buscar scores each<br/>drug + dose"]
    T["RxRx19a treated<br/>infected + drug + dose"] --> B
    B --> R["buscar on-score:<br/>closer to RxRx19a mock?<br/>buscar off-score:<br/>other features changed?"]
    R --> C["Compare drug + dose trends<br/>and known-active / inactive drugs<br/>across plates and<br/>CellProfiler / MorphEm"]
```

**What the labels mean:** RxRx19a `mock` means uninfected wells; these
fill buscar's **target** (healthy) role. RxRx19a `active_untreated`
means infected wells with no drug; these fill buscar's **reference**
(disease) role. RxRx19a `treated` wells contain infected cells given a
particular drug and dose. buscar's target/reference roles are not drug
controls: Remdesivir (known active) and Oseltamivir carboxylate (known
inactive) are drugs used to check whether the scores behave sensibly.
RxRx19a `uv` (inactivated virus) is another check, not buscar's target
or reference. There are no vehicle-only wells in RxRx19a, so its
`active_untreated` group is a disease-reference analog, not literally
"disease + vehicle."

**How scoring works:** buscar uses the two control groups on the same
plate to identify features that differ with infection (the *buscar on*
signature) and features that do not (the *buscar off* signature). For
each RxRx19a drug-and-dose group, the buscar on-score measures distance
from the healthy buscar target in infection-related features. RxRx19a
`active_untreated` has a buscar on-score of **1.0** by construction; a
score near **0** is closer to RxRx19a `mock`. The buscar off-score
measures the fraction of other features that change compared with
RxRx19a `mock`; lower is better for both scores. A buscar on-score can
exceed 1.0. These are morphology comparisons, not proof that a drug
treats infection or is free of side effects. buscar scores each group
against RxRx19a `mock`, using RxRx19a `active_untreated` to set the
buscar on-score scale; it does not compare two drugs directly.

**What we can compare from the scores:** Each scored plate and feature
set produces buscar on-scores and buscar off-scores for drug-and-dose
groups and RxRx19a controls. We can then compare dose trends and
known-active versus known-inactive drugs. Across plates, we can check
whether these patterns repeat. By scoring CellProfiler and MorphEm
separately, we can ask whether the two feature sets favor the same
treatments. Their feature values and signatures differ, so compare
score patterns rather than raw features. A plate whose healthy and
infected controls do not separate is skipped, not given buscar scores.

The finalize stage records RxRx19a well labels in
`Metadata_rxrx_control_type`, and groups replicates for scoring with
`Metadata_perturbation` (for example,
`Remdesivir (GS-5734)__1.0`). `Metadata_buscar_state` labels wells for
inspection but is not used to calculate scores. For each scored plate
and profiler, buscar writes `signatures.parquet` (on/off feature lists)
and `scores.parquet` (one row per group, including
`on_buscar_scores` and `off_buscar_scores`; the healthy buscar target
itself is excluded from this score table).

## The result tree

A finished run directory (see `rerx.runs.make_run_dir`) looks like this:

```text
runs/<run_id>/
├── metadata/
│   └── rxrx19a.parquet              # full parsed RxRx19a metadata
├── selection.json                    # wells picked for this run
├── shards.json                       # CellProfiler shard plan
├── profiles/cellprofiler/
│   ├── raw/
│   │   └── experiment=<e>/plate=<p>/profiles.parquet   # CytoTable output
│   ├── normalized/
│   │   └── experiment=<e>/plate=<p>/profiles.parquet   # Pycytominer normalize
│   └── feature_selected/
│       └── experiment=<e>/plate=<p>/profiles.parquet   # Pycytominer select_features
├── profiles/morphem/
│   └── feature_selected/
│       └── experiment=<e>/plate=<p>/profiles.parquet   # MorphEm embeddings
├── profiles/fused/
│   ├── fusion.json                                    # join key, column counts
│   └── feature_selected/
│       └── experiment=<e>/plate=<p>/profiles.parquet   # CellProfiler + MorphEm
├── baseline/
│   └── recursion_site_embeddings.parquet              # Recursion site embeddings
├── crops/cells/
│   └── <shard_id>.parquet            # one row per cell, 5 JPEG columns
├── buscar/cellprofiler/
│   └── experiment=<e>/plate=<p>/
│       ├── signatures.parquet
│       ├── scores.parquet
│       └── cellprofiler_summary.json
├── catalog/
│   └── run.ducklake                  # DuckLake catalog over every file above
├── validation_report.txt             # technical + biological QC summary
└── _SUCCESS                          # written only when the run passes
```

Every Parquet file under `profiles/`, `crops/`, and `buscar/` stays the
canonical data. The DuckLake catalog only points at these files; delete
and rebuild it any time with `rerx.catalog.build_run_catalog`.

Query the finished dataset with plain SQL, without DuckLake, using
DuckDB's `read_parquet`:

```sql
SELECT *
FROM read_parquet('profiles/cellprofiler/feature_selected/**/*.parquet');
```

## Memory footprint at scale

The `finalize` step (annotate/normalize/select/buscar/fuse/validate)
streams: it holds one plate's rows (or one shard's crops) in memory
at a time, never the whole run. Memory scales with the largest single
plate, not the full dataset — see `src/rerx/streaming.py` and the
comment on the `FINALIZE` process in `nextflow.config`.

One step does not yet stream: `recursion-buscar` downloads and
converts Recursion's published site-embedding archive (~1.5 GB
zipped) into one Parquet file in memory. It must run as a Slurm job,
not on a login node — a login-node run OOM-killed on Alpine's ~1.6 GB
per-user cap. `FINALIZE`'s 64 GB Slurm allocation covers it today;
revisit if the published archive grows.

## Adapting this pipeline to a different dataset

Four things are specific to RxRx19a today, and each has a clear home if
you port this pipeline to a different imaging dataset:

- **Metadata schema and channel layout** — `src/rerx/metadata.py` (URL
  format, column names, five-channel naming).
- **Control taxonomy** — `src/rerx/pycytominer.py`'s
  `RXRX_CONTROL_*` constants and `rxrx_control_type` (map your dataset's
  own control labels to the same three-column pattern:
  `Metadata_rxrx_control_type`, `Metadata_pycytominer_control_type`,
  `Metadata_buscar_state`).
- **CellProfiler pipeline** — `pipelines/rxrx19a.cppipe` is tuned for
  this dataset's cell type and stains. A new dataset needs its own
  `.cppipe`, built and validated the same way.
- **Batching unit** — `rerx.cytotable.plate_partitions`, as described
  above.

Everything else — sharding, container invocation, CytoTable conversion,
crops, MorphEm embedding, the QC gate, buscar, fusion, the catalog, and
the Nextflow/Slurm orchestration — works unchanged.

## References

`CITATION.cff` lists every paper, dataset, and tool this project cites
or depends on. Cite this software using the `authors` metadata at the
top of that file.

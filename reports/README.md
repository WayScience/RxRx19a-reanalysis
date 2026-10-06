# ReRx pilot reports

Five self-contained HTML reports built from the pilot run's data (HRCE-1
Plate 25, pipeline report from run `pilot-x8`). Each report embeds its data
directly, so it opens and works from a plain file:// URL with no server, no
network, and no build step.

- `phenotypic_overview.html` — compares CellProfiler features against
  Recursion's published deep-learning site embeddings and MorphEm
  per-cell embeddings (PCA plots, Spearman correlations of
  site-distance matrices, k-nearest-neighbor overlap). MorphEm ran on
  all 33,330 pilot cells on Alpine CPU nodes (1,920 raw features,
  1,552 after selection; 0.75 Spearman / 45% kNN overlap vs
  CellProfiler).
- `buscar_reversal.html` — buscar reversal scores for every pilot
  treatment (efficacy/specificity scatter, Remdesivir/Oseltamivir dose
  response, full score table). Explains why the report scores wells
  instead of single cells (see `rerx.validate.check_control_separation`).
- `pipeline_run.html` — operational/pipeline-health report, not biology:
  shard counts, Slurm stage timing, validation pass/fail, coSMicQC
  outlier-flag rates, CellProfiler/MorphEm fusion drop counts, and
  buscar skip reasons for a specific run (currently `pilot-x8`, 125,855
  cells across 176 wells).
- `segmentation_check.html` — the Cellpose-vs-CellProfiler nuclei
  segmentation cross-check (plan.md's segmentation review): shows the
  Otsu-threshold finding that motivated switching to Robust Background
  (two dim wells out of six sampled, 3-7x over-segmented; 304% mean
  abs. count error vs. Cellpose on those two wells, 9% on the other
  four). Notes that Cellpose is a reference, not ground truth (a later
  visual side-by-side via `scripts/segmentation_cytodataframe_compare.py`
  showed its boundaries trace the whole-cell signal, while the
  dim-well failure of Otsu/Adaptive/MCE is directly visible regardless).
  A methods/decision report, not a biology result.
- `decisions.html` — a curated record of other pipeline-configuration
  decisions (image-quality QC scope, illumination correction, buscar
  aggregation level, crop JPEG quality, pilot run size): options
  actually tried, what happened, and which one is in use today. Most
  entries are "decided"; one (crop JPEG quality) is still "pending"
  the lossless-vs-JPEG validation plan.md calls for.

## Screenshots

Static full-page screenshots of all five reports live in `screenshots/`, so
they render inline in GitHub PRs without opening the HTML files:

- `screenshots/phenotypic_overview.png`
- `screenshots/buscar_reversal.png`
- `screenshots/pipeline_run.png`
- `screenshots/segmentation_check.png`
- `screenshots/decisions.png`

## Regenerating the data

Seven scripts build the report payloads. Four read local copies of the
pilot run's Parquet files (synced from Alpine into `data/`, which is
gitignored — regenerate it, don't expect it to be there after a fresh
clone); two (`segmentation_report_data.py`, `decisions_report_data.py`)
read only data already committed under `reports/data/` or `plan.md`,
so they need no sync step:

```bash
uv run python reports/scripts/prepare_data.py        # core payloads for both biology reports
uv run python reports/scripts/morphem_report_data.py <run_dir> <out.json>  # MorphEm QC + PCA comparison payload
uv run --frozen --with umap-learn python reports/scripts/umap_report_data.py  # UMAP coordinates
uv run python reports/scripts/pipeline_run_report_data.py <run_dir> --sacct-file <sacct.txt>  # pipeline run report payload
uv run python reports/scripts/segmentation_report_data.py  # segmentation threshold check payload (reads reports/data/ only)
uv run python reports/scripts/decisions_report_data.py     # pipeline decisions payload (hand-curated, no external data)
uv run python reports/scripts/embed_data.py          # embed the JSON payloads into all five HTML files
```

`morphem_report_data.py` reads the finalized MorphEm table from the run's
durable storage, not from `data/` — pass the run directory as the first
argument (for example
`/pl/active/koala/ReRx/runs/pilot-dev`). It defaults to the local
`runs/pilot-dev` directory if you have synced it.

`pipeline_run_report_data.py` reads a run's durable `profiles/` tree plus
`validation_report.txt` directly (no `data/` sync needed) and, optionally,
pre-fetched Slurm `sacct` job rows for the timing section:
`sacct -X -P --format=JobID,JobName,Elapsed,State > sacct.txt` on Alpine,
scp'd locally. Without `--sacct-file` the report still renders, just
without the timing chart's numbers.

To rebuild the UMAP report data specifically, `data/` needs the
full CellProfiler feature-selected table
(`data/feature_selected/experiment=HRCE-1/plate=25/profiles.parquet`),
the Recursion site embeddings
(`data/recursion_site_embeddings.parquet`), and the MorphEm
feature-selected sample
(`data/morphem_feature_selected_sample.parquet` — a 3,333-cell sample
of the MorphEm finalized table, all 168 pilot sites); run
`umap_report_data.py` with `umap-learn` available as shown above.

## What data these reports need, and where it comes from

| File                                           | Source                                                                                |
| ---------------------------------------------- | ------------------------------------------------------------------------------------- |
| `data/feature_selected/**/*.parquet`           | `runs/<id>/profiles/cellprofiler/feature_selected/` on the run's durable storage      |
| `data/normalized/**/*.parquet`                 | `runs/<id>/profiles/cellprofiler/normalized/`                                         |
| `data/recursion_site_embeddings.parquet`       | RxRx19a's published `RxRx19a-DL-embeddings.zip`, filtered to this pilot's 168 sites   |
| `data/morphem_feature_selected_sample.parquet` | Sample of `runs/<id>/profiles/morphem/feature_selected/` (3,333 cells, all 168 sites) |
| `data/selection.json`                          | `runs/<id>/selection.json`                                                            |
| `data/pipeline_run_report_data.json`           | `runs/<id>/profiles/**`, `runs/<id>/validation_report.txt`, Slurm `sacct` output      |

This is pilot-scale data (one plate, 33,330-125,855 cells depending on the
run), not the full RxRx19a dataset. Numbers and plots describe the specific
pilot run named in each report only.

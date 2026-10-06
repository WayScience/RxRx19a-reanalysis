# ReRx notebooks

Exploration and analysis notebooks (plan.md's suggested repo shape:
"Use notebooks only for exploration and final analysis").

Notebooks are committed **with their outputs embedded** -- same
convention as the reports (self-contained snapshots that open anywhere,
no run directory needed). Re-running one locally needs the inputs noted
per notebook.

## segmentation_compare.ipynb

The side-by-side visual comparison behind the segmentation-thresholding
decision (see `reports/segmentation_check.html` and
`reports/decisions.html`): DNA (w1) images with mask boundaries for
Cellpose and every CellProfiler nuclei-thresholding variant (Robust
Background, Otsu, Minimum Cross-Entropy, Adaptive) on the same six real
pilot sites (AA02, AA08, AA09, AA16, E08, E16; AA08/E08 are the dim
wells).

- `comparison_meta.json` — the table the notebook renders (kept next to
  it, with the embedded outputs already showing it).
- Regenerate everything (overlays, metadata, notebook) with
  `scripts/segmentation_cytodataframe_compare.py` — needs the source
  PNGs, Cellpose masks, and the four CellProfiler variant masks; see
  that script's docstring. The rendered outputs are committed so the
  comparison is reviewable without rerunning.
<!--
PROJECT CARD
Based on templates/project_card_template.md from WayScience/playbooks.
Keep every section; "N/A" entries carry a one-line reason.
-->

# ReRx: RxRx19a Reanalysis — Project Card

> ReRx reanalyzes the public Recursion RxRx19a high-content imaging dataset to
> ask whether treatments move SARS-CoV-2-infected cells toward the mock-cell
> morphology. It processes all 305,520 five-channel images (48.2M detected
> cells) through CellProfiler, MorphEm, and the BU-SCAR (buscar) scoring
> pipeline, and has completed a verified full production run on Alpine.

## 1. Overview

| Field                | Value                                                                                                                                              |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Project name         | ReRx — RxRx19a reanalysis                                                                                                                          |
| Status               | complete (full production run verified Oct 28, 2026)                                                                                               |
| Data classification  | open                                                                                                                                               |
| Owner(s)             | Dave Bunten (d33bs), WayScience / CU Anschutz                                                                                                      |
| Related repositories | https://github.com/WayScience/RxRx19a-reanalysis (this project's upstream); https://github.com/recursionpharma/rxrx-datasets (source dataset docs) |
| Related publications | N/A — no project publication yet; RxRx19a dataset: https://www.rxrx.ai/rxrx19a                                                                     |
| Last updated         | 2026-10-29                                                                                                                                         |

Data classification governs what this card itself may say. The source dataset
(RxRx19a) is publicly released by Recursion (BSD-licensed images, open
metadata), and this repository is public under a BSD 3-Clause license, so
there are no restricted patient identifiers anywhere in this project.

## 2. Design

In plain terms: the disease is SARS-CoV-2 infection (the virus that causes
COVID-19) grown in cultured kidney cells — human HRCE and monkey Vero. The
"disease effect" being measured is the morphology change the virus causes in
those cells, relative to mock-infected cells. The treatments are small-molecule
compounds added to infected wells at several concentrations (µM) to see
whether they push the infected cells' appearance back toward the uninfected
state. Remdesivir (GS-5734) is the known-active positive-control compound, and
compounds with known weak or no antiviral activity act as negative controls
(plan.md); each compound's structure is recorded as SMILES in the RxRx19a
metadata.

- Hypothesis / question: Which treatments move SARS-CoV-2-infected cells
  toward the mock-cell morphology while limiting unrelated morphology changes?
  (plan.md §1)
- Experimental design (arms, perturbations/treatments, replicates): Secondary
  analysis of the existing RxRx19a screen. buscar (BU-SCAR) arms use mock
  cells as the target, virally-infected untreated cells as the reference, and
  viral + compound + concentration as the perturbation (plan.md §1). UV-
  inactivated virus wells are an extra control, not the primary target.
- Positive/negative controls: RxRx19a's own control semantics (implemented in
  `src/rerx/pycytominer.py`): mock infection (target), active SARS-CoV-2
  untreated (reference), UV-inactivated virus (extra control), plus the
  metadata's positive and negative control compounds at multiple
  concentrations each.
- Plating/seeding conditions: N/A — not performed by this project; inherited
  from the published RxRx19a protocol (see Data below).
- Plate / batch layout: inherited from RxRx19a metadata; four experiments
  (HRCE-1, HRCE-2, VERO-1, VERO-2), 57 plates, 305,520 sites total.
  Site-level layout is in the metadata.csv file referenced in section 6.
- Timepoints: N/A — single fixed timepoint per RxRx19a design; no repeated
  measures.
- Sample size (plates, wells, cell lines): 305,520 imaging sites across 57
  plates in 4 experiments (2 cell lines). Production run output:
  48,191,464 raw cells, 47,075,123 after QC. Pilot selection (base scale):
  42 HRCE wells; run `pilot-x8` used the arm-scaled variant.
- Data waves/batches: One pilot wave (pilot-x8 and scaled pilot-dev runs)
  preceded the production wave (rxrx19a-full-20261015T223716Z-g83c9641). The
  pilot used the well-level selection in `src/rerx/metadata.py`
  (`PILOT_ARM_SIZES`: mock 6, uv 6, active_untreated 6, positive 16,
  negative 8); the full run processes every site. Both waves used the same
  pinned CellProfiler pipeline and MorphEm model.

## 3. Biological material

### 3.1 Patients / subjects

| Field                             | Value                           |
| --------------------------------- | ------------------------------- |
| Patient/subject count             | N/A — no patient samples        |
| Source / cohort                   | N/A — no patient samples        |
| Demographics (age, sex)           | N/A — no patient samples        |
| Diagnosis / disease subtype       | N/A — no patient samples        |
| IRB / ethics approval             | N/A — public cell-line dataset  |
| De-identification / consent notes | N/A — public dataset            |
| Patient metadata file (location)  | N/A — none; cell-line data only |

### 3.2 Cell types

| Field                                                              | Value                                                                                                                                |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| Cell type(s) / line(s)                                             | HRCE (human renal cortical epithelial) and Vero (African green monkey kidney epithelial); experiments HRCE-1, HRCE-2, VERO-1, VERO-2 |
| Tissue of origin                                                   | Kidney epithelium (HRCE: human; Vero: monkey)                                                                                        |
| Primary vs. immortalized                                           | N/A — not recorded in RxRx19a metadata; both are standard immortalized cell lines                                                    |
| Genetic background / perturbation (e.g. CRISPR, isogenic)          | N/A — no engineered lines; infection status is the perturbation (Active SARS-CoV-2, Mock, UV-Inactivated)                            |
| Control cell line(s) (e.g. a reference line imaged on every plate) | Within-line controls: mock vs. active vs. UV-inactivated SARS-CoV-2 in both HRCE and Vero; no separate control line                  |

## 4. Assay

### 4.1 Stains / channels

| Channel name | Stain / dye / antibody | Target                 | Channel code |
| ------------ | ---------------------- | ---------------------- | ------------ |
| DNA          | Hoechst 33342          | Nucleus                | w1           |
| AGP (ConA)   | Concanavalin A         | Membrane glycoproteins | w2           |
| Actin        | Phalloidin             | Actin                  | w3           |
| RNA          | Syto14                 | RNA / nucleoli         | w4           |
| AGP (WGA)    | Wheat germ agglutinin  | Golgi                  | w5           |

Stain table from the official RxRx19a dataset README
(github.com/recursionpharma/rxrx-datasets, rxrx19a/README.md). Nuclei are
segmented from the w1 Hoechst channel (plan.md §12); the pipeline keys all
crop and MorphEm channel handling off the `w1`-`w5` filename suffixes.

- Assay protocol reference (link or doc): RxRx19a dataset documentation at
  https://www.rxrx.ai/rxrx19a and
  https://github.com/recursionpharma/rxrx-datasets (rxrx19a README).
- Fixation method: N/A — not performed by this project; inherited from RxRx19a.

## 5. Imaging

| Field                                  | Value                                                                                                                          |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Microscope / instrument(s)             | High-content imager as configured by Recursion for RxRx19a (images downloaded as PNGs; instrument not recorded in the dataset) |
| Objective / magnification              | N/A — not recorded in RxRx19a release                                                                                          |
| Image format                           | `.png`                                                                                                                         |
| Bit depth                              | 8-bit                                                                                                                          |
| Image dimensions                       | 1024 × 1024 pixels per channel, 0.65 µm/px                                                                                     |
| Channels per site                      | 5                                                                                                                              |
| Sites / fields of view (FOVs) per well | 4                                                                                                                              |
| Z-planes                               | 1 (single plane)                                                                                                               |

<!-- from src/rerx/metadata.py docstring: "five channels w1-w5, 1024x1024 8-bit PNGs, 0.65 um/px" -->

RxRx19a ships single-plane 8-bit PNGs; the reanalysis crops 96×96 JPEG
(quality 95) single-cell crops per channel and derives MorphEm's 1,920-dim
embeddings (5 channels × 384) from them.

## 6. Data

| Field                            | Value                                                                                                                                      |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Raw data location                | https://storage.googleapis.com/rxrx/RxRx19a/images (canonical public bucket; staged locally on Alpine per README "Full run on Alpine")     |
| Processed data location          | `/pl/active/koala/ReRx/runs/rxrx19a-full-20261015T223716Z-g83c9641/` (Active PetaLibrary; see README "Latest verified production results") |
| Data access / restrictions       | Open — public dataset and public code; no DUA.                                                                                             |
| Data use agreement / DUA on file | N/A — public dataset                                                                                                                       |
| Approx. size                     | Full run: 47M fused cell profiles + 48M crops in Parquet; source images ~305k PNGs (site-level); embeddings ZIP 1.48 GB                    |
| Pipeline / processing repo(s)    | This repository (ReRx)                                                                                                                     |

<!-- from README.md "Latest verified production results" and src/rerx/metadata.py URLs -->

## 7. Contacts & provenance

| Role                   | Name                                                             |
| ---------------------- | ---------------------------------------------------------------- |
| PI                     | Gregory Way                                                      |
| Project lead           | Dave Bunten                                                      |
| Data analyst(s)        | Dave Bunten                                                      |
| External collaborators | Recursion (source dataset producers; no collaboration agreement) |

## 8. Changelog

| Date       | Change                                                                                                                      | By                              |
| ---------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| 2026-10-29 | Initial card, filled from README.md, plan.md, src/rerx/metadata.py, src/rerx/pycytominer.py, and the RxRx19a dataset README | Dave Bunten (with Hermes Agent) |

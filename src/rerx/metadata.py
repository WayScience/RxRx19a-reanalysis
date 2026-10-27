"""
RxRx19a metadata handling: download, parse, controls, pilot selection.

Implements plan.md sections 7, 8, and 9.

The canonical metadata CSV (``RxRx19a/metadata.csv`` inside
``RxRx19a-metadata.zip``) has columns:

    site_id, well_id, cell_type, experiment, plate, well, site,
    disease_condition, treatment, treatment_conc, SMILES

``site_id`` is ``{experiment}_{plate}_{well}_{site}`` and images live at
``images/{experiment}/Plate{plate}/{well}_s{site}_w{channel}.png`` (five
channels w1-w5, 1024x1024 8-bit PNGs, 0.65 um/px).
"""

import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

METADATA_URL = "https://storage.googleapis.com/rxrx/RxRx19a/RxRx19a-metadata.zip"
EMBEDDINGS_URL = "https://storage.googleapis.com/rxrx/RxRx19a/RxRx19a-DL-embeddings.zip"
IMAGES_URL = "https://storage.googleapis.com/rxrx/RxRx19a/images"

# Disease states present in RxRx19a (disease_condition column values).
DISEASE_CONDITION_ACTIVE = "Active SARS-CoV-2"
DISEASE_CONDITION_MOCK = "Mock"
DISEASE_CONDITION_UV = "UV Inactivated SARS-CoV-2"

# Control groups used for reversal scoring (plan.md section 8).
HEALTHY_STATE = DISEASE_CONDITION_MOCK
DISEASE_STATE = DISEASE_CONDITION_ACTIVE

# Treatment label used for untreated wells (empty treatment string).
UNTREATED = "untreated"

METADATA_COLUMNS = [
    "site_id",
    "well_id",
    "cell_type",
    "experiment",
    "plate",
    "well",
    "site",
    "disease_condition",
    "treatment",
    "treatment_conc",
    "SMILES",
]
REQUIRED_METADATA_COLUMNS = METADATA_COLUMNS

CHANNELS = (1, 2, 3, 4, 5)
SITES_PER_WELL = 4


@dataclass(frozen=True)
class ImageSetID:
    """
    A single RxRx19a field of view (site).

    experiment, plate, well : str
        Identifying coordinates of the well.
    site : int
        Field index (1..4).
    """

    experiment: str
    plate: str
    well: str
    site: int

    @property
    def site_id(self) -> str:
        """Canonical RxRx19a site identifier."""
        return f"{self.experiment}_{self.plate}_{self.well}_{self.site}"

    @property
    def well_id(self) -> str:
        """Canonical RxRx19a well identifier."""
        return f"{self.experiment}_{self.plate}_{self.well}"

    def image_dir(self) -> str:
        """Image directory for this site's plate (flat RxRx19a layout)."""
        return f"{self.experiment}/Plate{int(self.plate)}"

    def image_path(self, channel: int) -> str:
        """Relative image path for one channel PNG of this site."""
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}, got {channel}")
        return f"{self.image_dir()}/{self.well}_s{self.site}_w{channel}.png"

    def image_url(self, channel: int) -> str:
        """Absolute GCS URL for one channel PNG of this site."""
        return f"{IMAGES_URL}/{self.image_path(channel)}"


def image_set_id(row: pd.Series) -> ImageSetID:
    """Build an ImageSetID from a metadata row."""
    return ImageSetID(
        experiment=str(row["experiment"]),
        plate=str(row["plate"]),
        well=str(row["well"]),
        site=int(row["site"]),
    )


def download_metadata(dest: Path, url: str = METADATA_URL, timeout: int = 300) -> Path:
    """
    Download and extract the RxRx19a metadata ZIP.

    Writes the canonical Parquet version and the extracted CSV beside it.

    Parameters
    ----------
    dest : Path
        Directory into which the ZIP and CSV are written.
    url : str
        Metadata ZIP URL.
    timeout : int
        Request timeout in seconds.

    Returns
    -------
    Path
        Path of the extracted metadata CSV (``metadata.csv``).
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / "RxRx19a-metadata.zip"
    if not zip_path.exists():
        # Stream to a .part temp file and only replace the final ZIP
        # after the full body is written (same pattern as
        # download_embeddings), so a mid-download failure never leaves
        # a truncated ZIP that later calls would treat as complete.
        tmp = zip_path.with_suffix(".part")
        with requests.get(url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    if chunk:
                        fh.write(chunk)
        tmp.replace(zip_path)
    csv_path = dest / "metadata.csv"
    if not csv_path.exists():
        with zipfile.ZipFile(zip_path) as zf:
            zf.extract("RxRx19a/metadata.csv", dest)
        extracted = dest / "RxRx19a" / "metadata.csv"
        extracted.rename(csv_path)
        (dest / "RxRx19a").rmdir()
    return csv_path


def parse_metadata(csv_path: Path) -> pd.DataFrame:
    """
    Parse the metadata CSV into a typed DataFrame (plan.md section 7).

    Parameters
    ----------
    csv_path : Path
        Path to ``metadata.csv``.

    Returns
    -------
    pd.DataFrame
        305,520 rows (one per site) with the canonical columns plus
        ``treatment_conc_num`` (float or NaN) and boolean helpers
        ``is_mock``, ``is_active``, ``is_uv``.
    """
    df = pd.read_csv(
        csv_path,
        dtype={
            "site_id": "string",
            "well_id": "string",
            "cell_type": "string",
            "experiment": "string",
            "plate": "string",
            "well": "string",
            "site": "int64",
            "disease_condition": "string",
            "treatment": "string",
            "treatment_conc": "string",
            "SMILES": "string",
        },
    )
    missing = [c for c in REQUIRED_METADATA_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"metadata missing columns: {missing}")
    df["treatment_conc_num"] = pd.to_numeric(df["treatment_conc"], errors="coerce")
    df["is_mock"] = df["disease_condition"] == DISEASE_CONDITION_MOCK
    df["is_active"] = df["disease_condition"] == DISEASE_CONDITION_ACTIVE
    df["is_uv"] = df["disease_condition"] == DISEASE_CONDITION_UV
    return df


def control_wells(metadata: pd.DataFrame) -> pd.DataFrame:
    """
    Well-level control table (plan.md section 8).

    Every well that carries a control disease condition (Mock, Active
    SARS-CoV-2 untreated, UV Inactivated) or an untreated treatment.

    Returns
    -------
    pd.DataFrame
        One row per control well: ``well_id``, ``experiment``, ``plate``,
        ``well``, ``disease_condition``, ``treatment``.
    """
    mask = metadata["disease_condition"].isin(
        [
            DISEASE_CONDITION_MOCK,
            DISEASE_CONDITION_UV,
        ]
    ) | (
        (metadata["disease_condition"] == DISEASE_CONDITION_ACTIVE)
        & (metadata["treatment"].isna() | (metadata["treatment"] == ""))
    )
    cols = ["well_id", "experiment", "plate", "well", "disease_condition", "treatment"]
    wells = metadata.loc[mask, cols].drop_duplicates()
    return wells.sort_values("well_id").reset_index(drop=True)


# Positive-control candidate preferred by the plan (data-driven: skipped
# when absent, another compound may substitute).
PILOT_POSITIVE_CONTROL = "Remdesivir (GS-5734)"
# Known-inactive neuraminidase inhibitor used as the weak-treatment arm.
PILOT_NEGATIVE_CONTROL = "Oseltamivir carboxylate"

# Minimum distinct concentrations for the series compounds (plan.md
# section 9: "several concentrations for at least two compounds").
MIN_PILOT_CONCENTRATIONS = 4

# Arm sizes for the pilot (plan.md section 9: 24-48 wells total).
PILOT_ARM_SIZES = {
    "mock": 6,
    "uv": 6,
    "active_untreated": 6,
    "positive_treated": 16,
    "negative_treated": 8,
}


def _pilot_plate_scan(metadata: pd.DataFrame) -> pd.DataFrame:
    """
    Per-plate pilot-spec coverage (plan.md section 9).

    For every HRCE plate: number of mock, UV, active-untreated wells, and
    whether the plate carries both the positive and negative control
    compounds with at least ``MIN_PILOT_CONCENTRATIONS`` distinct
    concentrations each.

    Returns
    -------
    pd.DataFrame
        Columns ``experiment``, ``plate``, ``n_mock``, ``n_uv``,
        ``n_active_untreated``, ``positive_concs``, ``negative_concs``.
    """
    cell = metadata[metadata["cell_type"] == "HRCE"].copy()

    def n_wells(df: pd.DataFrame) -> int:
        return int(df["well_id"].nunique())

    rows = []
    for (experiment, plate), plate_df in cell.groupby(
        ["experiment", "plate"], sort=True
    ):
        untreated_here = plate_df["treatment"].isna() | (plate_df["treatment"] == "")
        mock = plate_df[plate_df["is_mock"]]
        uv = plate_df[plate_df["is_uv"]]
        active_untreated = plate_df[plate_df["is_active"] & untreated_here]
        positive = plate_df[(plate_df["treatment"] == PILOT_POSITIVE_CONTROL)]
        negative = plate_df[(plate_df["treatment"] == PILOT_NEGATIVE_CONTROL)]
        rows.append(
            {
                "experiment": experiment,
                "plate": plate,
                "n_mock": n_wells(mock),
                "n_uv": n_wells(uv),
                "n_active_untreated": n_wells(active_untreated),
                "positive_concs": int(positive["treatment_conc_num"].nunique())
                if not positive.empty
                else 0,
                "negative_concs": int(negative["treatment_conc_num"].nunique())
                if not negative.empty
                else 0,
            }
        )
    return pd.DataFrame(
        rows,
        columns=pd.Index(
            [
                "experiment",
                "plate",
                "n_mock",
                "n_uv",
                "n_active_untreated",
                "positive_concs",
                "negative_concs",
            ]
        ),
    )


def select_full_sites(metadata: pd.DataFrame) -> pd.DataFrame:
    """Select every RxRx19a image set for the full feature dataset.

    Keep both HRCE and Vero sites. Reject duplicate identifiers so a
    full run cannot silently process the same site twice.
    """
    if metadata.empty:
        raise ValueError("no image sets in full metadata")
    if bool(metadata["site_id"].isna().any()) or bool(
        metadata["site_id"].duplicated().any()
    ):
        raise ValueError("duplicate site_id or missing site_id in full metadata")
    return metadata.sort_values(["experiment", "plate", "well", "site"]).reset_index(
        drop=True
    )


def select_pilot_wells(  # noqa: C901, PLR0915
    metadata: pd.DataFrame,
    cell_type: str = "HRCE",
    max_wells: int = 48,
    arm_scale: int = 1,
) -> pd.DataFrame:
    """
    Data-driven pilot selection (plan.md section 9).

    Finds the smallest set of plates that covers every required arm:

    - Mock controls (the healthy state; buscar's target).
    - Active SARS-CoV-2 untreated (the diseased state; buscar's
      reference / normalization anchor).
    - UV-inactivated virus (irradiated-virus control).
    - The known-active drug control (Remdesivir, when present) and a
      known weak or inactive treatment (Oseltamivir carboxylate), each
      with several concentrations and replicate wells.

    Wells are shared across arms where possible (e.g. mock wells double
    as mock-treatment checks). All four sites are kept per well, and the
    selection is capped at ``max_wells``. Deterministic: wells are
    chosen in sorted well_id order. ``arm_scale`` multiplies each arm
    size (used to size up the pilot without changing its design).

    Parameters
    ----------
    metadata : pd.DataFrame
        Parsed RxRx19a metadata.
    cell_type : str
        Cell type for the pilot (plan says HRCE).
    max_wells : int
        Upper bound on selected wells.
    arm_scale : int
        Multiplier applied to every arm size (1 = the base pilot).

    Returns
    -------
    pd.DataFrame
        One row per selected site, all metadata columns, sorted by
        experiment, plate, well, site.
    """
    cell = metadata[metadata["cell_type"] == cell_type].copy()
    if cell.empty:
        raise ValueError(f"no metadata rows for cell type {cell_type!r}")

    scan = _pilot_plate_scan(cell)
    # Smallest plate set: first try one plate that covers everything.
    complete = scan[
        (scan["n_mock"] > 0)
        & (scan["n_uv"] > 0)
        & (scan["n_active_untreated"] > 0)
        & (scan["positive_concs"] >= MIN_PILOT_CONCENTRATIONS)
        & (scan["negative_concs"] >= MIN_PILOT_CONCENTRATIONS)
    ]
    if complete.empty:
        raise ValueError(
            "no single plate covers all pilot arms; multi-plate pilot "
            "selection not yet implemented"
        )
    # Prefer plates with the most positive-control wells (best replicate
    # coverage), then most active untreated wells, then plate id.
    complete = complete.sort_values(
        ["positive_concs", "n_active_untreated", "experiment", "plate"],
        ascending=[False, False, True, True],
    )
    chosen_plate = complete.iloc[0]
    experiment = str(chosen_plate["experiment"])
    plate = str(chosen_plate["plate"])
    print(
        f"pilot selection: plate {experiment}/{plate} covers all arms "
        f"(mock={chosen_plate['n_mock']}, uv={chosen_plate['n_uv']}, "
        f"active_untreated={chosen_plate['n_active_untreated']})"
    )
    plate_df = cell[(cell["experiment"] == experiment) & (cell["plate"] == plate)]

    untreated_mask = plate_df["treatment"].isna() | (plate_df["treatment"] == "")

    selected: set[str] = set()

    def pick(label: str, rows: pd.DataFrame, n: int) -> None:
        if rows.empty:
            print(f"pilot selection: no wells for {label}")
            return
        # Deterministic: first n wells in sorted well_id order.
        wells = sorted(rows["well_id"].unique())
        chosen = wells[:n]
        print(f"pilot selection: {label} -> {len(chosen)} wells")
        selected.update(chosen)

    def pick_series(label: str, rows: pd.DataFrame, n: int) -> None:
        """
        Choose ``n`` wells stratified across concentrations.

        Round-robin over sorted concentrations, taking replicate wells
        from each in turn, so every distinct concentration is covered
        before any gets a second replicate.
        """
        if rows.empty:
            print(f"pilot selection: no wells for {label}")
            return
        by_conc: dict[float, list[str]] = {}
        for conc, group in rows.groupby("treatment_conc_num", sort=True):
            key = float(group["treatment_conc_num"].iloc[0])
            by_conc[key] = sorted(group["well_id"].unique().tolist())
        chosen: list[str] = []
        rounds = max(len(w) for w in by_conc.values())
        for round_idx in range(rounds):
            for conc, wells in by_conc.items():
                if round_idx < len(wells):
                    chosen.append(wells[round_idx])
                    if len(chosen) >= n:
                        break
            if len(chosen) >= n:
                break
        print(
            f"pilot selection: {label} -> {len(chosen)} wells "
            f"({len(by_conc)} concentrations)"
        )
        selected.update(chosen)

    if arm_scale < 1:
        raise ValueError(f"arm_scale must be >= 1, got {arm_scale}")
    arm_sizes = {k: v * arm_scale for k, v in PILOT_ARM_SIZES.items()}
    pick("mock", plate_df[plate_df["is_mock"]], arm_sizes["mock"])
    pick("uv", plate_df[plate_df["is_uv"]], arm_sizes["uv"])
    pick(
        "active_untreated",
        plate_df[plate_df["is_active"] & untreated_mask],
        arm_sizes["active_untreated"],
    )
    # Concentration series: every available concentration of the positive
    # and negative control compounds, replicate wells included.
    positive = plate_df[plate_df["treatment"] == PILOT_POSITIVE_CONTROL]
    negative = plate_df[plate_df["treatment"] == PILOT_NEGATIVE_CONTROL]
    if positive.empty:
        print(
            f"pilot selection: positive control {PILOT_POSITIVE_CONTROL!r} "
            "absent; skipping (plan keeps selection data-driven)"
        )
    else:
        pick_series("positive_treated", positive, arm_sizes["positive_treated"])
    if negative.empty:
        print(
            f"pilot selection: negative control {PILOT_NEGATIVE_CONTROL!r} "
            "absent; skipping (plan keeps selection data-driven)"
        )
    else:
        pick_series("negative_treated", negative, arm_sizes["negative_treated"])

    # Cap at max_wells while keeping wells whole (all four sites).
    wells = sorted(selected)
    if len(wells) > max_wells:
        raise ValueError(
            f"pilot selection has {len(wells)} wells, above the "
            f"{max_wells}-well cap; increase the cap or shrink the arms"
        )
    sites = cell[cell["well_id"].isin(selected)]
    return sites.sort_values(["experiment", "plate", "well", "site"]).reset_index(
        drop=True
    )


def pilot_summary(metadata: pd.DataFrame, pilot: pd.DataFrame) -> pd.DataFrame:
    """
    Per-experiment, per-plate site counts for a pilot selection.

    Returns
    -------
    pd.DataFrame
        Columns ``experiment``, ``plate``, ``site_count``.
    """
    return (
        pilot.groupby(["experiment", "plate"])
        .agg(site_count=("site", "size"))
        .reset_index()
        .sort_values(["experiment", "plate"])
        .reset_index(drop=True)
    )


def site_ids_for_wells(metadata: pd.DataFrame, wells: list[str]) -> list[str]:
    """Site ids for a list of well ids."""
    return (
        metadata[metadata["well_id"].isin(wells)]["site_id"]
        .drop_duplicates()
        .sort_values()
        .tolist()
    )

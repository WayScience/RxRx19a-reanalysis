"""
Recursion deep-learning site embeddings: download, hash, convert to Parquet.

Implements plan.md section 12: the 1.48 GB ``RxRx19a-DL-embeddings.zip`` is
downloaded once into the source cache, hashed, and converted to
``baseline/recursion_site_embeddings/recursion_site_embeddings.parquet``.
"""

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

from rerx.metadata import EMBEDDINGS_URL

ZIP_NAME = "RxRx19a-DL-embeddings.zip"


@dataclass(frozen=True)
class EmbeddingsResult:
    """
    Outcome of an embeddings download/convert step.

    Attributes
    ----------
    zip_path : Path
        Downloaded ZIP path in the source cache.
    zip_sha256 : str
        SHA-256 of the ZIP.
    parquet_path : Path | None
        Converted Parquet path (present when conversion ran).
    site_count : int
        Number of site rows in the converted Parquet.
    """

    zip_path: Path
    zip_sha256: str
    parquet_path: Path | None
    site_count: int


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """SHA-256 hex digest of a file (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def download_embeddings(
    cache_root: Path, url: str = EMBEDDINGS_URL, timeout: int = 600
) -> tuple[Path, str]:
    """
    Download the embeddings ZIP into the source cache (idempotent).

    Parameters
    ----------
    cache_root : Path
        Source cache root (for example ``$RERX_PETA_ROOT/source-cache/rxrx19a``).
    url : str
        Embeddings ZIP URL.
    timeout : int
        Request timeout in seconds.

    Returns
    -------
    tuple[Path, str]
        ``(zip_path, zip_sha256)``.
    """
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    zip_path = cache_root / ZIP_NAME
    if not zip_path.exists():
        # Stream to the .part temp file instead of buffering the whole
        # body in memory; the final path only appears once complete.
        tmp = zip_path.with_suffix(".part")
        with requests.get(url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    if chunk:
                        fh.write(chunk)
        tmp.rename(zip_path)
    return zip_path, sha256_file(zip_path)


def convert_embeddings(
    zip_path: Path,
    dest_dir: Path,
    zip_sha256: str | None = None,
) -> EmbeddingsResult:
    """
    Convert the embeddings ZIP to a single site-level Parquet file.

    The ZIP holds one CSV per experiment (``rxrx19a_embeddings_<exp>.csv``).
    All CSVs are read, concatenated, and written as Parquet alongside a
    sidecar JSON holding the ZIP hash.

    Parameters
    ----------
    zip_path : Path
        Path to the downloaded embeddings ZIP.
    dest_dir : Path
        Baseline destination directory
        (``baseline/recursion_site_embeddings`` inside the run).
    zip_sha256 : str | None
        Pre-computed ZIP hash; recomputed when omitted.

    Returns
    -------
    EmbeddingsResult
        Paths, hash, and site count.
    """
    import json

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    digest = zip_sha256 or sha256_file(zip_path)
    frames = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if name.lower().endswith(".csv"):
                with zf.open(name) as f:
                    frame = pd.read_csv(
                        io.TextIOWrapper(f, encoding="utf-8"),
                        dtype={"plate": str, "well": str},
                    )
                frames.append(frame)
    if not frames:
        raise RuntimeError(f"no CSVs found in {zip_path}")
    df = pd.concat(frames, ignore_index=True)
    parquet_path = dest_dir / "recursion_site_embeddings.parquet"
    df.to_parquet(parquet_path, index=False)
    sidecar = dest_dir / "source.json"
    sidecar.write_text(
        json.dumps(
            {
                "zip_name": ZIP_NAME,
                "zip_sha256": digest,
                "site_count": len(df),
                "source_url": EMBEDDINGS_URL,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return EmbeddingsResult(
        zip_path=zip_path,
        zip_sha256=digest,
        parquet_path=parquet_path,
        site_count=len(df),
    )


def build_recursion_buscar_profiles(
    embeddings: pd.DataFrame,
    selection: pd.DataFrame,
) -> pd.DataFrame:
    """
    Annotate Recursion site embeddings with buscar metadata.

    The published embeddings are site-level (one row per imaged field,
    ``feature_0`` .. ``feature_1023`` from a DenseNet-161 variant;
    biorxiv 2020.08.02.233064). buscar needs ``Metadata_perturbation``
    and ``Metadata_buscar_state`` to group rows and pick controls, so
    this joins each site to its selection row and derives both columns
    with the same rules the other profilers use
    (:func:`rerx.pycytominer.add_control_columns` /
    :func:`rerx.pycytominer.add_perturbation_column`).

    Parameters
    ----------
    embeddings : pd.DataFrame
        Site embeddings with a ``site_id`` column and ``feature_*``
        feature columns (see :func:`convert_embeddings`).
    selection : pd.DataFrame
        Pilot selection rows (site metadata incl.
        ``disease_condition``/``treatment``/``treatment_conc``), with
        ``site_id``.

    Returns
    -------
    pd.DataFrame
        One row per matched site: embedding features plus
        ``Metadata_perturbation`` and ``Metadata_buscar_state``.

    Raises
    ------
    ValueError
        If ``selection`` is empty or the join matches no sites.
    """
    from rerx.pycytominer import (
        add_control_columns,
        add_perturbation_column,
    )

    if selection.empty:
        raise ValueError("selection is empty: no sites to annotate")

    meta = selection[
        [
            "site_id",
            "experiment",
            "plate",
            "well",
            "disease_condition",
            "treatment",
            "treatment_conc",
        ]
    ].copy()
    profiles = embeddings.merge(meta, on="site_id", how="inner")
    if profiles.empty:
        raise ValueError(
            "no embedding rows matched the selection's site_ids; "
            "check that both cover the same experiment/plate/well/site"
        )
    # buscar discovers feature columns by prefix (rerx.buscar.
    # MORPHOLOGICAL_FEATURE_PREFIXES); rename the published
    # ``feature_<i>`` columns to ``Recursion_<i>`` so the same
    # discovery works in this feature space too.
    profiles = profiles.rename(
        columns={
            c: f"Recursion_{c.removeprefix('feature_')}"
            for c in profiles.columns
            if c.startswith("feature_")
        }
    )
    profiles = add_control_columns(profiles)
    profiles = add_perturbation_column(profiles)
    return profiles

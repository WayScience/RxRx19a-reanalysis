"""
Source-file manifest for RxRx19a: per-image SHA-256 hashes and byte sizes.

Implements plan.md section 9: ``manifest/source_files.parquet`` lists every
source image (and the metadata ZIP) with its SHA-256, size, and GCS URL.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from rerx.metadata import IMAGES_URL, ImageSetID

MANIFEST_COLUMNS = [
    "experiment",
    "plate",
    "well",
    "site",
    "channel",
    "gcs_url",
    "local_path",
    "byte_size",
    "sha256",
]


@dataclass(frozen=True)
class SourceFile:
    """
    A single source image file.

    Attributes
    ----------
    image_set : ImageSetID
        Site the image belongs to.
    channel : int
        Channel number (1..5).
    gcs_url : str
        Canonical GCS URL of the PNG.
    local_path : Path
        Staged local path of the PNG.
    byte_size : int
        File size in bytes.
    sha256 : str
        Hex SHA-256 of the file contents.
    """

    image_set: ImageSetID
    channel: int
    gcs_url: str
    local_path: Path
    byte_size: int
    sha256: str

    def row(self) -> dict:
        """Manifest row (flat dict) for this file."""
        return {
            "experiment": self.image_set.experiment,
            "plate": self.image_set.plate,
            "well": self.image_set.well,
            "site": self.image_set.site,
            "channel": self.channel,
            "gcs_url": self.gcs_url,
            "local_path": str(self.local_path),
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """
    Compute the SHA-256 hex digest of a file.

    Parameters
    ----------
    path : Path
        File to hash.
    chunk : int
        Read chunk size in bytes.

    Returns
    -------
    str
        Lowercase hex digest.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def build_source_manifest(
    metadata: pd.DataFrame,
    source_root: Path,
    channels: Iterable[int] = (1, 2, 3, 4, 5),
    hash_workers: int = 1,
) -> pd.DataFrame:
    """
    Build the source-file manifest by hashing staged files (plan.md section 9).

    Parameters
    ----------
    metadata : pd.DataFrame
        Pilot (or full) site metadata; each row yields ``channels`` images.
    source_root : Path
        Local RxRx19a image tree root (``<experiment>/Plate<n>/<well>/``).
    channels : Iterable[int]
        Channels present per site.
    hash_workers : int
        Reserved for a threaded implementation; currently serial.

    Returns
    -------
    pd.DataFrame
        Manifest with MANIFEST_COLUMNS.

    Raises
    ------
    FileNotFoundError
        If a staged image file is missing.
    """
    rows: list[dict] = []
    for _, row in metadata.iterrows():
        ids = ImageSetID(
            experiment=str(row["experiment"]),
            plate=str(row["plate"]),
            well=str(row["well"]),
            site=int(str(row["site"])),
        )
        for channel in channels:
            rel = ids.image_path(channel)
            local = Path(source_root) / rel
            if not local.is_file():
                raise FileNotFoundError(f"missing staged source image: {local}")
            rows.append(
                SourceFile(
                    image_set=ids,
                    channel=channel,
                    gcs_url=f"{IMAGES_URL}/{rel}",
                    local_path=local,
                    byte_size=local.stat().st_size,
                    sha256=sha256_file(local),
                ).row()
            )
    return pd.DataFrame(rows, columns=pd.Index(MANIFEST_COLUMNS))


def image_sets_table(
    metadata: pd.DataFrame,
    channels: Iterable[int] = (1, 2, 3, 4, 5),
) -> pd.DataFrame:
    """
    One row per image set with expected channel file lists (plan.md section 9).

    Returns
    -------
    pd.DataFrame
        Image-set table with per-set expected file count and GCS prefix.
    """
    rows = []
    for _, row in metadata.iterrows():
        ids = ImageSetID(
            experiment=str(row["experiment"]),
            plate=str(row["plate"]),
            well=str(row["well"]),
            site=int(str(row["site"])),
        )
        chans = list(channels)
        rows.append(
            {
                "experiment": ids.experiment,
                "plate": ids.plate,
                "well": ids.well,
                "site": ids.site,
                "cell_type": row.get("cell_type"),
                "disease_condition": row.get("disease_condition"),
                "treatment": row.get("treatment"),
                "channels": chans,
                "expected_file_count": len(chans),
                "gcs_prefix": f"{IMAGES_URL}/{ids.image_dir()}",
            }
        )
    return pd.DataFrame(rows)

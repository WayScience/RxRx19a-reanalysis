"""
Storage configuration for ReRx.

Resolves storage roots from environment variables (never committed literal paths,
per plan.md section 4). Provides a typed view of the three roots plus scratch
and the Slurm account.
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StorageConfig:
    """
    Resolved storage roots for a ReRx session.

    Attributes
    ----------
    source_root : Path
        RxRx19a source images root (Isilon or staged cache).
    peta_root : Path
        PetaLibrary allocation path for durable outputs
        (for example ``<allocation>/ReRx``).
    mirror_root : Path
        Isilon mirror root for published runs.
    scratch_root : Path
        Alpine scratch root for temporary outputs.
    slurm_account : str | None
        Slurm account/allocation to charge jobs to, if set.
    """

    source_root: Path
    peta_root: Path
    mirror_root: Path
    scratch_root: Path
    slurm_account: str | None

    def dataset_root(self, run_id: str) -> Path:
        """
        Durable dataset directory for a run.

        Parameters
        ----------
        run_id : str
            Full run identifier (for example
            ``rxrx19a-pilot-20260925T153000Z-g1a2b3c4``).

        Returns
        -------
        Path
            ``$RERX_PETA_ROOT/datasets/<run_id>``.
        """
        return self.peta_root / "datasets" / run_id

    def source_cache_root(self) -> Path:
        """
        Shared source cache root holding canonical RxRx19a source data.

        Returns
        -------
        Path
            ``$RERX_PETA_ROOT/source-cache/rxrx19a``.
        """
        return self.peta_root / "source-cache" / "rxrx19a"


def load_storage_config(env: dict[str, str] | None = None) -> StorageConfig:
    """
    Load storage configuration from environment variables.

    Required variables:
      - RERX_SOURCE_ROOT
      - RERX_PETA_ROOT
      - RERX_MIRROR_ROOT
      - RERX_SCRATCH_ROOT (defaults to /scratch/alpine/$USER/ReRx)

    Optional:
      - SLURM_ACCOUNT

    Parameters
    ----------
    env : dict[str, str] | None
        Environment mapping to read from. Defaults to ``os.environ``.

    Returns
    -------
    StorageConfig
        Resolved configuration.

    Raises
    ------
    RuntimeError
        If required variables are missing or empty.
    """
    e = os.environ if env is None else env
    required = ("RERX_SOURCE_ROOT", "RERX_PETA_ROOT", "RERX_MIRROR_ROOT")
    missing = [k for k in required if not e.get(k)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )
    scratch_default = (
        f"/scratch/alpine/{e.get('USER', os.environ.get('USER', 'user'))}/ReRx"
    )
    return StorageConfig(
        source_root=Path(e["RERX_SOURCE_ROOT"]),
        peta_root=Path(e["RERX_PETA_ROOT"]),
        mirror_root=Path(e["RERX_MIRROR_ROOT"]),
        scratch_root=Path(e.get("RERX_SCRATCH_ROOT") or scratch_default),
        slurm_account=e.get("SLURM_ACCOUNT") or None,
    )

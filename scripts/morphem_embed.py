#!/usr/bin/env python
"""
Container entry point for MorphEm shard embedding.

Runs INSIDE containers/morphem.def only (torch/transformers live there,
never in the host uv venv). Reads one crop shard Parquet from the run
tree, embeds it with the baked-in MorphEm model, and writes
profiles/morphem/raw/<shard>.parquet.

Usage (inside the container):
    morphem_embed.py <shard_id> [--crops-dir DIR] [--dest-root DIR]

The host equivalent (rerx_tasks.py morphem) invokes this via
Nextflow's MORPHEM process, which bind-mounts the run tree.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# The container ships src/rerx/morphem.py as /opt/morphem/rerx_morphem.py
# and puts /opt/morphem on PYTHONPATH (see containers/morphem.def).
import rerx_morphem as morphem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard_id", help="Crop shard identifier")
    parser.add_argument(
        "--crops-dir",
        type=Path,
        required=True,
        help="Directory holding crops/<shard_id>.parquet",
    )
    parser.add_argument(
        "--dest-root",
        type=Path,
        required=True,
        help="Run directory; profiles land in <dest>/profiles/morphem/raw/",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=morphem.MORPHEM_DEFAULT_BATCH_SIZE,
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Torch device (default: auto cuda > mps > cpu)",
    )
    args = parser.parse_args(argv)

    crops_path = args.crops_dir / f"{args.shard_id}.parquet"
    if not crops_path.is_file():
        print(f"error: crops shard not found: {crops_path}", file=sys.stderr)
        return 1

    crops = pd.read_parquet(crops_path)
    dest = args.dest_root / "profiles" / "morphem" / "raw" / f"{args.shard_id}.parquet"

    result = morphem.embed_shard(
        crops,
        shard_id=args.shard_id,
        dest=dest,
        model=None,
        device=args.device,
        batch_size=args.batch_size,
        load=True,
    )

    print(
        f"shard={result.shard_id} cells={result.cell_count} "
        f"features={result.feature_count} device={result.device} "
        f"elapsed={result.elapsed_seconds:.1f}s "
        f"path={result.profiles_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

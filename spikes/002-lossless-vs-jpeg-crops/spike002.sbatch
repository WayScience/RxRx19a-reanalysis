#!/bin/bash
#SBATCH --job-name=rerx-spike002
#SBATCH --account=amc-general
#SBATCH --qos=cpu-normal
#SBATCH --partition=acpu
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/alpine/dabu57888@xsede.org/rerx/spike002/slurm-%j.out

set -euo pipefail

REPO=/scratch/alpine/dabu57888@xsede.org/rerx/ReRx
RUN_DIR=/pl/active/koala/ReRx/runs/pilot-dev
MORPHEM_SIF=/scratch/alpine/dabu57888@xsede.org/rerx/morphem.sif
SPIKE_DIR=${REPO}/spikes/002-lossless-vs-jpeg-crops

mkdir -p /scratch/alpine/dabu57888@xsede.org/rerx/spike002

# Step 1: build the matched lossless/JPEG crop shards (host venv).
"${REPO}/.venv/bin/python" "${SPIKE_DIR}/prepare_lossless_jpeg_crops.py"

# Step 2: embed both and compare, inside the morphem container. The
# container sees the repo at /rerx (read-only) and the run tree at
# /rerx_run; the spike dir is bind-mounted at /rerx/spike002 for the
# results.json landing spot.
apptainer exec \
    --bind "${REPO}:/rerx:ro" \
    --bind "${RUN_DIR}:/rerx_run" \
    --bind "/scratch/alpine/dabu57888@xsede.org/rerx/spike002:/rerx/spike002" \
    "${MORPHEM_SIF}" \
    python /rerx/spikes/002-lossless-vs-jpeg-crops/embed_and_compare.py

echo "results:"
cat /scratch/alpine/dabu57888@xsede.org/rerx/spike002/results.json
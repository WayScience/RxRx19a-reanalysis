#!/usr/bin/env bash
# ReRx Alpine pilot launcher.
#
# Run ON Persistence1, inside tmux (skill pattern):
#     module load nextflow/25.10.2
#     bash scripts/alpine_launch.sh
#
# Layout (all paths derived from the required env vars):
#   repo:     $RERX_ROOT/ReRx
#   venv:     $RERX_ROOT/ReRx/.venv (uv sync)
#   sif:      $RERX_SIF or $RERX_ROOT/cellprofiler.sif
#   durable:  $RERX_PETA_ROOT/runs/<run-id>
#
# Pass RERX_RESUME=1 to add `-resume` (only reruns tasks whose inputs
# changed since the last run in the same -work-dir; safe default is off
# so a fresh RERX_RUN_ID always starts clean).
#
# Required environment (no personal defaults):
#     RERX_ROOT       scratch root (repo, venv, sifs, launch dirs live here)
#     RERX_PETA_ROOT  durable PetaLibrary root (run outputs land under
#                     $RERX_PETA_ROOT/runs/<run-id>)
# Optional: RERX_RUN_ID (default pilot-dev), RERX_RESUME=1,
#     RERX_PILOT_SCALE, RERX_SHARD_SIZE, SLURM_ACCOUNT, RERX_SIF,
#     RERX_MORPHEM_SIF.
set -euo pipefail

: "${RERX_ROOT:?RERX_ROOT (scratch root) must be set}"
: "${RERX_PETA_ROOT:?RERX_PETA_ROOT (durable PetaLibrary root) must be set}"

REPO="${RERX_ROOT}/ReRx"
VENV="${REPO}/.venv"
SIF="${RERX_SIF:-${RERX_ROOT}/cellprofiler.sif}"
KOALA_RUN="${RERX_PETA_ROOT}/runs/${RERX_RUN_ID:-pilot-dev}"
LAUNCH_DIR="${RERX_ROOT}/launch/${RERX_RUN_ID:-pilot-dev}"

# 1. Python venv pinned exactly to the repo's uv.lock (reproducible: the
# same lockfile that passes local tests/CI runs on Alpine, no drift from
# a hand-maintained package list).
if [ ! -x "${VENV}/bin/python" ]; then
    cd "${REPO}"
    uv sync --frozen
    cd -
fi

# 2. CellProfiler sif (built once from the pinned def).
if [ ! -f "${SIF}" ]; then
    cd "${RERX_ROOT}"
    apptainer build cellprofiler.sif "${REPO}/containers/cellprofiler.def"
    cd -
fi

# 3. Durable run directory.
mkdir -p "${KOALA_RUN}" "${LAUNCH_DIR}"

# 4. Launch Nextflow from the launch dir (keeps work/ per-run).
cd "${LAUNCH_DIR}"
export NXF_HOME="${RERX_ROOT}/nextflow_home"
mkdir -p "${NXF_HOME}"

RESUME_FLAG=()
if [ "${RERX_RESUME:-0}" = "1" ]; then
    RESUME_FLAG=(-resume)
fi

# Export the params main.nf reads from the environment (no personal
# defaults in the workflow itself).
export RERX_REPO="${REPO}"
export RERX_PYTHON="${VENV}/bin/python"
export RERX_RUN_DIR="${KOALA_RUN}"
export RERX_SCRATCH="${RERX_ROOT}"
export RERX_SOURCE="${RERX_PETA_ROOT}/source"
export RERX_SIF="${SIF}"
export RERX_MORPHEM_SIF="${RERX_MORPHEM_SIF:-${RERX_ROOT}/morphem.sif}"

nextflow -q run "${REPO}/workflows/main.nf" \
    -c "${REPO}/nextflow.config" \
    -work-dir "${RERX_ROOT}/nextflow_work/${RERX_RUN_ID:-pilot-dev}" \
    "${RESUME_FLAG[@]}" \
    --run_id "${RERX_RUN_ID:-pilot-dev}" \
    --pilot_scale "${RERX_PILOT_SCALE:-1}" \
    --shard_size "${RERX_SHARD_SIZE:-24}"

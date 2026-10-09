#!/usr/bin/env nextflow
/*
 * ReRx Alpine pilot (HRCE-1 Plate 25: 42 wells x 4 sites x 5 channels).
 *
 * Head node: Persistence1 (module load nextflow/25.10.2)
 * Compute:   Slurm acpu partition, account amc-general (nextflow.config)
 * Durable:   /pl/active/koala/ReRx (params.run_dir, params.source)
 * Scratch:   /scratch/alpine/<user>/rerx (params.scratch)
 *
 * All processes write directly to the shared filesystems via the task
 * driver (scripts/rerx_tasks.py); channels carry only shard ids, so the
 * DAG is a pure synchronization device. Each process exports the
 * RERX_* env vars the driver reads (os.environ) before invoking it.
 */

// Personal paths and allocation names are deliberately NOT defaulted
// here: pass them via the environment (scripts/alpine_launch.sh
// requires RERX_ROOT and RERX_PETA_ROOT) or as explicit CLI params
// (--repo, --python, --scratch, --source, --sif, --morphem_sif).
params.run_id     = params.run_id ?: 'pilot-dev'
params.repo       = params.repo       ?: System.getenv('RERX_REPO')
params.python     = params.python     ?: System.getenv('RERX_PYTHON')
params.run_dir    = params.run_dir    ?: System.getenv('RERX_RUN_DIR')
params.scratch    = params.scratch    ?: System.getenv('RERX_SCRATCH')
params.source     = params.source     ?: System.getenv('RERX_SOURCE')
params.sif        = params.sif        ?: System.getenv('RERX_SIF')
params.morphem_sif = params.morphem_sif ?: System.getenv('RERX_MORPHEM_SIF')
params.shard_size = params.shard_size ?: '24'
params.pilot_scale = params.pilot_scale ?: '1'
params.scope = params.scope ?: System.getenv('RERX_SCOPE') ?: 'pilot'

def rerxEnv = """
export RERX_REPO='${params.repo}'
export RERX_RUN_DIR='${params.run_dir}'
export RERX_SCRATCH='${params.scratch}'
export RERX_SOURCE='${params.source}'
export RERX_SIF='${params.sif}'
export RERX_MORPHEM_SIF='${params.morphem_sif}'
export RERX_RUN_ID='${params.run_id}'
export RERX_SHARD_SIZE='${params.shard_size}'
export RERX_PILOT_SCALE='${params.pilot_scale}'
export RERX_SCOPE='${params.scope}'
"""

process PREPARE {
    tag 'prepare'

    output:
    path 'shard_ids.txt', emit: ids
    path 'shard_plate_counts.tsv', emit: plate_counts

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py prepare
    python3 -c "import json, os; plan = json.load(open(os.environ['RERX_RUN_DIR'] + '/shards.json')); print('\\n'.join(s['shard_id'] for s in plan))" > shard_ids.txt
    cp ${params.run_dir}/shard_plate_counts.tsv shard_plate_counts.tsv
    """
}

process CELLPROFILER {
    tag "${shard_id}"

    input:
    val shard_id

    output:
    val shard_id, emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py download ${shard_id}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py cellprofiler ${shard_id}
    """
}

process CYTOTABLE {
    tag "${shard_id}"

    input:
    val shard_id

    output:
    val shard_id, emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py cytotable ${shard_id}
    """
}

process CROPS {
    tag "${shard_id}"

    input:
    val shard_id

    output:
    val shard_id, emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py crops ${shard_id}
    """
}

process MORPHEM {
    tag "${shard_id}"

    input:
    val shard_id

    output:
    val shard_id, emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py morphem ${shard_id}
    """
}

process FINALIZE_PLATE {
    tag "${plate_id}"
    input:
    val plate_id

    output:
    val plate_id, emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py finalize-plate ${plate_id}
    """
}

process COMPLETE_FULL {
    tag 'complete-full'
    input:
    val plates_done

    output:
    path '_SUCCESS', emit: done

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py complete-full
    cp ${params.run_dir}/_SUCCESS _SUCCESS
    """
}

process FINALIZE {
    tag 'finalize'

    input:
    val shard_done

    output:
    path '_SUCCESS', emit: done

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py finalize
    ${params.python} ${params.repo}/scripts/rerx_tasks.py recursion-buscar
    ${params.python} ${params.repo}/scripts/rerx_tasks.py projection
    ${params.python} ${params.repo}/scripts/rerx_tasks.py publish
    cp ${params.run_dir}/_SUCCESS _SUCCESS
    """
}

workflow {
    PREPARE()
    CELLPROFILER(PREPARE.out.ids.splitText { it.trim() })
    CYTOTABLE(CELLPROFILER.out.ids)
    CROPS(CYTOTABLE.out.ids)
    MORPHEM(CROPS.out.ids)
    if (params.scope == 'full') {
        shard_groups = PREPARE.out.plate_counts
            .splitCsv(sep: '\t')
            .map { row -> tuple(row[0], groupKey(row[1], row[2] as int)) }
        plate_ids = MORPHEM.out.ids
            .map { id -> tuple(id, id) }
            .join(shard_groups)
            .map { id, completed_id, key -> tuple(key, id) }
            .groupTuple()
            .map { key, shard_ids -> key.getGroupTarget() }
        FINALIZE_PLATE(plate_ids)
        COMPLETE_FULL(FINALIZE_PLATE.out.ids.collect())
    } else {
        FINALIZE(MORPHEM.out.ids.collect())
    }
}

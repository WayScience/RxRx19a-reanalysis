______________________________________________________________________

## name: alpine description: 'Use before changing, testing, or scaling formascute on CURC Alpine, especially for Nextflow, Slurm submission, Persistence1, Python runtimes, Apptainer/Singularity, uv environments, queue limits, or Alpine filesystem behavior.'

# Alpine Skill

Use this note before changing, testing, or scaling formascute on CURC Alpine,
especially for Nextflow, Slurm submission, `Persistence1`, or the runtime
environment behind the project's imaging/feature-extraction workload.

## Current Position

- Submit and orchestrate through `Persistence1`; treat this as selected project
  policy for Nextflow and other long-lived workflow managers, not an open
  question for that workflow shape.
- For non-Nextflow, single-node Python tools, a plain Slurm batch job submitted
  from the Alpine login node is acceptable. Do not introduce `Persistence1`
  just to run one compute-node Python process.
- Keep compute work on Slurm. `Persistence1` is for the long-lived workflow
  manager, not image processing or other heavy work.
- Use `queueSize = 200` as the production default. It matches a real, hard
  Slurm association limit (`MaxJobs=200`), not a soft guideline, and CURC has
  directly confirmed this queue size is fine for workloads made of many short
  tasks. See Fairshare, Priority, And The 200-Job Limit.
- Do not use `queueSize > 1000`. Treat `1000` as a stress-test ceiling, not a
  normal operating value.
- Do not enable `submitRateLimit` by default. It paces submissions at a
  constant rate starting from the very first job, regardless of total item
  count or `queueSize` headroom — it is not dormant just because the run is
  far below the job-count ceiling. For many-short-task workloads it imposes a
  wall-clock floor of roughly `task_count × (window / rate)` that can dominate
  total runtime. Prefer batching to cut task count first; only add a rate
  limit if CURC or observed scheduler pressure calls for it. See Queue And
  Batching Policy.
- Prefer moderate batching before aggressive submit throttling. For imaging
  work, batch by data locality so each task loads an image set once and emits
  validated outputs.
- Either a validated project-owned `uv` environment or a project-owned
  Apptainer/Singularity image work for the project's Python-based scientific
  workload; both are confirmed end-to-end (imports, real feature calls on
  real data for `uv`; imports and synthetic orchestration for Apptainer) with
  comparable resource footprints. CURC prefers Apptainer/Singularity for
  reproducibility on this shared project; `uv` iterates faster while the
  dependency set is still changing. See Feature-Extraction Workload Runtime
  and Runtime Direction.
- Set an explicit `process.memory` before any real production run of that
  workload — proven safe at `4 GB`. Nextflow's silent default (`1 CPU` /
  `1 GB`) is smaller than the `acpu` partition's own default and has been
  measured landing right at that ceiling for real feature-extraction work.
  See Slurm Defaults.
- Set an explicit `process.time` for every process. Alpine requires a
  walltime on every individual Slurm submission, not just the coordinator job,
  and this can change without notice — see Slurm Defaults.
- Monitor the Nextflow orchestrator on `Persistence1` against a confirmed hard
  cap: about `1.6 GB` RAM and `6.4` of `8` CPUs per user, enforced by cgroups.
  See Orchestrator Monitoring.
- Real per-image-set cost for the full feature-extraction workload has been
  measured at `~23s` (all routines, real data), not guessed. Use this as the
  calibration point for any production time estimate; see
  `docs/alpine-findings.md` for a worked example and its caveats.

## Connection

*Scope: general Alpine/`Persistence1` access. Applies whether or not Nextflow
is involved.*

From the local machine, use the `ssh-alpine` zsh alias:

```bash
zsh -lic 'ssh-alpine "hostname; pwd"'
```

The alias should be defined in the user's shell configuration as an SSH command
to `login.rc.colorado.edu` using that user's Alpine SSH key.

`Persistence1` is not directly resolvable from the local machine. Reach it from
the Alpine login node:

```bash
zsh -lic 'ssh-alpine "ssh Persistence1 hostname"'
```

For real runs, use `tmux` or `screen` on `Persistence1` so the workflow manager
survives SSH disconnects.

## Downtime Awareness

*Scope: general Alpine usage. Applies whether or not Nextflow is involved.*

Before debugging failed Alpine SSH, module, Slurm, or filesystem behavior, check
whether the date is near CURC planned maintenance.

CURC policy says the first Wednesday of each month is reserved for planned
maintenance. CURC resources, including compute clusters, filesystems, and
servers, may be unavailable. A CURC course-support page describes the practical
first-Wednesday window as roughly `7a-5p`; the status page is authoritative for
the actual current window because CURC can cancel, move, or extend maintenance.

Check the live status page first:

```bash
curl -fsSL https://curc.statuspage.io/api/v2/summary.json
```

Useful fields to inspect:

- top-level `status.indicator`; expected value during maintenance:
  `maintenance`
- `components[]` entry named `Alpine`; expected status during downtime:
  `under_maintenance`
- `scheduled_maintenances[]`; look for an `in_progress` or `scheduled` event
  affecting Alpine, Research Computing Core, PetaLibrary, or Open OnDemand
- `scheduled_for` and `scheduled_until`; use these exact timestamps over the
  rough first-Wednesday rule

What a live maintenance window actually looks like in practice, from a probe
taken during one:

- the status page's top-level status read `maintenance`, with Alpine, Research
  Computing Core, Blanca, PetaLibrary, and Open OnDemand all
  `under_maintenance`
- SSH to the login node and to `Persistence1` both still worked
- loading a Nextflow module and checking its version still worked
- `sinfo` still showed partitions such as `acpu` as `up`, so `sinfo` alone is
  not sufficient to rule out maintenance
- `scontrol show reservation` showed active reservations with `Flags=MAINT`
  across CPU, GPU, compile, testing, and DTN partitions — a much stronger
  signal than basic connectivity or `sinfo`
- `sbatch --test-only` failed with policy/transition messages during the
  window (including QOS/partition rename notices and allocation errors)

Interpretation: maintenance does not necessarily mean SSH, `Persistence1`,
Nextflow modules, or Slurm commands are totally unreachable. Treat the status
page and `scontrol show reservation` as stronger signals than basic connectivity
or `sinfo` partition state.

Slurm can also show maintenance before the outage begins. If jobs sit pending
with reason `ReqNodeNotAvail`, especially in the days leading up to the first
Wednesday, check maintenance reservations:

```bash
squeue -u "$USER" --start
scontrol show reservation
```

Long walltime jobs may not start if their requested runtime intersects the
maintenance reservation. Reduce walltime, wait until maintenance completes, or
resubmit after the status page returns Alpine to `operational`.

## Nextflow Module

*Scope: when using Nextflow.*

The regular Alpine login node does not expose a usable Nextflow module.
`Persistence1` does, with multiple versions available side by side. The
validated module load is:

```bash
module load nextflow/25.10.2
nextflow -version
```

Check `module avail nextflow` on `Persistence1` for the current set before
assuming a specific version is still the right one.

## Container Runtime Binaries

*Scope: general — applies whether or not Nextflow is orchestrating the
container. `apptainer`/`singularity` are plain system binaries here, not
modules.*

On `Persistence1` and on `acpu` compute nodes, `/usr/bin/apptainer` and
`/usr/bin/singularity` are both available, with no `module load` step
required. This holds for direct `apptainer build`/`apptainer exec` usage (see
Feature-Extraction Workload Runtime below) as much as for
Nextflow-orchestrated container tasks.

## Slurm Defaults

*Scope: `account`/`partition`/`QoS` are general Slurm settings — they apply to
any job on Alpine, including a plain `sbatch` script with no Nextflow involved.
`submit host`, `executor`, `queueSize`, and the submit throttle are Nextflow
or workflow-manager settings and only mean something for that orchestration
shape.*

Use these defaults for CPU work unless the user or CURC gives a newer allocation
policy:

- account: project allocation supplied by the user
- partition: `acpu`
- QoS: `cpu-normal`
- submit host: `Persistence1` *(Nextflow / workflow-manager orchestration only)*
- executor: Slurm *(Nextflow only)*
- production `queueSize`: `200` *(Nextflow only)*
- production submit throttle: do not enable by default; see Queue And
  Batching Policy *(Nextflow only)*

Slurm accepted `acpu` and `cpu-normal` as the current CPU partition/QOS names;
older docs or scripts may still reference predecessor names for the same
resources. Re-check naming if something that used to work stops resolving.

Keep CPU and GPU work separated into different profiles or runs — they draw
from the same per-user job-count budget (see Fairshare, Priority, And The
200-Job Limit) and typically need different partitions/QOS entirely. See GPU
Work below for what's known about the GPU side.

**Do not rely on Nextflow's silent per-task memory/CPU default.** Neither
`nextflow.config` nor `conf/alpine.reference.config` sets `process.memory` or
`process.cpus`, so any process without its own directive gets Nextflow's own
conservative default. Verified via `sacct --format=ReqMem,ReqCPUS,AllocTRES`:

- the silent default is exactly `ReqMem=1G`, `ReqCPUS=1`,
  `AllocTRES=cpu=1,mem=1G,node=1`
- this is *smaller* than the `acpu` partition's own default
  (`scontrol show partition acpu` reports `DefMemPerCPU=3840`, in MB, i.e.
  `~3.75 GB` for `1` CPU) — Nextflow's default undercuts what a bare `sbatch`
  with no `--mem` would get
- this is silently fine for small synthetic work, but real feature-extraction
  work has been measured hitting `~1.0-1.01 GB` peak RSS even for a partial
  (a subset of the full routine set) real-image run — right at this ceiling

Set an explicit `process.memory` before any real production run; do not rely
on Nextflow's default. Confirmed working: an explicit `memory '4 GB'` /
`cpus 2` directive, tested through real Nextflow+Slurm submission running the
full set of real feature-extraction routines, was honored exactly (`sacct`
showed `AllocTRES=cpu=2,mem=4G,node=1`) and comfortably covered the measured
`~1.0 GB` peak RSS with no OOM. Memory use appears dominated by one internal
peak (likely image/array loading) rather than growing additively per routine,
so `4 GB` has headroom for the full routine set on data of this scale —
re-verify against larger production images before assuming it always will.

**Every individual Slurm submission needs an explicit walltime, and this can
change without notice.** Alpine has been observed rejecting any `sbatch`
submission with no `--time` specified
(`Error 17: Time has not been specified ... Specifying job run time is now required`) — including individual per-task submissions generated by
Nextflow's Slurm executor, not just a coordinator job. A process with no
`time` directive that used to submit fine can start failing every submission
with no code change on this project's side. Fixed by adding `time = 30.m` to
the base `process {}` block in `conf/alpine.reference.config`, which protects
every process that doesn't set its own `time`. Treat platform submission
requirements as something that can change — if a previously-working
submission pattern suddenly fails, check for a platform policy change (e.g.
around a recent CURC maintenance window) before debugging application code.

## Production Submission Shape

*Scope: when using Nextflow.*

For production-scale workflow execution, prefer a direct `Persistence1` run in
`tmux` or `screen`. From the Alpine login node:

```bash
ssh Persistence1
tmux new -s formascute
cd /scratch/alpine/$USER/formascute-codex-test
module load nextflow/25.10.2
make preflight ACCOUNT=<allocation>
make run EXPERIMENT=<experiment> ITEMS=<n> ACCOUNT=<allocation> PROFILE=alpine RUN_ID=<run-id>
```

Use the generated `make submit ... SUBMIT_HOST=Persistence1` path for small smoke
tests and repository validation. Be cautious about making the Slurm coordinator
job the long-term UX because CURC positions `Persistence1` as the place to run
long-lived workflow managers directly.

This direct `make run`-in-`screen`/`tmux` pattern — a plain bash script run
directly on `Persistence1`, not a Slurm-submitted coordinator — is CURC's own
recommended shape for production, not just a testing convenience. Declare one
walltime via Nextflow's `params` section (this project does so via
`process.time = 30.m` in `conf/alpine.reference.config`).

Do NOT use `ulimit -m` to self-limit the orchestrator's memory: it is not
an RSS limit on Linux (Linux does not enforce `RLIMIT_RSS` on modern
kernels), so it does not
protect against the ~1.6 GB per-user cgroup cap. Instead:

- cap the Nextflow JVM heap explicitly, well under the cgroup cap, for
  example `export NXF_OPTS='-Xms256m -Xmx1024m'` before launching (Nextflow
  reads `NXF_OPTS` for JVM arguments), and
- monitor real usage against the cap during the run:
  `ps -o rss= -p $(pgrep -f 'nextflow' | head -1)` periodically, or watch
  `top -u "$USER"` (see Orchestrator Monitoring for how the `~1.6 GB` cap was
  measured; reconcile any value CURC suggests against that measurement).

Validated smoke path from a clone on Alpine shared scratch:

```bash
make check
make preflight ACCOUNT=<allocation>
make submit-dry-run EXPERIMENT=nf1_featurization_independent ITEMS=4 ACCOUNT=<allocation> SUBMIT_HOST=Persistence1
make submit EXPERIMENT=nf1_featurization_independent ITEMS=4 ACCOUNT=<allocation> SUBMIT_HOST=Persistence1
```

Expected result of a successful validation run:

- coordinator Slurm job completes
- `completion_status.txt` reports `nextflow_exit_status: 0`
- `validation.json` reports `"valid": true`
- `trace.tsv` contains native Slurm job IDs
- `slurm.tsv` is collected

## Orchestrator Monitoring

*Scope: when using Nextflow (the orchestrator is the Nextflow process). The
underlying cgroup limits documented below are a general `Persistence1` fact —
they cap everything a user runs there, Nextflow or not.*

Treat the Nextflow process on `Persistence1` as a constrained service.

Known `Persistence1` guidance from CURC:

- VM size: 8 cores and 8 GB RAM
- individual user cgroups: about 20% of total RAM and 80% of CPU
- the orchestrator may be cancelled if it exceeds RAM limits for too long

Confirmed directly by reading cgroup and systemd state on `Persistence1`
(read-only check, no job needs to be submitted). The limit is enforced on the
`user-<uid>.slice`, not on the SSH session scope one level below it, so check
the slice, not the session:

```bash
cat /sys/fs/cgroup/memory/user.slice/user-<uid>.slice/memory.limit_in_bytes
cat /sys/fs/cgroup/cpu,cpuacct/user.slice/user-<uid>.slice/cpu.cfs_quota_us
cat /sys/fs/cgroup/cpu,cpuacct/user.slice/user-<uid>.slice/cpu.cfs_period_us
systemctl show user-<uid>.slice -p MemoryLimit -p CPUQuotaPerSecUSec
```

Observed values, from an 8-core/8GB VM:

- host total RAM: `8069439488` bytes (about 7.51 GiB)
- host CPUs: `8`
- `memory.limit_in_bytes` on the user slice: `1613885440` bytes, which is
  exactly `20%` of host RAM (about 1.5 GiB / 1.6 GB)
- `cpu.cfs_quota_us` / `cpu.cfs_period_us` on the user slice: `640000 / 100000`,
  i.e. `6.4` of `8` CPUs, exactly `80%`
- enforced via legacy cgroup v1 fields driven by systemd drop-ins
  (`.../user-.slice.d/50-CPUQuota.conf`, `.../user-<uid>.slice.d/50-MemoryLimit.conf`),
  not `MemoryMax`/`MemoryHigh`, which both read `infinity`; check `MemoryLimit`
  and `CPUQuotaPerSecUSec` specifically, not the newer systemd properties
- at idle, the user slice's own `MemoryCurrent` was about `8 MB`, far below the
  `~1.6 GB` cap

This upgrades "monitor RAM, roughly 20%/80%" to a hard, quantified ceiling:
about `1.6 GB` RAM and `6.4` CPUs for everything the user runs on `Persistence1`,
including the Nextflow JVM. Tiny synthetic probes peak in the tens of MB of
RSS, nowhere near stressing this limit — real production-scale runs should log
orchestrator RSS against this `~1.6 GB` number specifically, not the VM's full
`8 GB`.

During real runs, monitor the user's processes:

```bash
htop -u "$USER"
top -u "$USER"
squeue -u "$USER"
```

Recommended implementation direction:

- capture or periodically log Nextflow/orchestrator RSS during larger runs
- keep the Nextflow JVM heap conservative, for example via `NXF_OPTS`, then tune
  from observed memory rather than guessing
- make preflight report host, loaded Nextflow, Python runtime, active job
  count, and suggested monitoring commands

## Queue And Batching Policy

*Scope: when using Nextflow (`queueSize` and `submitRateLimit` are Nextflow
executor settings). The underlying 200-job campus/user limit is a Slurm fact
that applies regardless of orchestration mechanism.*

The Alpine active-job limit for this account/user context is 200 jobs.
`queueSize = 1000` may be accepted by Nextflow, but Slurm will likely run only
about 200 jobs at once and leave the rest pending.

Use this order of operations when optimizing:

1. First make the process granularity sensible.
1. Batch small imaging tasks by image set, plate, well group, or compatible
   feature family.
1. Keep `queueSize = 200` for initial production runs.
1. Add `submitRateLimit` only if the scheduler or CURC guidance indicates
   real submit-rate pressure — not by default.
1. If added, adjust the rate window to match observed task walltime.
1. Test higher queue sizes only with explicit measurement and avoid anything
   above `1000`.

For imaging feature-extraction work, target tasks that are large enough to
amortize image I/O and Slurm overhead. The exact batch size should come from
`trace.tsv`, `slurm.tsv`, output validation, and image I/O behavior on real
data.

`submitRateLimit` is easy to reach for as a companion to `queueSize`, on the
theory that it only matters once submissions approach the job-count ceiling.
Testing at well under that ceiling (16 items against a `200`-job cap) shows
that theory is wrong:

- individual task submissions land about 18 seconds apart from the very first
  job — a rate exactly matching the configured window (e.g. `60 min / 200 jobs = 18s`)
- a 16-item workload that finishes unthrottled in `~51-52s` takes over `5`
  minutes with the rate limit active
- validation still passes, so the setting is safe, just slow

Interpretation: `submitRateLimit` is a steady-rate limiter from job 1, not a
threshold that only engages once the job count nears the configured limit. For a
workload of `N` short tasks, it imposes a wall-clock floor of roughly
`N × (window / rate)`, independent of `queueSize` or actual task duration. Treat
`submitRateLimit` as a throughput cap that multiplies against total task count,
not a pressure-relief valve that only bites past `queueSize`. Prefer batching to
cut task count before adding a submit-rate limit for many-short-task
workloads. For a workload made of many short tasks, CURC-confirmed guidance is
that `queueSize=200` alone is sufficient — batch into fewer, larger jobs only
if per-task walltime grows long, rather than adding a rate limit.

## Fairshare, Priority, And The 200-Job Limit

*Scope: general Slurm facts — these are association/QOS/partition properties,
not Nextflow settings, and apply regardless of orchestration mechanism.*

Confirmed via `sshare`, `sprio`, `levelfs`, `sacctmgr show qos`,
`sacctmgr show assoc`, `scontrol show config`, and `scontrol show partition`
(all read-only, no job needs to be submitted):

- **The `200` figure has a precise source.** It is a hard `MaxJobs=200` at the
  Slurm *association* level for the account+user combination
  (`sacctmgr show assoc`), not a soft campus guideline and not set in the QOS
  record itself (a QOS's own `MaxJobsPU` may be blank; its only relevant cap
  might be a looser, separate `MaxSubmitPU` ceiling on total submitted jobs).
  `queueSize=200` is correctly matched to a real enforced ceiling, and CURC
  has directly confirmed this is fine for a workload made of many short
  tasks.
- **That `MaxJobs=200` is shared across every QOS the association has**
  (`cpu-normal`, `gpu-normal`, `gpu-long`, etc.) — one pool, not 200 per QOS.
  Concurrent CPU and GPU work under the same account/user splits one 200-job
  budget, not 200 each.
- **Fairshare has two layers: user and institution, and only one is
  guaranteed healthy.** `levelfs $USER` reports both a user-level number
  within the account and a separate institution-level number for the
  institution the account belongs to. `LevelFS` above `1` means underused
  (better priority); below `1` means overused (worse priority). A user-level
  reading can be extremely healthy (orders of magnitude above `1`, i.e. this
  project is a very light user of its own account) while the
  institution-level reading sits close to `1.0` — parity, not headroom. A
  healthy user-level number does not protect against institution-wide usage:
  if other users sharing the same institutional allocation increase their
  usage on Alpine, the institution-level factor can drop below `1` and
  depress priority for every account under that institution, including this
  one — a shared-fate risk with no visibility into other users' usage and
  nothing this project controls. Re-check both numbers periodically rather
  than trusting a single past reading.
- **Fairshare is not the dominant priority factor.** `scontrol show config`
  exposes the actual multifactor weights on this cluster; job size and QOS
  choice have outweighed fairshare in what's been measured (fairshare and job
  age tied for the smallest of the nonzero weights; partition and
  per-association weights at zero). A "fairshare looks healthy" conclusion
  alone is incomplete — check the weights directly rather than assuming
  fairshare dominates.
- **QOS choice matters, but is not a free priority lever.** A "long" QOS
  variant (walltime up to multiple days) can carry a nonzero QOS priority
  where the default "normal" QOS (walltime under `24h`) carries none — but
  CURC's explicit guidance is to use the long-walltime QOS only when the
  walltime genuinely requires it, not as a general-purpose priority hack for
  short jobs. The default QOS remains correct for any workload with per-task
  walltime well under its ceiling.
- **Alpine's QOS/partition structure changes over time, and CURC does not
  guarantee current behavior holds under peak load.** A finer-grained
  QOS/partition split (separate short/long variants across CPU, memory, and
  GPU resource types) can replace a coarser one specifically to reduce long
  wait times the old structure caused — without a guarantee that it fully
  resolves the issue during named peak season. Queue-wait measurements taken
  outside a heavy-demand period should be treated as optimistic for a run
  scheduled during one; this is a real, unpredictable-in-advance risk
  independent of this project's own configuration. If CURC has asked for
  feedback on a new scheduling structure, report back after a real production
  run — it helps them and other groups anticipate problems.
- **An account's allocation tier may be administratively invisible from
  Alpine's own Slurm tooling**, if that account is managed by a different
  institution than the one operating the cluster. `sacctmgr show account`
  may return no useful tier/description/organization info in that case —
  confirming the gap can't be closed via Slurm queries alone. If it matters,
  ask that institution's own HPC support directly, not another `sacctmgr`
  probe.
- **The compute partition is typically large relative to any single run's
  footprint.** Check `scontrol show partition <name>` for total nodes/CPUs and
  `PriorityTier` before assuming partition size is a constraint — a
  moderate-sized run is often a small fraction of total partition capacity.

Interpretation: this project's own usage pattern is not a deprioritization
risk on its own — user-level fairshare has been consistently healthy, and a
single moderate production run barely dents it. CURC has directly confirmed
the core submission approach (`queueSize=200`, no rate limit, the default
short-walltime QOS) is fine for a many-short-task workload. But priority also
depends on factors this project doesn't fully control: institution-wide usage
that can sit at parity rather than headroom, job-size/QOS weights that can
outrank fairshare, cluster-wide seasonal demand CURC itself flags as
uncertain, and an allocation tier that may be administratively invisible from
this side. None of this is confirmed to currently cause deprioritization, but
treat "our own fairshare is healthy" as necessary, not sufficient. Check live
queue depth (`squeue -p <partition> | wc -l`, `sinfo -p <partition>`)
immediately before any real production run, and expect materially more
uncertainty during named peak periods than at other times.

## GPU Work (Reference For Future Integration)

*Scope: general — not yet implemented in this project; kept here for when GPU
work starts.*

GPU work needs a separate partition and QOS from CPU work, confirmed by CURC,
and therefore at least two separate Nextflow configs/Slurm setups (one CPU,
one GPU) rather than one shared profile. This is consistent with "keep CPU and
GPU work separated" under Slurm Defaults, and the two draw from the same
per-user `MaxJobs` budget (see Fairshare, Priority, And The 200-Job Limit), so
concurrent CPU+GPU campaigns share one job-count ceiling, not two.

A CU Anschutz GPU-partition introduction document exists as workshop
material; check for the current version and ask the project's CURC/Anschutz
contact if it's not readily found, since this kind of reference material gets
updated.

Profiling tools CURC has recommended:

- `nvidia-smi`, via direct SSH to the compute node, for live monitoring
- `nvitop` (installable via `miniforge`) as a friendlier live-monitoring
  alternative
- `sacct -j <jobID> -Pno TRESUsageInMax -p` / `TRESUsageInAve -p` for
  max/average GPU memory and utilization from Slurm accounting
- `nsys`/`ncu` (Nsight Compute) for deeper profiling (occupancy, memory
  coalescing, bandwidth) — see CURC's Nsight Compute documentation

## Synthetic Experiment Findings

*Scope: when using Nextflow.*

Small synthetic runs complete successfully through `Persistence1`.

Useful observed shape:

- one-item minimal overhead: about 21 seconds
- independent 16-item fan-out: about 51-52 seconds, 17 native Slurm jobs
- batch size 2 with 16 items: about 42 seconds, 9 native Slurm jobs
- batch size 4 with 16 items: about 41 seconds, 5 native Slurm jobs
- `queueSize=4` with 16 independent items: about 1 minute
- `submitRateLimit='4 / 1 min'`: about 4 minutes and should not be the first
  tuning knob
- queue wait is negligible in tiny tests, usually 0-1 seconds

Interpretation:

- Nextflow can submit, track, validate, and collect Slurm accounting on Alpine
  when submitted through `Persistence1`.
- Batching reduces Slurm job count and modestly improves elapsed time in tiny
  synthetic tests. Note this is calibrated on short synthetic tasks where
  Slurm overhead is a large fraction of total time — for real, longer-running
  work, batching's overhead-amortization benefit shrinks proportionally; see
  Feature-Extraction Workload Runtime for real per-task timing to recalibrate
  against.
- Batch sizes 2-4 are the first synthetic batching range to consider, but real
  imaging data should drive the production batch shape.
- Use `queueSize` before `submitRateLimit` for scheduler pressure control unless
  CURC explicitly asks for rate limiting.

For this kind of orchestration, prefer a thin Nextflow layer around existing
work-list generation and feature scripts before rewriting processing code.

## Feature-Extraction Workload Runtime

### When Not Using Nextflow: Building And Validating The Runtime

*Scope: general — building and sanity-checking a project-specific runtime does
not require Nextflow at all. Everything in this subsection can be run via a
plain `sbatch` job or direct `apptainer exec`/`apptainer build`, never through
`make run`/`make submit`.*

The base Python environment on `Persistence1` is not suitable for real work —
it's an old, general-purpose interpreter, not a project-owned one. Expect it
to be missing a current Python version and the heavy scientific dependencies
the target package needs (array, dataframe, and image-processing libraries,
plus the package itself) entirely. Verify with an import smoke check before
assuming otherwise; do not assume a prior check is still valid without
re-running it, since the base environment can change.

Real work needs a project-owned Python 3.11+ environment. A project-owned
`uv` environment satisfies that requirement. Consider an Apptainer/Singularity
image as an equally valid path, especially given CURC's explicit
reproducibility preference for containers on a shared project — see Runtime
Direction for how to choose between them.

CURC recommends Apptainer/Singularity over `uv`/conda for reproducibility on
this shared project, citing long-term maintainability over raw convenience,
and confirms the site Apptainer/Singularity versions handle Python 3.11. A
minimal smoke test confirms the mechanics work:

```bash
apptainer pull python311.sif docker://python:3.11-slim
apptainer exec python311.sif python3 --version
```

Run as a short (`10` min walltime, `1` CPU, `2` GB mem) batch job on the `acpu`
partition (not on `Persistence1`, consistent with keeping compute off the
orchestrator host). Expect: the site's `apptainer --version` to match what
CURC states, the base image pull to take well under a minute, and standard
library imports to work inside the container.

This kind of check is still only a mechanics check: it does not install or
import the target package or its heavy dependencies inside a container, and
it does not build a project-owned `.sif` image or test on real data. It
upgrades Apptainer from "available fallback, not preferred" to "confirmed
working for the required Python version on Alpine compute nodes," which is
enough evidence to justify a real side-by-side comparison against the `uv`
path before committing to one runtime for production.

**Real dependencies in Apptainer.** A project-owned image with the same
dependency set as the validated `uv` environment builds and imports cleanly:

```text
Bootstrap: docker
From: python:3.12-slim

%post
    apt-get update
    apt-get install -y --no-install-recommends procps
    rm -rf /var/lib/apt/lists/*
    pip install --no-cache-dir <target-package> <heavy scientific dependencies>
```

Built via `apptainer build --fakeroot` as a short batch job on `acpu`
(fakeroot works via a root-mapped namespace fallback even without
`/etc/subuid`/`/etc/subgid` entries for the submitting user — worth trying
directly rather than assuming it will fail). Build + install takes a few
minutes. Image should land under project storage
(`/projects/$USER/software/apptainer/`), not scratch, so it persists. Import
validation (direct `apptainer exec`, not Nextflow) should match the `uv`
probe on the target package's version — a mismatch is a signal the two
environments have drifted apart and need reconciling, not something to
ignore.

The `%post` step above includes `procps` (providing `ps`) from the start —
that's not incidental. See the next subsection for why it's required the
moment Nextflow orchestrates the container, even though it makes no
difference to a direct `apptainer exec` validation.

**Real feature calls on real data.** Import success alone doesn't prove the
target package actually runs correctly — real feature-extraction routines
should be run against real image data, not just imported. If the target
package ships its own real-data test fixtures (a small labeled sample
dataset), use those rather than only synthetic arrays; build the loader the
same way its own real-world tests do, then call the real routines and check
for finite values and the expected object count.

Measured results from one such probe (`2` CPUs / `4` GB, real sample data,
`5` labeled objects) via the `uv` environment, across `3` of the workload's
feature-extraction routines:

- a fast per-object summary routine: `5` rows, `2.835s`
- a fast shape/size routine: `5` rows, `0.599s`
- a spatial-texture routine run at full resolution (no downsampling): `5`
  rows, `12.878s`
- all finite, `5/5` expected objects, exit `0`
- peak RSS: `~1.01 GB` for just these `3` of `6` routines on one image

This is real, non-synthetic, non-import-only evidence that the target package
actually runs correctly end-to-end on Alpine. Two things fall out of it worth
keeping in mind:

- **Don't trust a remembered upstream benchmark number without re-measuring.**
  The spatial-texture routine measured here ran well above (roughly an order
  of magnitude, in one comparison) what upstream benchmarking for that
  package has reported for comparable real-world data at other points in
  time. Upstream numbers depend on which release/branch is installed and
  which parameters were used (no downsampling is likely the most expensive
  setting) — not just hardware. Always re-measure against the exact version
  actually installed before using any number for Alpine time-budget planning.
- **A partial real-run's peak RSS lands right at Nextflow's silent `1 GB`
  per-task default** (see Slurm Defaults). A real production task calling
  every feature-extraction routine, or a larger volume, can plausibly exceed
  `1 GB` and get OOM-killed with no explicit `process.memory` set. Treat
  setting an explicit, generous `process.memory` as a prerequisite for any
  real production run of this kind of workload, not an optimization to
  defer.

### When Using Nextflow: Orchestrating The Workload

*Scope: when using Nextflow. This subsection covers running the validated
runtimes above (`uv` or the Apptainer image) as actual Nextflow processes
through `make run`/`make submit`.*

A tiny synthetic experiment shaped after the real workload
(`zp_synthetic_features`) completes successfully through `Persistence1`:

```bash
make submit EXPERIMENT=zp_synthetic_features ITEMS=4 ACCOUNT=<allocation> SUBMIT_HOST=Persistence1 RUN_ID=<run-id>
```

Expected result: coordinator exit `0`, validation passes, one feature task per
item plus one validation task, peak RSS in the tens of MB. This confirms the
orchestration shape for tiny CPU-first 3D feature work, but does not validate
real imports or image I/O — it's a simulated payload, not a real feature
call.

**Full real-feature-set run through Nextflow.** A small standalone workflow
(outside the `bin/formascute` experiment framework, invoked directly via
`nextflow run ... -profile alpine`) that loads real sample image/mask data
plus a second-channel image for a cross-channel routine, and calls every real
feature-extraction routine with explicit `memory '4 GB'` / `cpus 2` process
directives, is the pattern to use for this kind of validation:

```bash
FORMASCUTE_ACCOUNT=<account> FORMASCUTE_PARTITION=acpu FORMASCUTE_QOS=cpu-normal \
  nextflow run workflows/real_feature_probe.nf -profile alpine \
  --image1 <image.tif> --label1 <mask.tiff> --image2 <second-channel.tif>
```

Measured result — real, per-image-set cost for the full routine set, not a
partial one, across `6` feature-extraction routines of varying cost:

- four lightweight routines, each under `~1.5s`
- one spatial-texture routine at full resolution: `12.47s`
- one cross-channel routine: `7.376s`
- **total: `~23.0s` for one real image set, all `6` routines, `5/5` objects,
  all finite, exit `0`**
- `sacct` confirmed `AllocTRES=cpu=2,mem=4G,node=1` (the explicit directives
  were honored) and `MaxRSS≈1.0 GB` — comfortably under the `4 GB` request, no
  OOM, and essentially the same peak as a partial-routine run, suggesting
  memory use is dominated by one internal peak (image/array loading) rather
  than growing per additional routine

The lightweight routines shared with an earlier partial probe can vary
noticeably between runs (roughly `2×` in one comparison) while the expensive
spatial-texture routine stayed closer between runs. Treat that spread as
ordinary run-to-run variance (different compute node, cache/JIT warmup), not a
discrepancy to chase — use the full-routine total as the working estimate for
a single production task, and note the two most expensive routines together
account for the large majority of it (roughly `85-90%` in what's been
measured).

**Container gotcha: `procps` is required for Nextflow-orchestrated Apptainer
tasks, even though a bare `apptainer exec` never needs it.** Running a
synthetic workload through Nextflow with `process.container` pointed at a
`.sif` and container support enabled can fail every task with:

```text
Command 'ps' required by nextflow to collect task metrics cannot be found
```

A bare `python:3.x-slim` base has no `procps` package, so no `ps` binary.
Nextflow's Slurm+container executor shells into the container to poll task
metrics for `trace.tsv`/RSS/CPU accounting, and needs `ps` present *inside*
the image, not just on the host. This never surfaces in a plain `apptainer exec` import validation — it only appears once Nextflow actually orchestrates
through the container. The `uv` path never hits this because it runs directly
on the host with no container boundary. Any Apptainer/Singularity image
intended for Nextflow-orchestrated execution on Alpine must include `procps`
(or equivalent), not just the workload's own Python dependencies — add it to
the `%post` step and rebuild if a container-based Nextflow run fails this way.

After that fix, a full container-based synthetic run completes cleanly with
exit `0`, validation passing, and per-task resource use comparable to the `uv`
path on the same workload. Apptainer is confirmed end-to-end for this
workload shape — same package version, same synthetic feature output,
comparable resource footprint to `uv` — but only after fixing a
container-specific plumbing gap that has no equivalent on the `uv` path.
Treat "does the image have `procps`" as a standard checklist item for any
Apptainer image built for this project's Nextflow pipeline. This still does
not test real image I/O at production scale or the heavier feature-extraction
routines (the ones known to cost meaningfully more than a bare import) inside
the container — only that the dependency set installs, imports, and survives
Nextflow's container orchestration.

## uv Prototype Path

### When Not Using Nextflow: Building And Validating The uv Environment

*Scope: general — building and import-checking the `uv` environment is plain
shell/Python work, no Nextflow involved.*

CURC documents a `uv` module for Python environments. The documented flow is:

```bash
module load uv
uv venv "$UV_ENVS/mycustomenv" --python 3.12
source "$UV_ENVS/mycustomenv/bin/activate"
uv pip install <packages>
```

The documentation says `module load uv` creates and sets `UV_ENVS` to
`/projects/$USER/software/uv/envs`. Neither the regular login node nor
`Persistence1` has reliably exposed an `uv` module via `module avail uv` or
`module spider uv` — check both before assuming the documented flow is
available; if it isn't, use the fallback below.

The tested fallback is a project-owned `uv` install that keeps the same CURC
environment layout:

```bash
export UV_HOME="/projects/$USER/software/uv"
export UV_INSTALL_DIR="$UV_HOME/bin"
export UV_ENVS="$UV_HOME/envs"
export UV_CACHE_DIR="/scratch/alpine/$USER/uv-cache"
export UV_LINK_MODE=copy
export PATH="$UV_INSTALL_DIR:$PATH"
mkdir -p "$UV_INSTALL_DIR" "$UV_ENVS" "$UV_CACHE_DIR"

if [[ ! -x "$UV_INSTALL_DIR/uv" ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

uv venv "$UV_ENVS/project-env" --python 3.12
source "$UV_ENVS/project-env/bin/activate"
uv pip install <target-package>
python - <<'PY'
import importlib.metadata as metadata
import <target_package>
print(metadata.version("<target_package>"))
print(<target_package>.__file__)
PY
```

`UV_CACHE_DIR` on scratch and the environment under `/projects` are on
different filesystems, so hardlinking is unavailable — set
`UV_LINK_MODE=copy` explicitly rather than letting `uv` fall back silently.

### When Using Nextflow: Running Through Nextflow

*Scope: when using Nextflow. This subsection covers activating the `uv`
environment validated above and then running it as an actual Nextflow process
through `make run`.*

```bash
cd /scratch/alpine/$USER/formascute-codex-test
module load nextflow/25.10.2
source "/projects/$USER/software/uv/envs/project-env/bin/activate"
make run EXPERIMENT=zp_synthetic_features ITEMS=4 ACCOUNT=<allocation> PROFILE=alpine RUN_ID=<run-id>
```

Expected: Nextflow resolves `python3` to the activated `uv` environment (check
via `preflight`'s recorded Python version), heavy dependencies resolve as
wheels, and the run completes with `nextflow_exit_status: 0` and
`validation.json` reporting `"valid": true`.

Load the Nextflow module first, then activate the `uv` environment, before
invoking `make run` — order matters. The generated `make submit` coordinator
currently treats `FORMASCUTE_ENV_DIR` as a replacement environment and skips
module loading when it exists, so it is not yet the right interface for a
combined Nextflow-module plus `uv` Python environment; use the direct
`make run` pattern above when testing `uv` specifically.

## Runtime Direction

*Scope: general decision guidance — applies regardless of whether Nextflow
ends up orchestrating the runtime.*

For current Alpine work, prioritize the validated `uv` runtime:

- keep the environment under `/projects/$USER/software/uv/envs`
- keep the cache under `/scratch/alpine/$USER/uv-cache`
- set `UV_LINK_MODE=copy`
- record the Python version, `uv` version, package versions, and Nextflow version
  in run manifests
- add import smoke checks for the target package and its scientific imaging
  dependencies

Apptainer/Singularity is a real, validated alternative, not just a later
packaging option. Both paths are confirmed end-to-end for the same synthetic
workload with comparable resource footprints and the same package version,
and both pass validation through Nextflow+Slurm. CURC explicitly prefers
Apptainer/Singularity for this shared project's reproducibility, and that
preference has real evidence behind it, not just a smoke test. Real feature
calls (not just imports) have been validated on the `uv` side against real
data; the Apptainer side has had synthetic orchestration and import
validation but not yet the same real-data feature-call check — worth closing
that gap before treating the two as fully equivalent. Neither has been tested
against real production-scale image I/O, and the spatial-texture-style
routine is known to be expensive and currently unoptimized upstream — worth
watching in any timing comparison. Decide between them based on operational
preference (CURC's reproducibility argument for images vs. `uv`'s faster
iteration for a still-changing dependency set) rather than technical
blockers — there are no known blockers on the Apptainer side beyond the
`procps` requirement noted in Feature-Extraction Workload Runtime.

## Important Failure Modes

### When Using Nextflow

- Submitting directly from the regular Alpine login node fails because the batch
  job can't load the Nextflow module. Use `Persistence1` unless the module
  environment changes.
- Do not let the orchestrator perform image processing directly on
  `Persistence1`.
- Do not assume a large Nextflow `queueSize` increases active Slurm concurrency
  beyond the site/user limit.
- Do not raise submit rate or queue size without checking orchestrator RSS and
  Slurm accounting first.
- Do not build an Apptainer/Singularity image for Nextflow-orchestrated
  execution without `procps` installed. Nextflow's Slurm+container executor
  needs `ps` *inside* the container for task metrics collection; a bare
  `python:3.x-slim` base lacks it and every task fails with `Command 'ps' required by nextflow to collect task metrics cannot be found`. A plain
  `apptainer exec` smoke test will not catch this — it only surfaces once
  Nextflow orchestrates through the container.
- Do not run real production work without setting an explicit
  `process.memory`. Nextflow's default (`1 GB`) is smaller than the `acpu`
  partition's own default and has been measured landing right at that ceiling
  for a partial real feature-extraction run. See Slurm Defaults.

### General (Alpine / Apptainer, Not Nextflow-Specific)

- Do not let `$HOME` fill up. Alpine SSH/session setup can fail before a remote
  command runs when home is `100%` full; observed failure text included
  `sed: couldn't open temporary file /home/.../.ssh/sed...: No space left on device`. Keep archives, virtual environments, caches, benchmark outputs, and
  Parsl/Nextflow work directories on scratch or project storage, not home.
- Do not rely on the base `Persistence1` Python environment for real
  production work — it lacks the project's required Python version and
  scientific dependencies.
- When building with `apptainer build --fakeroot` in a Slurm batch job, stage
  the `.def` file on a shared filesystem (scratch/project), not `/tmp` on the
  login node — compute nodes have their own local `/tmp`, not shared with the
  login node.
- Do not assume older successful runs prove current Slurm submission behavior.
  Alpine has rejected `sbatch` submissions with no walltime specified
  (`Error 17: Time has not been specified ... Specifying job run time is now required`), including individual per-task submissions generated by
  Nextflow's Slurm executor, not just the coordinator job. A previously-working
  workflow can break with no code change on this project's side — see Slurm
  Defaults for the fix (`process.time` set in `conf/alpine.reference.config`).
  Re-check this kind of platform-policy assumption after any CURC maintenance
  window or announced infrastructure change.

## Questions To Revisit With CURC

Answered:

- The 200 active-job limit: confirmed as a hard Slurm association-level cap.
  `queueSize=1000` would still be capped near 200 concurrent, with the rest
  pending. `submitRateLimit` is an available pressure valve but not necessary
  for a many-short-task workload — CURC confirmed `queueSize=200` alone is
  fine for that shape, recommending batching into fewer/larger jobs instead
  only if per-task walltime grows long.
- Container vs. `uv` vs. conda: CURC recommends Apptainer/Singularity for
  reproducibility on this shared project, confirms the site version handles
  the required Python version, calls conda harder to maintain long-term, and
  has no strong view on `uv` specifically. Both paths are now validated
  end-to-end (see Feature-Extraction Workload Runtime).
- `Persistence1` RAM: monitor via `top`/`htop -u "$USER"`; per-user cgroups cap
  RAM/CPU on that shared VM, and the orchestrator risks cancellation if it
  exceeds the limit for too long. Now quantified exactly (see Orchestrator
  Monitoring); cap the JVM heap via `NXF_OPTS` and watch RSS against the
  measured cap (see Production Submission Shape — `ulimit -m` is not an RSS
  limit and must not be used for this).
- Use `tmux`/`screen` on `Persistence1` for long-lived runs, running a plain
  bash script directly rather than submitting the coordinator as a Slurm job:
  confirmed as the right production shape, not just a testing convenience.
- Long-walltime QOS tradeoffs: use only if walltime genuinely needs it; not a
  general-purpose priority lever for short jobs. The default short-walltime
  QOS remains correct for short-task workloads.
- GPU work: confirmed it needs a separate partition and QOS, at least two
  Nextflow configs/Slurm setups total. See GPU Work for reference material and
  profiling tool guidance.
- Institution-level usage visibility: quantified directly by CURC and
  independently cross-checked via `sshare`/`levelfs` — confirms the fairshare
  mechanism, though the actual reading fluctuates and isn't something this
  project controls.

Still open:

- What allocation tier is this account specifically in, if the administering
  institution even uses a tier system analogous to CU Boulder's self-service
  one? Ask that institution's own HPC support directly, not another
  `sacctmgr` probe.
- Cap the Nextflow JVM heap via `NXF_OPTS` (set under the measured hard cgroup
  cap) and log orchestrator RSS during longer runs — `ulimit -m` is not an
  RSS limit and does not protect against the enforced cap.
- Request any reference orchestrator script CURC has offered but not yet
  provided — worth diffing against `bin/formascute`'s generated coordinator
  scripts.
- Does CURC prefer Nextflow-managed tasks over Slurm arrays for this workflow
  once retries, accounting, and output validation are considered?
- What orchestrator RSS range is acceptable for multi-day runs on
  `Persistence1`, relative to the confirmed `~1.6 GB` cgroup cap?
- Why does the workload's most expensive feature-extraction routine run well
  above what upstream benchmarking has reported for comparable real-world
  data at other points in time? Worth raising with that package's
  maintainers directly (version/branch and parameter differences, not
  necessarily an Alpine-specific question) before trusting any remembered
  upstream number for time-budget planning — always re-measure against the
  exact installed version instead.
- Was the "job run time is now required" Slurm policy part of a broader
  QOS/partition restructuring CURC has been rolling out? Worth reporting back
  to CURC if they've asked for feedback on how a new QOS structure performs
  in practice, especially heading into a named peak season.

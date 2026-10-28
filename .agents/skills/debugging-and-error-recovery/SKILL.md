______________________________________________________________________

## name: debugging-and-error-recovery description: 'Systematic debugging workflow for failing tests, CI breakages, and runtime errors. Use when behavior is unexpected or a check fails.'

# Debugging and Error Recovery

## Overview

Use a disciplined triage loop: reproduce, localize, reduce, fix, and guard against regressions.

## When to use

- CI failures
- Test regressions
- Runtime exceptions
- Template rendering or environment issues

## Workflow

1. Reproduce the issue with the smallest reliable command.
1. Localize failure to a file, function, or boundary.
1. Reduce to the minimal failing case.
1. Implement the smallest safe fix.
1. Add or update tests to prevent recurrence.
1. Re-run relevant checks, then broader validation.

## Rules

- Do not patch blindly without reproducing.
- Prefer evidence from logs, traces, and failing assertions.
- Keep recovery changes scoped; avoid unrelated refactors.
- If root cause is uncertain, state assumptions and verify them.

## Production resume checks

- Keep the same run ID, launch directory, work directory, and workflow paths when resuming with Nextflow.
- Run CellProfiler tests with a temporary root that has no dot-prefixed path components; image staging deliberately rejects those paths.
- Check both scheduler records and task logs before calling a stopped run an out-of-memory failure.
- Inspect interrupted tasks for both an existing final file and a partial directory at the expected file path.
- Convert and validate cell identifiers at a fresh temporary path on the destination filesystem, then publish the completed Parquet file by rename.
- Preserve legacy conversion directories and previous CellProfiler masks, SQLite files, and logs under unique sibling names before retrying.
- Do not treat a readable Parquet footer alone as proof that a shard has passed cell-ID validation.
- Test repeated conversion using a real shard on a compute node before restarting the full workflow.
- Record uncommitted deployment patches and source hashes alongside the run rather than presenting them as the original commit.
- Run progress loggers with the pinned project interpreter and explicit scheduler paths.
- Treat scheduler-command errors as unknown status, not as zero running jobs.
- Verify that the watcher produces a fresh snapshot on the same node as the workflow head.
- Bound DuckDB memory and threads explicitly inside Slurm jobs; the memory setting is not a total process RSS limit.
- Infer wide-table union schemas incrementally from Parquet footers rather than binding all shard files in one DuckDB query.
- Log periodic file counts during both schema discovery and catalog registration so a long scan is distinguishable from a stalled task.
- Preserve compatible type promotion and plate-specific columns when changing catalog schema discovery.
- Test a catalog against real plate outputs and query it from a fresh connection before full-scale publishing.
- Check scheduler minimum memory as well as per-CPU caps; Alpine `mem-normal` requires at least 240 GB per job.
- Preserve an interrupted catalog and its WAL together before rebuilding; do not alter its referenced scientific Parquet files.
- Record the latest verified results path and completion evidence in the README; a successful run may still contain plates whose biological QC skipped buscar.

## Suggested commands

- Targeted tests: `uv run --frozen pytest tests/<target> -q`
- Full tests: `uv run --frozen pytest`
- Full quality gate: `uv run --frozen poe pipeline`

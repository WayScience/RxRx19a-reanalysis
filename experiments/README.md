# Experiments

Each experiment lives in one directory: a spec, the code it runs, and the
results it produces. The spec is written before the experiment runs.

## The spec format

An experiment spec is a small YAML file (`spec.yaml`) that records every
consequential choice before the work starts:

- `decisions` — the choices that shape the results. Every decision
  carries a `rationale` (a written reason, not a commit message), a
  closed set of `options`, and a `default`.
- `outputs` — what the experiment produces. Every output names the
  `decisions` that shape it, so any single number traces back to the
  choices that produced it.
- `universes` — concrete runs, each picking one option per decision it
  varies. Comparing universes is how the experiment answers its
  question.

The format is deliberately plain: YAML, a validator, no schema
tooling. `rerx.spec.validate_spec` checks a spec, and
`rerx.spec.load_spec` reads one; `tests/test_experiment_specs.py` keeps
every shipped spec valid.

## Validating a spec

```bash
uv run --frozen python -c "from rerx.spec import load_spec; load_spec('experiments/jpeg-crop-quality/spec.yaml')"
```

The command prints nothing when the spec is valid and names the first
problem when it is not.

## Current experiments

- `jpeg-crop-quality/` — plan.md section 14: keep the JPEG 95 crop
  encoding only if MorphEm embeddings survive the compression. Two
  universes (stored JPEG crops vs lossless re-rendered crops), outputs
  naming the encoding decision.
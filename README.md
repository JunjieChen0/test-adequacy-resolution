# Replication package: Identifiability and Confounding in Test-Adequacy Metric Validity Studies

This package contains the collection pipeline, the collected measurements, and
the analysis code for a cross-language study of whether incremental-validity
claims about test-adequacy metrics can be statistically resolved at the sample
sizes the literature uses.

Two benchmarks are covered: **Defects4J** (Java) and **BugsInPy** (Python),
seven projects each.

## What is here

```
scripts/          collection and analysis pipeline
data/             Defects4J per-instance measurements (subsets + assertion features)
data-bip/         BugsInPy per-instance measurements + per-fault trigger metadata
results/          analysis outputs (analysis.json), figures, intermediate findings
paper/            LaTeX source and compiled manuscript
MANIFEST.md       full file listing
```

The BugsInPy collector needs scratch space: it keeps one clone per project,
switches a shared working tree between faults, and builds a virtual environment
per project. Those appear as `data-bip/repo/`, `data-bip/work/` and
`data-bip/venv/` (about 900\,MB in our run) and are **not** part of the package.
`REPRODUCE.md` describes how they are rebuilt.

Every number the manuscript reports is derived from `results/analysis.json`,
produced by `scripts/analyze_all.py`. No number in the paper is entered by hand.

## Key entry points

| Script | Purpose |
|---|---|
| `scripts/analyze_all.py` | **Primary analysis.** Produces `results/analysis.json`: model comparisons under three evaluation schemes, both bootstrap hierarchies, the project-fingerprint diagnostic, leave-one-project-out, project-composition sensitivity, and the resolution curves. |
| `scripts/simulate_confounding.py` | Parameterised simulation isolating project-level confounding. Produces `results/simulation-confounding.json`. |
| `scripts/plot_figures.py` | Renders both manuscript figures from the two JSON files above. |
| `scripts/robust_lib.py` | Shared modelling and resampling primitives used by the analysis. |
| `scripts/collect_subsets.py` | Defects4J subset construction, coverage and mutation measurement. |
| `scripts/bip_meta.py` | Extracts the per-fault trigger test from each BugsInPy `run_test.sh`. |
| `scripts/bip_collect.py` | BugsInPy subset construction and coverage measurement. |

## Reproducing the analysis from the shipped measurements

The analysis needs only Python 3.10+ with the packages in `requirements.txt`:

```bash
pip install -r requirements.txt
python3 scripts/analyze_all.py --benchmark both --boot 2000
python3 scripts/simulate_confounding.py
python3 scripts/plot_figures.py --lang en
```

`scripts/analyze_all.py` writes `results/analysis.json`; `--boot` sets the
number of bootstrap replicates (2000 in the manuscript, so that each tail of a
95% percentile interval is supported by about 50 replicates; the resolution
curves use 1000 replicates per point).

Reproducing the *collection* additionally requires the benchmark toolchains;
see `REPRODUCE.md`.

## Measurement protocol in one paragraph

A **version** is a `(project, fault)` pair. From each version's test suite we
sample subsets at fixed fractions of the suite, and measure each subset's size,
line coverage of the project's own sources, and assertion-level features. The
outcome is the subset's **differential fault detection rate**: the share of the
project's faults whose recorded trigger test lies inside the subset. This
outcome is computed from metadata alone, so no suite under study is executed to
obtain it.

On Defects4J, subsets are constructed by relocating the sources of non-selected
tests out of the test source directory, since the framework's coverage and
mutation commands do not accept an explicit test list. On BugsInPy, subsets are
constructed by passing an explicit list of pytest node identifiers.

## Relationship between the shipped data and the manuscript

`results/analysis.json` is the single source of every number in the manuscript.
It is computed from the shipped per-instance measurements by
`scripts/analyze_all.py`.

Records whose metrics could not be measured are retained in `data/` and
`data-bip/` but excluded by the analysis, so the version counts in the
manuscript's Table 1 are slightly below the raw file counts.

`data/subsets_v1/` holds the superseded first-round pilot records. No script in
this package reads it; it is kept only as a record of the pilot phase.

`results/reference-verification.md` records the pre-submission check of every
bibliographic entry in `paper/refs.bib` against Crossref.

As a check on this package, `scripts/analyze_all.py` was re-run against the
shipped `data/` and `data-bip/` after the measurements were frozen. Every point
estimate reproduced exactly --- model comparison under all three evaluation
schemes, both bootstrap hierarchies' point estimates, the project-fingerprint
accuracies, leave-one-project-out, project-composition sensitivity, and the
resolution curves. The only quantities that moved were the interval bounds,
whose width and centre are set by `--boot` (the check used 200 replicates
against the manuscript's 2000), which is the parameter's purpose.

The manuscript was frozen on the measurements as of the `v1.0-submission` tag.
Collection of additional Defects4J versions was still running when the tag was
cut; those records are included in `data/` but excluded from the analysis,
because their assertion-level features have not yet been extracted. They
therefore do not affect any reported number.

## Environment

| Component | Version used |
|---|---|
| JDK | OpenJDK 11 (required by the Defects4J v3 framework) |
| Maven | 3.8.x |
| Defects4J | v3 |
| Major mutation framework | 3.0.1 (jre11) |
| Python (BugsInPy collection) | 3.8.20, one virtual environment per project |
| Python (analysis) | 3.12 |
| pytest / coverage | 8.3.5 / 7.6.1 |

## Data file formats

- `data/subsets/*.json` — one record per `(project, fault)` version. Fields:
  `n_test_total`, `n_test_final`, `kept_tests_final` (the subset's test
  classes), `steps.coverage.line_cov_pct`, `steps.mutation.mutation_score_pct`,
  and `status` (`ok` for usable records).
- `data/assertions/*.json` — assertion-level features per test method, keyed by
  `project-bug-frac-rep`.
- `data-bip/meta/*.json` — for each BugsInPy fault, the trigger test parsed from
  `run_test.sh`, plus the buggy and fixed commit identifiers.
- `data-bip/subsets/*.json` — one record per BugsInPy version, with the test
  pool, the sampled subsets, and per-instance size, coverage and features.

## License

Code: MIT (see `LICENSE-CODE`).
Data and results: CC BY 4.0 (see `LICENSE-DATA`).

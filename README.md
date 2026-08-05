# UK Address Matcher on Databricks — documentation

A [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) site
documenting the benchmarked, optimal configuration for running
[`uk_address_matcher`](https://github.com/moj-analytical-services/uk_address_matcher)
on Databricks classic compute.

Based on
[Discussion #463](https://github.com/moj-analytical-services/uk_address_matcher/discussions/463)
and the accompanying cumulative test matrix.

## Website

The documentation is published at
<https://dodgydave67.github.io/ukam-databricks/>.

Every push to `main` runs `.github/workflows/deploy-docs.yml`. The workflow
builds the site and publishes it to the `gh-pages` branch.

## Local preview

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mkdocs serve
```

Then open <http://127.0.0.1:8000>.

## Structure

```
docs/
├── index.md                      Overview and the optimisation stack
├── why-slow.md                   Root causes of the default slowness
├── quickstart.md                 The minimal recipe
├── setup/
│   ├── cluster.md                Classic vs serverless, sizing
│   ├── prepare-canonical.md      prepare_canonical_folder(), once
│   ├── recommended-notebook.md   The full annotated notebook
│   └── outputs.md                Writing results back durably
├── reference/
│   ├── optimisations.md          Step-by-step, with rationale
│   ├── rejected.md               Three optimisations that made things worse
│   ├── settings.md               Every knob in one table
│   ├── accuracy.md               Result-changing settings, kept separate
│   └── troubleshooting.md        Failure modes and fixes
├── benchmarks/
│   ├── results.md                The published measurements
│   ├── methodology.md            How the matrix was designed
│   └── harness.md                Running it on your own cluster
└── assets/
    ├── ukam_databricks_match.py       Production notebook (downloadable)
    ├── ukam_databricks_benchmark.py   The test matrix (anonymised)
    └── extra.css
```

## The recommendation in one line

Copy the prepared canonical folder to `/local_disk0`, set two DuckDB pragmas,
and keep the connection **in memory**. That is arm 3 of the test matrix: 2.22×
faster than baseline on match time, 1.97× on wall time.

Three further optimisations were benchmarked and rejected — an on-disk DuckDB
database (25% slower), eager canonical materialisation (slower than the untuned
baseline), and copying small messy files locally (no effect). `docs/reference/rejected.md`
covers all three and the conditions under which they might still pay off.

## Notes on content

- All timings come from the six-arm, two-repeat matrix in Discussion #463.
  Reference environment: DBR 17.3, 8 cores, 57.4 GB RAM, DuckDB 1.5.0, UKAM
  1.2.4, Splink 4.0.16, 6,367 messy addresses.
- All 12 runs produced fingerprint `5194b60df1884b954e1fb2c21ce845e9`, so none
  of the performance work changed a match.
- Paths from the original harness have been replaced with `<your-path>`
  placeholders.
- The evidence base is two repeats of one workload. The I/O finding is robust;
  the rejections are more workload-specific and are flagged as such throughout.

## Licence

Documentation: CC BY 4.0. Code samples: MIT, matching the upstream library.
This site is unofficial and not maintained by the Ministry of Justice.

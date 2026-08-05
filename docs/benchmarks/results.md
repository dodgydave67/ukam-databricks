---
title: Results
---

# Results

Full cumulative test matrix, six arms, two repeats each, from
[Discussion #463](https://github.com/moj-analytical-services/uk_address_matcher/discussions/463).

!!! success "Recommended configuration: arm 3"

    Prepared canonical data copied to `/local_disk0`, DuckDB pragmas set,
    **in-memory** DuckDB database. Average match time **58.86 s** against a
    **130.84 s** baseline — a **2.22×** speed-up. Including the canonical copy,
    end-to-end wall time fell from **132.28 s** to **67.06 s** (**1.97×**).

    Arms 4, 5 and 6 each added something and each failed to pay for itself.

## Test environment

| Item | Value |
| --- | --- |
| Databricks Runtime | 17.3 |
| Compute | Classic, 8 CPU cores |
| Memory | 57.4 GB |
| Free `/local_disk0` space | 173.8 GB |
| DuckDB | 1.5.0 |
| uk_address_matcher | 1.2.4 |
| Splink | 4.0.16 |
| DuckDB threads | 8 |
| DuckDB effective memory limit | 45.9 GiB |
| Messy addresses | 6,367 |
| Repeats per arm | 2 |

## Averages across both runs

Copy costs are **excluded** from match time and **included** in wall time. Wall
time also covers setup, input counting, fingerprinting and cleanup, so it is the
better measure of a complete arm.

| Arm | Match | Wall | Match speed-up | Wall speed-up | Copy canonical | Copy messy | Materialise | Peak spill |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 Baseline | 130.84 s | 132.28 s | 1.00× | 1.00× | — | — | — | 0.32 GB |
| 2 + DuckDB pragmas | 126.79 s | 128.18 s | 1.03× | 1.03× | — | — | — | 0.00 GB |
| **3 + canonical on local disk** | **58.86 s** | **67.06 s** | **2.22×** | **1.97×** | 6.91 s | — | — | 0.00 GB |
| 4 + messy on local disk | 59.03 s | 71.89 s | 2.22× | 1.84× | 11.00 s | 0.09 s | — | 0.00 GB |
| 5 + on-disk DuckDB database | 74.07 s | 85.30 s | 1.77× | 1.55× | 9.21 s | 0.09 s | — | 0.00 GB |
| 6 + materialised canonical | 155.72 s | 167.73 s | 0.84× | 0.79× | 8.39 s | 0.08 s | 76.41 s | 0.00 GB |

### How copy cost is accounted

Copy time sits in its own columns, excluded from match time and included in wall
time. That split matters for how you read the table:

- On a **long-lived interactive cluster** the copy happens once per cluster
  lifetime, so **match time** is your number. Arm 3: 58.86 s.
- On a **fresh job cluster** you pay the copy every run, so **wall time** is your
  number. Arm 3: 67.06 s — still 1.97× better than baseline.

Copy time for the same canonical folder ranged **6.91 s to 11.00 s** across arms,
so treat it as a band rather than a constant.

## Per-stage timings

| Arm | Exact matching | Splink | Other work inside `match()` |
| --- | ---: | ---: | ---: |
| 1 Baseline | 18.98 s | 83.91 s | 27.95 s |
| 2 + DuckDB pragmas | 16.64 s | 80.69 s | 29.46 s |
| **3 + canonical on local disk** | **1.38 s** | **52.31 s** | **5.17 s** |
| 4 + messy on local disk | 1.34 s | 52.28 s | 5.40 s |
| 5 + on-disk DuckDB database | 1.53 s | 59.02 s | 13.52 s |
| 6 + materialised canonical | 0.58 s | 65.82 s | 89.32 s |

*Other work inside `match()`* is measured `match()` duration minus the published
exact-match and Splink stage diagnostics — UKAM work outside those stage timers.
For arm 6, materialisation happens inside `match()` and so appears here as well
as in its own column.

The exact-match stage is the clearest signal in the whole matrix: **18.98 s →
1.38 s**, a 14× improvement, purely from moving the canonical Parquet onto local
disk. That stage is dominated by reading the canonical side, so it responds
almost entirely to I/O latency.

## Run-to-run variation

| Arm | Run 1 match | Run 2 match | Difference | Run 1 wall | Run 2 wall |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 Baseline | 122.65 s | 139.02 s | 16.37 s | 123.88 s | 140.68 s |
| 2 + DuckDB pragmas | 126.98 s | 126.60 s | 0.38 s | 128.30 s | 128.05 s |
| 3 + canonical on local disk | 58.93 s | 58.79 s | 0.14 s | 67.30 s | 66.81 s |
| 4 + messy on local disk | 60.17 s | 57.88 s | 2.29 s | 72.15 s | 71.62 s |
| 5 + on-disk DuckDB database | 74.38 s | 73.76 s | 0.62 s | 85.26 s | 85.33 s |
| 6 + materialised canonical | 159.85 s | 151.58 s | 8.27 s | 173.84 s | 161.62 s |

Note the **16.37 s** spread on the baseline against **0.14 s** on arm 3. Reading
from DBFS is not only slower, it is far less predictable — which matters if you
have an SLA rather than just a stopwatch.

## Correctness

All 12 successful runs produced the same fingerprint:

```text
5194b60df1884b954e1fb2c21ce845e9
```

Every arm returned an identical `unique_id → resolved_canonical_id` mapping. The
performance differences did not change a single match.

| Metric | Value |
| --- | ---: |
| Matched | 6,358 / 6,367 (99.86%) |
| Unmatched | 9 |
| Exact-stage matches | 1,672 |
| Splink-stage matches | 4,686 |

## Findings

### 1. Local canonical data produced the entire gain

Match time **130.84 s → 58.86 s**. The exact-match stage fell from 18.98 s to
1.38 s and Splink from 83.91 s to 52.31 s. Repeated reads of the prepared
canonical Parquet were the dominant bottleneck.

### 2. Copying the messy data locally added nothing measurable

The copy itself cost **0.09 s** — the file is small. Match time moved from
58.86 s to 59.03 s, well inside noise for two repeats.

Arm 4's *wall* time looks worse (67.06 s → 71.89 s), but that is misleading:
the canonical copy in that arm happened to take **11.00 s** against **6.91 s**
in arm 3. The regression is copy-time variance on the same operation, not a cost
of the messy copy. Canonical copy time ranged from 6.91 s to 11.00 s across arms
for identical work.

### 3. The pragmas helped modestly and eliminated measured spill

`temp_directory` on `/local_disk0` plus `preserve_insertion_order=false` moved
match time from 130.84 s to 126.79 s. More usefully, baseline peak spill of
**0.32 GB** dropped to **zero**, and run-to-run spread collapsed from 16.37 s to
0.38 s.

### 4. An on-disk DuckDB database was slower

Match time rose from 59.03 s to **74.07 s**; wall time from 71.89 s to 85.30 s.
With 57.4 GB of RAM, a 45.9 GiB effective memory limit and no spill to relieve,
persistence added overhead and bought nothing.

!!! note "This contradicts general Splink guidance"

    Arm 5 implements the on-disk-database approach recommended in
    [splink#2652](https://github.com/moj-analytical-services/splink/discussions/2652#discussioncomment-12505866).
    It did not help *in this test*. That guidance targets workloads that are
    actually memory-constrained; this one was not. See
    [Tested and rejected](../reference/rejected.md#on-disk-duckdb-database).

### 5. Materialising the canonical relations was clearly counterproductive

Materialisation alone cost an average of **76.41 s**. Exact matching became very
fast (0.58 s) but total match time rose to **155.72 s** — slower than the
untuned baseline.

This does not support adding eager canonical materialisation to UKAM in the form
tested. A useful version would need to avoid the upfront table-creation cost, or
amortise it across several match batches.

## Recommendation

| Do | Do not |
| --- | --- |
| Copy the prepared canonical folder to `/local_disk0` | Use an on-disk DuckDB database unless you are actually spilling |
| Set `temp_directory` to `/local_disk0` | Materialise the canonical relations eagerly |
| Set `preserve_insertion_order=false` | Bother copying small messy files locally |
| Keep the DuckDB connection **in memory** | |

If you match several messy batches against the same canonical folder in one
cluster session, copy once and reuse — that avoids paying the ~7 s copy per
batch.

## Limitations

Stated by the benchmark author, and worth respecting before you generalise:

- Only **two repeats**; small differences should not be over-interpreted.
- One messy dataset of **6,367 addresses**, one prepared canonical dataset, one
  cluster shape.
- Filesystem and cluster-cache state may have influenced run order.
- Copy time varied noticeably between runs, particularly for the canonical
  folder.
- The spill monitor sampled every 2 s, so short-lived spill may have been missed.
- Materialisation may still pay off where one materialised table is reused across
  several matching calls — untested here.

Suggested follow-ups: at least five repeats with randomised arm order; copy the
canonical folder once then run several independent messy batches; test larger
messy datasets to find where local copying starts to matter; test the explicit
memory-limit arm; and time canonical loading, matcher initialisation, stage
execution and fingerprinting separately.

A further test matrix for `prepare_canonical_folder()` is planned.

## Timing definitions

| Term | Meaning |
| --- | --- |
| **Copy canonical** | Copying the prepared canonical folder from DBFS to `/local_disk0` |
| **Copy messy** | Copying the messy Parquet input from DBFS to `/local_disk0` |
| **Materialise** | Creating physical DuckDB tables from the lazy canonical and TF relations |
| **Match** | Elapsed time for `matcher.match()` only |
| **Wall** | Complete arm duration: copies, connection setup, input counting, matching, fingerprinting, cleanup |
| **Peak spill** | Greatest observed size of the monitored DuckDB temporary directory |

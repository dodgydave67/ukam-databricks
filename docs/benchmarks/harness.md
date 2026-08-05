---
title: Run it yourself
---

# Run the benchmark on your own cluster

The numbers on this site come from one workspace, one canonical dataset and one
messy dataset. Yours will differ in magnitude. The harness exists so you can
generate your own table rather than trusting someone else's.

[:material-download: Download `ukam_databricks_benchmark.py`](../assets/ukam_databricks_benchmark.py){ .md-button .md-button--primary }

Paths in the downloadable version are placeholders — the original was written
against a specific workspace and has been anonymised.

## Requirements

- **Classic** compute. The harness refuses to run on serverless:
  `RuntimeError: /local_disk0 not available`.
- Single-node cluster strongly recommended. It does work on multi-node clusters
  — the author ran these optimisations on clusters with multiple nodes — but
  workers contribute nothing and add noise.
- A prepared canonical folder and a messy dataset with `unique_id` and
  `address_concat` columns.
- Enough free space on `/local_disk0` for both copies plus spill.

## Configuration

Only the top block needs editing.

```python
# Prepared canonical folder (the output of prepare_canonical_folder).
PREPARED_DBUTILS = "dbfs:/mnt/<your-path>/ukam_prepared_canonical"
PREPARED_POSIX   = "/dbfs/mnt/<your-path>/ukam_prepared_canonical"   # (1)!

# Messy addresses to match. Must have unique_id + address_concat.
MESSY_DBUTILS = "dbfs:/mnt/<your-path>/messy"
MESSY_POSIX   = "/dbfs/mnt/<your-path>/messy"
MESSY_GLOB    = "*.parquet"

LOCAL_ROOT = "/local_disk0/ukam_match_bench"

ARMS_TO_RUN = [
    "1_baseline",
    "2_pragmas",
    "3_local_canonical",
    "4_local_messy",
    "5_ondisk_db",
    "6_materialise_canonical",
    # "7_memory_limit",
]

ACCURACY_ARMS = [                 # (2)!
    # "no_outside_postcode_block",
    # "improve_top_n_3",
]

REPEATS = 2
MEMORY_LIMIT_ARM7 = "48GB"
FINGERPRINT_MAX_ROWS = 5_000_000  # (3)!
```

1.  Both forms of the same path are needed. `dbutils.fs.cp` takes the URI form
    (`dbfs:/`); DuckDB reading DBFS directly in the baseline arms needs the FUSE
    form (`/dbfs/`).
2.  These deliberately change results and are reported separately with
    match-count deltas. Leave empty to skip.
3.  Above this row count the MD5 digest is skipped for cost. Counts are still
    compared.

## What it produces

**A per-arm table**, printed as Markdown ready to paste into an issue:

```text
| Arm | Match (s) | vs baseline | Copy canonical (s) | Copy messy (s) | Peak spill (GB) | Matched |
```

**A per-stage breakdown**, so you can see where time actually goes:

```text
**6_materialise_canonical**
- ExactMatchStage: <n>s, matched <n>, remaining <n>
- SplinkStage: <n>s, matched <n>, remaining <n>
```

**A correctness verdict:**

```text
======================================================================
CORRECTNESS CHECK
======================================================================
PASS — every performance arm produced identical match results.
```

**A results JSON** at `{LOCAL_ROOT}/match_benchmark_results.json`, containing
every timing, fingerprint and the captured environment.

!!! warning "Copy the JSON off local disk"

    `/local_disk0` is wiped on cluster termination. Add a
    `dbutils.fs.cp(f"file:{RESULTS_JSON}", "dbfs:/mnt/<your-path>/bench/")` at
    the end if you want to keep it.

## Reading the correctness check first

Before you look at a single timing, check the verdict. If it says:

```text
FAIL — performance arms produced DIFFERENT results. Timings are
not comparable until this is explained.
```

then something in your setup is not what you think it is. Common causes:

- Messy data changed between arms (a live table rather than a static snapshot)
- The prepared folder on DBFS differs from the copy on local disk — a stale
  local cache from a previous canonical version
- A non-deterministic `unique_id` in the messy data
- Two runs of the harness overlapping on the same cluster

Fix the cause. Do not reason about the timings until it passes.

## Interpreting your table

Reference results are in the [Results](results.md) table — compare yours against
them.

| Observation | What it means |
|---|---|
| Arm 3 gives a large jump | You are network-bound, as expected on DBFS-mounted storage. Reference: **2.22×**. |
| Arm 3 gives little | Your canonical dataset is small enough to sit in OS page cache, or your storage is unusually fast. Keep the copy anyway — it costs seconds. |
| Exact-match stage drops sharply in arm 3 | Confirms canonical reads were the bottleneck. Reference: 18.98 s → 1.38 s. |
| Arm 4 helps | Your messy data is large enough to matter, unlike the 6,367-row reference. Adopt it. |
| Arm 5 helps | You are genuinely memory-constrained. Check peak spill in the JSON — if it is non-zero, the on-disk database is earning its keep. The reference workload never spilled and arm 5 was 25% slower. |
| Arm 6 helps | Your Splink model has many blocking rules relative to canonical size. Reference: materialisation cost 76.41 s and never repaid it. |
| Peak spill near disk capacity | Provision more local storage before this becomes a failure. |
| Any arm slower than baseline | Normal. Three of six were, in the reference run. Negative results are the point of running a matrix. |

## Extending it

The harness is a plain script; the arm definitions are a dictionary of flags:

```python
ARM_FLAGS = {
    "1_baseline": dict(pragmas=0, local_canon=0, local_messy=0,
                       ondisk=0, materialise=0, memlimit=0),
    ...
}
```

Adding an arm means adding a row here and honouring the flag in `run_arm`.
Adding an accuracy variant means adding an entry to `ACCURACY_STAGES` returning
a list of stages:

```python
ACCURACY_STAGES = {
    "with_trigram_stage": lambda: [
        ExactMatchStage(),
        UniqueTrigramStage(),
        SplinkStage(),
    ],
}
```

Keep new result-changing variants in `ACCURACY_ARMS`, never in `ARMS_TO_RUN` —
that separation is the thing that makes the output trustworthy.

## Contributing your numbers back

If you run this on a different workspace, cloud or instance family, the results
are worth sharing on
[Discussion #463](https://github.com/moj-analytical-services/uk_address_matcher/discussions/463).
Include the environment block the harness captures — runtime version, core
count, RAM, DuckDB / UKAM / Splink versions — and the correctness verdict
alongside the timings.

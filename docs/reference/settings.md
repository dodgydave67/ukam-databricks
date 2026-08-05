---
title: Settings cheat sheet
---

# Settings cheat sheet

Every knob referenced on this site, in one place.

## DuckDB connection settings

| Setting | Recommended on Databricks | Default | Notes |
|---|---|---|---|
| `database` (constructor arg) | **leave default (`:memory:`)** | `:memory:` | An on-disk database on `/local_disk0` was benchmarked **25% slower** with no memory pressure to relieve. Use it only if you are genuinely spilling — see [Tested and rejected](rejected.md#on-disk-duckdb-database). |
| `temp_directory` | `/local_disk0/<job>/tmp` | process working dir | Spill destination. Unset means spill may land on network storage. Took measured peak spill from 0.32 GB to zero. |
| `preserve_insertion_order` | `false` | `true` | Reduces memory on large joins. Safe: UKAM output is keyed by `unique_id`. |
| `memory_limit` | leave unset | ~80% of RAM | Set only when sharing the driver. Too low forces needless spill. |
| `threads` | leave unset | core count | The default is correct on a dedicated single-node cluster. |

Inspect what you actually got:

```python
for key in ("threads", "memory_limit", "temp_directory",
            "preserve_insertion_order"):
    value = con.execute(f"SELECT current_setting('{key}')").fetchone()[0]
    print(f"{key:26} {value}")
```

## `AddressMatcher` arguments

| Argument | Type | Databricks guidance |
|---|---|---|
| `canonical_addresses` | relation \| path | **Always a prepared folder path, copied to `/local_disk0`.** This is the 2.22× step. Passing a raw relation makes every run redo cleaning, tokenisation and TF derivation. |
| `addresses_to_match` | relation \| list | A relation read on **this** connection. Reading straight from DBFS was no slower at 6,367 rows; copy locally only if yours is large. |
| `con` | connection | Fresh per run. Never shared across matcher instances. |
| `canonical_address_filter` | SQL string | Cheap regional narrowing, e.g. `"postcode LIKE 'SW%'"`. **Changes results.** |
| `stages` | list of stages | Defaults to `[ExactMatchStage(), SplinkStage()]`. Changing this changes results. |
| `cleaning_num_chunks` | int, default 10 | Only relevant when passing raw canonical data — which you should not be doing. |
| `show_progress` | `True` \| `False` \| `"auto"` \| `"stages"` \| `"off"` | Use `"stages"` in jobs, `True` interactively. |
| `debug_options` | `DebugOptions` | Off in production; it adds logging overhead. |

## `prepare_canonical_folder` arguments

| Argument | Default | Guidance |
|---|---|---|
| `num_of_chunks` | 10 | Lower for tight memory, `1` for small datasets. |
| `output_chunk_count` | 1 | `1` writes a single Parquet file. Above 1 writes hash-partitioned chunks. |
| `derive_distinguishing_wrt_adjacent_records` | `True` | Leave on — it improves matching of suffix-similar neighbours. |
| `overwrite` | `False` | `True` when refreshing in place. Prefer versioned output folders. |
| `show_progress` | `True` | `"stages"` for scheduled jobs. |

## `SplinkStage` — accuracy-affecting

These change results. Listed here for completeness; see
[Accuracy vs speed](accuracy.md) before touching any of them.

| Parameter | Default | Effect |
|---|---:|---|
| `predict_threshold_match_weight` | `-50` | Minimum score passed to Splink's `predict()`. Lower keeps more candidate pairs. |
| `improve_threshold_match_weight` | `-20` | Minimum score considered in the token-based adjustment step. |
| `improve_top_n_matches` | `5` | Candidates per messy address retained for token-based adjustment. Lowering is faster, less accurate. |
| `improve_use_bigrams` | `True` | Whether the improvement step uses bigrams as well as single tokens. |
| `final_match_weight_threshold` | `-20.0` | Minimum weight to emit a match. Raising improves precision, costs recall. |
| `final_distinguishability_threshold` | `0.0` | Minimum gap to the runner-up. `None` disables the filter. |
| `include_full_postcode_block` | `False` | Adds a strict full-postcode blocking rule. |
| `include_outside_postcode_block` | `True` | Broader rules that generate pairs across postcode boundaries. Disabling is much faster and loses cross-boundary matches. |

## Databricks environment

| Item | Requirement |
|---|---|
| Compute type | **Classic**. Serverless has no `/local_disk0`. |
| Cluster shape | Single node preferred; multi-node works but wastes money. |
| Local disk | Must fit prepared canonical + messy + DuckDB file + spill. Aim for 3× the prepared folder size. |
| Path form for `dbutils.fs.cp` | Source `dbfs:/...`, destination `file:/local_disk0/...`. |
| Path form for DuckDB reading DBFS directly | `/dbfs/...` (FUSE) — only relevant if you skip the local copy. |

## Quick environment capture

Paste into any run so your timings are attributable later:

```python
import os, duckdb, uk_address_matcher

env = {
    "databricks_runtime": os.environ.get("DATABRICKS_RUNTIME_VERSION"),
    "cpu_count": os.cpu_count(),
    "duckdb_version": duckdb.__version__,
    "ukam_version": uk_address_matcher.__version__,
}
try:
    import splink
    env["splink_version"] = splink.__version__
except ImportError:
    pass
with open("/proc/meminfo") as f:
    for line in f:
        if line.startswith("MemTotal:"):
            env["mem_total_gb"] = round(int(line.split()[1]) / 1024**2, 1)
            break
stat = os.statvfs("/local_disk0")
env["local_disk0_free_gb"] = round((stat.f_bavail * stat.f_frsize) / 1024**3, 2)

print(env)
```

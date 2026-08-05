---
title: Settings
---

# Settings

Use these values and leave the remaining DuckDB and UKAM options at their
defaults.

| Setting | Value |
| --- | --- |
| Compute | Classic |
| Cluster | Single-node preferred; multi-node supported |
| DuckDB connection | `duckdb.connect()` |
| `temp_directory` | `/local_disk0/ukam/tmp` |
| `preserve_insertion_order` | `false` |
| `threads` | Leave unset |
| `memory_limit` | Leave unset |
| `canonical_addresses` | Prepared folder copied to `/local_disk0` |
| `addresses_to_match` | Read with the same DuckDB connection |
| `show_progress` | `"stages"` for a scheduled job |

!!! warning "Do not tune matching behaviour by accident"

    Blocking rules, stages, match thresholds, candidate limits, and
    `canonical_address_filter` can change the result. They are not part of this
    Databricks performance setup.

## If something goes wrong

| Problem | Fix |
| --- | --- |
| `/local_disk0` does not exist | Use classic compute instead of serverless |
| A table already exists on the second run | Close the old connection and use a fresh `duckdb.connect()` |
| The job uses an old canonical copy | Restart the cluster or delete `/local_disk0/ukam/prepared` |
| The driver runs out of memory | Increase the driver size; keep `temp_directory` on local disk |

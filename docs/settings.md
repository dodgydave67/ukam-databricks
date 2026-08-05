---
title: Settings
---

# Recommended settings

## Databricks and storage

| Item | Recommended value | Why |
| --- | --- | --- |
| Compute | Classic | Provides `/local_disk0` |
| Cluster shape | Single Node | UKAM runs in the driver process |
| Prepared canonical data | Build once and store durably | Avoids repeating canonical cleaning and indexing |
| Match-time canonical path | `/local_disk0/ukam/prepared` | Keeps repeated Parquet reads off network storage |
| Output path | Write locally, then copy remotely | `/local_disk0` is fast but ephemeral |

## DuckDB and UKAM

| Setting | Recommended value | Notes |
| --- | --- | --- |
| Connection | `duckdb.connect()` | In-memory; open a fresh connection per matching run |
| `temp_directory` | `/local_disk0/ukam/tmp` | Places any spill on driver-local storage |
| `preserve_insertion_order` | `false` | Avoids unnecessary ordering overhead |
| `threads` | Leave unset | DuckDB uses the available cores |
| `memory_limit` | Leave unset | Set only if the driver shares memory with other workloads |
| `canonical_addresses` | Local prepared-folder path | Do not pass raw canonical data to every match |
| `addresses_to_match` | Relation created on the same connection | Avoids connection mismatch problems |
| `show_progress` | `"stages"` for jobs | Concise scheduled-job logging |

Check the active DuckDB settings:

```python
for key in (
    "threads",
    "memory_limit",
    "temp_directory",
    "preserve_insertion_order",
):
    value = con.execute(
        f"SELECT current_setting('{key}')"
    ).fetchone()[0]
    print(f"{key}: {value}")
```

!!! warning "Leave matching defaults alone unless you mean to change results"

    Blocking rules, match-weight thresholds, `canonical_address_filter`,
    stages, and candidate limits affect which address wins or whether an
    address matches. They are not part of this performance setup.

## Common problems

### `/local_disk0` does not exist

The cluster is serverless. Switch to classic compute.

### A table already exists on the second run

Close the DuckDB connection and create a new one for each `AddressMatcher`
run. Splink creates working tables on the connection.

### The prepared canonical data changed but the job uses the old copy

Set `REFRESH_CANONICAL = True` for one run, restart the cluster, or use a new
versioned local directory.

### The driver runs out of memory

Increase the driver size first. If memory must be capped, set a DuckDB
`memory_limit` and keep `temp_directory` on `/local_disk0` so spill remains
local.

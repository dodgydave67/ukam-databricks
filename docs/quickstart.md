---
title: Quickstart
---

# Quickstart

The whole recommended configuration. If you already have a prepared canonical
folder in cloud storage and a classic single-node cluster, this is it.

!!! warning "Prerequisites"

    - **Classic** compute (serverless has no `/local_disk0`)
    - A prepared canonical folder — see [Preparing canonical data](setup/prepare-canonical.md)
    - `pip install uk_address_matcher` on the cluster

## 1. Check you are on classic compute

```python
import os

stat = os.statvfs("/local_disk0")           # raises if you are on serverless
free_gb = (stat.f_bavail * stat.f_frsize) / 1024**3
print(f"Free space on /local_disk0: {free_gb:.2f} GB")
```

The reference environment reported 173.8 GB free — comfortably more than
Ordnance Survey data needs. If this raises `FileNotFoundError`, switch to a
classic cluster.

## 2. Copy the prepared canonical folder to local disk

This single step is worth **2.22×**.

```python
PREPARED_REMOTE = "dbfs:/mnt/<your-path>/ukam_prepared_canonical"   # (1)!
MESSY_REMOTE    = "dbfs:/mnt/<your-path>/messy"

LOCAL_ROOT     = "/local_disk0/ukam"
LOCAL_PREPARED = f"{LOCAL_ROOT}/prepared"
LOCAL_TMP      = f"{LOCAL_ROOT}/tmp"

os.makedirs(LOCAL_TMP, exist_ok=True)

# Copy once per cluster lifetime. The manifest file is the cache marker.
if not os.path.exists(f"{LOCAL_PREPARED}/ukam_manifest.json"):        # (2)!
    dbutils.fs.cp(PREPARED_REMOTE, f"file:{LOCAL_PREPARED}/", recurse=True)
```

1.  Use the `dbfs:/` form for `dbutils.fs.cp`, not the `/dbfs/` FUSE form.
    Unity Catalog Volumes use `dbfs:/Volumes/<catalog>/<schema>/<volume>/...`.
2.  `ukam_manifest.json` is written by `prepare_canonical_folder()`. Guarding on
    it means a half-finished copy is not mistaken for a complete one — and makes
    the cell cheap to re-run.

!!! tip "Leave the messy data where it is"

    Copying it locally was benchmarked at **no measurable benefit** for a
    6,367-row dataset. Revisit only if your messy data is large — see
    [Tested and rejected](reference/rejected.md#copying-the-messy-data-to-local-disk).

## 3. Open a tuned in-memory connection

```python
import duckdb

con = duckdb.connect()                           # (1)!
con.execute(f"SET temp_directory='{LOCAL_TMP}'") # (2)!
con.execute("SET preserve_insertion_order=false")
```

1.  **In memory.** An on-disk database was benchmarked 25% slower here — it only
    helps when you are genuinely memory-constrained. Use a **fresh** connection
    for each `match()` run.
2.  Without this, spill lands wherever the process happens to be running — often
    a network-backed path. Setting it took measured peak spill from 0.32 GB to
    zero.

## 4. Match

```python
from uk_address_matcher import AddressMatcher

messy = con.read_parquet(f"/dbfs/mnt/<your-path>/messy/*.parquet")   # (1)!

matcher = AddressMatcher(
    canonical_addresses=LOCAL_PREPARED,
    addresses_to_match=messy,
    con=con,
    show_progress="stages",                                          # (2)!
)

result = matcher.match()
result.matches().show(max_width=10_000)
```

1.  Read the messy data on **this** connection. A relation bound to a different
    connection will not behave as you expect.
2.  `"stages"` logs stage boundaries without live progress bars — what you want
    in a scheduled job. Use `True` for interactive work.

## 5. Write results back to durable storage

`/local_disk0` disappears when the cluster terminates.

```python
LOCAL_OUT  = f"{LOCAL_ROOT}/matches.parquet"
REMOTE_OUT = "dbfs:/mnt/<your-path>/ukam_matches/matches.parquet"

con.execute(
    f"COPY ({result.matches().sql_query()}) TO '{LOCAL_OUT}' (FORMAT PARQUET)"
)
dbutils.fs.cp(f"file:{LOCAL_OUT}", REMOTE_OUT)

con.close()
```

---

## What you should *not* add

Three optimisations were benchmarked and rejected. Skipping them is a
recommendation, not an omission:

- ~~On-disk DuckDB database~~ — 25% slower with no spill to relieve
- ~~Materialising the canonical tables~~ — slower than the untuned baseline
- ~~Copying small messy files locally~~ — no measurable effect

Details and the conditions under which each might still pay off:
[Tested and rejected](reference/rejected.md).

## Next steps

- The [full annotated notebook](setup/recommended-notebook.md) with preflight
  checks, diagnostics and result-writing.
- [Optimisation reference](reference/optimisations.md) — what each step buys.
- [Benchmarks](benchmarks/results.md) — the complete matrix.

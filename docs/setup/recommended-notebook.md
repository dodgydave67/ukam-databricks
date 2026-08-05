---
title: The recommended notebook
---

# The recommended notebook

The benchmarked-optimal configuration — steps 0, 2 and 3 of the
[optimisation stack](../reference/optimisations.md) — as a Databricks notebook
you can paste in and edit. The [Quickstart](../quickstart.md) is the same thing
without the safety rails.

[:material-download: Download as .py](../assets/ukam_databricks_match.py){ .md-button .md-button--primary }
[:material-flask: Download the benchmark harness](../assets/ukam_databricks_benchmark.py){ .md-button }

!!! note "What is deliberately absent"

    No on-disk DuckDB database, no canonical materialisation, no local copy of
    the messy data. All three were benchmarked and
    [rejected](../reference/rejected.md). The notebook has flags to enable them
    if you want to test them on your own data — all default to off.

---

## Cell 1 — Configuration

Everything you need to edit lives here.

```python
# --- remote (durable) locations -------------------------------------------
PREPARED_REMOTE = "dbfs:/mnt/<your-path>/ukam_prepared_canonical"
MESSY_REMOTE    = "dbfs:/mnt/<your-path>/messy"
MESSY_POSIX     = "/dbfs/mnt/<your-path>/messy"     # (1)!
OUTPUT_REMOTE   = "dbfs:/mnt/<your-path>/ukam_matches"

MESSY_GLOB = "*.parquet"

# --- local (ephemeral) working locations ----------------------------------
LOCAL_ROOT     = "/local_disk0/ukam"
LOCAL_PREPARED = f"{LOCAL_ROOT}/prepared"
LOCAL_MESSY    = f"{LOCAL_ROOT}/messy"
LOCAL_TMP      = f"{LOCAL_ROOT}/tmp"
LOCAL_OUT      = f"{LOCAL_ROOT}/matches.parquet"

# --- optional --------------------------------------------------------------
CANONICAL_FILTER = None    # e.g. "postcode LIKE 'SW%'" — CHANGES RESULTS
FRESH_LOCAL_COPY = False   # True forces a re-copy of the prepared folder

# --- benchmarked SLOWER: leave False unless testing on your own data -------
COPY_MESSY_LOCALLY = False # arm 4: no measurable effect at 6,367 rows
USE_ONDISK_DB      = False # arm 5: 25% slower with no memory pressure
```

1.  Both path forms are needed: the `dbfs:/` URI for `dbutils.fs.cp`, the
    `/dbfs/` FUSE path for DuckDB reading the messy data in place.

## Cell 2 — Preflight

Fail fast rather than halfway through a long run.

```python
import os
import shutil
import time

import duckdb
import uk_address_matcher
from uk_address_matcher import AddressMatcher

try:
    stat = os.statvfs("/local_disk0")
except OSError as exc:
    raise RuntimeError(
        "/local_disk0 is not available — this notebook requires CLASSIC "
        "compute. Serverless will not work."
    ) from exc

free_gb = (stat.f_bavail * stat.f_frsize) / 1024**3

print(f"ukam {uk_address_matcher.__version__} | duckdb {duckdb.__version__} | "
      f"{os.cpu_count()} cores | /local_disk0 free {free_gb:.1f} GB")
```

## Cell 3 — Stage the canonical folder on local disk

The step worth 2.22×.

```python
def stage(remote_uri: str, local_dir: str, marker: str | None = None,
          force: bool = False) -> float | None:
    """Copy `remote_uri` to `local_dir`, skipping if already present.

    Returns seconds spent copying, or None if the copy was skipped.
    """
    probe = os.path.join(local_dir, marker) if marker else local_dir
    if os.path.exists(probe) and not force:
        print(f"cached: {local_dir}")
        return None

    if os.path.isdir(local_dir):
        shutil.rmtree(local_dir, ignore_errors=True)
    os.makedirs(local_dir, exist_ok=True)

    t = time.perf_counter()
    dbutils.fs.cp(remote_uri, f"file:{local_dir}/", recurse=True)
    elapsed = round(time.perf_counter() - t, 2)
    print(f"copied {remote_uri} -> {local_dir} in {elapsed}s")
    return elapsed


os.makedirs(LOCAL_TMP, exist_ok=True)

copy_canonical_s = stage(
    PREPARED_REMOTE, LOCAL_PREPARED,
    marker="ukam_manifest.json",     # (1)!
    force=FRESH_LOCAL_COPY,
)

if COPY_MESSY_LOCALLY:
    copy_messy_s = stage(MESSY_REMOTE, LOCAL_MESSY)
    messy_path = f"{LOCAL_MESSY}/{MESSY_GLOB}"
else:
    copy_messy_s = None
    messy_path = f"{MESSY_POSIX}/{MESSY_GLOB}"       # (2)!
```

1.  `ukam_manifest.json` is the last artefact written by
    `prepare_canonical_folder()`, so its presence means the copy finished.
    Guarding on the folder alone would treat a half-finished copy as complete.
2.  Messy data read straight from DBFS. Benchmarked as no worse at 6,367 rows;
    flip `COPY_MESSY_LOCALLY` if yours is substantially larger.

!!! tip "Interactive clusters"

    Cells 1–3 run once and every subsequent match reuses the local copy. Copy
    time in the benchmark ranged 6.91–11.00 s for the same folder, so amortising
    it across batches is worth doing. Set `FRESH_LOCAL_COPY = True` after
    publishing a new canonical version.

## Cell 4 — Tuned connection

```python
con = duckdb.connect()                             # (1)!
con.execute(f"SET temp_directory='{LOCAL_TMP}'")   # (2)!
con.execute("SET preserve_insertion_order=false")  # (3)!

print({
    s: con.execute(f"SELECT current_setting('{s}')").fetchone()[0]
    for s in ("threads", "memory_limit", "temp_directory")
})
```

1.  **In memory.** Arm 5 tested a persistent database on `/local_disk0` and was
    25% slower — there was no spill for it to relieve. Enable `USE_ONDISK_DB`
    only if you see memory pressure.
2.  Spill destination. Took measured peak spill from 0.32 GB to zero.
3.  Lets DuckDB drop row-order guarantees it does not need. Safe: UKAM output is
    keyed by `unique_id`, not input order.

??? example "If you set `USE_ONDISK_DB = True`"

    Delete the database file first — stale tables from a previous run are the
    fastest route to a confusing failure.

    ```python
    LOCAL_DB = f"{LOCAL_ROOT}/ukam.duckdb"
    for path in (LOCAL_DB, LOCAL_DB + ".wal"):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    con = duckdb.connect(database=LOCAL_DB)
    ```

## Cell 5 — Match

```python
messy = con.read_parquet(messy_path)               # (1)!
print(f"messy rows: {messy.count('*').fetchone()[0]:,}")

kwargs = dict(
    canonical_addresses=LOCAL_PREPARED,
    addresses_to_match=messy,
    con=con,
    show_progress="stages",
)
if CANONICAL_FILTER:
    kwargs["canonical_address_filter"] = CANONICAL_FILTER

matcher = AddressMatcher(**kwargs)

t = time.perf_counter()
result = matcher.match()
match_seconds = round(time.perf_counter() - t, 2)

print(f"match: {match_seconds}s  (copy_canonical: {copy_canonical_s}s)")
```

1.  Read the messy data **on this connection**. A relation created on a
    different connection silently changes what is being measured.

## Cell 6 — Inspect

```python
matches_sql = result.matches().sql_query()

n_rows, n_matched = con.execute(
    f"SELECT COUNT(*), COUNT(resolved_canonical_id) FROM ({matches_sql})"
).fetchone()
print(f"matched {n_matched:,} / {n_rows:,} ({n_matched / n_rows:.1%})")

con.execute(
    f"SELECT match_reason, COUNT(*) AS n FROM ({matches_sql}) "
    "GROUP BY 1 ORDER BY n DESC"
).show()
```

The reference run matched **6,358 of 6,367** (99.86%) — 1,672 from the exact
stage, 4,686 from Splink. A match-reason breakdown well outside that shape is
worth investigating before you trust the output.

Log the fingerprint too. It costs almost nothing and turns "did anything
change?" from an argument into a lookup:

```python
digest = con.execute(f"""
    SELECT md5(string_agg(
        CAST(unique_id AS VARCHAR) || '>' ||
        COALESCE(CAST(resolved_canonical_id AS VARCHAR), ''),
        '|' ORDER BY CAST(unique_id AS VARCHAR)
    ))
    FROM ({matches_sql})
""").fetchone()[0]
print(f"result digest: {digest}")
```

## Cell 7 — Write results back

```python
con.execute(f"COPY ({matches_sql}) TO '{LOCAL_OUT}' (FORMAT PARQUET)")

dbutils.fs.mkdirs(OUTPUT_REMOTE)
dbutils.fs.cp(f"file:{LOCAL_OUT}", f"{OUTPUT_REMOTE}/matches.parquet")

con.close()
print(f"written to {OUTPUT_REMOTE}/matches.parquet")
```

!!! danger "Do not skip this"

    `/local_disk0` is destroyed when the cluster terminates. Results that exist
    only there are gone.

See [Writing results back](outputs.md) for Delta tables, partitioned output and
`unique_id` type handling.

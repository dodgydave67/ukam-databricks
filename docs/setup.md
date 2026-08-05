---
title: Setup
---

# Set up Databricks

## Cluster

| Setting | Use |
| --- | --- |
| Compute | **Classic**, not serverless |
| Cluster mode | **Single Node** |
| Driver | Start with at least 8 cores and about 64 GB RAM; increase the driver for larger workloads |
| Local storage | Enough `/local_disk0` space for the prepared canonical folder, output, and possible DuckDB spill |
| Workers | None |

UKAM and DuckDB run in the driver process. Adding workers increases cost
without adding matching capacity. `/local_disk0` is required and is not
available on serverless compute.

Check the cluster before starting:

```python
import os

stat = os.statvfs("/local_disk0")
free_gb = (stat.f_bavail * stat.f_frsize) / 1024**3
print(f"/local_disk0 free: {free_gb:.1f} GB")
```

## Install UKAM

Use a cluster library or the first notebook cell:

```python
%pip install uk_address_matcher==1.2.4
dbutils.library.restartPython()
```

Pinning the version prevents an upstream change from silently changing a
scheduled job. Test a newer UKAM version before updating the pin.

## Choose three paths

The production notebook asks you to edit only these values:

```python
PREPARED_REMOTE = "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_prepared_canonical"
MESSY_INPUT = "/Volumes/<catalog>/<schema>/<volume>/messy/*.parquet"
OUTPUT_REMOTE_DIR = "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_matches"
```

- `PREPARED_REMOTE` uses a `dbfs:/...` URI because `dbutils.fs.cp` copies it to
  local disk.
- `MESSY_INPUT` is a POSIX-style path that DuckDB can read. For an older DBFS
  mount, use `/dbfs/mnt/<path>/*.parquet`.
- `OUTPUT_REMOTE_DIR` is durable storage. Results must not remain only on
  `/local_disk0`, which is erased with the cluster.

## Prepare the canonical folder once

If you already have a folder created by `prepare_canonical_folder()`, skip this
section. Do not place preparation inside every matching job.

Run preparation when the canonical source changes, then store the prepared
folder in the location configured as `PREPARED_REMOTE`:

```python
import os

import duckdb
from uk_address_matcher import prepare_canonical_folder

LOCAL_ROOT = "/local_disk0/ukam_prepare"
LOCAL_PREPARED = f"{LOCAL_ROOT}/prepared"
REMOTE_PREPARED = (
    "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_prepared_canonical"
)

os.makedirs(LOCAL_ROOT, exist_ok=True)
con = duckdb.connect(database=f"{LOCAL_ROOT}/prepare.duckdb")
con.execute(f"SET temp_directory='{LOCAL_ROOT}/tmp'")
con.execute("SET preserve_insertion_order=false")

raw_canonical = con.read_parquet(
    "/local_disk0/ukam_prepare/raw/*.parquet"
)

prepare_canonical_folder(
    raw_canonical,
    LOCAL_PREPARED,
    con=con,
    overwrite=True,
    show_progress="stages",
)

dbutils.fs.cp(
    f"file:{LOCAL_PREPARED}",
    REMOTE_PREPARED,
    recurse=True,
)
con.close()
```

Copy the raw canonical Parquet files to the local `raw` directory before
running this preparation block. Version prepared folders when the canonical
release changes, or refresh the local cached copy before the next match.

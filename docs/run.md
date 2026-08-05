---
title: Run UKAM
---

# Run UKAM

Paste these five cells into a Databricks notebook and run them in order, or
download the ready-made notebook.

[Download the Databricks notebook](assets/ukam_databricks_match.py){ .md-button .md-button--primary }

## Cell 1 — Install UKAM

```python
%pip install uk_address_matcher==1.2.4
dbutils.library.restartPython()
```

## Cell 2 — Set your paths

Edit these three values only:

```python
PREPARED_REMOTE = "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_prepared_canonical"
MESSY_INPUT = "/Volumes/<catalog>/<schema>/<volume>/messy/*.parquet"
OUTPUT_FOLDER = "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_matches"
```

## Cell 3 — Copy the prepared data locally

The copy is skipped when it is already present on the cluster.

```python
import os

import duckdb
from uk_address_matcher import AddressMatcher

LOCAL_PREPARED = "/local_disk0/ukam/prepared"
LOCAL_TMP = "/local_disk0/ukam/tmp"
LOCAL_OUTPUT = "/local_disk0/ukam/matches.parquet"

os.makedirs(LOCAL_PREPARED, exist_ok=True)
os.makedirs(LOCAL_TMP, exist_ok=True)

if not os.listdir(LOCAL_PREPARED):
    dbutils.fs.cp(
        PREPARED_REMOTE,
        f"file:{LOCAL_PREPARED}/",
        recurse=True,
    )
```

Restart the cluster, or delete `/local_disk0/ukam/prepared`, when you need to
copy a newer canonical version.

## Cell 4 — Match

```python
con = duckdb.connect()
con.execute(f"SET temp_directory='{LOCAL_TMP}'")
con.execute("SET preserve_insertion_order=false")

matcher = AddressMatcher(
    canonical_addresses=LOCAL_PREPARED,
    addresses_to_match=con.read_parquet(MESSY_INPUT),
    con=con,
    show_progress="stages",
)

matches = matcher.match().matches()
```

## Cell 5 — Save the results

```python
matches.write_parquet(LOCAL_OUTPUT, overwrite=True)

dbutils.fs.mkdirs(OUTPUT_FOLDER)
dbutils.fs.rm(f"{OUTPUT_FOLDER}/matches.parquet")
dbutils.fs.cp(
    f"file:{LOCAL_OUTPUT}",
    f"{OUTPUT_FOLDER}/matches.parquet",
)

con.close()
print(f"Saved {OUTPUT_FOLDER}/matches.parquet")
```

The result is now in durable storage and will remain available after the
cluster terminates.

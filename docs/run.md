---
title: Run UKAM
---

# Run UKAM

This is the production notebook. It has one execution path: copy the prepared
canonical folder to driver-local storage, open a tuned in-memory connection,
match, and copy the result back to durable storage.

[:material-download: Download the Databricks notebook](assets/ukam_databricks_match.py){ .md-button .md-button--primary }

## Before running it

Edit these three values near the top of the notebook:

```python
PREPARED_REMOTE = "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_prepared_canonical"
MESSY_INPUT = "/Volumes/<catalog>/<schema>/<volume>/messy/*.parquet"
OUTPUT_REMOTE_DIR = "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_matches"
```

Set `REFRESH_CANONICAL = True` only when the prepared canonical folder has
changed on a long-lived cluster. Job clusters start with an empty local disk,
so the normal cache check copies automatically.

## Complete notebook

```python
--8<-- "docs/assets/ukam_databricks_match.py"
```

The notebook is meant to be edited in one place and run as a production job.

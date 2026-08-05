---
title: Before you run
---

# Before you run

## 1. Use classic compute

`/local_disk0` must be available. A single-node cluster is usually the cheapest
choice because matching mainly uses the driver, but a multi-node classic
cluster works too. Size the driver for your data.

## 2. Install UKAM

Run this in the first notebook cell:

```python
%pip install uk_address_matcher==1.2.4
dbutils.library.restartPython()
```

## 3. Have a prepared canonical folder

The matching notebook expects a folder previously created with
`prepare_canonical_folder()`. Preparation is a separate, occasional task — do
not prepare the canonical data again inside every matching job.

If you do not have one yet, create it first by following the
[official UKAM documentation](https://moj-analytical-services.github.io/uk_address_matcher/).

## 4. Know your three paths

The notebook asks for:

| Name | Example |
| --- | --- |
| Prepared canonical folder | `dbfs:/Volumes/catalog/schema/volume/ukam_prepared_canonical` |
| Messy Parquet input | `/Volumes/catalog/schema/volume/messy/*.parquet` |
| Results folder | `dbfs:/Volumes/catalog/schema/volume/ukam_matches` |

For an older DBFS mount, the equivalent input path is
`/dbfs/mnt/<path>/*.parquet`. Keep the `dbfs:/...` form for paths passed to
`dbutils.fs` and the POSIX form for paths read by DuckDB.

That is all the setup required for the matching notebook.

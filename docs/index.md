---
title: Start here
---

# UK Address Matcher on Databricks

This site shows the shortest recommended way to run
[`uk_address_matcher`](https://github.com/moj-analytical-services/uk_address_matcher)
(UKAM) on Databricks.

If you already have a prepared canonical folder, go straight to
[Run UKAM](run.md). The notebook asks for three paths and can be run from top
to bottom.

## What you need

- a classic Databricks cluster — single-node or multi-node
- a canonical folder already created by `prepare_canonical_folder()`
- the addresses you want to match, stored as Parquet
- a durable folder for the results

## Recommended configuration

| Part | Use |
| --- | --- |
| Compute | Classic compute so `/local_disk0` is available |
| Cluster | Single-node when available; multi-node is supported |
| Canonical data | Copy the prepared folder to `/local_disk0` once per cluster |
| DuckDB | A fresh in-memory connection for each matching run |
| DuckDB settings | Local `temp_directory` and `preserve_insertion_order=false` |
| Output | Write locally, then copy to durable storage |

[:material-play: Copy and run the notebook](run.md){ .md-button .md-button--primary }
[:material-cog: Check the prerequisites](setup.md){ .md-button }

## What stays at the default

The notebook does not change UKAM's matching rules, stages, thresholds, or
candidate limits. It only changes the Databricks and DuckDB execution setup.

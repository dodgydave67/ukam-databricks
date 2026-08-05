---
title: Why it is slow by default
---

# Why the default Databricks setup is slow

Understanding the cause makes the fix obvious, and makes it clear which parts of
this configuration you can safely drop.

## UKAM is a single-process DuckDB application

`AddressMatcher` takes a `duckdb.DuckDBPyConnection` and does all its work
through it. There is no Spark in the path. That has three consequences on
Databricks:

1. **Workers do nothing.** All matching happens in the driver's Python process.
   A 10-node cluster matches addresses exactly as fast as a 1-node cluster, and
   costs ten times as much.
2. **Driver RAM and driver local disk are the only resources that matter.**
3. **Cluster storage abstractions work against you.** Spark is built to stream
   large scans from object storage once. DuckDB, driving a Splink model with
   multiple blocking rules, scans the canonical side repeatedly.

## DBFS mounts are network storage wearing a filesystem costume

A path like `/dbfs/mnt/.../ukam_prepared_canonical` looks local. It is not. It
is a FUSE mount over Azure Blob Storage / S3. Every read is an HTTP request with
network latency, and FUSE adds its own per-operation overhead on top.

`/local_disk0`, by contrast, is ephemeral storage physically attached to the
driver VM — SSD or NVMe depending on the instance type. It behaves like a
laptop's internal disk.

!!! quote "From the benchmark discussion"

    The DBFS mount is a path to remote object storage, whereas `/local_disk0`
    is temporary storage attached directly to the cluster's driver VM. This
    gives DuckDB lower-latency local file access and avoids repeatedly reading
    the prepared Parquet files over the network.

## Lazy relations mean the canonical data is read many times

When you pass a prepared folder path to `AddressMatcher`, the library loads it
as **lazy** `con.read_parquet(...)` relations rather than materialised tables.
Lazy relations are the right default — they avoid loading data a query never
touches.

But the Splink stage generates candidate pairs using a set of blocking rules,
and each rule causes the canonical side to be re-evaluated. Multiply "re-read
the canonical Parquet" by the number of blocking rules, then multiply again by
network latency, and the cost compounds.

This is why the **exact-match stage** is the clearest diagnostic in the
benchmark. It is almost pure canonical reading, and it responds almost entirely
to where that data lives:

| Canonical data location | Exact-match stage |
| --- | ---: |
| DBFS mount | 18.98 s |
| `/local_disk0` | **1.38 s** |

!!! failure "The obvious fix is the wrong one"

    If repeated scans are the problem, materialising the canonical relation into
    a physical table should fix it. That was benchmarked as **arm 6** and it made
    things dramatically worse — 155.72 s against 58.86 s, slower even than the
    untuned baseline. Materialisation cost 76.41 s on its own.

    Once the Parquet is on local NVMe, re-scanning it is cheap enough that
    eliminating the re-scans is not worth the write. Fix the *latency*, not the
    *repetition*. See [Tested and rejected](reference/rejected.md#materialising-the-canonical-relations).

## Spill has nowhere good to go

DuckDB spills intermediate results to disk when a query exceeds its memory
budget. With no `temp_directory` set, spill lands in a `.tmp` folder under the
process working directory — which on a Databricks notebook is frequently the
container root or, worse, a DBFS-backed path. You end up writing spill over the
network.

The benchmark measured **0.32 GB** of peak spill in the baseline arm and
**zero** in every arm after `temp_directory` was pointed at `/local_disk0`.

An on-disk DuckDB database would go further still, letting DuckDB page whole
tables out rather than only query intermediates. On a workload that is genuinely
memory-constrained that is the difference between a slow run and a crash — but
on the benchmarked workload, with 57.4 GB of RAM and no spill left to relieve,
it was simply 25% slower. See
[Tested and rejected](reference/rejected.md#on-disk-duckdb-database).

## Summary of the causes

| Symptom | Cause | Fix |
|---|---|---|
| Long runtime, low CPU utilisation | Network-bound reads from DBFS | **Copy the canonical folder to `/local_disk0`** |
| Wildly variable runtime between identical runs | Network latency variance | Same — local disk took run-to-run spread from 16.37 s to 0.14 s |
| Slow exact-match stage | Canonical Parquet being read over the network | Same — 18.98 s to 1.38 s |
| Runtime spikes on large joins | Spill written to a slow or wrong location | `SET temp_directory` to local disk |
| Out-of-memory failures | In-memory database cannot page out | On-disk database — but [only if you are actually spilling](reference/rejected.md#on-disk-duckdb-database) |
| Cost far above expectation | Paying for idle worker nodes | Single-node classic cluster |
| `Table ... already exists` on the second run | Reused DuckDB connection | New connection per match run |

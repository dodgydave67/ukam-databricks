---
title: Cluster configuration
---

# Cluster configuration

## Classic compute is mandatory

Every optimisation on this site depends on `/local_disk0`, the ephemeral disk
attached to the driver VM. **Serverless compute does not expose it.** If you run
the benchmark harness on serverless it stops immediately with:

```text
RuntimeError: /local_disk0 not available — you are probably on serverless
compute. This harness requires classic compute.
```

Detect it yourself before doing anything expensive:

```python
import os

try:
    stat = os.statvfs("/local_disk0")
    free_gb = (stat.f_bavail * stat.f_frsize) / 1024**3
    print(f"/local_disk0 available — {free_gb:.2f} GB free")
except OSError:
    raise RuntimeError(
        "/local_disk0 not available. Switch to classic compute."
    )
```

## Single node beats multi-node

UKAM runs entirely inside the driver's Python process via DuckDB. Worker nodes
contribute nothing to matching.

!!! tip "Recommended: **Single Node** classic cluster"

    Choose the largest driver you can justify and no workers at all. This is
    both faster per pound and simpler to reason about.

The configuration does still work on a multi-node cluster — the benchmark author
ran these optimisations on clusters with multiple nodes — you are just paying
for idle machines.

## Sizing the driver

| Resource | Guidance |
|---|---|
| **vCPU** | DuckDB defaults its thread count to the core count. More cores help the Splink join stages materially. 16–32 vCPU is a sensible band. |
| **RAM** | The benchmark ran comfortably in 57.4 GB with **zero spill**, using an in-memory database. Provision enough that you are not spilling; if you are, read [Tested and rejected](../reference/rejected.md#on-disk-duckdb-database) before reaching for an on-disk database. |
| **Local disk** | Must hold the prepared canonical folder plus any spill. The reference environment reported **173.8 GB free**, more than enough for Ordnance Survey data. |
| **Instance family** | Prefer families with NVMe-backed local storage. On Azure, that means an instance type with a local temp disk; the benchmark notes local storage may be SSD or NVMe depending on VM type. |

### Checking headroom before a run

```python
import os, shutil

stat = os.statvfs("/local_disk0")
free_gb = (stat.f_bavail * stat.f_frsize) / 1024**3

# Size of the prepared folder you are about to copy in
prepared_gb = sum(
    f.size for f in dbutils.fs.ls("dbfs:/mnt/<your-path>/ukam_prepared_canonical")
) / 1024**3

print(f"prepared: {prepared_gb:.2f} GB   free: {free_gb:.2f} GB")
assert free_gb > prepared_gb * 3, "Leave headroom for spill and the DuckDB file"
```

The 3× rule of thumb leaves headroom for spill during the Splink stage. The
benchmark measured zero spill once `temp_directory` was set, so this is
deliberately conservative.

## Cluster lifecycle and the local copy

`/local_disk0` is wiped when the cluster terminates or restarts. That has two
practical consequences:

- **The copy is a per-cluster-lifetime cost, not a per-run cost.** If you match
  several messy datasets against the same canonical data, copy once and reuse.
  The cache guard in the [recommended notebook](recommended-notebook.md) does
  this automatically.
- **Never treat local disk as storage.** Results must be copied back to a
  Volume, mount or external location before the cluster goes away.

!!! note "Job clusters vs interactive clusters"

    On a job cluster the copy happens on every run, because the cluster is
    fresh. Its cost is reported separately in the benchmark and is not part of
    the match time — see [Results](../benchmarks/results.md#how-copy-cost-is-accounted).
    For very short matching jobs on a large canonical dataset, the copy can
    dominate; for anything sustained it pays for itself immediately.

## Library installation

Install onto the cluster (or use a notebook-scoped install for experimentation):

```python
%pip install uk_address_matcher==1.2.4
dbutils.library.restartPython()
```

Pin the version so your timings stay attributable across runs. The reference
benchmark used UKAM **1.2.4**, DuckDB **1.5.0** and Splink **4.0.16** on DBR
**17.3**.

Record the versions in your run logs — the benchmark harness captures all of
them:

```python
import os, duckdb, splink, uk_address_matcher

print({
    "databricks_runtime": os.environ.get("DATABRICKS_RUNTIME_VERSION"),
    "cpu_count": os.cpu_count(),
    "duckdb": duckdb.__version__,
    "splink": splink.__version__,
    "ukam": uk_address_matcher.__version__,
})
```

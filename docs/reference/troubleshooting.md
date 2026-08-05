---
title: Troubleshooting
---

# Troubleshooting

## `/local_disk0 not available`

```text
FileNotFoundError: [Errno 2] No such file or directory: '/local_disk0'
```

**Cause:** you are on serverless compute.

**Fix:** switch to a classic cluster. There is no workaround — the entire
configuration depends on driver-local disk. See
[Cluster configuration](../setup/cluster.md).

---

## `Table ... already exists` / Splink table collisions on the second run

**Cause:** a DuckDB connection reused across `AddressMatcher` runs. Splink
creates named tables on the connection and does not expect to find them already
there.

**Fix:** open a fresh connection per match run.

```python
con = duckdb.connect()          # fresh, every run
```

If you are using an on-disk database, delete the file first — stale tables from
a previous run are the fastest route to a confusing failure:

```python
for path in (LOCAL_DB, LOCAL_DB + ".wal"):
    if os.path.exists(path):
        os.remove(path)
con = duckdb.connect(database=LOCAL_DB)
```

The benchmark harness treats this as mandatory, not optional — it opens a new
connection for every arm and every repeat.

---

## Out of memory during the Splink stage

Symptoms range from a hard OOM kill to the notebook detaching.

Work through these in order:

1. **`temp_directory` on local disk** so spill has somewhere fast to go, and
   **`preserve_insertion_order=false`**. Together these took measured peak spill
   from 0.32 GB to zero in the benchmark.
2. **Confirm you are actually spilling** before doing anything drastic — measure
   peak spill with the snippet below rather than guessing.
3. **Bigger driver.** UKAM only uses the driver, so this is the only node size
   that matters.
4. **Narrow the canonical side** with `canonical_address_filter` if your messy
   data is geographically bounded — but note this
   [changes results](accuracy.md).
5. **On-disk database**, `duckdb.connect(database="/local_disk0/.../ukam.duckdb")`,
   as a last resort. It was **25% slower** in the benchmark, where there was no
   spill to relieve. It is insurance, not an optimisation — see
   [Tested and rejected](rejected.md#on-disk-duckdb-database).

Setting `memory_limit` *lower* sometimes helps: it makes DuckDB spill early and
deliberately instead of being killed by the OS. Try `memory_limit` at ~60% of
driver RAM before giving up.

---

## Disk full on `/local_disk0`

```text
IOError: Could not write to temporary file ... No space left on device
```

You need room for the prepared canonical copy, the messy copy, the DuckDB
database file, **and** spill. Monitor spill directly:

```python
import os

def dir_size_gb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1024**3

print(f"spill: {dir_size_gb(LOCAL_TMP):.2f} GB")
```

The benchmark harness runs this on a background thread during matching to
capture **peak** spill, which is the number you actually need for sizing. See
the [harness](../benchmarks/harness.md).

Fixes: a larger instance type with more local storage, a
`canonical_address_filter` to shrink the working set, or splitting the messy
data into batches.

---

## The copy is slower than the match

For a small messy dataset matched once against a large canonical folder on a
fresh job cluster, `dbutils.fs.cp` of the prepared folder can dominate.

Options:

- **Use an interactive cluster** and amortise the copy across many runs — this
  is what makes the local-disk approach so effective in practice.
- **Batch your matching.** One job matching ten datasets pays the copy once.
- **Filter the canonical folder** before copying, if you only ever match one
  region: prepare a regional canonical folder instead of the full UK one.
- **Measure it.** The harness reports copy time in its own columns, separate
  from match time, precisely so you can make this call with numbers.

---

## Results changed after an upgrade

Check, in this order:

1. `uk_address_matcher.__version__` and `splink.__version__` — model or default
   changes upstream.
2. Whether your canonical folder was rebuilt. A new AddressBase release changes
   results legitimately.
3. Whether you are running any of the experimental variants from
   [Tested and rejected](rejected.md) — the materialisation subclass in
   particular depends on private internals that can move between releases.
4. Your fingerprint digests, if you have been logging them. This is the reason
   to log them. The reference run was `5194b60df1884b954e1fb2c21ce845e9`.

---

## `AddressMatcher` has no attribute `_canonical_clean`

**Cause:** the experimental materialisation subclass is running against a
version of `uk_address_matcher` whose internals have changed. It was verified
against **1.2.4**.

**Fix:** drop the subclass and use plain `AddressMatcher`. You lose nothing —
materialisation was benchmarked **slower than the untuned baseline** and is not
part of the recommended configuration.

---

## Matching is fast but the cluster costs are high

You are almost certainly paying for worker nodes that do nothing. UKAM runs
entirely in the driver process. Switch to a **Single Node** cluster with a large
driver.

---

## Progress output floods the job log

```python
matcher = AddressMatcher(..., show_progress="stages")
```

`"stages"` logs stage boundaries only. `"off"` suppresses output entirely.
`True` renders live progress, which is what you want interactively and never
what you want in a scheduled job.

---
title: Optimisation reference
---

# Optimisation reference

The benchmark applied changes **cumulatively** — each arm adds exactly one
change to the previous one, so each contribution is isolated. This page covers
the three that earn their place. The three that did not are on
[Tested and rejected](rejected.md).

!!! info "Correctness first"

    All six arms produced byte-identical results — fingerprint
    `5194b60df1884b954e1fb2c21ce845e9` across all 12 runs. The harness verifies
    this with row counts, matched counts and an order-independent MD5 digest of
    every `unique_id → resolved_canonical_id` pair, and fails the matrix if any
    arm diverges. Settings that *do* change the answer are on
    [Accuracy vs speed](accuracy.md).

---

## Step 0 — Prepare the canonical data once

Not an arm — a precondition. Every arm in the matrix read a **prepared**
canonical folder.

If you pass a raw relation as `canonical_addresses`, `AddressMatcher` redoes
cleaning, tokenisation, term-frequency derivation and inverted-index generation
on every run. For the full UK that is documented as roughly ten minutes. Paying
it per run dwarfs everything else on this page.

See [Preparing canonical data](../setup/prepare-canonical.md).

---

## Arm 1 — Baseline

Prepared canonical folder on DBFS, messy data on DBFS, `duckdb.connect()` with
no arguments, DuckDB defaults.

```python
con = duckdb.connect()
matcher = AddressMatcher(
    canonical_addresses="/dbfs/mnt/<path>/ukam_prepared_canonical",
    addresses_to_match=con.read_parquet("/dbfs/mnt/<path>/messy/*.parquet"),
    con=con,
)
```

**130.84 s** average match time, **132.28 s** wall, **0.32 GB** peak spill, and a
**16.37 s** spread between the two runs.

---

## Step 2 — DuckDB pragmas

```python
os.makedirs(LOCAL_TMP, exist_ok=True)
con.execute(f"SET temp_directory='{LOCAL_TMP}'")
con.execute("SET preserve_insertion_order=false")
```

**130.84 s → 126.79 s** (1.03×). Modest on the clock; better than that in
practice.

**`temp_directory`** tells DuckDB where to spill. Unset, spill lands in a `.tmp`
directory under the process working directory — on a Databricks notebook,
frequently a slow or network-backed path.

**`preserve_insertion_order=false`** releases DuckDB from maintaining row order
through operators that would otherwise buffer to guarantee it.

The two real wins are not in the mean:

- Peak spill went from **0.32 GB to zero**, and stayed at zero for every
  subsequent arm.
- Run-to-run spread collapsed from **16.37 s to 0.38 s**.

!!! question "Is dropping insertion order safe?"

    Yes. UKAM output is keyed by `unique_id`; nothing downstream depends on
    physical row order. Confirmed empirically — the fingerprint is computed with
    an explicit `ORDER BY`, and arms with and without the pragma produce the same
    digest.

**Cost:** none. **Skip it:** never.

---

## Step 3 — Copy the prepared canonical folder to local disk

The one that matters.

```python
if not os.path.exists(f"{LOCAL_PREPARED}/ukam_manifest.json"):
    dbutils.fs.cp(PREPARED_REMOTE, f"file:{LOCAL_PREPARED}/", recurse=True)

matcher = AddressMatcher(canonical_addresses=LOCAL_PREPARED, ...)
```

| Metric | Arm 2 (DBFS) | Arm 3 (local) | Change |
| --- | ---: | ---: | ---: |
| Match time | 126.79 s | **58.86 s** | **2.15×** |
| Wall time (incl. 6.91 s copy) | 128.18 s | **67.06 s** | **1.91×** |
| Exact-match stage | 16.64 s | **1.38 s** | **12×** |
| Splink stage | 80.69 s | **52.31 s** | 1.54× |
| Other work in `match()` | 29.46 s | **5.17 s** | 5.7× |

Against the untuned baseline: **2.22×** on match time, **1.97×** on wall time.

A DBFS mount is remote object storage behind a FUSE layer; `/local_disk0` is
NVMe attached to the VM. DuckDB reads the canonical Parquet repeatedly during
matching, so per-read latency is multiplied many times over.

The **exact-match stage is the tell**: 12–14× faster. That stage is almost pure
canonical reading, so it responds almost entirely to I/O latency. Splink improved
too, but less, because it is doing real compute alongside its reads.

**Cost:** one bulk copy per cluster lifetime — 6.91 s to 11.00 s in this test
for the same folder. **Skip it:** only if the canonical data does not fit on
local disk. The reference environment had 173.8 GB free.

!!! tip "Use `dbfs:/`, not `/dbfs/`, with `dbutils.fs.cp`"

    `dbutils.fs.cp` takes URI-style paths (`dbfs:/mnt/...`), and the destination
    needs the `file:` scheme to mean local disk. Mixing these up silently copies
    DBFS-to-DBFS.

---

## Keep the connection in memory

Deliberately *not* doing something.

```python
con = duckdb.connect()          # correct
# con = duckdb.connect(database="/local_disk0/ukam/ukam.duckdb")   # slower here
```

Arm 5 tested an on-disk database and was **25% slower** (59.03 s → 74.07 s). With
57.4 GB RAM and zero measured spill, persistence bought nothing and cost real
time. See [Tested and rejected](rejected.md#on-disk-duckdb-database) for when to
revisit this — the short answer is: when you have evidence of memory pressure,
not before.

---

## Mandatory hygiene: one connection per match run

Not an optimisation — a correctness requirement.

```python
con = duckdb.connect()
try:
    matcher = AddressMatcher(..., con=con)
    result = matcher.match()
    # ... consume result here, while the connection is open
finally:
    con.close()
```

Reusing a DuckDB connection across `AddressMatcher` runs causes Splink table
collisions. The harness opens a new connection for every arm and every repeat,
and re-reads the messy relation on that connection each time — a relation bound
to another connection would silently make the runs non-comparable.

---

## Summary

| Step | Change | Effect on match time | Verdict |
| --- | --- | ---: | --- |
| 0 | Prepared canonical folder | avoids ~10 min per run | **Essential** |
| 2 | `temp_directory` + `preserve_insertion_order` | 1.03×, spill → 0 | **Always** |
| 3 | Canonical on `/local_disk0` | **2.22×** | **The main win** |
| 4 | Messy on `/local_disk0` | 1.00× | [Skip](rejected.md#copying-the-messy-data-to-local-disk) unless large |
| 5 | On-disk DuckDB database | 0.80× | [Skip](rejected.md#on-disk-duckdb-database) unless spilling |
| 6 | Materialise canonical | 0.38× | [Skip](rejected.md#materialising-the-canonical-relations) |

The recommended configuration is **steps 0, 2 and 3**. Everything after that was
measured and rejected on this workload.

---
title: Preparing canonical data
---

# Preparing canonical data

Everything else on this site assumes you have a **prepared canonical folder**.
This is the single largest performance decision you will make, and it is made
before any of the I/O tuning matters.

## What preparation does

`prepare_canonical_folder()` performs address cleaning and tokenisation, term
frequency computation, and inverted index generation, then writes the results
to a folder:

| Artefact | Contents |
|---|---|
| `ukam_canonical_addresses.parquet` *(or `ukam_canonical_addresses_chunks/`)* | Cleaned and tokenised addresses |
| `ukam_term_frequencies.parquet` | Term frequency lookup table |
| `ukam_inverted_index.parquet` | Inverted index for candidate retrieval |
| `ukam_manifest.json` | Provenance metadata — version, row counts, hashes |

If you pass a **raw** relation as `canonical_addresses`, `AddressMatcher` does
all of that work inside every single match run. For the full UK, the project
documents this as a one-time preprocessing step of around ten minutes. Paying it
per run is the most expensive mistake available.

!!! success "Rule"

    Prepare once, offline, into durable storage. Every matching job then reads a
    prepared folder — never a raw dataset.

## Running preparation on Databricks

Run this as a **separate job**, on the same kind of classic single-node cluster,
whenever your canonical source is refreshed (for AddressBase, typically every
six weeks).

```python
import duckdb
from uk_address_matcher import prepare_canonical_folder

LOCAL_ROOT = "/local_disk0/ukam_prepare"
LOCAL_OUT  = f"{LOCAL_ROOT}/prepared"
REMOTE_OUT = "dbfs:/mnt/<your-path>/ukam_prepared_canonical"

con = duckdb.connect(database=f"{LOCAL_ROOT}/prepare.duckdb")   # (1)!
con.execute(f"SET temp_directory='{LOCAL_ROOT}/tmp'")
con.execute("SET preserve_insertion_order=false")

raw = con.read_parquet("/local_disk0/ukam_prepare/raw/*.parquet")  # (2)!

prepare_canonical_folder(
    raw,
    LOCAL_OUT,
    con=con,
    num_of_chunks=10,            # (3)!
    output_chunk_count=1,        # (4)!
    overwrite=True,
    show_progress="stages",
)

dbutils.fs.cp(f"file:{LOCAL_OUT}", REMOTE_OUT, recurse=True)   # (5)!
con.close()
```

1.  Preparation is a different workload from matching: it is memory-hungry and
    writes large intermediates, so an on-disk database on local SSD is a
    reasonable default here even though matching is faster in memory. This has
    **not** been benchmarked — a test matrix for `prepare_canonical_folder()`
    is planned.
2.  Copy the raw canonical data down to `/local_disk0` first, exactly as you do
    for the prepared folder in a matching job.
3.  Controls chunking during cleaning and term-frequency derivation. Lower it if
    memory is tight; set to `1` for no chunking on small datasets.
4.  Keep at `1` to write a single `ukam_canonical_addresses.parquet`. Values
    above 1 write hash-partitioned chunks under
    `ukam_canonical_addresses_chunks/`, which is worth trying if a single file
    is unwieldy for your storage layer.
5.  Write locally, then copy up. Writing large Parquet directly through the DBFS
    FUSE layer is slow and occasionally unreliable.

## Where to store the prepared folder

| Option | Notes |
|---|---|
| **Unity Catalog Volume** | Preferred on modern workspaces. Paths look like `dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_prepared_canonical`. |
| **DBFS mount** | What the benchmark used. Works fine as the *source* of the local copy. |
| **Workspace files / repo** | Not suitable — size limits and no efficient bulk copy. |

Whichever you choose, the prepared folder is read-only at match time, so it can
be shared across teams and jobs.

## Filtering without re-preparing

If you only match within a region, do not build a separate prepared folder.
`AddressMatcher` accepts a SQL filter applied after load:

```python
matcher = AddressMatcher(
    canonical_addresses=LOCAL_PREPARED,
    canonical_address_filter="postcode LIKE 'SW%'",
    addresses_to_match=messy,
    con=con,
)
```

This narrows the canonical side substantially and is one of the cheapest
speedups available when your messy data is geographically bounded.

!!! warning "This changes results"

    Filtering restricts what can be matched. If a messy address genuinely sits
    outside the filter it will now go unmatched. Treat this as an
    accuracy-affecting change and measure it as one — see
    [Accuracy vs speed](../reference/accuracy.md).

## Refresh checklist

- [ ] New canonical release downloaded to cloud storage
- [ ] Preparation job run on a classic cluster, output written to a **new**
      versioned folder (`.../ukam_prepared_canonical/2026-08/`)
- [ ] `ukam_manifest.json` present and row counts sane
- [ ] Matching jobs re-pointed at the new folder
- [ ] Local caches on long-lived interactive clusters cleared, or the cluster
      restarted, so stale copies on `/local_disk0` are not reused

That last point is the one people get wrong: the cache guard in the recommended
notebook keys on the *presence* of `ukam_manifest.json`, not its contents. When
you publish a new canonical version, either use a new local folder name or clear
the old one.

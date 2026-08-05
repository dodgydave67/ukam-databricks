---
title: Start here
---

# UK Address Matcher on Databricks

This is the production recipe for running
[`uk_address_matcher`](https://github.com/moj-analytical-services/uk_address_matcher)
(UKAM) efficiently on Databricks. It contains only the settings and code needed
to configure and run the matcher.

## Use this configuration

1. Run on **classic compute**. A single-node cluster is the most cost-efficient
   choice when available, but UKAM also runs on a multi-node cluster. Matching
   mainly uses the driver, so size the driver for the workload.
2. Build the canonical folder once with `prepare_canonical_folder()` and keep
   it in durable storage.
3. At the start of a cluster session, copy that prepared folder to
   `/local_disk0`.
4. Use a **fresh, in-memory DuckDB connection** for each matching run.
5. Put DuckDB temporary files on `/local_disk0` and disable insertion-order
   preservation.
6. Write the result locally, then copy it back to durable storage.

The downloadable notebook implements those choices directly.

[:material-play: Run the matcher](run.md){ .md-button .md-button--primary }
[:material-cog: Configure Databricks](setup.md){ .md-button }

## The important code

```python
import duckdb
from uk_address_matcher import AddressMatcher

con = duckdb.connect()
con.execute("SET temp_directory='/local_disk0/ukam/tmp'")
con.execute("SET preserve_insertion_order=false")

matcher = AddressMatcher(
    canonical_addresses="/local_disk0/ukam/prepared",
    addresses_to_match=con.read_parquet(MESSY_INPUT),
    con=con,
    show_progress="stages",
)

matches = matcher.match().matches()
```

The canonical folder must be prepared already and copied to the local path
before this block runs. The [Run UKAM](run.md) page provides the complete
notebook, including that copy and durable output.

!!! note "Matching behaviour"

    This setup changes where the work runs, not how UKAM scores addresses. It
    leaves blocking rules, thresholds, stages, and other accuracy-affecting
    options at their UKAM defaults.

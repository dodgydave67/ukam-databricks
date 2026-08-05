---
title: Writing results back
---

# Writing results back

Matching happens in DuckDB on ephemeral local disk. Getting results into durable
storage — and often into a Delta table — is the last mile, and it is easy to
undo your performance gains here.

## The rule: write locally, then copy

Writing a large Parquet file directly through the DBFS FUSE layer
(`/dbfs/mnt/...`) is slow and occasionally flaky. Write to `/local_disk0`, then
use `dbutils.fs.cp`.

```python
LOCAL_OUT  = "/local_disk0/ukam/matches.parquet"
REMOTE_OUT = "dbfs:/mnt/<your-path>/ukam_matches/matches.parquet"

con.execute(
    f"COPY ({result.matches().sql_query()}) TO '{LOCAL_OUT}' (FORMAT PARQUET)"
)
dbutils.fs.cp(f"file:{LOCAL_OUT}", REMOTE_OUT)
```

`result.matches()` returns a DuckDB relation; `.sql_query()` gives you the SQL
behind it, which `COPY` can consume directly without pulling the result into
Python.

## What is in the output

By default `matches()` returns the resolved match plus diagnostics:

| Column | Meaning |
|---|---|
| `unique_id` | Your messy record identifier |
| `resolved_canonical_id` | The matched canonical identifier — `NULL` if unmatched |
| `original_address_concat` | The messy address as supplied |
| `original_address_concat_canonical` | The matched canonical address |
| `match_reason` | Which stage produced the match |
| `match_weight` | Strength of evidence (Splink matches) |
| `distinguishability` | Gap to the runner-up candidate; `NULL` usually means only one candidate survived blocking |

Pass `all_columns=True` to `matches()` when you need the full intermediate set
for debugging. Do not write that version to production storage — it is much
wider.

!!! tip "Keep `distinguishability`"

    It is the single most useful field for triaging matches downstream. A high
    `match_weight` with a low `distinguishability` means the model was confident
    about a *set* of candidates, not about the winner.

## Writing to a Delta table

Two workable routes.

=== "Via Parquet (recommended)"

    Keeps DuckDB doing what it is good at and hands Spark a clean file.

    ```python
    con.execute(
        f"COPY ({result.matches().sql_query()}) TO '{LOCAL_OUT}' (FORMAT PARQUET)"
    )
    dbutils.fs.cp(f"file:{LOCAL_OUT}", f"{STAGING}/matches.parquet")

    (
        spark.read.parquet(f"{STAGING}/matches.parquet")
        .write.mode("overwrite")
        .saveAsTable("catalog.schema.ukam_matches")
    )
    ```

=== "Via Arrow (small results only)"

    Avoids the round trip through disk, but pulls the whole result into driver
    memory. Fine for tens of thousands of rows, not for millions.

    ```python
    arrow_table = result.matches().arrow()
    spark.createDataFrame(arrow_table.to_pandas()) \
        .write.mode("overwrite") \
        .saveAsTable("catalog.schema.ukam_matches")
    ```

## Type gotcha: `unique_id`

UKAM is permissive about `unique_id` types, but round-tripping through Parquet
and Spark is not. If your identifiers are strings that look like integers
(`"00123"`), cast explicitly on the way out so leading zeros survive:

```sql
COPY (
    SELECT CAST(unique_id AS VARCHAR) AS unique_id,
           CAST(resolved_canonical_id AS VARCHAR) AS resolved_canonical_id,
           * EXCLUDE (unique_id, resolved_canonical_id)
    FROM (<matches query>)
) TO '/local_disk0/ukam/matches.parquet' (FORMAT PARQUET)
```

This is also what the benchmark harness does when fingerprinting results, for
exactly the same reason: identifier types must not silently change between runs.

## Partitioned output for large jobs

If you match tens of millions of records, write partitioned Parquet rather than
one enormous file:

```python
con.execute(f"""
    COPY ({result.matches().sql_query()})
    TO '/local_disk0/ukam/matches'
    (FORMAT PARQUET, PARTITION_BY (match_reason), OVERWRITE_OR_IGNORE)
""")
dbutils.fs.cp("file:/local_disk0/ukam/matches", f"{OUTPUT_REMOTE}/matches",
              recurse=True)
```

Partitioning by `match_reason` is convenient operationally — the unmatched and
low-confidence partitions are the ones humans review.

## Clean up

```python
con.close()

import shutil
shutil.rmtree("/local_disk0/ukam/tmp", ignore_errors=True)
for suffix in ("", ".wal"):
    path = "/local_disk0/ukam/ukam.duckdb" + suffix
    if os.path.exists(path):
        os.remove(path)
```

Leave the `prepared/` and `messy/` copies in place on an interactive cluster —
that is the cache. Remove the database file and spill directory, which are large
and worthless after the run.

---
title: Chunk large inputs
---

# Chunk large inputs

You will normally need chunking when matching a messy input of more than one
million rows. Use this version sooner if the normal [Run UKAM](run.md)
notebook runs out of driver memory.

It splits the input into evenly sized chunks, matches one chunk at a time, and
saves each chunk immediately. If the notebook stops, run it again and it will
continue from the first unfinished chunk.

## Before you run it

Run cells 1–3 on [Run UKAM](run.md) first. The Parquet files at `MESSY_INPUT`
should contain:

| Column | Contents |
| --- | --- |
| `unique_id` | A unique ID for each address |
| `address_concat` | The full messy address |
| `postcode` | The postcode, or null/blank when missing |

## Cell 4 — Choose the output and chunk count

Change the output table. Start with 64 chunks; increase this only if an
individual chunk still runs out of memory.

```python
OUTPUT_TABLE = "<catalog>.<schema>.ukam_matches"
NUMBER_OF_CHUNKS = 64
```

Use a new output table if you change `NUMBER_OF_CHUNKS`.

## Cell 5 — Match in chunks

```python
import duckdb
from pyspark.sql import functions as F
from uk_address_matcher import AddressMatcher

messy_addresses = spark.read.parquet(MESSY_INPUT)

# Hashing the unique ID gives evenly sized chunks, including rows with no postcode.
chunked_addresses = messy_addresses.withColumn(
    "ukam_chunk_id",
    F.pmod(F.xxhash64("unique_id"), F.lit(NUMBER_OF_CHUNKS)),
)

# A rerun skips chunks that are already in the output table.
if spark.catalog.tableExists(OUTPUT_TABLE):
    completed_chunk_ids = {
        row["ukam_chunk_id"]
        for row in spark.table(OUTPUT_TABLE)
        .select("ukam_chunk_id")
        .distinct()
        .collect()
    }
else:
    completed_chunk_ids = set()

for chunk_id in range(NUMBER_OF_CHUNKS):
    if chunk_id in completed_chunk_ids:
        print(f"Skipping completed chunk {chunk_id}")
        continue

    print(f"Matching chunk {chunk_id + 1} of {NUMBER_OF_CHUNKS}")
    con = duckdb.connect()

    try:
        con.execute(f"SET temp_directory='{LOCAL_TMP}'")
        con.execute("SET preserve_insertion_order=false")

        addresses_to_match = con.from_arrow(
            chunked_addresses
            .where(F.col("ukam_chunk_id") == chunk_id)
            .select("unique_id", "address_concat", "postcode")
            .toArrow()
        )

        matcher = AddressMatcher(
            canonical_addresses=LOCAL_PREPARED,
            addresses_to_match=addresses_to_match,
            con=con,
            show_progress="stages",
        )

        chunk_matches = spark.createDataFrame(
            matcher.match().matches().to_arrow_table()
        ).withColumn("ukam_chunk_id", F.lit(chunk_id))

        chunk_matches.write.format("delta").mode("append").saveAsTable(OUTPUT_TABLE)
        print(f"Saved chunk {chunk_id}")
    finally:
        con.close()

print(f"All matches saved to {OUTPUT_TABLE}")
```

The two Arrow calls are only the efficient hand-off between Spark and UKAM's
driver-side DuckDB process. Replacing them with pandas would use more driver
memory; there are no Arrow settings to configure.

Each Delta append is transactional, so completed chunks remain saved if the
cluster or Python process stops later.

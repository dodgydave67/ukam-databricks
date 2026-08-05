---
title: Chunk large inputs
---

# Chunk large inputs

Large, messy inputs can exhaust the driver's memory during matching. Use this
chunked version when the normal [Run UKAM](run.md) notebook runs out of memory,
or for inputs around a million rows and above. The exact limit depends on the
data and the driver's available memory.

This pattern:

1. groups addresses by the first character of their postcode;
2. spreads missing postcodes across 64 smaller buckets;
3. converts one Spark chunk at a time to Arrow for UKAM;
4. writes each completed chunk immediately to a Delta table; and
5. skips completed chunks when the notebook is restarted.

## Before you run it

Complete cells 1–3 on [Run UKAM](run.md) first. This installs UKAM and copies
the prepared canonical data to `LOCAL_PREPARED` on the driver.

Your Spark DataFrame must be called `tdp_for_ukam` and contain:

| Column | Required value |
| --- | --- |
| `unique_id` | A unique ID for each messy address |
| `address_concat` | The full messy address |
| `postcode` | The postcode, or null/blank when missing |

## Cell 4 — Choose the output table

Change this one value:

```python
OUTPUT_TABLE = "<catalog>.<schema>.ukam_matches"
```

If the table already exists, the code reads its `postcode_chunked` column and
continues from the first unfinished chunk.

## Cell 5 — Create the chunks

```python
from pyspark.sql import functions as F

tdp_for_ukam_chunked = tdp_for_ukam.withColumn(
    "postcode_chunked",
    F.when(
        F.col("postcode").isNull() | (F.col("postcode") == ""),
        F.concat(
            F.lit("MISSING_"),
            F.lpad(
                F.pmod(F.xxhash64("unique_id"), F.lit(64)).cast("string"),
                2,
                "0",
            ),
        ),
    ).otherwise(F.left("postcode", F.lit(1))),
)

if spark.catalog.tableExists(OUTPUT_TABLE):
    completed_chunks = {
        row["postcode_chunked"]
        for row in spark.table(OUTPUT_TABLE)
        .select("postcode_chunked")
        .distinct()
        .collect()
    }
else:
    completed_chunks = set()

postcode_chunk_list = sorted(
    row["postcode_chunked"]
    for row in tdp_for_ukam_chunked
    .select("postcode_chunked")
    .distinct()
    .collect()
)

print(f"Completed: {len(completed_chunks)} of {len(postcode_chunk_list)} chunks")
```

## Cell 6 — Match and save each chunk

The matching stages and thresholds below are the ones used in
[discussion #438](https://github.com/moj-analytical-services/uk_address_matcher/discussions/438).
They affect which addresses match, not memory usage. Use them only if they are
your approved matching rules.

```python
import duckdb
from uk_address_matcher import (
    AddressMatcher,
    ExactMatchStage,
    PeeledAddressStage,
    SplinkStage,
    UniqueTrigramStage,
)

for number, chunk_name in enumerate(postcode_chunk_list, start=1):
    if chunk_name in completed_chunks:
        print(f"Skipping completed chunk: {chunk_name}")
        continue

    print(f"Processing {number} of {len(postcode_chunk_list)}: {chunk_name}")
    con = duckdb.connect()

    try:
        con.execute(f"SET temp_directory='{LOCAL_TMP}'")
        con.execute("SET preserve_insertion_order=false")

        chunk_arrow = (
            tdp_for_ukam_chunked
            .where(F.col("postcode_chunked") == chunk_name)
            .select("unique_id", "address_concat", "postcode")
            .toArrow()
        )
        print(f"Rows in chunk: {chunk_arrow.num_rows:,}")

        matcher = AddressMatcher(
            canonical_addresses=LOCAL_PREPARED,
            addresses_to_match=con.from_arrow(chunk_arrow),
            con=con,
            stages=[
                ExactMatchStage(),
                PeeledAddressStage(),
                UniqueTrigramStage(),
                SplinkStage(
                    final_match_weight_threshold=10.0,
                    final_distinguishability_threshold=1.0,
                ),
            ],
        )

        matches_arrow = matcher.match().matches().to_arrow_table()
        result = (
            spark.createDataFrame(matches_arrow)
            .withColumn("postcode_chunked", F.lit(chunk_name))
        )

        result.write.format("delta").mode("append").saveAsTable(OUTPUT_TABLE)
        print(f"Saved chunk: {chunk_name}")

        del matcher, matches_arrow, chunk_arrow, result
    finally:
        con.close()

print(f"All chunks saved to {OUTPUT_TABLE}")
```

Each append to the Delta table is transactional. If Python or the cluster
stops later, previously saved chunks remain available and the next run skips
them.

## If one chunk still runs out of memory

Postcode-first-letter chunks are not all the same size. If one is still too
large, split the input into more hash buckets or use a larger driver, then
write to a new output table and rerun from the beginning.

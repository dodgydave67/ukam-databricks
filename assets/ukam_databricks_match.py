# Databricks notebook source

# COMMAND ----------
# MAGIC %pip install uk_address_matcher==1.2.4
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
# Edit these three values.
PREPARED_REMOTE = (
    "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_prepared_canonical"
)
MESSY_INPUT = "/Volumes/<catalog>/<schema>/<volume>/messy/*.parquet"
OUTPUT_FOLDER = "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_matches"

# COMMAND ----------
import os

import duckdb
from uk_address_matcher import AddressMatcher

LOCAL_PREPARED = "/local_disk0/ukam/prepared"
LOCAL_TMP = "/local_disk0/ukam/tmp"
LOCAL_OUTPUT = "/local_disk0/ukam/matches.parquet"

os.makedirs(LOCAL_PREPARED, exist_ok=True)
os.makedirs(LOCAL_TMP, exist_ok=True)

if not os.path.exists(f"{LOCAL_PREPARED}/ukam_manifest.json"):
    dbutils.fs.cp(  # noqa: F821
        PREPARED_REMOTE,
        f"file:{LOCAL_PREPARED}/",
        recurse=True,
    )

# COMMAND ----------
con = duckdb.connect()
con.execute(f"SET temp_directory='{LOCAL_TMP}'")
con.execute("SET preserve_insertion_order=false")

matcher = AddressMatcher(
    canonical_addresses=LOCAL_PREPARED,
    addresses_to_match=con.read_parquet(MESSY_INPUT),
    con=con,
    show_progress="stages",
)

matches = matcher.match().matches()

# COMMAND ----------
matches.write_parquet(LOCAL_OUTPUT, overwrite=True)

dbutils.fs.mkdirs(OUTPUT_FOLDER)  # noqa: F821
dbutils.fs.rm(f"{OUTPUT_FOLDER}/matches.parquet")  # noqa: F821
dbutils.fs.cp(  # noqa: F821
    f"file:{LOCAL_OUTPUT}",
    f"{OUTPUT_FOLDER}/matches.parquet",
)

con.close()
print(f"Saved {OUTPUT_FOLDER}/matches.parquet")

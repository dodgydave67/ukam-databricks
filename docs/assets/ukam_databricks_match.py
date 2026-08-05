# Databricks notebook source
# UK Address Matcher - production Databricks configuration
#
# Edit the three remote/input paths below, then run the notebook top to bottom.
# It contains one production execution path.

# COMMAND ----------
# MAGIC %pip install uk_address_matcher==1.2.4
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
# EDIT THESE VALUES
PREPARED_REMOTE = (
    "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_prepared_canonical"
)
MESSY_INPUT = "/Volumes/<catalog>/<schema>/<volume>/messy/*.parquet"
OUTPUT_REMOTE_DIR = (
    "dbfs:/Volumes/<catalog>/<schema>/<volume>/ukam_matches"
)

# Set True for one run after publishing a new prepared canonical folder to a
# long-lived cluster. Job clusters copy automatically because local disk starts
# empty.
REFRESH_CANONICAL = False

# Driver-local working paths. Do not change these unless the job shares the
# cluster with another UKAM job.
LOCAL_ROOT = "/local_disk0/ukam"
LOCAL_PREPARED = f"{LOCAL_ROOT}/prepared"
LOCAL_TMP = f"{LOCAL_ROOT}/tmp"
LOCAL_OUTPUT = f"{LOCAL_ROOT}/matches.parquet"

# COMMAND ----------
import os
import shutil

import duckdb
from uk_address_matcher import AddressMatcher

try:
    os.statvfs("/local_disk0")
except OSError as exc:
    raise RuntimeError(
        "This notebook requires a classic Databricks cluster with "
        "/local_disk0. Serverless compute is not supported."
    ) from exc

# COMMAND ----------
# Copy the prepared canonical folder once per cluster lifetime.
manifest = os.path.join(LOCAL_PREPARED, "ukam_manifest.json")

if REFRESH_CANONICAL or not os.path.exists(manifest):
    shutil.rmtree(LOCAL_PREPARED, ignore_errors=True)
    os.makedirs(LOCAL_PREPARED, exist_ok=True)
    dbutils.fs.cp(  # noqa: F821
        PREPARED_REMOTE,
        f"file:{LOCAL_PREPARED}/",
        recurse=True,
    )

if not os.path.exists(manifest):
    raise RuntimeError(
        "The local canonical copy is incomplete: ukam_manifest.json "
        "was not found. Check PREPARED_REMOTE."
    )

os.makedirs(LOCAL_TMP, exist_ok=True)

# COMMAND ----------
# Use a fresh, in-memory connection for this match.
con = duckdb.connect()
con.execute(f"SET temp_directory='{LOCAL_TMP}'")
con.execute("SET preserve_insertion_order=false")

messy = con.read_parquet(MESSY_INPUT)

matcher = AddressMatcher(
    canonical_addresses=LOCAL_PREPARED,
    addresses_to_match=messy,
    con=con,
    show_progress="stages",
)

matches = matcher.match().matches()

# COMMAND ----------
# Write locally, then copy to durable storage.
if os.path.exists(LOCAL_OUTPUT):
    os.remove(LOCAL_OUTPUT)

con.execute(
    f"COPY ({matches.sql_query()}) "
    f"TO '{LOCAL_OUTPUT}' (FORMAT PARQUET)"
)

dbutils.fs.mkdirs(OUTPUT_REMOTE_DIR)  # noqa: F821
dbutils.fs.cp(  # noqa: F821
    f"file:{LOCAL_OUTPUT}",
    f"{OUTPUT_REMOTE_DIR}/matches.parquet",
)

con.close()
print(f"Wrote {OUTPUT_REMOTE_DIR}/matches.parquet")

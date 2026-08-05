# Databricks notebook source
# =============================================================================
# UK Address Matcher - recommended Databricks configuration
#
# Benchmarked in:
#   https://github.com/moj-analytical-services/uk_address_matcher/discussions/463
#
# APPLIES (the configuration that won the test matrix, arm 3):
#   0. prepared canonical folder, built once offline
#   2. DuckDB pragmas: temp_directory on /local_disk0 + preserve_insertion_order
#   3. prepared canonical folder copied to /local_disk0     <- 2.22x speed-up
#      ... with an IN-MEMORY DuckDB connection
#
# DELIBERATELY OMITTED (benchmarked slower - flags below to test yourself):
#   4. messy data copied locally      no measurable effect at 6,367 rows
#   5. on-disk DuckDB database        25% slower; no spill to relieve
#   6. materialised canonical tables  slower than the untuned baseline
#
# REQUIRES CLASSIC COMPUTE. Serverless has no /local_disk0.
# Single-node cluster recommended: UKAM runs entirely in the driver process.
#
# Reference environment: DBR 17.3, 8 cores, 57.4 GB RAM, DuckDB 1.5.0,
# uk_address_matcher 1.2.4, Splink 4.0.16.
# =============================================================================

# COMMAND ----------
# MAGIC %pip install uk_address_matcher==1.2.4
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
# -----------------------------------------------------------------------------
# CELL 1 - CONFIGURATION. Edit this block only.
# -----------------------------------------------------------------------------

# Remote (durable) locations
PREPARED_REMOTE = "dbfs:/mnt/<your-path>/ukam_prepared_canonical"
MESSY_REMOTE = "dbfs:/mnt/<your-path>/messy"
MESSY_POSIX = "/dbfs/mnt/<your-path>/messy"
OUTPUT_REMOTE = "dbfs:/mnt/<your-path>/ukam_matches"
MESSY_GLOB = "*.parquet"

# Local (ephemeral) working locations
LOCAL_ROOT = "/local_disk0/ukam"
LOCAL_PREPARED = f"{LOCAL_ROOT}/prepared"
LOCAL_MESSY = f"{LOCAL_ROOT}/messy"
LOCAL_TMP = f"{LOCAL_ROOT}/tmp"
LOCAL_DB = f"{LOCAL_ROOT}/ukam.duckdb"
LOCAL_OUT = f"{LOCAL_ROOT}/matches.parquet"

# Optional
CANONICAL_FILTER = None  # e.g. "postcode LIKE 'SW%'" - CHANGES RESULTS
FRESH_LOCAL_COPY = False  # True forces a re-copy of the prepared folder

# Benchmarked SLOWER. Leave False unless testing on your own data.
COPY_MESSY_LOCALLY = False  # arm 4
USE_ONDISK_DB = False  # arm 5
MEMORY_LIMIT = None  # e.g. "48GB"; None lets DuckDB decide

# COMMAND ----------
# -----------------------------------------------------------------------------
# CELL 2 - PREFLIGHT
# -----------------------------------------------------------------------------
import os
import shutil
import time

import duckdb

import uk_address_matcher
from uk_address_matcher import AddressMatcher

try:
    _stat = os.statvfs("/local_disk0")
except OSError as exc:
    raise RuntimeError(
        "/local_disk0 is not available - this notebook requires CLASSIC "
        "compute. Serverless will not work."
    ) from exc

FREE_GB = (_stat.f_bavail * _stat.f_frsize) / 1024**3

ENV = {
    "databricks_runtime": os.environ.get("DATABRICKS_RUNTIME_VERSION"),
    "cpu_count": os.cpu_count(),
    "duckdb_version": duckdb.__version__,
    "ukam_version": uk_address_matcher.__version__,
    "local_disk0_free_gb": round(FREE_GB, 2),
}
try:
    import splink

    ENV["splink_version"] = splink.__version__
except ImportError:
    pass
try:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                ENV["mem_total_gb"] = round(int(line.split()[1]) / 1024**2, 1)
                break
except OSError:
    pass

print("Environment:")
for k, v in ENV.items():
    print(f"  {k}: {v}")

# COMMAND ----------
# -----------------------------------------------------------------------------
# CELL 3 - STAGE THE CANONICAL FOLDER ON LOCAL DISK  (the 2.22x step)
# -----------------------------------------------------------------------------


def stage(remote_uri, local_dir, marker=None, force=False):
    """Copy remote_uri to local_dir, skipping if already present.

    Returns seconds spent copying, or None if the copy was skipped.
    """
    probe = os.path.join(local_dir, marker) if marker else local_dir
    if os.path.exists(probe) and not force:
        print(f"cached: {local_dir}")
        return None

    if os.path.isdir(local_dir):
        shutil.rmtree(local_dir, ignore_errors=True)
    os.makedirs(local_dir, exist_ok=True)

    t = time.perf_counter()
    dbutils.fs.cp(remote_uri, f"file:{local_dir}/", recurse=True)  # noqa: F821
    elapsed = round(time.perf_counter() - t, 2)
    print(f"copied {remote_uri} -> {local_dir} in {elapsed}s")
    return elapsed


os.makedirs(LOCAL_TMP, exist_ok=True)

copy_canonical_s = stage(
    PREPARED_REMOTE,
    LOCAL_PREPARED,
    marker="ukam_manifest.json",  # written last by prepare_canonical_folder
    force=FRESH_LOCAL_COPY,
)

if COPY_MESSY_LOCALLY:
    copy_messy_s = stage(MESSY_REMOTE, LOCAL_MESSY)
    messy_path = f"{LOCAL_MESSY}/{MESSY_GLOB}"
else:
    copy_messy_s = None
    messy_path = f"{MESSY_POSIX}/{MESSY_GLOB}"

# COMMAND ----------
# -----------------------------------------------------------------------------
# CELL 4 - TUNED CONNECTION
# -----------------------------------------------------------------------------

# A FRESH connection per match run. Reusing one across runs causes Splink
# table-name collisions.
if USE_ONDISK_DB:
    for _p in (LOCAL_DB, LOCAL_DB + ".wal"):
        if os.path.isdir(_p):
            shutil.rmtree(_p, ignore_errors=True)
        elif os.path.exists(_p):
            os.remove(_p)
    con = duckdb.connect(database=LOCAL_DB)
else:
    con = duckdb.connect()  # in-memory: faster in the benchmark

con.execute(f"SET temp_directory='{LOCAL_TMP}'")
con.execute("SET preserve_insertion_order=false")
if MEMORY_LIMIT:
    con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")

print(
    {
        s: con.execute(f"SELECT current_setting('{s}')").fetchone()[0]
        for s in ("threads", "memory_limit", "temp_directory")
    }
)

# COMMAND ----------
# -----------------------------------------------------------------------------
# CELL 5 - MATCH
# -----------------------------------------------------------------------------

# Read messy data on THIS connection.
messy = con.read_parquet(messy_path)
print(f"messy rows: {messy.count('*').fetchone()[0]:,}")

kwargs = dict(
    canonical_addresses=LOCAL_PREPARED,
    addresses_to_match=messy,
    con=con,
    show_progress="stages",
)
if CANONICAL_FILTER:
    kwargs["canonical_address_filter"] = CANONICAL_FILTER

matcher = AddressMatcher(**kwargs)

_t = time.perf_counter()
result = matcher.match()
match_seconds = round(time.perf_counter() - _t, 2)

print(
    f"match: {match_seconds}s "
    f"(copy_canonical: {copy_canonical_s}s, copy_messy: {copy_messy_s}s)"
)

# COMMAND ----------
# -----------------------------------------------------------------------------
# CELL 6 - INSPECT
# -----------------------------------------------------------------------------
matches_sql = result.matches().sql_query()

n_rows, n_matched = con.execute(
    f"SELECT COUNT(*), COUNT(resolved_canonical_id) FROM ({matches_sql})"
).fetchone()
print(f"matched {n_matched:,} / {n_rows:,} ({n_matched / n_rows:.1%})")

con.execute(
    f"SELECT match_reason, COUNT(*) AS n FROM ({matches_sql}) "
    "GROUP BY 1 ORDER BY n DESC"
).show()

# Order-independent fingerprint - log this so you can prove later which
# configuration produced which output. Reference run: 5194b60df1884b954e1fb2c21ce845e9
digest = con.execute(
    f"""
    SELECT md5(string_agg(
        CAST(unique_id AS VARCHAR) || '>' ||
        COALESCE(CAST(resolved_canonical_id AS VARCHAR), ''),
        '|' ORDER BY CAST(unique_id AS VARCHAR)
    ))
    FROM ({matches_sql})
    """
).fetchone()[0]
print(f"result digest: {digest}")

# COMMAND ----------
# -----------------------------------------------------------------------------
# CELL 7 - WRITE RESULTS BACK, THEN CLEAN UP
# -----------------------------------------------------------------------------
con.execute(f"COPY ({matches_sql}) TO '{LOCAL_OUT}' (FORMAT PARQUET)")

dbutils.fs.mkdirs(OUTPUT_REMOTE)  # noqa: F821
dbutils.fs.cp(f"file:{LOCAL_OUT}", f"{OUTPUT_REMOTE}/matches.parquet")  # noqa: F821
print(f"written to {OUTPUT_REMOTE}/matches.parquet")

con.close()

# Remove spill and any database file; KEEP prepared/ as the cache.
shutil.rmtree(LOCAL_TMP, ignore_errors=True)
for _p in (LOCAL_DB, LOCAL_DB + ".wal"):
    if os.path.exists(_p):
        os.remove(_p)

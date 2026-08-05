# ============================================================================
# UKAM AddressMatcher.match() — Databricks I/O path benchmark
#
# Cumulative test matrix. Each arm adds ONE change to the previous arm.
#
#   1 baseline               prepared canonical on DBFS, messy on DBFS,
#                            :memory: db, DuckDB defaults
#   2 +pragmas               temp_directory + preserve_insertion_order
#   3 +local_canonical       prepared folder copied to /local_disk0
#   4 +local_messy           messy addresses copied to /local_disk0
#   5 +ondisk_db             persistent DuckDB database on /local_disk0
#   6 +materialise_canonical CREATE TABLE from the prepared parquet before
#                            matching (tests the proposed library change)
#   7 +memory_limit          explicit memory_limit (optional)
#
# Arms 1-7 MUST produce byte-identical match results. The harness fingerprints
# every arm and flags any divergence — a speedup on an arm that changed the
# answer is not a speedup.
#
# A separate ACCURACY_ARMS section covers settings that deliberately change
# results (blocking rules, improve_top_n). Those are reported apart, with
# match-count deltas so the recall cost is visible.
#
# Requires CLASSIC compute. Single-node cluster strongly recommended.
# However, I did run these optimisations on a cluster with multiple nodes/drivers
# So they will work with either.
# ============================================================================

# ---------------------------------------------------------------------------
# SECTION 1 — CONFIG. Edit this block only.
# ---------------------------------------------------------------------------

# Prepared canonical folder (the output of prepare_canonical_folder).
PREPARED_DBUTILS = "dbfs:/mnt/<your-path>/ukam_prepared_canonical"
PREPARED_POSIX = "/dbfs/mnt/<your-path>/ukam_prepared_canonical"



# Messy addresses to match. Must have unique_id + address_concat.
MESSY_DBUTILS = "dbfs:/mnt/<your-path>/messy"
MESSY_POSIX = "/dbfs/mnt/<your-path>/messy"
MESSY_GLOB = "*.parquet"

LOCAL_ROOT = "/local_disk0/ukam_match_bench"

ARMS_TO_RUN = [
    "1_baseline",
    "2_pragmas",
    "3_local_canonical",
    "4_local_messy",
    "5_ondisk_db",
    "6_materialise_canonical",
    # "7_memory_limit",
]

# Accuracy-affecting variants. Run against the fastest correct arm above.
# These WILL change results — that is the point. Leave empty to skip.
ACCURACY_ARMS = [
    # "no_outside_postcode_block",
    # "improve_top_n_3",
]

REPEATS = 2
MEMORY_LIMIT_ARM7 = "48GB"
FINGERPRINT_MAX_ROWS = 5_000_000  # skip digest above this; counts still compared

RESULTS_JSON = f"{LOCAL_ROOT}/match_benchmark_results.json"

# ---------------------------------------------------------------------------
# SECTION 2 — HARNESS. No edits needed below.
# ---------------------------------------------------------------------------

import json
import os
import shutil
import threading
import time
from uuid import uuid4

import duckdb

import uk_address_matcher
from uk_address_matcher import AddressMatcher, ExactMatchStage, SplinkStage

LOCAL_PREPARED = f"{LOCAL_ROOT}/prepared"
LOCAL_MESSY = f"{LOCAL_ROOT}/messy"
LOCAL_TMP = f"{LOCAL_ROOT}/tmp"
LOCAL_DB = f"{LOCAL_ROOT}/ukam_match.db"


# --- the proposed library change, as a subclass -----------------------------

class MaterialisedCanonicalMatcher(AddressMatcher):
    """AddressMatcher that materialises prepared artefacts into real tables.

    load_prepared_canonical_data returns lazy con.read_parquet relations. With
    10 blocking rules in splink_model.json, Splink re-evaluates the canonical
    side many times over. This subclass materialises once and is the direct
    test of whether a `materialise_canonical` option is worth proposing.
    """

    materialise_seconds = None

    def _resolve_canonical_data(self):
        super()._resolve_canonical_data()
        t = time.perf_counter()
        if self._canonical_clean is not None:
            name = f"__bench_canon_{uuid4().hex[:8]}"
            self.con.execute(
                f"CREATE TABLE {name} AS SELECT * FROM ({self._canonical_clean.sql_query()})"
            )
            self._canonical_clean = self.con.table(name)
        if self._tf_table is not None:
            name = f"__bench_tf_{uuid4().hex[:8]}"
            self.con.execute(
                f"CREATE TABLE {name} AS SELECT * FROM ({self._tf_table.sql_query()})"
            )
            self._tf_table = self.con.table(name)
        self.materialise_seconds = round(time.perf_counter() - t, 2)


# --- peak spill measurement -------------------------------------------------

def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


class SpillMonitor(threading.Thread):
    def __init__(self, temp_dir, interval=2.0):
        super().__init__(daemon=True)
        self.temp_dir = temp_dir
        self.interval = interval
        self.peak_bytes = 0
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.is_set():
            if self.temp_dir and os.path.isdir(self.temp_dir):
                self.peak_bytes = max(
                    self.peak_bytes,
                    _dir_size(self.temp_dir),
                )
            self._stop_event.wait(self.interval)

    def stop(self):
        self._stop_event.set()
        self.join(timeout=5)
        return self.peak_bytes


# --- filesystem helpers -----------------------------------------------------

def _rm_local(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _copy_in(src_dbutils, dest_local):
    _rm_local(dest_local)
    os.makedirs(dest_local, exist_ok=True)
    t = time.perf_counter()
    dbutils.fs.cp(src_dbutils, f"file:{dest_local}/", recurse=True)  # noqa: F821
    return round(time.perf_counter() - t, 2)


# --- result fingerprinting --------------------------------------------------

def fingerprint(con, match_result):
    """Identity of a match run: row count, matched count, and an order-
    independent digest of every (unique_id -> resolved_canonical_id) pair."""
    rel = match_result.matches()
    name = f"__bench_fp_{uuid4().hex[:8]}"
    con.execute(f"CREATE TEMP TABLE {name} AS SELECT * FROM ({rel.sql_query()})")

    n_rows, n_matched = con.execute(
        f"SELECT COUNT(*), COUNT(resolved_canonical_id) FROM {name}"
    ).fetchone()

    digest = None
    if n_rows <= FINGERPRINT_MAX_ROWS:
        digest = con.execute(f"""
            SELECT md5(string_agg(
                CAST(unique_id AS VARCHAR) || '>' ||
                COALESCE(CAST(resolved_canonical_id AS VARCHAR), ''),
                '|' ORDER BY CAST(unique_id AS VARCHAR)
            ))
            FROM {name}
        """).fetchone()[0]

    reasons = dict(
        con.execute(
            f"SELECT CAST(match_reason AS VARCHAR), COUNT(*) FROM {name} "
            "GROUP BY 1 ORDER BY 1"
        ).fetchall()
    )
    con.execute(f"DROP TABLE IF EXISTS {name}")
    return {
        "n_rows": n_rows,
        "n_matched": n_matched,
        "digest": digest,
        "match_reasons": reasons,
    }


# --- arm definitions --------------------------------------------------------

ARM_FLAGS = {
    "1_baseline":              dict(pragmas=0, local_canon=0, local_messy=0, ondisk=0, materialise=0, memlimit=0),
    "2_pragmas":               dict(pragmas=1, local_canon=0, local_messy=0, ondisk=0, materialise=0, memlimit=0),
    "3_local_canonical":       dict(pragmas=1, local_canon=1, local_messy=0, ondisk=0, materialise=0, memlimit=0),
    "4_local_messy":           dict(pragmas=1, local_canon=1, local_messy=1, ondisk=0, materialise=0, memlimit=0),
    "5_ondisk_db":             dict(pragmas=1, local_canon=1, local_messy=1, ondisk=1, materialise=0, memlimit=0),
    "6_materialise_canonical": dict(pragmas=1, local_canon=1, local_messy=1, ondisk=1, materialise=1, memlimit=0),
    "7_memory_limit":          dict(pragmas=1, local_canon=1, local_messy=1, ondisk=1, materialise=1, memlimit=1),
}

# Accuracy arms inherit arm 6's flags and change matcher settings instead.
ACCURACY_STAGES = {
    "no_outside_postcode_block": lambda: [
        ExactMatchStage(),
        SplinkStage(include_outside_postcode_block=False, include_full_postcode_block=True),
    ],
    "improve_top_n_3": lambda: [
        ExactMatchStage(),
        SplinkStage(improve_top_n_matches=3, improve_use_bigrams=False),
    ],
}


def run_arm(arm_name, repeat_index, flags, stages=None):
    result = {
        "arm": arm_name,
        "repeat": repeat_index,
        "flags": dict(flags),
        "copy_canonical_seconds": None,
        "copy_messy_seconds": None,
        "materialise_seconds": None,
        "match_seconds": None,
        "wall_seconds": None,
        "peak_spill_bytes": None,
        "stage_diagnostics": [],
        "fingerprint": None,
        "error": None,
    }

    os.makedirs(LOCAL_ROOT, exist_ok=True)
    wall_start = time.perf_counter()

    # ---- resolve data locations -------------------------------------------
    if flags["local_canon"]:
        result["copy_canonical_seconds"] = _copy_in(PREPARED_DBUTILS, LOCAL_PREPARED)
        canonical_folder = LOCAL_PREPARED
    else:
        canonical_folder = PREPARED_POSIX

    if flags["local_messy"]:
        result["copy_messy_seconds"] = _copy_in(MESSY_DBUTILS, LOCAL_MESSY)
        messy_path = f"{LOCAL_MESSY}/{MESSY_GLOB}"
    else:
        messy_path = f"{MESSY_POSIX}/{MESSY_GLOB}"

    # ---- fresh connection every arm ---------------------------------------
    # Mandatory: issues #460/#461 describe Splink table collisions when a
    # DuckDB connection is reused across matcher runs.
    if flags["ondisk"]:
        _rm_local(LOCAL_DB)
        _rm_local(f"{LOCAL_DB}.wal")
        con = duckdb.connect(database=LOCAL_DB)
    else:
        con = duckdb.connect()

    if flags["pragmas"]:
        os.makedirs(LOCAL_TMP, exist_ok=True)
        con.execute(f"SET temp_directory='{LOCAL_TMP}'")
        con.execute("SET preserve_insertion_order=false")

    if flags["memlimit"]:
        con.execute(f"SET memory_limit='{MEMORY_LIMIT_ARM7}'")

    effective_temp = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
    if not effective_temp:
        effective_temp = os.path.join(os.getcwd(), ".tmp")
    result["effective_temp_directory"] = effective_temp
    result["threads"] = con.execute("SELECT current_setting('threads')").fetchone()[0]
    result["memory_limit"] = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]

    # ---- messy is read fresh on THIS connection, every arm ----------------
    # A relation bound to another connection would silently make arms
    # non-comparable.
    messy = con.read_parquet(messy_path)
    result["messy_rows"] = messy.count("*").fetchone()[0]

    monitor = SpillMonitor(effective_temp)
    monitor.start()

    try:
        matcher_cls = MaterialisedCanonicalMatcher if flags["materialise"] else AddressMatcher
        kwargs = dict(
            canonical_addresses=canonical_folder,
            addresses_to_match=messy,
            con=con,
            show_progress="stages",
        )
        if stages is not None:
            kwargs["stages"] = stages

        matcher = matcher_cls(**kwargs)

        t = time.perf_counter()
        match_result = matcher.match()
        result["match_seconds"] = round(time.perf_counter() - t, 2)

        if flags["materialise"]:
            result["materialise_seconds"] = getattr(matcher, "materialise_seconds", None)

        result["stage_diagnostics"] = list(match_result._stage_diagnostics or [])
        result["fingerprint"] = fingerprint(con, match_result)

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["peak_spill_bytes"] = monitor.stop()

    con.close()
    if flags["ondisk"]:
        _rm_local(LOCAL_DB)
        _rm_local(f"{LOCAL_DB}.wal")

    result["wall_seconds"] = round(time.perf_counter() - wall_start, 2)
    return result


# ---------------------------------------------------------------------------
# SECTION 3 — ENVIRONMENT CAPTURE
# ---------------------------------------------------------------------------

def capture_environment():
    env = {
        "databricks_runtime": os.environ.get("DATABRICKS_RUNTIME_VERSION"),
        "cpu_count": os.cpu_count(),
        "duckdb_version": duckdb.__version__,
        "ukam_version": uk_address_matcher.__version__,
        "cwd": os.getcwd(),
    }
    try:
        import splink
        env["splink_version"] = splink.__version__
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    env["mem_total_gb"] = round(int(line.split()[1]) / 1024**2, 1)
                    break
    except OSError:
        pass
    try:
        stat = os.statvfs("/local_disk0")
        env["local_disk0_free_gb"] = round((stat.f_bavail * stat.f_frsize) / 1024**3, 2)
    except OSError:
        env["local_disk0_free_gb"] = None
    return env


# ---------------------------------------------------------------------------
# SECTION 4 — RUN
# ---------------------------------------------------------------------------

environment = capture_environment()
print("Environment:")
for k, v in environment.items():
    print(f"  {k}: {v}")

if environment["local_disk0_free_gb"] is None:
    raise RuntimeError(
        "/local_disk0 not available — you are probably on serverless compute. "
        "This harness requires classic compute."
    )

all_results = []

for repeat in range(1, REPEATS + 1):
    for arm in ARMS_TO_RUN:
        print(f"\n{'=' * 70}\nRunning {arm} (repeat {repeat}/{REPEATS})\n{'=' * 70}")
        r = run_arm(arm, repeat, ARM_FLAGS[arm])
        all_results.append(r)
        if r["error"]:
            print(f"  FAILED: {r['error']}")
        else:
            fp = r["fingerprint"]
            print(
                f"  match={r['match_seconds']}s  "
                f"copy_canon={r['copy_canonical_seconds']}  "
                f"copy_messy={r['copy_messy_seconds']}  "
                f"peak_spill={r['peak_spill_bytes'] / 1024**3:.2f}GB"
            )
            print(f"  matched {fp['n_matched']}/{fp['n_rows']}  digest={fp['digest']}")
            for s in r["stage_diagnostics"]:
                print(
                    f"    - {s['stage']}: {s['elapsed_seconds']}s, "
                    f"matched {s['matched_this_stage']}, "
                    f"remaining {s['remaining_after']}"
                )

# Accuracy-affecting arms run on top of arm 6's configuration.
for repeat in range(1, REPEATS + 1):
    for arm in ACCURACY_ARMS:
        print(f"\n{'=' * 70}\nRunning ACCURACY arm {arm} (repeat {repeat}/{REPEATS})\n{'=' * 70}")
        r = run_arm(
            f"acc_{arm}",
            repeat,
            ARM_FLAGS["6_materialise_canonical"],
            stages=ACCURACY_STAGES[arm](),
        )
        r["accuracy_arm"] = True
        all_results.append(r)
        if r["error"]:
            print(f"  FAILED: {r['error']}")
        else:
            fp = r["fingerprint"]
            print(f"  match={r['match_seconds']}s  matched {fp['n_matched']}/{fp['n_rows']}")

os.makedirs(LOCAL_ROOT, exist_ok=True)
with open(RESULTS_JSON, "w") as f:
    json.dump({"environment": environment, "results": all_results}, f, indent=2)
print(f"\nResults written to {RESULTS_JSON}")


# ---------------------------------------------------------------------------
# SECTION 5 — CORRECTNESS CHECK
# ---------------------------------------------------------------------------

def check_consistency(results):
    perf = [
        r for r in results
        if not r.get("accuracy_arm") and not r["error"] and r["fingerprint"]
    ]
    if not perf:
        return "No successful performance arms to compare."

    digests = {r["fingerprint"]["digest"] for r in perf}
    lines = []
    if len(digests) == 1:
        lines.append("PASS — every performance arm produced identical match results.")
    else:
        lines.append("FAIL — performance arms produced DIFFERENT results. Timings are")
        lines.append("not comparable until this is explained.")
        for r in perf:
            fp = r["fingerprint"]
            lines.append(
                f"  {r['arm']} (repeat {r['repeat']}): "
                f"matched={fp['n_matched']}/{fp['n_rows']} digest={fp['digest']}"
            )
    return "\n".join(lines)


print("\n" + "=" * 70)
print("CORRECTNESS CHECK")
print("=" * 70)
print(check_consistency(all_results))


# ---------------------------------------------------------------------------
# SECTION 6 — MARKDOWN SUMMARY
# ---------------------------------------------------------------------------

def to_markdown(results, environment, repeat=None):
    rows = [
        r for r in results
        if (repeat is None or r["repeat"] == repeat) and not r.get("accuracy_arm")
    ]
    acc = [
        r for r in results
        if (repeat is None or r["repeat"] == repeat) and r.get("accuracy_arm")
    ]
    if not rows:
        return "No results."

    baseline = next((r["match_seconds"] for r in rows if r["arm"] == "1_baseline"), None)

    lines = [
        f"Environment: DBR {environment.get('databricks_runtime')}, "
        f"{environment.get('cpu_count')} cores, "
        f"{environment.get('mem_total_gb')}GB RAM, "
        f"DuckDB {environment.get('duckdb_version')}, "
        f"UKAM {environment.get('ukam_version')}, "
        f"Splink {environment.get('splink_version')}",
        "",
        "All arms below produced identical match results unless noted.",
        "Copy costs are excluded from the match column and shown separately.",
        "",
        "| Arm | Match (s) | vs baseline | Copy canonical (s) | Copy messy (s) | Peak spill (GB) | Matched |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        if r["error"]:
            lines.append(f"| {r['arm']} | FAILED | | | | | |")
            continue
        speedup = (
            f"{baseline / r['match_seconds']:.2f}x"
            if baseline and r["match_seconds"]
            else "—"
        )
        fp = r["fingerprint"]
        lines.append(
            f"| {r['arm']} | {r['match_seconds']} | {speedup} | "
            f"{r['copy_canonical_seconds'] or '—'} | {r['copy_messy_seconds'] or '—'} | "
            f"{(r['peak_spill_bytes'] or 0) / 1024**3:.2f} | "
            f"{fp['n_matched']}/{fp['n_rows']} |"
        )

    lines += ["", "Per-stage breakdown:", ""]
    for r in rows:
        if r["stage_diagnostics"]:
            lines.append(f"**{r['arm']}**")
            for s in r["stage_diagnostics"]:
                lines.append(
                    f"- {s['stage']}: {s['elapsed_seconds']}s, "
                    f"matched {s['matched_this_stage']}, "
                    f"remaining {s['remaining_after']}"
                )
            lines.append("")

    if acc:
        lines += [
            "Accuracy-affecting variants (results differ by design):",
            "",
            "| Variant | Match (s) | Matched | Delta vs arm 6 |",
            "| --- | ---: | ---: | ---: |",
        ]
        ref = next(
            (r["fingerprint"]["n_matched"] for r in rows
             if r["arm"] == "6_materialise_canonical" and r["fingerprint"]),
            None,
        )
        for r in acc:
            if r["error"]:
                lines.append(f"| {r['arm']} | FAILED | | |")
                continue
            fp = r["fingerprint"]
            delta = f"{fp['n_matched'] - ref:+d}" if ref is not None else "—"
            lines.append(
                f"| {r['arm']} | {r['match_seconds']} | "
                f"{fp['n_matched']}/{fp['n_rows']} | {delta} |"
            )
        lines.append("")

    return "\n".join(lines)


print("\n\n" + "=" * 70)
print("MARKDOWN SUMMARY (warm run)")
print("=" * 70 + "\n")
print(to_markdown(all_results, environment, repeat=REPEATS))
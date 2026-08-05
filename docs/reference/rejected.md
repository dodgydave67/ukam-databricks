---
title: Tested and rejected
---

# Tested and rejected

Three plausible optimisations were benchmarked and did not pay for themselves.
They are documented here rather than deleted, because "we tried it and it was
slower" is more useful than silence — and because each has conditions under
which it might still win.

!!! info "All three preserved correctness"

    None of these changed a single match. All 12 runs across the matrix produced
    fingerprint `5194b60df1884b954e1fb2c21ce845e9`. They are rejected purely on
    cost.

---

## Copying the messy data to local disk

**Arm 4.** Match time 58.86 s → 59.03 s. Copy cost 0.09 s.

The messy dataset was 6,367 addresses in a small Parquet file. Reading it was
never a bottleneck, and copying something that small changes nothing.

Arm 4's wall time appears 4.8 s worse than arm 3, but that is an artefact: the
canonical copy took 11.00 s in arm 4 versus 6.91 s in arm 3 — the same operation,
varying run to run. The messy copy contributed 0.09 s of it.

### When to reconsider

- **Large messy datasets.** The canonical result shows local disk matters
  enormously once reads are repeated and volume is real. There is a crossover
  point; this test was nowhere near it.
- **Many small batches in one session.** Copy once, match repeatedly.
- **Messy data on slow or heavily contended storage.**

The code costs two lines and 0.09 s. If you suspect your messy data is large
enough to matter, measure it — do not assume either way.

---

## On-disk DuckDB database

**Arm 5.** Match time 59.03 s → **74.07 s**. Wall time 71.89 s → 85.30 s. A
**25% regression** on match time.

Replacing `duckdb.connect()` with `duckdb.connect(database="/local_disk0/...")`
made everything slower. Splink rose from 52.28 s to 59.02 s and *other work
inside `match()`* from 5.40 s to 13.52 s — persistence overhead spread across the
whole run.

!!! warning "This contradicts general Splink guidance"

    Arm 5 implements the approach recommended in
    [splink#2652](https://github.com/moj-analytical-services/splink/discussions/2652#discussioncomment-12505866).
    That advice is sound for its intended case — workloads that exceed memory
    and need DuckDB to page tables out. This workload did not: 57.4 GB RAM, a
    45.9 GiB effective memory limit, 6,367 messy addresses, and **zero measured
    spill** in every arm after the pragmas were set.

    An on-disk database is insurance. Insurance you do not need is just cost.

### When to reconsider

Reach for it when you have evidence of memory pressure, not pre-emptively:

- Peak spill approaching or exceeding available RAM
- Out-of-memory failures on the driver
- A canonical dataset far larger than the one tested here — full-UK AddressBase
  against millions of messy records is a different problem from 6,367
- A driver shared with Spark or other memory-hungry work

Check first. Instrument spill, then decide:

```python
import os, threading, time

def dir_size_gb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1024**3
```

If peak spill stays near zero, an in-memory database is correct and faster.

---

## Materialising the canonical relations

**Arm 6.** Match time 59.03 s → **155.72 s** — slower than the untuned baseline
of 130.84 s. Materialisation alone cost **76.41 s**.

The theory was sound: UKAM loads prepared canonical artefacts as lazy
`con.read_parquet` relations, and Splink re-evaluates the canonical side once per
blocking rule, so materialising once should turn *n* scans into one.

The theory was even partly vindicated — exact matching fell to **0.58 s**, the
fastest of any arm. But `CREATE TABLE AS SELECT` over the canonical and
term-frequency relations cost more than every re-scan it eliminated, and Splink
itself got *slower* (52.28 s → 65.82 s), likely from operating on freshly written
tables rather than Parquet DuckDB can scan with its own optimisations.

Once the canonical data is on local NVMe, re-scanning it is cheap. Arm 3 already
removed the expensive part of the problem; arm 6 was paying to solve what was
left.

### When to reconsider

- **Reuse across batches.** The 76.41 s is a one-off. Materialise once, then run
  ten messy batches against the same tables on one connection, and the
  arithmetic inverts. This is untested and is the obvious next experiment.
- **Many more blocking rules.** More re-scans to eliminate shifts the balance.
- **Canonical data still on DBFS.** If you cannot copy locally, materialisation
  may substitute for it — also untested.

### Implications for the library

A `materialise_canonical` option added to `AddressMatcher` in the form tested
would be a footgun: on the recommended configuration it makes things
significantly worse. A more promising direction — raised on the discussion — is
letting UKAM **take a cache folder as input**, so the library manages local
staging itself rather than leaving each user to reimplement `dbutils.fs.cp`.

If you want to experiment, the subclass is preserved below. It is **not** part of
the recommended configuration.

??? example "MaterialisedCanonicalMatcher (for experimentation only)"

    Uses private `AddressMatcher` internals. Verified against **UKAM 1.2.4**.

    ```python
    import time
    from uuid import uuid4
    from uk_address_matcher import AddressMatcher


    class MaterialisedCanonicalMatcher(AddressMatcher):
        """Materialise prepared canonical artefacts into physical tables.

        BENCHMARKED SLOWER on a single-batch workload (arm 6: 155.72 s vs
        58.86 s). Only worth testing if you reuse the tables across batches.
        """

        materialise_seconds = None

        def _resolve_canonical_data(self):
            super()._resolve_canonical_data()
            t = time.perf_counter()
            if self._canonical_clean is not None:
                name = f"__ukam_canon_{uuid4().hex[:8]}"
                self.con.execute(
                    f"CREATE TABLE {name} AS "
                    f"SELECT * FROM ({self._canonical_clean.sql_query()})"
                )
                self._canonical_clean = self.con.table(name)
            if self._tf_table is not None:
                name = f"__ukam_tf_{uuid4().hex[:8]}"
                self.con.execute(
                    f"CREATE TABLE {name} AS "
                    f"SELECT * FROM ({self._tf_table.sql_query()})"
                )
                self._tf_table = self.con.table(name)
            self.materialise_seconds = round(time.perf_counter() - t, 2)
    ```

---

## The general lesson

Two of these three are things a competent engineer would reasonably assume help.
On-disk databases prevent OOM; materialisation eliminates redundant scans. Both
are true statements. Neither was relevant once the actual bottleneck — network
reads of the canonical Parquet — had been removed.

Optimisations are not additive, and they are not free. Measure on your own data
before adopting any of them, including the ones this site recommends.

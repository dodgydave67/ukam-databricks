---
title: Accuracy vs speed
---

# Accuracy vs speed

Everything in the [optimisation reference](optimisations.md) is free: it makes
matching faster without changing a single match. This page is about the other
category — settings that make matching faster **by doing less work on the
model**, and therefore change the answer.

!!! danger "The rule"

    A speedup on a change that altered the result is not a speedup. Keep these
    two categories in separate columns of your reporting, always.

## How the benchmark keeps them apart

The harness has two sections. Performance arms are asserted to be identical:

```python
digests = {r["fingerprint"]["digest"] for r in performance_arms}
if len(digests) == 1:
    "PASS — every performance arm produced identical match results."
else:
    "FAIL — timings are not comparable until this is explained."
```

In the reference run all 12 performance runs returned
`5194b60df1884b954e1fb2c21ce845e9` — 6,358 of 6,367 addresses matched, 1,672
from the exact stage and 4,686 from Splink.

Accuracy arms run separately, on top of the fastest correct configuration
(arm 3), and are reported with an explicit match-count delta so the recall cost
is visible:

| Variant | Match (s) | Matched | Delta vs arm 3 |
|---|---:|---:|---:|

Adopt the same structure in your own reporting. If you cannot say what a change
did to your match count, you do not know what the change did.

## The two variants the harness ships with

### Disabling out-of-postcode blocking

```python
stages = [
    ExactMatchStage(),
    SplinkStage(
        include_outside_postcode_block=False,
        include_full_postcode_block=True,
    ),
]
```

`include_outside_postcode_block` controls the broader blocking rules that
generate candidate pairs across postcode boundaries. Disabling it and switching
to a strict full-postcode block dramatically shrinks the candidate space, which
is where most of the Splink stage's time goes.

**What you lose:** any match where the messy record's postcode is wrong, missing
or falls on the other side of a boundary from the true canonical record. In
practice that is a meaningful share of real-world messy data — postcodes are one
of the most commonly mistyped fields in an address.

**Consider it when:** your messy data comes from a source with validated
postcodes, and you have measured the recall loss on labelled data.

### Reducing `improve_top_n_matches`

```python
stages = [
    ExactMatchStage(),
    SplinkStage(improve_top_n_matches=3, improve_use_bigrams=False),
]
```

`improve_top_n_matches` (default `5`) is how many candidate pairs per messy
address are retained for the token-based score adjustment step;
`improve_use_bigrams` (default `True`) controls whether that step considers
bigrams as well as single tokens. Both make the improvement step cheaper.

**What you lose:** the improvement step is what separates near-ties. Cutting it
back tends to hurt exactly the ambiguous cases you most needed help with —
watch `distinguishability` in the output, not just the match count.

## Other levers in the same category

| Lever | Speed effect | Accuracy effect |
|---|---|---|
| `canonical_address_filter="postcode LIKE 'SW%'"` | Large — shrinks the canonical side directly | Anything genuinely outside the filter becomes unmatchable |
| Raising `final_match_weight_threshold` | Small | Fewer, more precise matches; recall falls |
| Raising `final_distinguishability_threshold` | Small | Drops ambiguous winners; often the *right* trade for automated pipelines |
| Adding `UniqueTrigramStage()` before Splink | Can be net positive — cheap deterministic stage removes records before the expensive stage | Changes which stage claims a match; usually raises precision |
| Removing `ExactMatchStage()` | Negative — you push easy cases into Splink | Slower and no better |

That fourth row is worth dwelling on. Adding a cheap deterministic stage is the
one accuracy-affecting change that often makes things both faster and better,
because every record it resolves is a record the Splink stage never sees. Check
the per-stage diagnostics to see how many records each stage is actually
removing.

## Measuring properly

Do not evaluate these on match count alone — match count going up is not the
same as match count going up *correctly*.

1. Hold out a labelled sample. The library ships accuracy tooling
   (`result.accuracy_data()`, `result.accuracy_analysis()`) built for exactly
   this.
2. Run the change against the fastest **correct** configuration, not against the
   baseline, so you are measuring one thing.
3. Report precision and recall alongside runtime.
4. Record the fingerprint digest of every run so you can prove later which
   configuration produced which output.

```python
fingerprint = con.execute(f"""
    SELECT md5(string_agg(
        CAST(unique_id AS VARCHAR) || '>' ||
        COALESCE(CAST(resolved_canonical_id AS VARCHAR), ''),
        '|' ORDER BY CAST(unique_id AS VARCHAR)
    ))
    FROM ({result.matches().sql_query()})
""").fetchone()[0]
```

That digest is order-independent and cheap. Log it with every production run —
it turns "did anything change?" from an argument into a lookup.

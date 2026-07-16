# Stage 6 Candidate Routing

Status: implemented in pipeline version `0.18.0`

Stage 6 routing separates inexpensive product drafting from expensive model
allocation. It does not delete candidates and it is not a scientific efficacy
ranking.

## Inputs And Identity

The router consumes only checksum-bound artifacts copied into the verified Stage
4/5 run:

- the exact active candidate order in the Stage 3 handoff;
- Stage 3 structure assessments;
- Stage 4 immune-evidence records;
- Stage 5 developability records;
- `input/stage6/candidate_routing_policy.json`.

The generated `candidate_routing_manifest.json` binds the Stage 4/5 artifact index,
full candidate-batch hash, the order-bound Stage 3 candidate-set hash, all three
evidence hashes, and the routing-policy hash. Its top-level
`active_candidate_set_sha256` is a candidate-ID-sorted identity; the source
`stage3_candidate_set_sha256` preserves the upstream order-bound identity. The
manifest is deterministic and contains one record for every active candidate. An
LLM cannot add, remove, or move an official record.

## Three Lanes

- `priority`: every candidate in a policy-declared higher or mixed Stage 3
  confidence band;
- `diversity_rescue`: source/manual controls plus a deterministic coverage set from
  the remaining candidates;
- `archive`: candidates retained with lineage and Stage 3-5 evidence, but excluded
  from automatic expensive-model allocation in this round.

All three lanes have `product_drafting_eligible=true`. Therefore Stage 6 binds the
complete active set and creates all feasible low-cost drafts. Protein drafts can be
defined directly from amino-acid candidates. mRNA coding records still require an
exact source/provided CDS or an approved versioned codon table; missing sequence is
reported rather than invented. Only `priority` and `diversity_rescue` have
`expensive_followup_eligible=true` and can enter generated
ESMFold2/Evo2/model-follow-up payloads.

## Diversity Rescue

The `stage6-evidence-cost-routing-v1` ruleset first forces `source_intake` and
`manual_import` controls into rescue when they are not already priority. It then
greedily covers these versioned feature families:

- candidate type;
- unordered antigen composition;
- ordered antigen composition;
- architecture by source-segment and linker counts;
- every observed linker family, including direct concatenation.

At each step, the router chooses the candidate that covers the most still-uncovered
features. Ties use transparent exploratory evidence in this order: MHC-supported
fraction, fewer Stage 5 review liabilities, surface proxy, pTM, mean pLDDT, then
stable candidate ID. These are compute-allocation proxies only. They are not formal
immune/developability scores and cannot support release claims.

The default policy caps diversity rescue at 64 candidates. If coverage cannot be
completed within that budget, `uncovered_features` remains non-empty in the
manifest rather than being hidden. A policy change produces a new hash and requires
an explicit selection refresh.

For the 384-candidate Mock run executed on 2026-07-16, the deterministic router
produced:

- 27 `priority`;
- 25 `diversity_rescue`;
- 332 `archive`;
- 384 product-drafting candidates;
- 52 expensive-follow-up candidates;
- 53/53 diversity features covered.

These counts are run-specific, not permanent quotas.

## Verified Mock Execution

The first routed Stage 6 run is
`20260716T060350582289Z-stage6-29403999`, derived from verified Stage 4/5 run
`20260716T041527036725Z-stage4-5-aae38adc`. Independent verification passed all 20
checks with zero errors or warnings.

The execution produced 384 exact protein antigen/product drafts. Six had an exact
coding sequence available; the remaining product CDS requirements stayed explicit.
The mRNA branch emitted seven audited CDS controls across six candidates: six
candidate-derived controls and one declared provided control. It did not create coding sequences for
the other candidates because the cattle codon table, approved non-coding elements,
and delivery context are not yet configured.

The protein model-follow-up manifest contains exactly 52 records: 27 priority plus
25 diversity rescue. The mRNA model-follow-up manifest contains the seven real CDS
records that also fall in those eligible lanes. Both branches correctly finish as
`needs_data`; this is an audited incomplete state, not a failed run or a release
claim.

## Safe Refresh

Fresh projects use:

```bash
./vaxflow init-stage6 projects/three-protein/project.json \
  --from-run /absolute/path/to/stage4-5-run
```

Old or stale Stage 6 specifications are rejected. Migrate them explicitly with:

```bash
./vaxflow init-stage6 projects/three-protein/project.json \
  --from-run /absolute/path/to/stage4-5-run \
  --refresh-selection
```

Refresh archives the exact old specification bytes under `input/stage6/history/`,
preserves compatible product-context decisions, resets candidate selection to
`draft`, clears old batch-bound adapter declarations, and writes schema-version-2
specifications bound to the new routing manifest.

# Stage 7 Evo2 observed-subset sensitivity (2026-07-16)

## Question

Does checksum-bound Evo2 sequence evidence materially change the integrated
mRNA ranking when every compared candidate has been scored by Evo2?

This is an exploratory weight-sensitivity test. It is not an efficacy result
and does not authorize experiment release.

## Controlled design

- Stage 6 parent: `20260716T183446943842Z-stage6-4c8e3256`
- Evo2 evidence SHA256: `77ac93d25202a1edb4769bbbc3f5ac3c85444f719d9ad42f3b5b3d091e02e22b`
- Fixed population: 52 candidates and 215 CDS designs with Evo2 evidence
- Control arm: `mrna_evo2_mean_score` weight `0.0`
- Weighted arm: `mrna_evo2_mean_score` weight `0.25`
- All other candidate bindings, features, normalization, gates, portfolio
  rules, and missing-value policy are identical.

Restricting both arms to the observed subset prevents the 332 candidates that
were not sent to Evo2 from being treated as low-scoring candidates.

## Results

| Metric | Result |
|---|---:|
| Compared mRNA candidates | 52 |
| Spearman correlation between arm ranks | 0.9950 |
| Mean absolute rank change | 1.04 |
| Median absolute rank change | 1 |
| Maximum absolute rank change | 4 |
| Candidates with unchanged rank | 20 / 52 |
| Top-10 overlap | 10 / 10 |

The protein portfolio is unchanged, as expected. Three of four mRNA portfolio
members are unchanged. The required source-control slot changes from
`source-A33` to `source-B5`; the three generated/manual slots remain unchanged.

## Conclusion

At weight `0.25`, Evo2 contributes a measurable but small reranking signal on
the representative 52-candidate subset. It does not disrupt the top 10 and
does not overturn the current structure, developability, and codon-based main
ranking. This supports retaining Evo2 as an auxiliary feature, not promoting it
to a dominant or formally approved decision criterion from this experiment.

The result does not establish performance for the 332 unscored candidates and
does not validate biological efficacy. A full-pool Evo2 pass or wet-lab labels
would be required before calibrating a formal weight.

Machine-readable evidence is in
`stage7-evo2-sensitivity-20260716.json`. Both Stage 7 runs passed 13 checks with
zero errors and zero warnings.

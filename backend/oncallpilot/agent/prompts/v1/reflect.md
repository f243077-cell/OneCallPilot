Review your hypotheses against all the evidence, including the disproof query's result.

For each hypothesis by rank:
- `citations_supported`: do the cited references really support the claim?
- `adjusted_confidence`: your confidence after this review, from 0 to 1;
- `note`: one short sentence explaining the adjustment.

Then give `top_verdict` for hypothesis 1: `disproved` if the evidence contradicts it, otherwise `survived`.

Be strict: lower the confidence of claims that rest on weak or indirect evidence.

# Event-to-Posting Integrity Audit — KPI Definitions

All figures are computed from synthetic data. CAD values use `unit_value_cad_sim` and are **indicators, not losses, savings or ROI**.

| KPI | Formula and denominator | Exclusions / limits |
|---|---|---|
| Raw rows | Data rows in each raw CSV | — |
| Valid / quarantined | `valid` = rows that pass R01–R03 and are not repeats; `quarantined` = rows that raise R01–R04 and are excluded; `repeats ignored` = identical re-delivered rows | `Valid % = valid ÷ raw rows` per file |
| Unique cases | Distinct `case_key` over all issues (one real-world problem = one case) | Secondary issues are not separate cases |
| Cases by rule / severity | Count of **primary** issues | Overlapping findings are never summed as separate losses |
| Unposted events by cutoff (per shift) | `completed − posted_by_cutoff` for closed shifts; split into *posted later (timing)* and *never posted* | Open shifts are not yet due and show blanks |
| Count agreement rate | `AGREES ÷ comparable counts`; comparable = valid COMPLETE counts plus PARTIAL counts that are not "not-comparable"; excludes VOID | Counts classified EXPLAINED / IN_FLIGHT stay in the denominator but not the numerator |
| Count coverage | SKU-locations with ≥ 1 valid COMPLETE count ÷ eligible SKU-locations | Eligible = positive opening balance or a valid completed movement; shipping staging bin excluded |
| Count variance (R12) | Units: `counted − event ledger`; CAD: `|units| × unit_value_cad_sim` | Only unexplained variances; explained gaps are linked, not valued twice |
| Simulated exposure | Σ simulated CAD value over **primary** cases that carry a quantity difference: R05–R09, R12, R13, R14 (see table below). Secondary issues are excluded | Indicator only, not a loss: each rule values a different quantity. R01–R04, R10, R11 carry no value. Cases without a quantity difference (R09 movement-type mismatch, R14 broken references) are valued 0 |
| Outstanding handoffs | R11 primary cases; aging = closed shifts since the event's shift | Handoffs to a shift still open are not due |
| Next-count priority | `ABC points (A30/B20/C10) + recency (never 30, >48h 20, >24h 10) + 10 × open stock-impacting cases (max 40) + 20 if unexplained variance` | ABC from simulated pick value (cumulative share before the item: <80% A, <95% B). Ties: ABC, SKU, location |
| Simulation before/after | Resolved = primary case present before and absent on the working copy; acknowledged = timing accepted with no data change; side-effects = late corrective postings (expected); unexpected = anything else new (must be 0) | A cleaner simulated result does not prove any real improvement |

Limitation: the engine explains count gaps by **presence** of linked issues, and by exact magnitude only through the per-item gap partition (S − E equals the sum of item gaps, tested). It does not attribute root cause.

## What the simulated CAD value means, rule by rule
`value = |quantity difference in base UoM| × unit_value_cad_sim`, rounded to cents, priced at the case's SKU. Each formula below was recomputed independently from the raw CSV files in `tests/test_07_cad_values.py` (all demo cases, plus severity-escalation checks).

| Rule | Quantity that is valued |
|---|---|
| R05 | Base quantity of the *extra* postings (system stock is overstated/understated by that amount) |
| R06 | Base quantity of the completed event that was never posted |
| R07 | Base quantity of the posting that no event supports |
| R08 | Normalised difference between posting and event |
| R09 | Missing leg: the event quantity. SKU or location mismatch: the quantity of the misposted movement, priced at the **event's** SKU. Movement-type mismatch: 0 |
| R12 | `|counted − event-ledger balance|` (the unexplained part only) |
| R13 | The negative event-ledger balance at its first breach (system-side secondary R13 is never summed) |
| R14 | Reversal event: quantity difference to the original; reversal posting: difference to the reversed posting; broken reference: 0 |

Severity escalation by value (raising to HIGH at ≥ `value_high_cad`) applies **only** to R05, R06, R07, R08 and R12. R09, R13 and R14 carry a value for ranking and for the exposure indicator but their severity comes from the rule table.

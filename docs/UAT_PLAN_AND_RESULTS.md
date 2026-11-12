# UAT Plan and Results (rule → test evidence)

Results are from an actual run (command `python -m pytest -q`). "Boundary" = hand-specified fixture `data/fixtures/boundary/` with hand-computed `expected_issues.csv` / `expected_counts.csv`. "Sens." = single-anomaly injection into the clean fixture (`tests/test_03_sensitivity.py`). "Neg." = negative control that must **not** be flagged (`NEGATIVE` in `tests/test_02_clean_and_golden.py`). "SQL" = also reproduced by `sql/*.sql` and compared with Python.

| Rule | Positive case(s) | Negative / near-miss | Sensitivity | SQL | Result |
|---|---|---|---|---|---|
| R01 | boundary EV-B25 (zero → LOW, accepted), EV-B26 (blank), CC-B05 (blank count) | zero count valid; EV-B22 | blank qty event; blank vs zero count | yes | PASS |
| R02 | EV-B21 (1 s before SKU effective), EV-B24 (location end is exclusive) | EV-B22, EV-B23 (inclusive start / last valid second) | — | yes | PASS |
| R03 | EV-B27 (DZ) | EV-B06, EV-B07 (CS with explicit conversion) | — | yes | PASS |
| R04 | PO-B13 (identical repeat) | EV-B14 / PO-B14 twin picks | identical posting row | yes | PASS |
| R05 | EV-B11 | EV-B12 reverse/repost chain, twin picks, transfer legs | duplicate posting | yes | PASS |
| R06 | EV-B16 (unposted), EV-B17 (possible match) | EV-B15 (shift still open), EV-B01 | missing posting | yes | PASS |
| R07 | PO-B18, PO-B39 (cancelled event), PO-B40 | PO-B17 (consumed possible match), reversal postings | orphan posting | yes | PASS |
| R08 | EV-B07 (11 vs 12 EA), EV-B10 (outside tolerance) | EV-B06 (2 CS = 24 EA), EV-B08/B09 (rounding / inside tolerance) | quantity +5 | yes | PASS |
| R09 | EV-B19 (missing leg) | EV-B20 (both legs) | location change | demo only (not in SQL) | PASS |
| R10 | EV-B02 (+1 s), B31 (HIGH), B32 (MEDIUM), B33 (LOW), B04 (label), B41 (effective time), B44 (posted 40 min before the event) | EV-B01 (exactly at cutoff), B03 (event at shift start), B05 (UTC), B42 (effective 1 s early) | late +10 min; at cutoff; +1 s | LATE only | PASS |
| R11 | EV-B34, EV-B37, EV-B43 | EV-B35 (handoff to open shift), EV-B36 (closed) | — | no | PASS |
| R12 | CC-B03 (partial overage), CC-B07 (KG outside tolerance) | CC-B01, B02, B04, B06, B08, B10 | count −3; KG +0.4 inside tolerance; partial below balance | yes | PASS |
| R13 | SKU-B3 / LOC-A | SKU-B1 bins | — (demo planted) | no | PASS |
| R14 | EV-B30 (broken reference) | EV-B29, PO-B12b (valid reversals) | — (demo planted incl. posting-level) | no | PASS |

Every implemented subtype now has at least one test (independent review, `tests/test_06_adversarial.py`). Remaining gaps (partial SQL coverage, desktop-Excel items): see `docs/LIMITATIONS.md`.

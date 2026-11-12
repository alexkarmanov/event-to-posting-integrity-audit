# Event-to-Posting Integrity Audit — SPEC (v1.0 working draft)

Status: **checked against the project requirements on 2026-11-08**. Items marked `ASSUMPTION` are design decisions made without input from a business user and can be changed.
All data in this project is **synthetic**. The fictional company is *Cinder Creek Distribution*; it does not correspond to any employer.

## 1. Purpose and users
Reconcile two independent representations of warehouse stock — the **operational event log** (what physically happened, per the floor record) and the **ERP/WMS posting extract** (what the system recorded) — against **point-in-time cycle counts**. Produce a ranked, evidence-backed exception queue that tells a Shift Lead / Inventory Control Analyst *what to review first and why*, without changing source data.

Users: Shift Lead, Inventory Control Analyst, Operations Systems Analyst, QA/UAT Analyst (roles only; no personal names).

## 2. Scope
In: synthetic data generator (seeded), validation, reconciliation engine (R01–R14), SQLite/SQL independent checks, Excel workbook, dry-run correction simulation with before/after, tests, docs.
Out (non-goals): live ERP/WMS, web/mobile app, login, cloud, paid API, ML scoring, real stock adjustments, fabricated ROI, Power BI.

## 3. Scenario (fictional)
Cinder Creek Distribution (fictional) added a **night shift** and now runs three shifts. Floor events (receipts, putaways, replenishments, picks, adjustments) are logged on handhelds in local time (a Mountain-time zone, fixed offset -07:00 in the data window). The ERP posts them later and stores **UTC** timestamps. Some events are posted after a shift closes, some are handed over unfinished, some postings are duplicated/missing/mismatched, and cycle counts are taken at various points. A shift lead must separate *timing and process* problems from *true count variances* before anyone touches system stock.

## 4. Time model (binding)
- All timestamps in raw files are ISO-8601 **with explicit offset** (`-07:00` local or `Z`). Naive timestamps are invalid (R01).
- All comparisons are done in UTC instants.
- Shifts (`shifts.csv`): Day 06:00–14:00, Evening 14:00–22:00, Night 22:00–06:00 (+1 day), local. `shift_id = <start-date>-<D|E|N>`. Intervals are **half-open `[start, end)`**: an event at exactly `end` belongs to the next shift. The shift's **cutoff = its end**.
- A posting is **timely** for an event if `system_post_timestamp <= cutoff(shift of event)` (inclusive: a posting at exactly the cutoff is timely).
- Two timestamps per movement: `event_timestamp` (business event) and `system_post_timestamp` (ERP posting). Optional `event_effective_timestamp` on a posting is the ERP's claimed event time.
- Extract snapshot: `snapshot_ts` in `rule_parameters.csv`. Records after the snapshot are invalid (R01). A shift whose cutoff is after the snapshot is **open**: its events are *not yet due* and never raise R06/R10.
- Two balance bases, always labelled:
  - **System balance S(sku,loc,t)** = opening + Σ POSTED, valid postings with `post_ts <= t` (posting-time basis).
  - **Event balance E(sku,loc,t)** = opening + Σ completed, valid events with `event_ts <= t` (event-time basis).
  - Count C is independent point-in-time evidence at `observed_at`; comparisons use `<=` on both bases. No record later than `t` can influence a result at `t`.
- Opening balances are the agreed baseline at `2026-02-09T06:00-07:00` for both S and E (ASSUMPTION: from a prior full count).

## 5. Ledger convention (binding)
Movement effect on balance (events): `+qty at to_location`, `-qty at from_location` (both for transfers). Receipt = to only; pick = from only; adjustment = exactly one side; reversal = the *inverse movement itself* (from/to describe the reversing movement). Postings: RECEIPT `+to`, ISSUE `-from`, TRANSFER_OUT `-from`, TRANSFER_IN `+to`, ADJUSTMENT `+to`/`-from`, REVERSAL `+to`/`-from`. A transfer event expects **two** postings (OUT and IN); counted once per leg, never double counted. Pending and cancelled events have **no** effect on E. Reversal of a transfer is out of scope (flagged R14 `UNSUPPORTED_TARGET`).
Quantities are normalized to the SKU `base_uom` via explicit `uom_conversions.csv` (`Decimal`, rounded half-up to 3 dp). **No guessed conversions.**

## 6. Validation gate (R01–R04) and quarantine
Records failing R01/R02/R03 are **quarantined**: raise an issue and take no part in matching or balances. A valid counterpart whose partner is quarantined is `COUNTERPART_QUARANTINED` and raises **no** second issue. R04: duplicate IDs. Identical re-delivery → keep first, flag; conflicting duplicates → quarantine all rows of that ID. Zero quantity is *valid* (warning only); blank/non-numeric/negative is *rejected*.

## 7. Matching
1. Postings link to events by `source_event_id` (strong). 2. Postings without `source_event_id` may form a `POSSIBLE_MATCH` only when exactly one unmatched event has the same SKU, movement side/type, base quantity (within tolerance), locations, and `|Δt| <= possible_match_window_minutes`; never a confident link. 3. REVERSAL postings referencing a posting in the same group net it out (not a duplicate). Active postings are compared with the expected set for the event type.

## 8. Rules
R01–R14 per `docs/RULE_CATALOG.md` and `config/rule_catalog.csv` (single source for severity, owner role, review action). Precedence for choosing the *primary* issue in a case (lower wins): R04, R01, R02, R03, R14, R09, R05, R06, R08, R07, R11, R10, R12, R13. Secondary issues are kept (`is_primary=false`) and excluded from unique-case counts and value sums.

R12 classification of each valid COMPLETE count: `AGREES` (|C−S|≤tol); `EXPLAINED_BY_OPEN_ISSUES` or `IN_FLIGHT_POSTING_LAG` (C≈E but S≠E); `UNEXPLAINED_COUNT_VARIANCE` (C differs from E and S → R12 exception); PARTIAL counts that are ≤ both balances are `PARTIAL_NOT_COMPARABLE`; VOID counts are excluded.

## 9. Output
`python -m stocktrace build` writes `Integrity_Audit_Demo.xlsx`, `Exceptions.csv`, `Manager_Summary.csv`, `Simulation_Before_After.csv`, `Count_Reconciliation.csv`, `Status_History.csv`, `run_manifest.json`. Existing user-modified outputs are not overwritten without `--force`.

## 10. Assumptions and decisions
| # | Decision | Rationale |
|---|---|---|
| A1 | Python 3.9 stdlib + `xlsxwriter` (write, with cached formula values) + `openpyxl` (read/verify) + `pytest` | System Python is 3.9.6; no installs beyond pip in `.venv` |
| A2 | Workbook formulas use plain A1 ranges (no `XLOOKUP`/dynamic arrays/macros) | Compatibility; avoids unverifiable features |
| A3 | Power Query not implemented; documented as optional, unverified | Cannot create/test a real query programmatically |
| A4 | R13 system-side negatives are evaluated at shift cutoffs only | Posting order within a shift is not meaningful |
| A5 | Illustrative CAD values use `unit_value_cad_sim` | Synthetic; labelled simulated everywhere |
| A6 | Fixed UTC-7 offset in window (no DST transition between 2026-02-09 and 2026-02-12) | Keeps the demo explainable; DST noted in limitations |

## 11. One-week phase plan
| Phase | Output | Gate |
|---|---|---|
| 0 | Environment audit, SPEC, scope notes | Ambiguities documented |
| 1 | Seeded generator, demo/clean data, boundary fixture | Deterministic; labels absent from raw |
| 2 | Engine R01–R14, SQL cross-check | Golden + invariant tests pass |
| 3 | Workbook, simulation | Structural checks pass; Excel behaviour confirmed manually; 2-page print pack passed; evidence-trail walkthrough not yet verified |
| 4 | QA, UAT, adversarial passes | All shipped rules verified |
| 5 | README, case study, interview guide, drafts | Truthfulness/privacy review |
| 6 | Final reproducibility + handoff | Technical handoff complete; publication is a separate, explicit decision |

## 12. Acceptance
Ten release gates: reproducible build; clean, golden, sensitivity, invariant, traceability and independent cross-check tests; Excel integrity; privacy and repeatability; documentation truthfulness. Status labels in docs: `VERIFIED / PENDING USER / NOT IMPLEMENTED`.

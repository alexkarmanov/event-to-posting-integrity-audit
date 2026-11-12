# Process and Data Flow

```text
 data/raw/<scenario>/*.csv  (never modified)
        │  load: UTF-8/BOM ok, cell whitespace trimmed (only normalisation)
        ▼
 ┌────────────────────────┐   quarantine: R01 fields · R02 references · R03 UoM · R04 duplicate IDs
 │ 1 VALIDATION GATE      │──► invalid rows raise one issue and take no part in matching or balances
 └───────────┬────────────┘
             ▼
 ┌────────────────────────┐   events ──(source_event_id)──► ERP postings; reversals net their target
 │ 2 MATCHING             │   no reference → POSSIBLE_MATCH only if exactly one candidate
 └───────────┬────────────┘
             ▼
 ┌────────────────────────┐   R05 duplicates · R06 unposted · R07 orphan · R08 quantity · R09 SKU/bin/leg
 │ 3 EVENT↔POSTING RULES  │   R10 timing (cutoff, effective time, label, early posting) · R11 handoffs · R14 reversals
 └───────────┬────────────┘
             ▼
 ┌────────────────────────┐   S = system balance (posting time)      E = event-ledger balance (event time)
 │ 4 COUNTS & BALANCES    │   C = independent count at observed_at   AGREES / EXPLAINED / IN-FLIGHT / UNEXPLAINED
 └───────────┬────────────┘   R12 only when C matches neither; R13 impossible balances
             ▼
 ┌────────────────────────┐   cases (primary + secondary), severity, priority score, owner role, review action
 │ 5 TRIAGE               │
 └───────────┬────────────┘
             ▼
 ┌────────────────────────┐   dry run on an in-memory COPY: hypothetical approval → apply → rerun engine
 │ 6 SIMULATION           │   → resolved / acknowledged / remaining / expected timing side-effects / unexpected (must be 0)
 └───────────┬────────────┘
             ▼
 output/<name>/  Integrity_Audit_Demo.xlsx · Exceptions.csv · Manager_Summary.csv · Simulation_Before_After.csv ·
                 Count_Reconciliation.csv · Status_History.csv · run_manifest.json

 Independent path:  same CSVs ─► SQLite (sql/01–03) ─► issue keys + S/E/C balances ─► compared with step 3–4
```

## Process controls (what stops a wrong conclusion)
| Control | Where | Effect |
|---|---|---|
| Source data is read-only | loader, simulation works on clones | No silent correction; tests assert raw tables are unchanged |
| Two time bases | S vs E | A late posting cannot masquerade as missing stock |
| Cutoff semantics | shifts `[start,end)`, posting at the cutoff is timely | Boundary results are testable to the second |
| Explicit UoM only | `uom_conversions.csv` | Unknown conversion → R03, never a guess |
| Quarantine | R01–R04 | Bad rows cannot distort balances or create secondary noise |
| Cases, not rows | `case_key`, primary/secondary | One problem is counted once; values are never summed across overlaps |
| Count attribution | gap partition | S − E equals the sum of per-item gaps (tested) |
| Independent check | SQLite layer, hand-written boundary fixture | Python is not the only judge of itself |
| Overwrite protection | CLI | A user-modified output is not replaced without `--force` |

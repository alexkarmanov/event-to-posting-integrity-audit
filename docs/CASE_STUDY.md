# Event-to-Posting Integrity Audit — case study: separating timing from loss in a multi-shift warehouse

*Synthetic data throughout. The company, quantities and dollar values are generated; nothing here describes a real operation. Figures are from `python -m stocktrace build --scenario demo` and the files in `sample_output/`.*

## The problem
When a warehouse adds a night shift, the same complaint recurs: *"the count doesn't match the system."* Three very different things hide behind it, and treating them alike wastes time or, worse, creates errors:
1. **Timing.** The work happened before the shift closed but the ERP posted it afterwards. Nothing is lost; adjusting stock would create an error.
2. **Process or data defects.** A posting was doubled, never made, keyed against the wrong bin or unit, or a handoff was left open.
3. **A genuine variance.** The count differs from what both the floor log and the ERP say.

## Approach
* Compare **three independent views**: the floor event log (event time), the ERP posting extract (posting time) and cycle counts.
* Judge timing against **each event's own shift cutoff**, with half-open shift intervals, so "late" is a measured fact and a posting exactly at the cutoff is on time.
* Keep **two balances** per item and bin: system (by posting time) and event ledger (by event time). A count that agrees with the ledger but not the system is *explained* by open posting issues; only a count that matches neither is a real variance.
* Apply **14 documented rules** (R01–R14). Overlapping findings collapse into one case with a primary issue, so nothing is double-counted. Root causes are hypotheses and every action is a proposal for a named owner role.
* Never change source data. Corrections are simulated on an in-memory copy.

## What it found on the demo data
Input: 652 events, 750 postings, 126 counts. Output: 71 issues in **65 unique cases** — 22 HIGH, 38 MEDIUM, 5 LOW (the Excel Executive Summary page is in the README).

| Situation | Example case | What the Integrity Audit does |
|---|---|---|
| Timing, not loss | `EX-R10-EV-000194`: a pick of 12 units at 03:48 on the night shift was posted at 14:30, after the next shift had also closed | Reports a late-posting timing case, states it is not evidence of loss, proposes no stock adjustment |
| A duplicate that would look like a shortage | `EX-R05-EV-000349`: a transfer of 80 cases posted twice | Raises the duplicate; counts taken afterwards are linked to it instead of being booked as a second loss |
| A real variance | `EX-R12-CC-0010`: counted 2127 against a system and ledger balance of 2132 | No open issue explains it, so it is queued for a **recount**, not an adjustment |

Across all 116 comparable counts, 100 agree (86.2%), 12 are explained by open posting issues or normal posting lag and 4 are unexplained. Five late-posting and six handoff cases are reported separately from stock-impacting ones. A dry-run correction with a hypothetical approval resolves 22 cases, acknowledges 5 timing cases and creates 0 unexpected new exceptions; that shows the logic is consistent, not that any real operation improved. The simulated exposure of about CAD 44,070 uses a made-up unit value and is an indicator only.

## How the results are checked
* A **clean** dataset must raise nothing. A hand-written **boundary** fixture has 30 expected issues and 9 count classifications computed by hand.
* The seeded generator plants 65 anomalies **outside** the raw inputs; a test requires every one to be found with the right subtype and flags nothing unplanted except consequential secondary findings.
* An **independent SQLite implementation** reproduces the engine's results without sharing code. **Coverage is partial:** R01–R08, R10 (late postings only) and R12, plus both balances — 10 of the 14 rules. On the demo, clean and boundary data its issue keys agree 47/47, 0/0 and 21/21. The other rules rely on the Python tests and the boundary fixture.
* Every simulated CAD value is recomputed independently in tests; spreadsheet formula injection and the print layout are covered by regression tests; the suite has 180 tests.

## Design decisions worth noting
* **Quarantine, don't correct.** Invalid rows are set aside and reported (R01–R04); the build never repairs source data.
* **Explicit units.** Unit-of-measure conversion happens only through a supplied table; nothing is assumed.
* **Evidence first.** Each exception names its source files, record IDs and CSV rows, and the workbook lets a reviewer open both records side by side.
* **A manager page that prints.** The Executive Summary fits one landscape Letter page; the evidence stays interactive in Excel rather than being squeezed onto paper.
* **Untrusted input stays text.** Source values such as `=1+1` are written as text in the workbook and neutralised in CSV reports.

## Limits
Synthetic data only; no live system and no write-back; a dry-run simulation; a fixed UTC-07:00 window; partial SQL verification; Power Query not implemented; the print layout of the longer list sheets and an evidence-trail walkthrough in desktop Excel were not verified. Developed with AI assistance (Claude Code). See [`LIMITATIONS.md`](LIMITATIONS.md).

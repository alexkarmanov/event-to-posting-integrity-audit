# Scenario Story (fictional)

> **Synthetic.** *Cinder Creek Distribution* does not exist. Every name, quantity, time and dollar value below is generated. The scenario borrows the *kind* of pressure found in multi-shift warehouses; it is not a replica of any real operation, system or incident.

## The setting
Cinder Creek is a small distribution centre that recently added a **night shift** (22:00–06:00) to its Day (06:00–14:00) and Evening (14:00–22:00) shifts. Floor staff log receipts, putaways, replenishments and picks on handhelds in local time. The ERP/WMS records the resulting postings in its own time (UTC), usually minutes later. Cycle counters count a few bins at the end of each shift.

## The problem
Since the third shift started, the Shift Lead and the Inventory Control Analyst spend their mornings on the same argument: *"the count doesn't match the system."* Three very different things hide behind that sentence:
1. **Timing** — the work happened before the shift closed but the ERP posted it afterwards. Nothing is lost; adjusting stock would create an error.
2. **Process or data defects** — a posting was doubled, never made, keyed against the wrong bin, converted with the wrong unit, or left open in a handoff note.
3. **A real variance** — the count differs from what *both* the floor log and the ERP say.

Today they are mixed in one spreadsheet, so people either chase everything or adjust the system to match the count.

## What the Integrity Audit does with it (three examples from the demo data)
* **Timing, not loss.** A pick of 12 units happened at 03:48 on the night shift (cutoff 06:00). The ERP posted it at 14:30 — after the *next* shift also closed. The Integrity Audit reports a late-posting timing case (`EX-R10-EV-000194`), says "not evidence of loss", and proposes no stock adjustment.
* **A duplicate that would have looked like a shortage.** A transfer of 80 cases was posted twice (`EX-R05-EV-000349`). Any bin count taken afterwards would disagree with the system; the Integrity Audit links the count gap to this open case instead of booking a second loss.
* **A real variance.** A count of 2,127 against a ledger and system balance of 2,132 (`EX-R12-CC-0010`) is not explained by any open posting issue. It is queued for a **recount**, not an adjustment.

## Who uses it
Shift Lead (what to chase first), Inventory Control Analyst (evidence and review actions), Operations Systems Analyst (data defects), QA/UAT Analyst (rules, tests, retest). Roles only — no personal names.

## What it deliberately does not do
It does not change stock, connect to a live system, or claim a cause: root causes are hypotheses, review actions are proposals, and the correction simulation is a dry run on a copy with a *hypothetical* approval.

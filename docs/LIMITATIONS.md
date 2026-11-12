# Event-to-Posting Integrity Audit — Known Limitations

* **Synthetic only.** The company, items, quantities, anomalies and CAD values are invented. Nothing here shows results at any real operation.
* **No live system.** No ERP/WMS connection, no write-back, no authentication, no automatic stock adjustment.
* **Dry run only.** The correction simulation uses *hypothetical* approvals on an in-memory copy; corrective postings appear as late by construction.
* **Time model.** Fixed UTC-07:00 in the demo window; a daylight-saving change inside a data window is not modelled. Shift calendars must be supplied.
* **Reversals.** Reversal of a *transfer* is out of scope (flagged `UNSUPPORTED_TARGET`).
* **Matching.** Without `source_event_id` only a single unambiguous candidate becomes a `POSSIBLE_MATCH`; no fuzzy matching.
* **Count attribution.** Count gaps are explained by linked open issues and per-item gap amounts; root causes remain hypotheses.
* **R13 system side** is evaluated at shift cutoffs only.
* **Excel.** `REVIEW_QUEUE.status` is a working column and is not read back by the build. Power Query is **not implemented** (no tested connection); refresh is the documented command. Checked manually in desktop Excel: the workbook opens, formulas recalculate and filters work, and the 2-page print pack (executive summary + evidence example) printed correctly. Not verified in desktop Excel: the print layout of the longer list sheets (layout preview only) and the evidence-trail walkthrough.
* **Untrusted text.** CSV values are treated as data, never as formulas: the workbook keeps them as text (no cell is created as a formula or hyperlink from source text) and the generated report CSVs prefix any text that starts with `=`, `+`, `-`, `@`, tab or CR with an apostrophe (plain numbers are untouched). Raw input files are never rewritten. Residual: an apostrophe shows literally when a report CSV is opened in Excel, and a user who edits such a text cell by hand and confirms it can still turn it into a formula; that is ordinary spreadsheet behaviour, not something the build can prevent.
* **SQL verification is partial.** The independent SQL/SQLite implementation covers R01–R08, R10 (late postings only) and R12, plus both balance calculations: 10 of the 14 rules. R09, R11, R13, R14 and the other R10 variants are covered by the Python tests and the hand-written boundary fixture only, not by the SQL cross-check.
* **Test coverage.** Every implemented issue subtype has at least one automated test. Fuzzing covered the event, posting, count and opening-balance files but not the master and configuration files (those stop the run with a clear error).
* **Scale.** Tested on ~650 events / ~750 postings / ~125 counts. The engine scans lists linearly (fine at this size; not a big-data tool).

# Event-to-Posting Integrity Audit — Data Dictionary

All data is **synthetic** (fictional *Cinder Creek Distribution*). Raw inputs are plain CSV (UTF-8, header row, `\n`). Raw files never contain issue labels; the generator's expected anomalies live only in `data/fixtures/demo_expected_anomalies.json`. Schemas are enforced in `stocktrace/schema.py` and tested.

**Timestamps:** ISO-8601 with an explicit offset (`-07:00` local, or `Z`). A timestamp without an offset is invalid (R01). All comparisons use UTC.
**Quantities:** plain unsigned decimals (`5`, `5.25`, `.5`); no thousands separators, units, `+` sign or exponent. `0` is valid; blank/non-numeric/negative is rejected (R01).
**Loading:** files are read as UTF-8 (a BOM is accepted); leading/trailing whitespace in a cell is trimmed — the only normalisation applied, identically by the Python and SQL loaders. Master/configuration files that cannot be parsed stop the run with a clear error.

## Master and configuration files
| File | Column | Meaning |
|---|---|---|
| `sku_master.csv` | `sku_id` | Item key (`SKU-####`) |
| | `description`, `category` | Fictional description / category |
| | `base_uom` | Unit used for all balances (`EA` or `KG`) |
| | `unit_value_cad_sim` | **Simulated** unit value in CAD, used only for illustrative exposure and ABC ranking |
| | `count_tolerance_base` | Allowed absolute difference (base UoM) when comparing a count with a balance |
| | `effective_from`, `effective_to` | Optional validity window; `from` inclusive, `to` exclusive |
| `location_master.csv` | `location_id`, `zone` | Bin / area |
| | `active_flag` | Informational (status at snapshot); validity is decided by the dates |
| | `effective_from`, `effective_to` | Validity window (from inclusive, to exclusive) |
| `uom_conversions.csv` | `sku_id`, `uom`, `to_base_factor` | Explicit conversion to base UoM. The base UoM is implicit (factor 1). Nothing else is ever assumed. |
| `opening_balances.csv` | `timestamp`, `sku_id`, `location_id`, `quantity_in_base_uom` | Agreed baseline for both S and E |
| `shifts.csv` | `shift_id`, `shift_name`, `start_ts`, `end_ts` | Calendar; interval `[start, end)`; cutoff = `end_ts` |
| `rule_parameters.csv` | `key`, `value`, `description` | `snapshot_ts`, `match_qty_tolerance_base`, `possible_match_window_minutes`, `late_low_max_minutes`, `clock_skew_tolerance_minutes` (default 5 if absent), `handoff_medium_aging_shifts`, `handoff_high_aging_shifts`, `value_high_cad`, `value_medium_cad` |

## `business_events.csv` — operational event log (the floor record)
| Column | Meaning |
|---|---|
| `event_id` | Immutable ID (`EV-######`) |
| `event_timestamp` | When it physically happened (handheld clock; local offset or `Z`) |
| `shift_id` | Shift label recorded by the operator. The engine derives the shift from the timestamp; a disagreement is R10 `SHIFT_LABEL_MISMATCH` |
| `event_type` | `receipt`, `pick`, `transfer`, `adjustment`, `reversal` |
| `sku_id`, `quantity`, `uom` | Item and quantity in `uom` |
| `from_location`, `to_location` | Receipt: `to` only. Pick: `from` only. Transfer: both, different. Adjustment/reversal: exactly one side (describes the movement itself) |
| `status` | `completed` (physical effect), `pending` (no effect yet), `cancelled` (never happened) |
| `related_id` | Reversal: the event being reversed |
| `handoff_to_shift`, `handoff_status` | Cross-shift handoff: receiving shift and `OPEN`/`CLOSED`/blank |

## `system_postings.csv` — ERP/WMS posting extract
| Column | Meaning |
|---|---|
| `posting_id` | Immutable ID (`PO-######`) |
| `source_event_id` | Event the posting claims to support (may be blank) |
| `system_post_timestamp` | When the ERP recorded it (UTC `Z`) |
| `event_effective_timestamp` | Optional: the event time the ERP was told; a different shift than the event is R10 |
| `posting_type` | `RECEIPT` (+to), `ISSUE` (−from), `TRANSFER_OUT` (−from), `TRANSFER_IN` (+to), `ADJUSTMENT`/`REVERSAL` (+to or −from). Transfer legs carry both locations for integrity checks |
| `sku_id`, `quantity`, `uom` | Usually base UoM, sometimes the event's UoM |
| `from_location`, `to_location` | See posting type |
| `posting_status` | `POSTED` (in the ledger) or `FAILED` (attempted, not in the ledger; evidence for R06) |
| `related_posting_id` | `REVERSAL`: the posting being reversed |

## `physical_counts.csv` — point-in-time cycle counts
| Column | Meaning |
|---|---|
| `count_id`, `observed_at`, `sku_id`, `location_id` | Immutable ID and the exact count time |
| `counted_qty`, `uom` | Counted quantity. `0` is a valid count; blank is rejected |
| `count_status` | `COMPLETE`, `PARTIAL` (lower bound), `VOID` (ignored) |
| `evidence_ref` | Synthetic count-sheet reference |

## Derived concepts (not stored in raw files)
* **System balance S(sku, loc, t)** — opening + valid `POSTED` postings with posting time ≤ t.
* **Event balance E(sku, loc, t)** — opening + valid `completed` events with event time ≤ t.
* **Grain:** every balance is `sku_id × location_id × as-of time`; shift tables are `shift`; exceptions are one row per rule firing, grouped into **cases** (`case_key`).
* **Quarantine:** rows failing R01–R03, and all rows of an ID with conflicting duplicates (R04), are excluded from matching and balances.

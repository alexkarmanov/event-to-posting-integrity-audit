"""One-off writer for the HAND-SPECIFIED boundary fixture. Every row and every expected result below was worked out by
hand (see docs/UAT_PLAN_AND_RESULTS.md); nothing here calls the generator or the engine."""
import os
from stocktrace.loader import write_csv
from stocktrace.schema import SCHEMAS

OUT = "data/fixtures/boundary"
os.makedirs(OUT, exist_ok=True)
D, E, N, D2 = "2026-03-02-D", "2026-03-02-E", "2026-03-02-N", "2026-03-03-D"

sku = [
 dict(sku_id="SKU-B1", description="Boundary item one (each)", base_uom="EA", category="TEST", unit_value_cad_sim="10", count_tolerance_base="0", effective_from="", effective_to=""),
 dict(sku_id="SKU-B2", description="Boundary item two (kg)", base_uom="KG", category="TEST", unit_value_cad_sim="2", count_tolerance_base="0.5", effective_from="", effective_to=""),
 dict(sku_id="SKU-B3", description="Boundary item three (new item)", base_uom="EA", category="TEST", unit_value_cad_sim="3", count_tolerance_base="0", effective_from="2026-03-02T12:00:00-07:00", effective_to=""),
]
loc = [
 dict(location_id="LOC-A", zone="A", active_flag="Y", effective_from="", effective_to=""),
 dict(location_id="LOC-B", zone="B", active_flag="Y", effective_from="", effective_to=""),
 dict(location_id="LOC-OLD", zone="X", active_flag="N", effective_from="", effective_to="2026-03-02T10:00:00-07:00"),
]
conv = [dict(sku_id="SKU-B1", uom="CS", to_base_factor="12"), dict(sku_id="SKU-B2", uom="LB", to_base_factor="0.45359237"),
        dict(sku_id="SKU-B3", uom="CS", to_base_factor="6")]
O = "2026-03-02T06:00:00-07:00"
opening = [dict(timestamp=O, sku_id="SKU-B1", location_id="LOC-A", quantity_in_base_uom="150"),
           dict(timestamp=O, sku_id="SKU-B1", location_id="LOC-OLD", quantity_in_base_uom="5"),
           dict(timestamp=O, sku_id="SKU-B2", location_id="LOC-A", quantity_in_base_uom="50"),
           dict(timestamp=O, sku_id="SKU-B3", location_id="LOC-A", quantity_in_base_uom="10")]
shifts = [dict(shift_id=D, shift_name="Day", start_ts="2026-03-02T06:00:00-07:00", end_ts="2026-03-02T14:00:00-07:00"),
          dict(shift_id=E, shift_name="Evening", start_ts="2026-03-02T14:00:00-07:00", end_ts="2026-03-02T22:00:00-07:00"),
          dict(shift_id=N, shift_name="Night", start_ts="2026-03-02T22:00:00-07:00", end_ts="2026-03-03T06:00:00-07:00"),
          dict(shift_id=D2, shift_name="Day", start_ts="2026-03-03T06:00:00-07:00", end_ts="2026-03-03T14:00:00-07:00")]
params = [dict(key=k, value=v, description="boundary fixture") for k, v in [
 ("clock_skew_tolerance_minutes", "5"), ("snapshot_ts", "2026-03-03T10:00:00-07:00"), ("match_qty_tolerance_base", "0.005"), ("possible_match_window_minutes", "60"),
 ("late_low_max_minutes", "60"), ("handoff_medium_aging_shifts", "2"), ("handoff_high_aging_shifts", "4"),
 ("value_high_cad", "500"), ("value_medium_cad", "100")]]

def ev(i, ts, shift, typ, s, q, u, f, t, status="completed", rel="", hto="", hst=""):
    return dict(event_id="EV-B%02d" % i, event_timestamp=ts, shift_id=shift, event_type=typ, sku_id=s, quantity=q, uom=u,
                from_location=f, to_location=t, status=status, related_id=rel, handoff_to_shift=hto, handoff_status=hst)
def po(pid, src, ts, typ, s, q, u, f, t, eff="", status="POSTED", rel=""):
    return dict(posting_id=pid, source_event_id=src, system_post_timestamp=ts, event_effective_timestamp=eff, posting_type=typ,
                sku_id=s, quantity=q, uom=u, from_location=f, to_location=t, posting_status=status, related_posting_id=rel)

events = [
 ev(1, "2026-03-02T13:50:00-07:00", D, "pick", "SKU-B1", "5", "EA", "LOC-A", ""),
 ev(2, "2026-03-02T13:55:00-07:00", D, "pick", "SKU-B1", "3", "EA", "LOC-A", ""),
 ev(3, "2026-03-02T14:00:00-07:00", E, "pick", "SKU-B1", "2", "EA", "LOC-A", ""),
 ev(4, "2026-03-02T13:59:00-07:00", E, "pick", "SKU-B1", "2", "EA", "LOC-A", ""),          # label says E, timestamp says D
 ev(5, "2026-03-02T21:59:59Z", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),               # UTC timestamp = 14:59:59 local
 ev(6, "2026-03-02T15:00:00-07:00", E, "pick", "SKU-B1", "2", "CS", "LOC-A", ""),
 ev(7, "2026-03-02T16:00:00-07:00", E, "pick", "SKU-B1", "1", "CS", "LOC-A", ""),
 ev(8, "2026-03-02T16:30:00-07:00", E, "pick", "SKU-B2", "10", "LB", "LOC-A", ""),
 ev(9, "2026-03-02T16:35:00-07:00", E, "pick", "SKU-B2", "10", "LB", "LOC-A", ""),
 ev(10, "2026-03-02T16:40:00-07:00", E, "pick", "SKU-B2", "10", "LB", "LOC-A", ""),
 ev(11, "2026-03-02T17:00:00-07:00", E, "pick", "SKU-B1", "4", "EA", "LOC-A", ""),
 ev(12, "2026-03-02T17:10:00-07:00", E, "pick", "SKU-B1", "4", "EA", "LOC-A", ""),
 ev(13, "2026-03-02T17:20:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),
 ev(14, "2026-03-02T17:20:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),
 ev(15, "2026-03-03T08:00:00-07:00", D2, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),
 ev(16, "2026-03-02T20:00:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),
 ev(17, "2026-03-02T18:00:00-07:00", E, "pick", "SKU-B1", "7", "EA", "LOC-A", ""),
 ev(19, "2026-03-02T19:00:00-07:00", E, "transfer", "SKU-B1", "10", "EA", "LOC-A", "LOC-B"),
 ev(20, "2026-03-02T19:30:00-07:00", E, "transfer", "SKU-B1", "6", "EA", "LOC-A", "LOC-B"),
 ev(21, "2026-03-02T11:59:59-07:00", D, "pick", "SKU-B3", "1", "EA", "LOC-A", ""),
 ev(22, "2026-03-02T12:00:00-07:00", D, "pick", "SKU-B3", "1", "EA", "LOC-A", ""),
 ev(23, "2026-03-02T09:59:59-07:00", D, "pick", "SKU-B1", "1", "EA", "LOC-OLD", ""),
 ev(24, "2026-03-02T10:00:00-07:00", D, "pick", "SKU-B1", "1", "EA", "LOC-OLD", ""),
 ev(25, "2026-03-02T11:00:00-07:00", D, "receipt", "SKU-B1", "0", "EA", "", "LOC-A"),
 ev(26, "2026-03-02T11:10:00-07:00", D, "pick", "SKU-B1", "", "EA", "LOC-A", ""),
 ev(27, "2026-03-02T11:20:00-07:00", D, "pick", "SKU-B1", "1", "DZ", "LOC-A", ""),
 ev(28, "2026-03-02T12:00:00-07:00", D, "receipt", "SKU-B1", "12", "EA", "", "LOC-B"),
 ev(29, "2026-03-02T12:20:00-07:00", D, "reversal", "SKU-B1", "12", "EA", "LOC-B", "", rel="EV-B28"),
 ev(30, "2026-03-02T12:40:00-07:00", D, "reversal", "SKU-B1", "3", "EA", "LOC-A", "", rel="EV-NOPE"),
 ev(31, "2026-03-02T13:00:00-07:00", D, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),
 ev(32, "2026-03-02T20:30:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),
 ev(33, "2026-03-02T21:00:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),
 ev(34, "2026-03-02T09:00:00-07:00", D, "pick", "SKU-B1", "1", "EA", "LOC-A", "", status="pending", hto=E, hst="OPEN"),
 ev(35, "2026-03-02T21:30:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", "", status="pending", hto=D2, hst="OPEN"),
 ev(43, "2026-03-03T09:00:00-07:00", D2, "pick", "SKU-B1", "1", "EA", "LOC-A", "", status="pending", hto=D2, hst="OPEN"),
 ev(36, "2026-03-02T17:30:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", "", hto=N, hst="CLOSED"),
 ev(37, "2026-03-02T17:40:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", "", hto=N, hst="OPEN"),
 ev(38, "2026-03-02T13:00:00-07:00", D, "pick", "SKU-B3", "15", "EA", "LOC-A", ""),
 ev(39, "2026-03-02T18:30:00-07:00", E, "pick", "SKU-B1", "2", "EA", "LOC-A", "", status="cancelled"),
 ev(41, "2026-03-02T20:35:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),
 ev(42, "2026-03-02T20:36:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),
 ev(44, "2026-03-02T19:40:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),               # posted 40 min before it happened
 ev(45, "2026-03-02T19:45:00-07:00", E, "pick", "SKU-B1", "1", "EA", "LOC-A", ""),               # posted 3 min early: inside clock-skew tolerance
]
postings = [
 po("PO-B01", "EV-B01", "2026-03-02T21:00:00Z", "ISSUE", "SKU-B1", "5", "EA", "LOC-A", ""),         # exactly at cutoff
 po("PO-B02", "EV-B02", "2026-03-02T21:00:01Z", "ISSUE", "SKU-B1", "3", "EA", "LOC-A", ""),         # one second after
 po("PO-B03", "EV-B03", "2026-03-02T21:10:00Z", "ISSUE", "SKU-B1", "2", "EA", "LOC-A", ""),
 po("PO-B04", "EV-B04", "2026-03-02T20:59:30Z", "ISSUE", "SKU-B1", "2", "EA", "LOC-A", ""),
 po("PO-B05", "EV-B05", "2026-03-02T22:30:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B06", "EV-B06", "2026-03-02T22:10:00Z", "ISSUE", "SKU-B1", "24", "EA", "LOC-A", ""),
 po("PO-B07", "EV-B07", "2026-03-02T23:05:00Z", "ISSUE", "SKU-B1", "11", "EA", "LOC-A", ""),
 po("PO-B08", "EV-B08", "2026-03-02T23:35:00Z", "ISSUE", "SKU-B2", "4.5359", "KG", "LOC-A", ""),
 po("PO-B09", "EV-B09", "2026-03-02T23:40:00Z", "ISSUE", "SKU-B2", "4.54", "KG", "LOC-A", ""),
 po("PO-B10", "EV-B10", "2026-03-02T23:45:00Z", "ISSUE", "SKU-B2", "4.55", "KG", "LOC-A", ""),
 po("PO-B11a", "EV-B11", "2026-03-03T00:05:00Z", "ISSUE", "SKU-B1", "4", "EA", "LOC-A", ""),
 po("PO-B11b", "EV-B11", "2026-03-03T00:06:00Z", "ISSUE", "SKU-B1", "4", "EA", "LOC-A", ""),
 po("PO-B12a", "EV-B12", "2026-03-03T00:15:00Z", "ISSUE", "SKU-B1", "4", "EA", "LOC-A", ""),
 po("PO-B12b", "EV-B12", "2026-03-03T00:16:00Z", "REVERSAL", "SKU-B1", "4", "EA", "", "LOC-A", rel="PO-B12a"),
 po("PO-B12c", "EV-B12", "2026-03-03T00:17:00Z", "ISSUE", "SKU-B1", "4", "EA", "LOC-A", ""),
 po("PO-B13", "EV-B13", "2026-03-03T00:25:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B14", "EV-B14", "2026-03-03T00:25:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B17", "", "2026-03-03T01:05:00Z", "ISSUE", "SKU-B1", "7", "EA", "LOC-A", ""),
 po("PO-B18", "", "2026-03-03T01:20:00Z", "ISSUE", "SKU-B1", "9", "EA", "LOC-A", ""),
 po("PO-B19o", "EV-B19", "2026-03-03T02:05:00Z", "TRANSFER_OUT", "SKU-B1", "10", "EA", "LOC-A", "LOC-B"),
 po("PO-B20o", "EV-B20", "2026-03-03T02:35:00Z", "TRANSFER_OUT", "SKU-B1", "6", "EA", "LOC-A", "LOC-B"),
 po("PO-B20i", "EV-B20", "2026-03-03T02:36:00Z", "TRANSFER_IN", "SKU-B1", "6", "EA", "LOC-A", "LOC-B"),
 po("PO-B22", "EV-B22", "2026-03-02T19:05:00Z", "ISSUE", "SKU-B3", "1", "EA", "LOC-A", ""),
 po("PO-B23", "EV-B23", "2026-03-02T17:05:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-OLD", "", eff="2026-03-02T16:59:59Z"),
 po("PO-B25", "EV-B25", "2026-03-02T18:05:00Z", "RECEIPT", "SKU-B1", "0", "EA", "", "LOC-A"),
 po("PO-B28", "EV-B28", "2026-03-02T19:05:00Z", "RECEIPT", "SKU-B1", "12", "EA", "", "LOC-B"),
 po("PO-B29", "EV-B29", "2026-03-02T19:25:00Z", "REVERSAL", "SKU-B1", "12", "EA", "LOC-B", "", rel="PO-B28"),
 po("PO-B30", "EV-B30", "2026-03-02T19:55:00Z", "REVERSAL", "SKU-B1", "3", "EA", "LOC-A", "", rel="PO-B02"),
 po("PO-B31", "EV-B31", "2026-03-03T05:30:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B32", "EV-B32", "2026-03-03T06:10:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B33", "EV-B33", "2026-03-03T05:20:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B36", "EV-B36", "2026-03-03T00:35:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B37", "EV-B37", "2026-03-03T00:45:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B38", "EV-B38", "2026-03-02T20:05:00Z", "ISSUE", "SKU-B3", "15", "EA", "LOC-A", ""),
 po("PO-B39", "EV-B39", "2026-03-03T01:40:00Z", "ISSUE", "SKU-B1", "2", "EA", "LOC-A", ""),
 po("PO-B40", "EV-GHOST", "2026-03-03T01:50:00Z", "RECEIPT", "SKU-B2", "1", "KG", "", "LOC-B"),
 po("PO-B41", "EV-B41", "2026-03-03T03:45:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", "", eff="2026-03-03T05:00:00Z"),
 po("PO-B44", "EV-B44", "2026-03-03T02:00:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B45", "EV-B45", "2026-03-03T02:42:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", ""),
 po("PO-B42", "EV-B42", "2026-03-03T03:46:00Z", "ISSUE", "SKU-B1", "1", "EA", "LOC-A", "", eff="2026-03-03T04:59:59Z"),
]
postings.insert(postings.index([p for p in postings if p["posting_id"] == "PO-B13"][0]) + 1, dict([p for p in postings if p["posting_id"] == "PO-B13"][0]))  # identical re-delivery

def cnt(i, ts, s, l, q, u, st):
    return dict(count_id="CC-B%02d" % i, observed_at=ts, sku_id=s, location_id=l, counted_qty=q, uom=u, count_status=st, evidence_ref="BOUNDARY-SHEET line %d" % i)
counts = [
 cnt(1, "2026-03-02T14:00:00-07:00", "SKU-B1", "LOC-A", "134", "EA", "COMPLETE"),
 cnt(2, "2026-03-02T14:00:00-07:00", "SKU-B1", "LOC-A", "120", "EA", "PARTIAL"),
 cnt(3, "2026-03-02T14:00:00-07:00", "SKU-B1", "LOC-A", "141", "EA", "PARTIAL"),
 cnt(4, "2026-03-02T06:30:00-07:00", "SKU-B1", "LOC-B", "0", "EA", "COMPLETE"),
 cnt(5, "2026-03-02T14:00:00-07:00", "SKU-B1", "LOC-A", "", "EA", "COMPLETE"),
 cnt(6, "2026-03-02T22:00:00-07:00", "SKU-B2", "LOC-A", "36.4", "KG", "COMPLETE"),
 cnt(7, "2026-03-02T22:00:00-07:00", "SKU-B2", "LOC-A", "37", "KG", "COMPLETE"),
 cnt(8, "2026-03-03T08:30:00-07:00", "SKU-B1", "LOC-B", "16", "EA", "COMPLETE"),
 cnt(9, "2026-03-02T14:00:00-07:00", "SKU-B1", "LOC-A", "", "EA", "VOID"),
 cnt(10, "2026-03-02T12:02:00-07:00", "SKU-B3", "LOC-A", "9", "EA", "COMPLETE"),
]
for fname, rows in [("sku_master.csv", sku), ("location_master.csv", loc), ("uom_conversions.csv", conv),
                    ("opening_balances.csv", opening), ("business_events.csv", events), ("system_postings.csv", postings),
                    ("physical_counts.csv", counts), ("shifts.csv", shifts), ("rule_parameters.csv", params)]:
    write_csv(os.path.join(OUT, fname), SCHEMAS[fname], rows)

# ----- expected results, worked out by hand (independent of the engine)
exp_issues = [
 ("R01","CC-B05","MISSING_FIELD","MEDIUM"), ("R01","EV-B25","ZERO_QTY","LOW"), ("R01","EV-B26","MISSING_FIELD","MEDIUM"),
 ("R02","EV-B21","SKU_NOT_EFFECTIVE","MEDIUM"), ("R02","EV-B24","LOCATION_NOT_EFFECTIVE","MEDIUM"),
 ("R03","EV-B27","UNKNOWN_UOM_CODE","MEDIUM"),
 ("R04","PO-B13","IDENTICAL_REDELIVERY","MEDIUM"),
 ("R05","EV-B11","DUPLICATE_POSTING","HIGH"),
 ("R06","EV-B16","UNPOSTED","MEDIUM"), ("R06","EV-B17","POSSIBLE_MATCH","MEDIUM"),
 ("R07","PO-B18","NO_SOURCE_REFERENCE","MEDIUM"), ("R07","PO-B39","POSTING_FOR_CANCELLED_EVENT","MEDIUM"), ("R07","PO-B40","ORPHAN_REFERENCE","MEDIUM"),
 ("R08","EV-B07","QUANTITY_MISMATCH","MEDIUM"), ("R08","EV-B10","QUANTITY_MISMATCH","MEDIUM"),
 ("R09","EV-B19","MISSING_LEG","HIGH"),
 ("R10","EV-B02","LATE_POSTING","LOW"), ("R10","EV-B31","LATE_POSTING","HIGH"), ("R10","EV-B32","LATE_POSTING","MEDIUM"),
 ("R10","EV-B33","LATE_POSTING","LOW"), ("R10","EV-B04","SHIFT_LABEL_MISMATCH","LOW"), ("R10","EV-B44","POSTED_BEFORE_EVENT","LOW"), ("R10","EV-B41","EFFECTIVE_SHIFT_MISMATCH","LOW"),
 ("R11","EV-B34","UNRESOLVED_HANDOFF_AFTER_CUTOFF","MEDIUM"), ("R11","EV-B43","INVALID_HANDOFF_TARGET","LOW"), ("R11","EV-B37","STALE_OPEN_HANDOFF","LOW"),
 ("R12","CC-B03","UNEXPLAINED_COUNT_VARIANCE","MEDIUM"), ("R12","CC-B07","UNEXPLAINED_COUNT_VARIANCE","MEDIUM"),
 ("R13","SKU-B3|LOC-A","IMPOSSIBLE_BALANCE","HIGH"),
 ("R14","EV-B30","BROKEN_REFERENCE","MEDIUM"),
]
write_csv(os.path.join(OUT, "expected_issues.csv"), ["rule_id","record_id","subtype","severity"],
          [dict(rule_id=a, record_id=b, subtype=c, severity=d) for a, b, c, d in exp_issues])
exp_counts = [  # count_id, classification, system balance S, event balance E (hand-calculated)
 ("CC-B01","EXPLAINED_BY_ISSUES_AND_POSTING_LAG","140","134"), ("CC-B02","PARTIAL_NOT_COMPARABLE","140","134"),
 ("CC-B03","UNEXPLAINED_COUNT_VARIANCE","140","134"), ("CC-B04","AGREES","0","0"),
 ("CC-B06","AGREES","36.374","36.392"), ("CC-B07","UNEXPLAINED_COUNT_VARIANCE","36.374","36.392"),
 ("CC-B08","EXPLAINED_BY_OPEN_ISSUES","6","16"), ("CC-B09","VOID_EXCLUDED","",""), ("CC-B10","IN_FLIGHT_POSTING_LAG","10","9"),
]
write_csv(os.path.join(OUT, "expected_counts.csv"), ["count_id","classification","system_balance","event_balance"],
          [dict(count_id=a, classification=b, system_balance=c, event_balance=d) for a, b, c, d in exp_counts])
print("boundary fixture written")

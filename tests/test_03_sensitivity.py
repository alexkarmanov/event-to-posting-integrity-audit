"""Inject ONE anomaly into the otherwise clean fixture; assert exactly that issue appears. Plus non-error near misses."""
from datetime import timedelta

import pytest

from stocktrace.common import fmt_utc, parse_ts

from helpers import plain_picks, primary_keys, run_mutated


@pytest.fixture(scope="module")
def target(ds_clean):
    picks = plain_picks(ds_clean)
    assert len(picks) > 50
    e, p, sh = picks[len(picks) // 3]
    return e, p, sh


def _post(d, pid):
    return [r for r in d.rows("system_postings.csv") if r["posting_id"] == pid][0]


def test_baseline_has_no_primary_issues(ds_clean):
    assert primary_keys(run_mutated(ds_clean, lambda d: None)) == set()


def test_missing_posting_raises_r06(ds_clean, target):
    e, p, _ = target
    res = run_mutated(ds_clean, lambda d: d.tables["system_postings.csv"].remove(_post(d, p["posting_id"])))
    assert primary_keys(res) == {("R06", e["event_id"])}


def test_duplicate_posting_raises_r05(ds_clean, target):
    e, p, _ = target

    def mut(d):
        dup = dict(_post(d, p["posting_id"]), posting_id="PO-DUP-1", _row=9999)
        d.tables["system_postings.csv"].append(dup)
    assert primary_keys(run_mutated(ds_clean, mut)) == {("R05", e["event_id"])}


def test_quantity_change_raises_r08(ds_clean, target):
    e, p, _ = target

    def mut(d):
        r = _post(d, p["posting_id"])
        r["quantity"] = str(Decimal_plus(r["quantity"], 5))
    assert primary_keys(run_mutated(ds_clean, mut)) == {("R08", e["event_id"])}


def Decimal_plus(q, n):
    from decimal import Decimal
    return Decimal(q) + n


def test_late_posting_raises_r10(ds_clean, target):
    e, p, sh = target

    def mut(d):
        _post(d, p["posting_id"])["system_post_timestamp"] = fmt_utc(parse_ts(sh["end_ts"]) + timedelta(minutes=10))
    assert primary_keys(run_mutated(ds_clean, mut)) == {("R10", e["event_id"])}


def test_location_change_raises_r09(ds_clean, target):
    e, p, _ = target

    def mut(d):
        r = _post(d, p["posting_id"])
        r["from_location"] = "SHP-01"
    assert primary_keys(run_mutated(ds_clean, mut)) == {("R09", e["event_id"])}


def test_blank_quantity_event_raises_r01(ds_clean, target):
    e, p, _ = target

    def mut(d):
        row = dict([r for r in d.rows("business_events.csv") if r["event_id"] == e["event_id"]][0])
        row.update(event_id="EV-NEW-1", quantity="", _row=9999)
        d.tables["business_events.csv"].append(row)
    assert primary_keys(run_mutated(ds_clean, mut)) == {("R01", "EV-NEW-1")}


def test_orphan_posting_raises_r07(ds_clean, target):
    e, p, _ = target

    def mut(d):
        row = dict(_post(d, p["posting_id"]))
        row.update(posting_id="PO-ORPH-1", source_event_id="", quantity="999", _row=9999)
        d.tables["system_postings.csv"].append(row)
    keys = primary_keys(run_mutated(ds_clean, mut))
    assert ("R07", "PO-ORPH-1") in keys and len(keys) <= 2          # a 999-unit issue may also drive a bin negative


def test_identical_posting_row_raises_r04_only(ds_clean, target):
    e, p, _ = target
    res = run_mutated(ds_clean, lambda d: d.tables["system_postings.csv"].append(dict(_post(d, p["posting_id"]), _row=9999)))
    assert primary_keys(res) == {("R04", p["posting_id"])}


def test_count_variance_raises_r12(ds_clean):
    from decimal import Decimal

    def pick(d):
        for c in d.rows("physical_counts.csv"):
            if c["count_status"] == "COMPLETE" and c["uom"] == "EA" and Decimal(c["counted_qty"]) > 10:
                return c

    cid = pick(ds_clean)["count_id"]

    def mut(d):
        c = [r for r in d.rows("physical_counts.csv") if r["count_id"] == cid][0]
        c["counted_qty"] = str(Decimal(c["counted_qty"]) - 3)
    assert primary_keys(run_mutated(ds_clean, mut)) == {("R12", cid)}


# ---------------- near misses: look like errors, must not be flagged
def test_posting_exactly_at_cutoff_is_not_late(ds_clean, target):
    e, p, sh = target
    res = run_mutated(ds_clean, lambda d: _post(d, p["posting_id"]).update(system_post_timestamp=fmt_utc(parse_ts(sh["end_ts"]))))
    assert primary_keys(res) == set()


def test_posting_one_second_after_cutoff_is_late(ds_clean, target):
    e, p, sh = target
    res = run_mutated(ds_clean, lambda d: _post(d, p["posting_id"]).update(system_post_timestamp=fmt_utc(parse_ts(sh["end_ts"]) + timedelta(seconds=1))))
    assert primary_keys(res) == {("R10", e["event_id"])}


def test_same_quantity_in_other_uom_is_not_a_mismatch(ds_clean):
    """A case-quantity event posted in base units equals after normalisation."""
    from decimal import Decimal
    ev = {e["event_id"]: e for e in ds_clean.rows("business_events.csv")}
    cs = [(ev[p["source_event_id"]], p) for p in ds_clean.rows("system_postings.csv")
          if p["source_event_id"] in ev and ev[p["source_event_id"]]["uom"] == "CS" and p["uom"] != "CS"]
    assert cs, "clean fixture must contain a CS event posted in base units"
    assert primary_keys(run_mutated(ds_clean, lambda d: None)) == set()


def test_count_inside_tolerance_is_not_a_variance(ds_clean):
    from decimal import Decimal
    kg = [c for c in ds_clean.rows("physical_counts.csv") if c["count_status"] == "COMPLETE" and c["uom"] == "KG"]
    assert kg
    cid = kg[0]["count_id"]

    def mut(d):
        c = [r for r in d.rows("physical_counts.csv") if r["count_id"] == cid][0]
        c["counted_qty"] = str(Decimal(c["counted_qty"]) + Decimal("0.4"))     # tolerance is 0.5 kg
    assert primary_keys(run_mutated(ds_clean, mut)) == set()


def test_partial_count_below_balance_is_not_a_shortage(ds_clean):
    from decimal import Decimal
    cid = [c for c in ds_clean.rows("physical_counts.csv") if c["count_status"] == "PARTIAL"][0]["count_id"]

    def mut(d):
        c = [r for r in d.rows("physical_counts.csv") if r["count_id"] == cid][0]
        c["counted_qty"] = "1"
    assert primary_keys(run_mutated(ds_clean, mut)) == set()


def test_zero_count_is_valid_and_blank_count_is_rejected(ds_clean):
    zero = [c for c in ds_clean.rows("physical_counts.csv") if c["counted_qty"] == "0" and c["count_status"] == "COMPLETE"]
    assert zero
    assert primary_keys(run_mutated(ds_clean, lambda d: None)) == set()
    cid = zero[0]["count_id"]
    res = run_mutated(ds_clean, lambda d: [r for r in d.rows("physical_counts.csv") if r["count_id"] == cid][0].update(counted_qty=""))
    assert primary_keys(res) == {("R01", cid)}


def test_event_at_exact_shift_start_belongs_to_new_shift(ds_clean):
    starts = {s["start_ts"]: s["shift_id"] for s in ds_clean.rows("shifts.csv")}
    exact = [e for e in ds_clean.rows("business_events.csv") if e["event_timestamp"] in starts]
    assert exact
    for e in exact:
        assert e["shift_id"] == starts[e["event_timestamp"]]

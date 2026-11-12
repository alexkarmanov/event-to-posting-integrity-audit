"""Clean fixture yields nothing; hand-specified boundary fixture matches exactly."""
import csv

import pytest

from conftest import path


def test_clean_fixture_has_no_exceptions(res_clean):
    assert [i.exception_id for i in res_clean.issues] == []


def test_clean_fixture_covers_benign_patterns(res_clean):
    classes = {r["class"] for r in res_clean.count_table}
    assert {"AGREES", "IN_FLIGHT_POSTING_LAG", "PARTIAL_NOT_COMPARABLE", "VOID_EXCLUDED"} <= classes
    assert len(res_clean.v.events) > 500 and len(res_clean.v.postings) > 500


def _expected_issues():
    rows = list(csv.DictReader(open(path("data", "fixtures", "boundary", "expected_issues.csv"))))
    return {(r["rule_id"], r["record_id"], r["subtype"], r["severity"]) for r in rows}


def test_boundary_issue_set_matches_hand_expectation_exactly(res_boundary):
    got = {(i.rule_id, i.key_id, i.subtype, i.severity) for i in res_boundary.issues}
    exp = _expected_issues()
    assert got - exp == set(), "unexpected issues"
    assert exp - got == set(), "missing issues"


def test_boundary_covers_every_rule():
    assert {r[0] for r in _expected_issues()} == {"R%02d" % i for i in range(1, 15)}


def test_boundary_count_classification_and_balances(res_boundary):
    exp = {r["count_id"]: r for r in csv.DictReader(open(path("data", "fixtures", "boundary", "expected_counts.csv")))}
    seen = set()
    for row in res_boundary.count_table:
        e = exp[row["count"].id]
        seen.add(row["count"].id)
        assert row["class"] == e["classification"], row["count"].id
        if e["system_balance"]:
            assert abs(float(row["S"]) - float(e["system_balance"])) < 1e-6
            assert abs(float(row["E"]) - float(e["event_balance"])) < 1e-6
    assert seen == set(exp)


# negative controls: records that look suspicious but must NOT be flagged for the named rule
NEGATIVE = {
    "R01": ["EV-B22"],                 # SKU effective at exactly effective_from; zero-qty event is accepted (checked below)
    "R02": ["EV-B22", "EV-B23"],       # boundary-valid SKU / location (inclusive start, exclusive end)
    "R03": ["EV-B06", "EV-B07"],       # case quantities with an explicit conversion
    "R04": ["EV-B14", "PO-B14"],       # twin picks: same SKU/qty/time, different immutable IDs
    "R05": ["EV-B12", "EV-B13", "EV-B14", "EV-B20"],   # reverse/repost chain; twin picks; two transfer legs
    "R06": ["EV-B15", "EV-B01"],       # event in a shift that is still open; posted event
    "R07": ["PO-B17", "PO-B12b", "PO-B29"],   # consumed possible match; legitimate reversal postings
    "R08": ["EV-B06", "EV-B08", "EV-B09"],    # equal after normalisation; inside rounding tolerance
    "R09": ["EV-B20", "EV-B12"],
    "R10": ["EV-B01", "EV-B03", "EV-B05", "EV-B42", "EV-B45"],   # at cutoff; event at shift start; UTC timestamp; effective 1s before cutoff
    "R11": ["EV-B35", "EV-B36"],       # handoff to an open shift; properly closed handoff
    "R12": ["CC-B01", "CC-B02", "CC-B04", "CC-B06", "CC-B08", "CC-B10"],
    "R13": ["SKU-B1|LOC-A", "SKU-B1|LOC-B"],
    "R14": ["EV-B29", "PO-B12b"],      # valid full reversal
}


@pytest.mark.parametrize("rule", sorted(NEGATIVE))
def test_negative_controls_not_flagged(res_boundary, rule):
    flagged = {i.key_id for i in res_boundary.issues if i.rule_id == rule}
    assert not (flagged & set(NEGATIVE[rule])), flagged & set(NEGATIVE[rule])


def test_zero_quantity_is_accepted_not_rejected(res_boundary):
    assert "EV-B25" in {e.id for e in res_boundary.v.events}
    assert "EV-B26" not in {e.id for e in res_boundary.v.events}       # blank quantity is rejected


def test_posting_at_cutoff_is_timely_but_one_second_later_is_not(res_boundary):
    late = {i.key_id for i in res_boundary.issues if i.rule_id == "R10" and i.subtype == "LATE_POSTING"}
    assert "EV-B01" not in late and "EV-B02" in late

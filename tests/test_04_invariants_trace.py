"""Reconciliation invariants, traceability, SQL cross-check and the planted-anomaly check."""
import csv
import json
from collections import defaultdict
from decimal import Decimal

import pytest

from stocktrace import engine, sqlcheck
from stocktrace.common import ZERO
from stocktrace.engine import _gap
from stocktrace.loader import load_dataset

from conftest import path


@pytest.mark.parametrize("name", ["clean", "demo", "boundary"])
def test_gap_partition_identity(name, request):
    """S - E at every count equals the sum of per-item gaps: no movement is lost or counted twice."""
    res = request.getfixturevalue("res_" + name)
    for row in res.count_table:
        if row["S"] is None:
            continue
        c = row["count"]
        total = sum((_gap(it, c.sku, c.loc, c.observed) for it in res.items), ZERO)
        assert total == row["S"] - row["E"], c.id


@pytest.mark.parametrize("name", ["clean", "demo"])
def test_transfers_conserve_stock(name, request):
    res = request.getfixturevalue("res_" + name)
    for e in res.v.events:
        if e.type == "transfer" and e.status == "completed":
            assert sum((d for _, d in e.effects()), ZERO) == 0
    # every posted transfer pair that is not flagged nets to zero per SKU
    ds = request.getfixturevalue("ds_" + name)
    flagged = {r for i in res.issues for r in i.related}
    by_src = defaultdict(list)
    for p in res.v.postings:
        if p.status == "POSTED" and p.type in ("TRANSFER_OUT", "TRANSFER_IN") and p.source:
            by_src[p.source].append(p)
    for src, ps in by_src.items():
        if src in flagged or any(p.id in flagged for p in ps) or len(ps) != 2:
            continue
        assert sum((d for p in ps for _, d in p.effects()), ZERO) == 0, src


def test_uncounted_combinations_are_not_treated_as_counted(res_demo):
    counted_ids = {c.id for c in res_demo.v.counts}
    assert {r["count"].id for r in res_demo.count_table} == counted_ids
    from stocktrace import kpis
    k = kpis.compute(res_demo)
    assert k["counted_pairs"] <= k["eligible_pairs"] and k["count_coverage_pct"] < 100


@pytest.mark.parametrize("name", ["clean", "demo", "boundary"])
def test_repeated_runs_add_no_issues(name, request):
    ds = request.getfixturevalue("ds_" + name)
    a, b = engine.run(ds), engine.run(ds.clone())
    assert [(i.exception_id, i.severity, i.is_primary) for i in a.issues] == [(i.exception_id, i.severity, i.is_primary) for i in b.issues]
    assert a.run_id == b.run_id


def test_future_records_cannot_change_an_earlier_as_of_result(ds_demo, res_demo):
    """Drop every event/posting later than a count's time: that count's S and E must be unchanged."""
    from stocktrace.common import parse_ts
    picked = [r for r in res_demo.count_table if r["S"] is not None][::17][:6]
    assert picked
    for row in picked:
        c = row["count"]
        d = ds_demo.clone()
        d.tables["system_postings.csv"] = [p for p in d.rows("system_postings.csv")
                                           if p["system_post_timestamp"].strip() == "" or parse_ts(p["system_post_timestamp"]) <= c.observed
                                           if _safe_le(p["system_post_timestamp"], c.observed)]
        d.tables["business_events.csv"] = [e for e in d.rows("business_events.csv") if _safe_le(e["event_timestamp"], c.observed)]
        r2 = engine.run(d)
        again = [x for x in r2.count_table if x["count"].id == c.id][0]
        assert again["S"] == row["S"] and again["E"] == row["E"], c.id


def _safe_le(ts, t):
    from stocktrace.common import parse_ts
    try:
        return parse_ts(ts) <= t
    except ValueError:
        return True


# ---------------- traceability
def _raw_index(ds):
    idx = {}
    for fname, col in (("business_events.csv", "event_id"), ("system_postings.csv", "posting_id"), ("physical_counts.csv", "count_id")):
        for r in ds.rows(fname):
            idx[(fname, r["_row"])] = r[col]
    return idx


@pytest.mark.parametrize("name", ["demo", "boundary"])
def test_every_issue_source_points_to_an_existing_raw_record(name, request):
    res = request.getfixturevalue("res_" + name)
    ds = request.getfixturevalue("ds_" + name)
    idx = _raw_index(ds)
    ids = {fname: {v for (f, _), v in idx.items() if f == fname} for fname in ("business_events.csv", "system_postings.csv", "physical_counts.csv")}
    checked = 0
    for i in res.issues:
        for fname, rid, row in i.sources:
            assert rid in ids[fname] or rid.startswith(fname.replace(".csv", "") + "#row"), (i.exception_id, fname, rid)
            if row:
                assert idx[(fname, row)] == rid or rid.startswith(fname.replace(".csv", "") + "#row"), (i.exception_id, fname, row)
                checked += 1
    assert checked >= 10


def test_at_least_ten_issue_to_source_links_resolve_by_hand(res_demo, ds_demo):
    """Spot check straight from the CSV text, without engine objects."""
    rows = list(csv.DictReader(open(path("data", "raw", "demo", "system_postings.csv"))))
    by_id = {r["posting_id"]: r for r in rows}
    seen = 0
    for i in res_demo.issues:
        for fname, rid, row in i.sources:
            if fname == "system_postings.csv" and rid in by_id:
                assert by_id[rid]["posting_id"] == rid
                seen += 1
    assert seen >= 10


def test_each_exception_has_required_fields(res_demo):
    for i in res_demo.issues:
        assert i.exception_id.startswith("EX-R") and i.rule_id and i.severity in ("HIGH", "MEDIUM", "LOW")
        assert i.reason and i.hypothesis is not None and i.owner_role and i.review_action and i.status == "OPEN"
        assert i.is_primary == (i.priority_rank > 0)
    ids = [i.exception_id for i in res_demo.issues]
    assert len(ids) == len(set(ids))


def test_priority_is_deterministic_and_dense(res_demo):
    ranks = sorted(i.priority_rank for i in res_demo.issues if i.is_primary)
    assert ranks == list(range(1, len(ranks) + 1))
    scores = [i.priority_score for i in sorted((i for i in res_demo.issues if i.is_primary), key=lambda x: x.priority_rank)]
    assert scores == sorted(scores, reverse=True)


# ---------------- independent SQL / Python
@pytest.mark.parametrize("name", ["demo", "clean", "boundary"])
def test_sql_and_python_agree(name):
    r = sqlcheck.compare(path(*{"demo": ("data", "raw", "demo"), "clean": ("data", "fixtures", "clean"),
                               "boundary": ("data", "fixtures", "boundary")}[name]))
    assert r["only_sql"] == [] and r["only_python"] == []
    assert r["balance_mismatches"] == [] and r["class_mismatches"] == []
    assert r["n_counts_compared"] > 0


def test_sql_actually_detects_something_on_demo():
    r = sqlcheck.compare(path("data", "raw", "demo"))
    assert r["n_sql"] >= 40


# ---------------- planted anomalies (generator-side manifest, never seen by the engine)
def test_every_planted_anomaly_is_found_with_the_planted_subtype(res_demo):
    m = json.load(open(path("data", "fixtures", "demo_expected_anomalies.json")))
    got = defaultdict(set)
    for i in res_demo.issues:
        got[(i.rule_id, i.key_id)].add(i.subtype)
    missing = [(p["rule_id"], p["record_id"]) for p in m["planted"] if (p["rule_id"], p["record_id"]) not in got]
    wrong = [(p["rule_id"], p["record_id"], p["subtype"]) for p in m["planted"]
             if (p["rule_id"], p["record_id"]) in got and p["subtype"] not in got[(p["rule_id"], p["record_id"])]]
    assert missing == [] and wrong == []


def test_only_consequential_secondary_negatives_are_unplanted(res_demo):
    """No false positives: anything not planted must be a secondary R13 caused by a planted case."""
    m = json.load(open(path("data", "fixtures", "demo_expected_anomalies.json")))
    planted = {(p["rule_id"], p["record_id"]) for p in m["planted"]}
    planted_cases = {i.case_key for i in res_demo.issues if (i.rule_id, i.key_id) in planted}
    for i in res_demo.issues:
        if (i.rule_id, i.key_id) in planted:
            continue
        assert i.rule_id == "R13" and i.subtype == "SYSTEM_NEGATIVE_AT_CUTOFF" and not i.is_primary, (i.exception_id, i.reason)
        assert i.case_key in planted_cases, i.exception_id


def test_demo_near_misses_are_not_flagged(res_demo):
    m = json.load(open(path("data", "fixtures", "demo_expected_anomalies.json")))
    assert len(m["near_misses"]) >= 10
    flagged = defaultdict(set)
    for i in res_demo.issues:
        flagged[i.key_id].add(i.rule_id)
    bad = [(n["record_id"], r) for n in m["near_misses"] for r in n["rules_not_expected"] if r in flagged.get(n["record_id"], set())]
    assert bad == [], bad

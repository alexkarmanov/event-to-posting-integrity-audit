"""Independent recomputation of every simulated CAD value from the raw CSV text (own arithmetic, own conversion code).
Defines exactly which rules carry a value, and what the number means."""
import csv
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

import pytest

from stocktrace import sqlcheck

from conftest import path

VALUED = {"R05", "R06", "R07", "R08", "R09", "R12", "R13", "R14"}
NOT_VALUED = {"R01", "R02", "R03", "R04", "R10", "R11"}


def _rows(name, *p):
    return list(csv.DictReader(open(path(*p, name), encoding="utf-8-sig")))


@pytest.fixture(scope="module")
def raw():
    base = ("data", "raw", "demo")
    sku = {r["sku_id"]: r for r in _rows("sku_master.csv", *base)}
    conv = {(r["sku_id"], r["uom"]): Decimal(r["to_base_factor"]) for r in _rows("uom_conversions.csv", *base)}
    ev = {r["event_id"]: r for r in _rows("business_events.csv", *base)}          # first row wins (no value-bearing case sits on a duplicated ID)
    po = defaultdict(list)
    for r in _rows("system_postings.csv", *base):
        po[r["source_event_id"]].append(r)
    po_id = {r["posting_id"]: r for rs in po.values() for r in rs}
    return sku, conv, ev, po, po_id


def base_qty(raw, sku_id, uom, qty):
    sku, conv, *_ = raw
    f = Decimal(1) if uom == sku[sku_id]["base_uom"] else conv[(sku_id, uom)]
    return (Decimal(qty) * f).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def price(raw, sku_id):
    return Decimal(raw[0][sku_id]["unit_value_cad_sim"])


def money(x):
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def test_only_the_documented_rules_carry_a_value(res_demo):
    with_value = {i.rule_id for i in res_demo.issues if i.value_cad_sim > 0}
    assert with_value <= VALUED
    assert VALUED - {"R14"} <= with_value                       # every valued rule actually produces one in the demo
    assert not (with_value & NOT_VALUED)


@pytest.mark.parametrize("rule", sorted(NOT_VALUED))
def test_unvalued_rules_are_always_zero(res_demo, res_boundary, rule):
    for res in (res_demo, res_boundary):
        assert all(i.value_cad_sim == 0 for i in res.issues if i.rule_id == rule)


def test_r06_value_is_event_base_quantity_times_price(res_demo, raw):
    n = 0
    for i in res_demo.issues:
        if i.rule_id == "R06":
            e = raw[2][i.key_id]
            assert i.value_cad_sim == money(base_qty(raw, e["sku_id"], e["uom"], e["quantity"]) * price(raw, e["sku_id"])), i.exception_id
            n += 1
    assert n >= 4


def test_r05_value_is_the_extra_postings_base_quantity_times_price(res_demo, raw):
    exp_type = {"receipt": "RECEIPT", "pick": "ISSUE", "transfer": "TRANSFER_OUT"}
    n = 0
    for i in res_demo.issues:
        if i.rule_id == "R05":
            e = raw[2][i.key_id]
            same = sorted([p for p in raw[3][e["event_id"]] if p["posting_type"] == exp_type[e["event_type"]]],
                          key=lambda p: (p["system_post_timestamp"], p["posting_id"]))
            extra = sum((base_qty(raw, p["sku_id"], p["uom"], p["quantity"]) for p in same[1:]), Decimal(0))
            assert i.value_cad_sim == money(extra * price(raw, e["sku_id"])), i.exception_id
            n += 1
    assert n == 3


def test_r07_value_is_the_unsupported_posting_quantity_times_price(res_demo, raw):
    allp = {r["posting_id"]: r for r in _rows("system_postings.csv", "data", "raw", "demo")}
    for i in res_demo.issues:
        if i.rule_id == "R07":
            p = allp[i.key_id]
            assert i.value_cad_sim == money(base_qty(raw, p["sku_id"], p["uom"], p["quantity"]) * price(raw, p["sku_id"])), i.exception_id


def test_r08_value_is_the_normalised_quantity_difference_times_price(res_demo, raw):
    exp_type = {"receipt": "RECEIPT", "pick": "ISSUE", "transfer": "TRANSFER_OUT"}
    n = 0
    for i in res_demo.issues:
        if i.rule_id == "R08":
            e = raw[2][i.key_id]
            eb = base_qty(raw, e["sku_id"], e["uom"], e["quantity"])
            diffs = [abs(base_qty(raw, p["sku_id"], p["uom"], p["quantity"]) - eb) for p in raw[3][e["event_id"]]
                     if p["posting_type"] in (exp_type[e["event_type"]], "TRANSFER_IN") and abs(base_qty(raw, p["sku_id"], p["uom"], p["quantity"]) - eb) > Decimal("0.005")]
            assert i.value_cad_sim == money(diffs[0] * price(raw, e["sku_id"])), i.exception_id
            n += 1
    assert n == 4


def test_r09_value_is_the_misposted_movement_quantity_priced_at_the_event_sku(res_demo, raw):
    n = 0
    for i in res_demo.issues:
        if i.rule_id != "R09":
            continue
        e = raw[2][i.key_id]
        ps = raw[3][e["event_id"]]
        if i.subtype == "MISSING_LEG":
            q = base_qty(raw, e["sku_id"], e["uom"], e["quantity"])           # the whole movement is unrecorded on one side
        elif i.subtype == "MOVEMENT_TYPE_MISMATCH":
            assert i.value_cad_sim == 0                                          # no quantity difference to value
            continue
        else:
            bad = [p for p in ps if p["sku_id"] != e["sku_id"] or p["from_location"] != e["from_location"]
                   or p["to_location"] != e["to_location"]][0]
            q = base_qty(raw, bad["sku_id"], bad["uom"], bad["quantity"])
        assert i.value_cad_sim == money(q * price(raw, e["sku_id"])), i.exception_id
        n += 1
    assert n == 4


def test_r12_value_is_count_minus_event_ledger_times_price_using_the_sql_balances(res_demo, raw):
    _, sql_counts = sqlcheck.sql_results(path("data", "raw", "demo"))
    n = 0
    for i in res_demo.issues:
        if i.rule_id == "R12":
            q = sql_counts[i.key_id]
            assert i.value_cad_sim == money(abs(Decimal(str(q["C"])) - Decimal(str(q["E"]))) * price(raw, q["sku"])), i.exception_id
            n += 1
    assert n == 4


def test_r13_value_is_the_negative_balance_times_price(res_demo, raw):
    n = 0
    for i in res_demo.issues:
        if i.rule_id == "R13" and i.subtype == "IMPOSSIBLE_BALANCE":
            sku, loc = i.key_id.split("|")
            opening = sum((Decimal(r["quantity_in_base_uom"]) for r in _rows("opening_balances.csv", "data", "raw", "demo")
                           if r["sku_id"] == sku and r["location_id"] == loc), Decimal(0))
            moves = []
            for e in raw[2].values():
                if e["status"] == "completed" and e["sku_id"] == sku:
                    q = base_qty(raw, sku, e["uom"], e["quantity"])
                    if e["to_location"] == loc:
                        moves.append((e["event_timestamp"], q))
                    if e["from_location"] == loc:
                        moves.append((e["event_timestamp"], -q))
            from stocktrace.common import parse_ts
            bal = opening
            for _, d in sorted(moves, key=lambda m: (parse_ts(m[0]), m[1] < 0)):
                bal += d
                if bal < 0:
                    break
            assert bal < 0 and i.value_cad_sim == money(abs(bal) * price(raw, sku))
            n += 1
    assert n == 1


def test_r14_value_is_the_reversal_quantity_difference_times_price(res_demo, raw):
    allp = {r["posting_id"]: r for r in _rows("system_postings.csv", "data", "raw", "demo")}
    seen = set()
    for i in res_demo.issues:
        if i.rule_id != "R14":
            continue
        if i.subtype == "QUANTITY_MISMATCH":
            e = raw[2][i.key_id]
            o = raw[2][e["related_id"]]
            d = abs(base_qty(raw, e["sku_id"], e["uom"], e["quantity"]) - base_qty(raw, o["sku_id"], o["uom"], o["quantity"]))
            assert i.value_cad_sim == money(d * price(raw, e["sku_id"]))
        elif i.subtype == "POSTING_QTY_MISMATCH":
            p = allp[i.key_id]
            r = allp[p["related_posting_id"]]
            d = abs(base_qty(raw, p["sku_id"], p["uom"], p["quantity"]) - base_qty(raw, r["sku_id"], r["uom"], r["quantity"]))
            assert i.value_cad_sim == money(d * price(raw, p["sku_id"]))
        else:
            assert i.value_cad_sim == 0, i.subtype            # broken references have no quantity difference
        seen.add(i.subtype)
    assert {"QUANTITY_MISMATCH", "POSTING_QTY_MISMATCH", "BROKEN_REFERENCE"} <= seen


def test_total_exposure_is_the_sum_of_primary_case_values_only(res_demo):
    from stocktrace import kpis
    k = kpis.compute(res_demo)
    assert k["exposure_value_cad_sim"] == sum((i.value_cad_sim for i in res_demo.issues if i.is_primary), Decimal(0))
    secondary_value = sum((i.value_cad_sim for i in res_demo.issues if not i.is_primary), Decimal(0))
    assert secondary_value > 0 and k["exposure_value_cad_sim"] != k["exposure_value_cad_sim"] + secondary_value   # secondary excluded


def test_value_escalation_of_severity_applies_only_to_the_documented_rules(res_demo):
    hi = Decimal("500")
    for i in res_demo.issues:
        if i.value_cad_sim >= hi and i.severity != "HIGH":
            assert i.rule_id in {"R14", "R11", "R10", "R04"} or i.forced_severity, i.exception_id    # never a silently un-escalated R05-R08/R12

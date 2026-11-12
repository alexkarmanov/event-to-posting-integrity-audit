"""Regression tests for defects found in the independent review (independent review) and for previously untested subtypes."""
import os
import shutil

import openpyxl
import pytest

from stocktrace import cli, engine, loader, sqlcheck
from stocktrace.common import parse_qty
from stocktrace.schema import SCHEMAS

from conftest import path


def ev(**k):
    r = dict(event_id="", event_timestamp="2026-03-02T19:00:00-07:00", shift_id="2026-03-02-E", event_type="pick", sku_id="SKU-B2",
             quantity="1", uom="KG", from_location="LOC-A", to_location="", status="completed", related_id="", handoff_to_shift="",
             handoff_status="", _row=9000)
    r.update(k)
    return r


def po(**k):
    r = dict(posting_id="", source_event_id="", system_post_timestamp="2026-03-03T02:10:00Z", event_effective_timestamp="",
             posting_type="ISSUE", sku_id="SKU-B2", quantity="1", uom="KG", from_location="LOC-A", to_location="",
             posting_status="POSTED", related_posting_id="", _row=9000)
    po_ = r
    po_.update(k)
    return po_


def keys(res):
    return {(i.rule_id, i.key_id, i.subtype) for i in res.issues}


def mutated(ds, evs=(), pos=(), fn=None):
    d = ds.clone()
    d.tables["business_events.csv"] += list(evs)
    d.tables["system_postings.csv"] += list(pos)
    if fn:
        fn(d)
    return engine.run(d)


# ---------------- found by the independent SQL oracle: Python and SQL disagreed on number formats
@pytest.mark.parametrize("value", [" 5 ", "+5", ".5", "5.", "007", "5,0", "1e1"])
def test_python_and_sql_agree_on_odd_number_formats(tmp_path, ds_clean, value):
    d = ds_clean.clone()
    row = [r for r in d.rows("business_events.csv") if r["event_type"] == "pick"][5]
    row["quantity"] = value
    shutil.copytree(path("data", "fixtures", "clean"), str(tmp_path / "c"))
    loader.write_csv(str(tmp_path / "c" / "business_events.csv"), SCHEMAS["business_events.csv"], d.rows("business_events.csv"))
    r = sqlcheck.compare(str(tmp_path / "c"))
    assert r["only_sql"] == [] and r["only_python"] == []


def test_signed_quantity_is_not_a_plain_decimal():
    with pytest.raises(ValueError):
        parse_qty("+5")


def test_surrounding_whitespace_is_trimmed_on_load(tmp_path):
    shutil.copytree(path("data", "fixtures", "boundary"), str(tmp_path / "b"))
    p = str(tmp_path / "b" / "business_events.csv")
    t = open(p, encoding="utf-8").read().replace("EV-B01,", "EV-B01 ,", 1)
    open(p, "w", encoding="utf-8").write(t)
    ds = loader.load_dataset(str(tmp_path / "b"))
    assert ds.rows("business_events.csv")[0]["event_id"] == "EV-B01"


def test_csv_with_utf8_bom_loads(tmp_path):
    shutil.copytree(path("data", "fixtures", "boundary"), str(tmp_path / "b"))
    for f in os.listdir(str(tmp_path / "b")):
        if f.endswith(".csv"):
            p = str(tmp_path / "b" / f)
            text = open(p, encoding="utf-8").read()
            open(p, "w", encoding="utf-8").write("\ufeff" + text)
    ds = loader.load_dataset(str(tmp_path / "b"))
    assert {(i.rule_id, i.key_id) for i in engine.run(ds).issues} >= {("R05", "EV-B11")}


def test_corrupt_master_data_gives_a_clear_error_not_a_traceback(ds_boundary):
    d = ds_boundary.clone()
    d.tables["sku_master.csv"][0]["effective_from"] = "not-a-date"
    with pytest.raises(loader.SchemaError) as ex:
        engine.run(d)
    assert "master" in str(ex.value)


# ---------------- ordering / timing edge cases
def test_receipt_and_pick_in_the_same_second_do_not_raise_negative_balance(ds_boundary):
    r = mutated(ds_boundary,
                [ev(event_id="EV-X1", quantity="3"), ev(event_id="EV-X2", quantity="5", event_type="receipt", from_location="LOC-B", to_location="LOC-B")][:1] +
                [ev(event_id="EV-X2", quantity="5", event_type="receipt", from_location="", to_location="LOC-A")],
                [po(posting_id="PO-X1", source_event_id="EV-X1", quantity="3"),
                 po(posting_id="PO-X2", source_event_id="EV-X2", quantity="5", posting_type="RECEIPT", from_location="", to_location="LOC-A")])
    assert not [k for k in keys(r) if k[0] == "R13" and k[1] == "SKU-B2|LOC-A"]


def test_transfer_with_legs_on_both_sides_of_the_cutoff_is_one_late_posting(ds_boundary):
    r = mutated(ds_boundary,
                [ev(event_id="EV-X3", event_timestamp="2026-03-02T13:30:00-07:00", shift_id="2026-03-02-D", event_type="transfer",
                    sku_id="SKU-B1", uom="EA", quantity="2", from_location="LOC-A", to_location="LOC-B")],
                [po(posting_id="PO-X3o", source_event_id="EV-X3", system_post_timestamp="2026-03-02T20:50:00Z", posting_type="TRANSFER_OUT",
                    sku_id="SKU-B1", uom="EA", quantity="2", from_location="LOC-A", to_location="LOC-B"),
                 po(posting_id="PO-X3i", source_event_id="EV-X3", system_post_timestamp="2026-03-02T21:20:00Z", posting_type="TRANSFER_IN",
                    sku_id="SKU-B1", uom="EA", quantity="2", from_location="LOC-A", to_location="LOC-B")])
    k = keys(r)
    assert ("R10", "EV-X3", "LATE_POSTING") in k and not [x for x in k if x[1] == "EV-X3" and x[0] != "R10"]


def test_cancelled_event_with_properly_reversed_posting_is_not_flagged(ds_boundary):
    r = mutated(ds_boundary,
                [ev(event_id="EV-X4", status="cancelled", sku_id="SKU-B1", uom="EA")],
                [po(posting_id="PO-X4a", source_event_id="EV-X4", sku_id="SKU-B1", uom="EA"),
                 po(posting_id="PO-X4b", source_event_id="EV-X4", posting_type="REVERSAL", sku_id="SKU-B1", uom="EA",
                    from_location="", to_location="LOC-A", related_posting_id="PO-X4a", system_post_timestamp="2026-03-03T02:20:00Z")])
    assert not [k for k in keys(r) if "X4" in k[1]]


def test_posting_recorded_before_the_event_is_flagged_but_small_clock_skew_is_not(res_boundary):
    k = {(i.rule_id, i.key_id, i.subtype) for i in res_boundary.issues}
    assert ("R10", "EV-B44", "POSTED_BEFORE_EVENT") in k          # 40 minutes early
    assert not [x for x in k if x[1] == "EV-B45"]                  # 3 minutes early: inside the 5-minute tolerance


# ---------------- subtypes that previously had no dedicated test
def test_invalid_enum_value_is_rejected(ds_boundary):
    assert ("R01", "EV-X5", "INVALID_VALUE") in keys(mutated(ds_boundary, [ev(event_id="EV-X5", event_type="pickup")]))


def test_event_outside_shift_calendar_is_rejected(ds_boundary):
    r = mutated(ds_boundary, [ev(event_id="EV-X6", event_timestamp="2026-03-02T05:00:00-07:00")])
    assert ("R01", "EV-X6", "OUTSIDE_SHIFT_CALENDAR") in keys(r)


def test_negative_opening_balance_is_rejected(ds_boundary):
    def mut(d):
        d.tables["opening_balances.csv"].append(dict(timestamp="2026-03-02T06:00:00-07:00", sku_id="SKU-B2", location_id="LOC-B",
                                                    quantity_in_base_uom="-4", _row=99))
    assert ("R01", "opening#row99", "INVALID_OPENING") in keys(mutated(ds_boundary, fn=mut))


def test_completed_event_with_open_handoff_and_no_target(ds_boundary):
    def mut(d):
        e = [r for r in d.rows("business_events.csv") if r["event_id"] == "EV-B01"][0]
        e["handoff_status"] = "OPEN"
    assert ("R11", "EV-B01", "OPEN_HANDOFF_NO_TARGET") in keys(mutated(ds_boundary, fn=mut))


def _rev(eid, related, **k):
    base = dict(event_id=eid, event_type="reversal", related_id=related, event_timestamp="2026-03-02T19:10:00-07:00", from_location="",
                to_location="LOC-A", sku_id="SKU-B1", uom="EA", quantity="5")
    base.update(k)
    return ev(**base)


@pytest.mark.parametrize("name,evt,expected", [
    ("unsupported", _rev("EV-R1", "EV-B19", from_location="LOC-B", to_location="", quantity="10"), "UNSUPPORTED_TARGET"),
    ("not_completed", _rev("EV-R2", "EV-B34", quantity="1"), "TARGET_NOT_COMPLETED"),
    ("sku", _rev("EV-R3", "EV-B01", sku_id="SKU-B2", uom="KG"), "SKU_MISMATCH"),
    ("not_inverse", _rev("EV-R4", "EV-B01", from_location="LOC-A", to_location=""), "NOT_INVERSE"),
])
def test_reversal_event_integrity_subtypes(ds_boundary, name, evt, expected):
    assert ("R14", evt["event_id"], expected) in keys(mutated(ds_boundary, [evt]))


def test_second_reversal_of_the_same_event_is_flagged(ds_boundary):
    r = mutated(ds_boundary, [_rev("EV-R5", "EV-B01"), _rev("EV-R6", "EV-B01", event_timestamp="2026-03-02T19:11:00-07:00")])
    k = keys(r)
    assert ("R14", "EV-R6", "DOUBLE_REVERSAL") in k and not [x for x in k if x[0] == "R14" and x[1] == "EV-R5"]


def test_reversal_posting_with_a_different_sku_is_flagged(ds_boundary):
    r = mutated(ds_boundary, pos=[po(posting_id="PO-R7", posting_type="REVERSAL", sku_id="SKU-B2", uom="KG", quantity="5", from_location="",
                                     to_location="LOC-A", related_posting_id="PO-B01")])
    assert ("R14", "PO-R7", "POSTING_SKU_MISMATCH") in keys(r)


def test_system_gap_caused_by_a_later_reversed_orphan_is_unlinked_not_hidden(ds_boundary):
    """An orphan posting that is reversed later is 'closed', but a count taken while it was live must not be silently explained."""
    cnt = dict(count_id="CC-X8", observed_at="2026-03-02T19:30:00-07:00", sku_id="SKU-B2", location_id="LOC-A", counted_qty="36.392",
               uom="KG", count_status="COMPLETE", evidence_ref="x", _row=99)

    def mut(d):
        d.tables["physical_counts.csv"].append(cnt)
    r = mutated(ds_boundary, pos=[po(posting_id="PO-X8a", system_post_timestamp="2026-03-03T02:00:00Z", quantity="2"),
                                  po(posting_id="PO-X8b", posting_type="REVERSAL", from_location="", to_location="LOC-A", quantity="2",
                                     related_posting_id="PO-X8a", system_post_timestamp="2026-03-03T03:00:00Z")], fn=mut)
    row = [x for x in r.count_table if x["count"].id == "CC-X8"][0]
    assert row["class"] == "SYSTEM_GAP_UNLINKED"
    assert ("R12", "CC-X8", "SYSTEM_GAP_UNLINKED") in keys(r)


# ---------------- reporting defects found in review
def test_funnel_rows_add_up_for_every_file(res_demo, res_boundary):
    for res in (res_demo, res_boundary):
        for f, v in res.v.funnel.items():
            assert v["valid"] + v["quarantined"] + v["identical_repeats_ignored"] == v["raw_rows"], f


def test_workbook_priority_list_cached_text_equals_the_headline(tmp_path, ds_demo, res_demo):
    from stocktrace.common import headline
    out = str(tmp_path / "o")
    cli.write_outputs(res_demo, ds_demo, out, False, "demo")
    path_x = os.path.join(out, "Integrity_Audit_Demo.xlsx")
    wv = openpyxl.load_workbook(path_x, data_only=True)
    wf = openpyxl.load_workbook(path_x)
    s = wv["EXECUTIVE_SUMMARY"]
    prim = sorted([i for i in res_demo.issues if i.is_primary], key=lambda i: i.priority_rank)
    cells = [(r, 13) for r in range(1, 60) if s.cell(r, 12).value in range(1, 9) and s.cell(r, 13).value]
    assert len(cells) == 8
    for (r, c), item in zip(cells, prim[:8]):
        assert s.cell(r, c).value == "[%s] %s" % (item.severity, headline(item.rule_id, item.subtype, item.key_id, item.sku_id, item.value_cad_sim))
        assert wf["EXECUTIVE_SUMMARY"].cell(r, c).value.startswith('=IFERROR("["&INDEX(')    # live lookup into REVIEW_QUEUE
    q = wv["REVIEW_QUEUE"]
    assert [q.cell(r, 8).value for r in range(5, 5 + len(prim))][:3] == [i.reason for i in sorted(res_demo.issues, key=lambda x: (0 if x.is_primary else 1, x.priority_rank if x.is_primary else 10 ** 6, x.exception_id))[:3]]   # full reason stays in the queue


def test_every_sheet_says_it_is_synthetic(tmp_path, ds_demo, res_demo):
    out = str(tmp_path / "o")
    cli.write_outputs(res_demo, ds_demo, out, False, "demo")
    wb = openpyxl.load_workbook(os.path.join(out, "Integrity_Audit_Demo.xlsx"), data_only=True)
    for ws in wb:
        head = " ".join(str(c.value) for row in ws.iter_rows(min_row=1, max_row=4) for c in row if c.value)
        assert "SYNTHETIC" in head.upper(), ws.title


def test_conflicting_duplicate_reason_says_what_differs(res_demo):
    r4 = [i for i in res_demo.issues if i.rule_id == "R04" and i.subtype == "CONFLICTING_CONTENT" and i.key_id.startswith("EV-")][0]
    assert "quantity:" in r4.reason and "|" in r4.reason


def test_high_count_is_split_by_family_in_workbook_and_csv(tmp_path, ds_demo, res_demo):
    from stocktrace.common import family
    out = str(tmp_path / "o")
    cli.write_outputs(res_demo, ds_demo, out, False, "demo")
    wv = openpyxl.load_workbook(os.path.join(out, "Integrity_Audit_Demo.xlsx"), data_only=True)
    wf = openpyxl.load_workbook(os.path.join(out, "Integrity_Audit_Demo.xlsx"))
    prim = [i for i in res_demo.issues if i.is_primary and i.severity == "HIGH"]
    n = {f: sum(1 for i in prim if family(i.rule_id) == f) for f in ("STOCK_IMPACT", "TIMING_PROCESS", "DATA_QUALITY")}
    assert sum(n.values()) == wv["EXECUTIVE_SUMMARY"]["D5"].value
    assert wv["EXECUTIVE_SUMMARY"]["B7"].value == "Of the HIGH cases: %d stock-impacting, %d timing/handoff, %d data-quality." % (
        n["STOCK_IMPACT"], n["TIMING_PROCESS"], n["DATA_QUALITY"])
    assert wf["EXECUTIVE_SUMMARY"]["B7"].value.startswith('="Of the HIGH cases: "&COUNTIFS(')
    import csv
    rows = list(csv.DictReader(open(os.path.join(out, "Exceptions.csv"))))
    assert {r["case_family"] for r in rows} <= {"STOCK_IMPACT", "TIMING_PROCESS", "DATA_QUALITY"}


# ---------------- formula injection (found by the independent audit): untrusted text must never execute in Excel or CSV
INJECTED = {5: "=1+1", 6: "@SUM(1+1)", 7: "-2+3", 8: "+cmd|calc", 9: "http://evil.example/x"}


@pytest.fixture(scope="module")
def injected_build(tmp_path_factory):
    import csv
    root = tmp_path_factory.mktemp("inj")
    src = str(root / "raw")
    shutil.copytree(path("data", "raw", "demo"), src)
    p = os.path.join(src, "business_events.csv")
    with open(p, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    col = rows[0].index("sku_id")
    for k, v in INJECTED.items():
        rows[k][col] = v
    with open(p, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)
    out = str(root / "out")
    ds = loader.load_dataset(src, "inj")
    cli.write_outputs(engine.run(ds), ds, out, False, "inj")
    return out


@pytest.mark.parametrize("raw,expected", [
    ("=1+1", "'=1+1"), ("@SUM(1+1)", "'@SUM(1+1)"), ("+cmd|calc", "'+cmd|calc"), ("-2+3", "'-2+3"),
    ("\t=1+1", "'\t=1+1"), ("\r=1+1", "'\r=1+1"), ("-", "'-"),
    ("-5", "-5"), ("+2.5", "+2.5"), ("12", "12"), ("SKU-A", "SKU-A"), ("a=b", "a=b"), ("", ""), (7, 7)])
def test_csv_safe_cell_rules(raw, expected):
    assert loader.csv_safe(raw) == expected


def test_report_csvs_never_start_a_cell_with_a_formula_trigger(injected_build):
    import csv
    seen = 0
    for name in ("Exceptions.csv", "Manager_Summary.csv", "Simulation_Before_After.csv", "Count_Reconciliation.csv", "Status_History.csv"):
        with open(os.path.join(injected_build, name), newline="", encoding="utf-8") as fh:
            for row in csv.reader(fh):
                for cell in row:
                    assert loader.csv_safe(cell) == cell, "%s holds an executable-looking cell: %r" % (name, cell)
                    seen += cell.startswith("'")
    assert seen >= 4          # the injected values really reached the report and were neutralised
    with open(os.path.join(injected_build, "Exceptions.csv"), newline="", encoding="utf-8") as fh:
        skus = {r["sku_id"] for r in csv.DictReader(fh)}
    assert {"'=1+1", "'@SUM(1+1)", "'-2+3", "'+cmd|calc"} <= skus


def test_workbook_keeps_untrusted_text_as_text_not_formulas_or_links(injected_build):
    import re
    import zipfile
    x = os.path.join(injected_build, "Integrity_Audit_Demo.xlsx")
    wb = openpyxl.load_workbook(x)
    ws = wb["DATA_EVENTS"]
    sku_col = [c.column for c in ws[3] if c.value == "sku_id"][0]
    for k, v in INJECTED.items():
        cell = ws.cell(row=3 + k, column=sku_col)          # header row 3, data row k at 3+k
        assert cell.value == v and cell.data_type == "s", (k, cell.value, cell.data_type)
        assert cell.hyperlink is None
    for sheet in wb:
        for row in sheet.iter_rows():
            for c in row:
                if c.data_type == "f":
                    assert not any(t in str(c.value) for t in ("1+1", "cmd|calc", "-2+3", "evil.example")), (sheet.title, c.coordinate)
    z = zipfile.ZipFile(x)
    for n in z.namelist():
        if n.startswith("xl/worksheets/sheet"):
            data = z.read(n)
            assert not re.search(rb"<f>[^<]*(1\+1|cmd\|calc|-2\+3|evil\.example)", data), n

    for n in z.namelist():
        if n.endswith(".rels"):
            assert b"evil.example" not in z.read(n), n         # no external hyperlink was created from cell text


def test_real_formulas_still_work_after_hardening(injected_build):
    wb = openpyxl.load_workbook(os.path.join(injected_build, "Integrity_Audit_Demo.xlsx"))
    es = wb["EXECUTIVE_SUMMARY"]
    formulas = [c.value for row in es.iter_rows() for c in row if c.data_type == "f"]
    assert len(formulas) >= 20
    assert any("COUNTIFS" in f for f in formulas)


def test_raw_source_files_are_not_neutralised(tmp_path):
    """Raw inputs must stay byte-for-byte as supplied: the default writer never rewrites values."""
    p = str(tmp_path / "x.csv")
    loader.write_csv(p, ["a"], [{"a": "=1+1"}])
    assert open(p).read() == "a\n=1+1\n"

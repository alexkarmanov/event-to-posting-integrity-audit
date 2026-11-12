"""Workbook integrity, CSV outputs, simulation safety, privacy and CLI overwrite protection."""
import csv
import hashlib
import json
import os
import re
import zipfile

import openpyxl
import pytest

from stocktrace import cli, kpis, simulate, workbook
from stocktrace.engine import run
from stocktrace.loader import load_dataset

from conftest import ROOT, path

_ABS = "/Us" + "ers/"


@pytest.fixture(scope="module")
def built(tmp_path_factory, ds_demo, res_demo):
    out = str(tmp_path_factory.mktemp("out") / "demo")
    info = cli.write_outputs(res_demo, ds_demo, out, False, "demo")
    return out, info


def test_workbook_has_all_required_sheets(built):
    wb = openpyxl.load_workbook(os.path.join(built[0], "Integrity_Audit_Demo.xlsx"))
    required = {"START_HERE", "EXECUTIVE_SUMMARY", "REVIEW_QUEUE", "EVIDENCE_DETAIL", "SHIFT_HANDOFF", "COUNT_RECONCILIATION",
                "CORRECTION_SIMULATION", "RULE_CATALOG", "GLOSSARY_ASSUMPTIONS"}
    assert required <= set(wb.sheetnames) and wb.sheetnames[0] == "START_HERE"


def test_workbook_tables_filters_and_freeze_panes(built):
    wb = openpyxl.load_workbook(os.path.join(built[0], "Integrity_Audit_Demo.xlsx"))
    q = wb["REVIEW_QUEUE"]
    assert "tblQueue" in q.tables and q.freeze_panes == "C5" and q.tables["tblQueue"].autoFilter is not None
    assert q.max_row - 4 >= 60                                  # populated, not blank
    assert wb["EXECUTIVE_SUMMARY"].page_setup.orientation == "landscape" and wb["EXECUTIVE_SUMMARY"].sheet_properties.pageSetUpPr.fitToPage


def test_no_error_values_and_no_empty_summary(built):
    wb = openpyxl.load_workbook(os.path.join(built[0], "Integrity_Audit_Demo.xlsx"), data_only=True)
    bad = []
    for ws in wb:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and re.match(r"^#(REF|VALUE|DIV/0|NAME|N/A|NUM|NULL)", c.value):
                    bad.append((ws.title, c.coordinate, c.value))
    assert bad == []
    s = wb["EXECUTIVE_SUMMARY"]
    assert s["B5"].value and s["B5"].value > 0


def test_formulas_are_present_and_cached_values_match_independent_recount(built, res_demo):
    path_x = os.path.join(built[0], "Integrity_Audit_Demo.xlsx")
    wf = openpyxl.load_workbook(path_x)
    wv = openpyxl.load_workbook(path_x, data_only=True)
    s_f, s_v = wf["EXECUTIVE_SUMMARY"], wv["EXECUTIVE_SUMMARY"]
    n_formulas = sum(1 for ws in wf for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("="))
    assert n_formulas >= 100
    prim = [i for i in res_demo.issues if i.is_primary]
    assert s_f["B5"].value.startswith("=COUNTIF(") and s_v["B5"].value == len(prim)
    assert s_v["D5"].value == sum(1 for i in prim if i.severity == "HIGH")
    # per-rule table: rows 16..29 hold R01..R14 (rule in B, cases in F, simulated value in J)
    for r in range(16, 30):
        rid = s_v.cell(r, 2).value
        assert s_v.cell(r, 6).value == sum(1 for i in prim if i.rule_id == rid), rid
        assert s_f.cell(r, 6).value.startswith("=COUNTIFS(")
    # recompute the Exceptions.csv from disk as the independent source
    rows = list(csv.DictReader(open(os.path.join(built[0], "Exceptions.csv"))))
    assert len([r for r in rows if r["primary_case"] == "Y"]) == len(prim)
    total_row = [r for r in range(16, 40) if s_v.cell(r, 2).value == "Total"][0]
    assert s_v.cell(total_row, 6).value == len(prim)
    assert abs(s_v.cell(total_row, 10).value - float(sum(i.value_cad_sim for i in prim))) < 0.01


def test_review_queue_matches_exceptions_csv(built, res_demo):
    wv = openpyxl.load_workbook(os.path.join(built[0], "Integrity_Audit_Demo.xlsx"), data_only=True)
    q = wv["REVIEW_QUEUE"]
    ids = [q.cell(r, 2).value for r in range(5, q.max_row + 1) if q.cell(r, 2).value]
    csv_ids = [r["exception_id"] for r in csv.DictReader(open(os.path.join(built[0], "Exceptions.csv")))]
    assert ids == csv_ids and len(ids) == len(res_demo.issues)
    ranks = [q.cell(r, 1).value for r in range(5, q.max_row + 1) if q.cell(r, 1).value not in (None, "")]
    assert ranks == sorted(ranks) and ranks[0] == 1


def test_evidence_rows_link_back_to_raw_files(built, ds_demo):
    wv = openpyxl.load_workbook(os.path.join(built[0], "Integrity_Audit_Demo.xlsx"), data_only=True)
    e = wv["EVIDENCE_DETAIL"]
    ev = {r["_row"]: r["event_id"] for r in ds_demo.rows("business_events.csv")}
    po = {r["_row"]: r["posting_id"] for r in ds_demo.rows("system_postings.csv")}
    cc = {r["_row"]: r["count_id"] for r in ds_demo.rows("physical_counts.csv")}
    idx = {"business_events.csv": ev, "system_postings.csv": po, "physical_counts.csv": cc}
    n = 0
    for r in range(5, e.max_row + 1):
        exid, fname, rid, row = e.cell(r, 1).value, e.cell(r, 3).value, e.cell(r, 4).value, e.cell(r, 5).value
        if not exid:
            continue
        assert idx[fname][row] == rid, (exid, fname, rid, row)
        n += 1
    assert n >= 50


def test_workbook_has_no_macros_external_links_or_absolute_paths(built):
    p = os.path.join(built[0], "Integrity_Audit_Demo.xlsx")
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        assert not [n for n in names if "vbaProject" in n or "externalLink" in n]
        blob = b"".join(z.read(n) for n in names if n.endswith(".xml"))
    assert _ABS.encode() not in blob and b"C:\\" not in blob
    assert b"SYNTHETIC" in blob


def test_no_unsupported_functions_in_formulas(built):
    wf = openpyxl.load_workbook(os.path.join(built[0], "Integrity_Audit_Demo.xlsx"))
    forbidden = re.compile(r"(XLOOKUP|LET\(|FILTER\(|SORT\(|UNIQUE\(|LAMBDA|WEBSERVICE|\[1\])", re.I)
    for ws in wf:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    assert not forbidden.search(c.value), (ws.title, c.coordinate)


def test_csv_outputs_exist_and_manager_summary_is_labeled(built):
    for f in ("Exceptions.csv", "Manager_Summary.csv", "Simulation_Before_After.csv", "Count_Reconciliation.csv", "Status_History.csv",
              "run_manifest.json"):
        assert os.path.getsize(os.path.join(built[0], f)) > 0
    ms = {r["metric"]: r for r in csv.DictReader(open(os.path.join(built[0], "Manager_Summary.csv")))}
    assert "value_cad_sim" in "".join(ms) and "indicator, not a loss" in ms["exposure_value_cad_sim"]["definition"].lower()
    manifest = json.load(open(os.path.join(built[0], "run_manifest.json")))
    assert _ABS not in json.dumps(manifest)


def test_build_outputs_are_reproducible(tmp_path, ds_demo, res_demo):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    cli.write_outputs(res_demo, ds_demo, a, False, "demo")
    cli.write_outputs(run(ds_demo), ds_demo, b, False, "demo")
    for f in ("Exceptions.csv", "Manager_Summary.csv", "Simulation_Before_After.csv", "Count_Reconciliation.csv", "Status_History.csv"):
        assert open(os.path.join(a, f), "rb").read() == open(os.path.join(b, f), "rb").read(), f


# ---------------- simulation
def test_simulation_is_safe_and_leaves_raw_data_untouched(ds_demo, res_demo):
    before = {k: [dict(r) for r in v] for k, v in ds_demo.tables.items()}
    s = simulate.simulate(ds_demo, res_demo)
    assert before == ds_demo.tables                           # raw tables not modified
    assert s.unexpected_new == []                             # corrections introduce nothing unexpected
    assert len(s.resolved) >= 15 and len(s.acknowledged) >= 3
    assert all(h["by_role"] for h in s.history)
    approvals = [h for h in s.history if h["status"] == "APPROVED_FOR_SIMULATION"]
    assert approvals and all("HYPOTHETICAL" in h["by_role"] for h in approvals)


def test_simulation_actually_clears_the_resolved_exceptions(ds_demo, res_demo):
    s = simulate.simulate(ds_demo, res_demo)
    after_ids = {i.exception_id for i in s.after.issues}
    assert not (set(s.resolved) & after_ids)
    assert s.count_after.get("EXPLAINED_BY_OPEN_ISSUES", 0) <= s.count_before.get("EXPLAINED_BY_OPEN_ISSUES", 0)


# ---------------- privacy / publication hygiene
TEXT_EXT = (".py", ".md", ".csv", ".json", ".sql", ".txt", ".yml", ".yaml", ".cfg", ".toml")


# ---------------- CLI
def test_cli_refuses_to_overwrite_user_modified_workbook(tmp_path):
    out = str(tmp_path / "o")
    args = ["build", "--scenario", "clean", "--output", out]
    assert cli.main(args) == 0
    xl = os.path.join(out, "Integrity_Audit_Demo.xlsx")
    with open(xl, "ab") as fh:
        fh.write(b"user edit")
    with pytest.raises(SystemExit) as ex:
        cli.main(args)
    assert "modified" in str(ex.value)
    assert cli.main(args + ["--force"]) == 0


def test_cli_refuses_foreign_non_empty_folder(tmp_path):
    (tmp_path / "keep.txt").write_text("mine")
    with pytest.raises(SystemExit):
        cli.main(["build", "--scenario", "clean", "--output", str(tmp_path)])
    assert (tmp_path / "keep.txt").read_text() == "mine"


def test_clean_scenario_builds_a_valid_workbook_with_zero_queue_rows(tmp_path):
    out = str(tmp_path / "c")
    assert cli.main(["build", "--scenario", "clean", "--output", out]) == 0
    wv = openpyxl.load_workbook(os.path.join(out, "Integrity_Audit_Demo.xlsx"), data_only=True)
    assert wv["EXECUTIVE_SUMMARY"]["B5"].value == 0


def test_count_agreement_and_coverage_tiles_are_one_decimal_percentages(built, res_demo):
    p = os.path.join(built[0], "Integrity_Audit_Demo.xlsx")
    wf, wv = openpyxl.load_workbook(p), openpyxl.load_workbook(p, data_only=True)
    for cell in ("F5", "H5"):
        assert wf["EXECUTIVE_SUMMARY"][cell].number_format == "0.0%", cell
        assert wf["EXECUTIVE_SUMMARY"][cell].value.startswith("=J"), cell           # still a formula (points at the count panel)
        assert 0 < wv["EXECUTIVE_SUMMARY"][cell].value < 1                          # still a fraction, not a pre-multiplied number
    assert round(wv["EXECUTIVE_SUMMARY"]["F5"].value * 100, 1) == 86.2 and round(wv["EXECUTIVE_SUMMARY"]["H5"].value * 100, 1) == 58.0


def test_cad_footnote_lists_every_rule_that_carries_a_value(built):
    wv = openpyxl.load_workbook(os.path.join(built[0], "Integrity_Audit_Demo.xlsx"), data_only=True)
    text = " ".join(str(c.value) for row in wv["EXECUTIVE_SUMMARY"].iter_rows() for c in row if c.value and "Simulated CAD value =" in str(c.value))
    assert "R05-R09, R12, R13, R14" in text and "R01-R04, R10, R11" in text and "not a loss" in text
    ms = {r["metric"]: r for r in csv.DictReader(open(os.path.join(built[0], "Manager_Summary.csv")))}
    assert "R05-R09, R12-R14" in ms["exposure_value_cad_sim"]["definition"]

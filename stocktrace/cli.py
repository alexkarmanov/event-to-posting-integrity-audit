"""Command line: generate synthetic scenarios, build outputs, run the SQL cross-check."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import sys
from typing import Dict, List

from . import __version__
from .common import family, fmt_dec, fmt_local
from .engine import Result, run
from .loader import Dataset, load_dataset, write_csv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS = {"demo": "data/raw/demo", "clean": "data/fixtures/clean", "boundary": "data/fixtures/boundary"}
MANIFEST_NAME = ".build_manifest.json"


def scenario_path(name: str) -> str:
    return os.path.join(ROOT, SCENARIOS[name])


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_overwrite(out: str, force: bool) -> None:
    """Refuse to overwrite files the user changed (hash differs from the last build) unless --force."""
    if not os.path.isdir(out) or not os.listdir(out):
        return
    mf = os.path.join(out, MANIFEST_NAME)
    if not os.path.exists(mf):
        if force:
            return
        raise SystemExit("Output folder %s is not empty and was not created by this tool; use another folder or --force." % out)
    with open(mf, encoding="utf-8") as fh:
        last = json.load(fh).get("files", {})
    changed = [f for f, h in last.items() if os.path.exists(os.path.join(out, f)) and _sha(os.path.join(out, f)) != h
               and not f.endswith(".xlsx")]
    # .xlsx metadata differs run to run only in timestamps, so compare it by presence: warn if the user edited it
    edited = [f for f in last if f.endswith(".xlsx") and os.path.exists(os.path.join(out, f)) and _sha(os.path.join(out, f)) != h_of(last, f)]
    if (changed or edited) and not force:
        raise SystemExit("These output files were modified since the last build: %s. Copy them elsewhere or rerun with --force."
                         % ", ".join(changed + edited))


def h_of(last: Dict[str, str], f: str) -> str:
    return last[f]


def write_outputs(res: Result, ds: Dataset, out: str, force: bool = False, scenario: str = "") -> Dict[str, object]:
    from . import kpis, simulate, workbook
    check_overwrite(out, force)
    os.makedirs(out, exist_ok=True)
    k = kpis.compute(res)
    prio = kpis.count_priority(res, top=25)
    sim = simulate.simulate(ds, res)
    files: Dict[str, str] = {}

    ex_cols = ["exception_id", "detection_run_id", "rule_id", "issue_type", "severity", "primary_case", "priority_rank",
               "priority_score", "case_key", "sku_id", "location_id", "shift_id", "reference_time_local", "reason",
               "observed", "expected", "difference_base_uom", "illustrative_value_cad_sim", "source_file_and_ids",
               "root_cause_hypothesis", "proposed_review_action", "owner_role", "status", "retest_evidence", "case_family"]
    outcome = {h["exception_id"]: h["status"] for h in sim.history if h["seq"] == 5}
    rows = []
    for i in sorted(res.issues, key=lambda x: (0 if x.is_primary else 1, x.priority_rank or 10 ** 6, x.exception_id)):
        rows.append({"exception_id": i.exception_id, "detection_run_id": res.run_id, "rule_id": i.rule_id, "issue_type": i.subtype,
                     "severity": i.severity, "primary_case": "Y" if i.is_primary else "N",
                     "priority_rank": i.priority_rank if i.is_primary else "", "priority_score": i.priority_score,
                     "case_key": i.case_key, "sku_id": i.sku_id, "location_id": i.location_id, "shift_id": i.shift_id,
                     "reference_time_local": fmt_local(i.ref_ts), "reason": i.reason, "observed": i.observed,
                     "expected": i.expected, "difference_base_uom": fmt_dec(i.qty_diff),
                     "illustrative_value_cad_sim": str(i.value_cad_sim),
                     "source_file_and_ids": "; ".join("%s:%s" % (s[0], s[1]) for s in i.sources),
                     "root_cause_hypothesis": i.hypothesis, "proposed_review_action": i.review_action,
                     "owner_role": i.owner_role, "status": i.status,
                     "retest_evidence": ("simulation: " + outcome[i.exception_id]) if i.exception_id in outcome else "",
                     "case_family": family(i.rule_id)})
    write_csv(os.path.join(out, "Exceptions.csv"), ex_cols, rows, safe=True)
    files["Exceptions.csv"] = ""

    ms = [("raw_rows_events", sum(1 for _ in ds.rows("business_events.csv")), "Rows in business_events.csv"),
          ("raw_rows_postings", len(ds.rows("system_postings.csv")), "Rows in system_postings.csv"),
          ("raw_rows_counts", len(ds.rows("physical_counts.csv")), "Rows in physical_counts.csv"),
          ("valid_events", k["events_valid"], "Events that passed validation (denominator for matching)"),
          ("issues_total", k["issues_total"], "All issues including secondary"),
          ("cases_unique", k["cases_unique"], "Distinct case_key (one real-world problem = one case)"),
          ("primary_cases_high", k["cases_by_severity"].get("HIGH", 0), "Primary cases with severity HIGH"),
          ("primary_cases_medium", k["cases_by_severity"].get("MEDIUM", 0), "Primary cases with severity MEDIUM"),
          ("primary_cases_low", k["cases_by_severity"].get("LOW", 0), "Primary cases with severity LOW"),
          ("timing_cases_r10", k["timing_cases"], "Late-posting / boundary timing cases (no loss implied)"),
          ("handoff_cases_r11", k["handoff_cases"], "Cross-shift handoff cases"),
          ("count_coverage_pct", k["count_coverage_pct"], "COMPLETE-counted SKU-locations / eligible SKU-locations"),
          ("count_agreement_pct", k["count_agreement_pct"], "AGREES / comparable counts (excludes partial-not-comparable and void)"),
          ("r12_cases", k["r12_cases"], "Unexplained count variances"),
          ("r12_value_cad_sim", k["r12_value_cad_sim"], "Simulated CAD value of unexplained count variances"),
          ("exposure_value_cad_sim", k["exposure_value_cad_sim"], "Simulated CAD value (|quantity difference| x simulated unit value) summed over primary cases that carry a quantity difference: R05-R09, R12-R14. Indicator, not a loss estimate")]
    write_csv(os.path.join(out, "Manager_Summary.csv"), ["metric", "value", "definition"],
              [{"metric": a, "value": str(b), "definition": c} for a, b, c in ms], safe=True)
    files["Manager_Summary.csv"] = ""

    sim_rows = [
        {"metric": "primary_cases_before", "value": len([i for i in res.issues if i.is_primary])},
        {"metric": "resolved_by_simulated_correction", "value": len(sim.resolved)},
        {"metric": "acknowledged_timing_no_data_change", "value": len(sim.acknowledged)},
        {"metric": "expected_timing_side_effects", "value": len(sim.side_effects)},
        {"metric": "unexpected_new_exceptions", "value": len(sim.unexpected_new)},
    ]
    for cl in sorted(set(sim.count_before) | set(sim.count_after)):
        sim_rows.append({"metric": "count_class_before:" + cl, "value": sim.count_before.get(cl, 0)})
        sim_rows.append({"metric": "count_class_after:" + cl, "value": sim.count_after.get(cl, 0)})
    write_csv(os.path.join(out, "Simulation_Before_After.csv"), ["metric", "value"], sim_rows, safe=True)
    files["Simulation_Before_After.csv"] = ""

    cr = []
    for r in res.count_table:
        c = r["count"]
        cr.append({"count_id": c.id, "observed_local": fmt_local(c.observed), "sku_id": c.sku, "location_id": c.loc,
                   "count_status": c.status, "counted_base": fmt_dec(c.qty), "system_balance": fmt_dec(r["S"]) if r["S"] is not None else "",
                   "event_balance": fmt_dec(r["E"]) if r["E"] is not None else "", "classification": r["class"],
                   "linked_issues": ",".join(sorted({x.exception_id for x in r["linked"]})), "r12_exception": r["issue_id"]})
    write_csv(os.path.join(out, "Count_Reconciliation.csv"), list(cr[0].keys()) if cr else ["count_id"], cr, safe=True)
    files["Count_Reconciliation.csv"] = ""
    write_csv(os.path.join(out, "Status_History.csv"), ["exception_id", "seq", "status", "by_role", "note"], sim.history, safe=True)
    files["Status_History.csv"] = ""

    xlsx = os.path.join(out, "Integrity_Audit_Demo.xlsx")
    winfo = workbook.build(xlsx, res, k, sim, ds, prio, {"scenario": scenario or ds.name})
    files["Integrity_Audit_Demo.xlsx"] = ""

    manifest = {"version": __version__, "scenario": scenario or ds.name, "detection_run_id": res.run_id,
                "input_fingerprint": res.input_hash, "python": platform.python_version(),
                "counts": {"issues": len(res.issues), "primary_cases": len([i for i in res.issues if i.is_primary])},
                "files": {}}
    write_manifest_files = list(files)
    with open(os.path.join(out, "run_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(manifest, files=list(files)), fh, indent=1, sort_keys=True)
        fh.write("\n")
    files["run_manifest.json"] = ""
    manifest["files"] = {f: _sha(os.path.join(out, f)) for f in files}
    with open(os.path.join(out, MANIFEST_NAME), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return {"kpi": k, "sim": sim, "workbook": winfo, "prio": prio}


def cmd_build(a) -> int:
    path = a.input or scenario_path(a.scenario)
    ds = load_dataset(path, a.scenario or "custom")
    res = run(ds)
    out = a.output if os.path.isabs(a.output) else os.path.join(os.getcwd(), a.output)
    info = write_outputs(res, ds, out, a.force, a.scenario or "custom")
    k = info["kpi"]
    print("Event-to-Posting Integrity Audit %s | scenario=%s | run=%s" % (__version__, a.scenario or "custom", res.run_id))
    print("events=%s postings=%s counts=%s" % (len(ds.rows("business_events.csv")), len(ds.rows("system_postings.csv")),
                                               len(ds.rows("physical_counts.csv"))))
    print("issues=%d  unique cases=%d  by severity=%s" % (len(res.issues), k["cases_unique"], dict(sorted(k["cases_by_severity"].items()))))
    s = info["sim"]
    print("simulation: resolved=%d acknowledged=%d unexpected_new=%d" % (len(s.resolved), len(s.acknowledged), len(s.unexpected_new)))
    print("wrote %s" % os.path.relpath(out, os.getcwd()))
    return 0


def cmd_generate(a) -> int:
    from . import anomalies, generator
    os.chdir(ROOT)
    generator.generate_clean(SCENARIOS["clean"])
    m = anomalies.generate_demo(SCENARIOS["demo"], "data/fixtures/demo_expected_anomalies.json")
    print("generated clean + demo (%d planted anomalies, %d near-miss records)" % (len(m["planted"]), len(m["near_misses"])))
    return 0


def cmd_sqlcheck(a) -> int:
    from . import sqlcheck
    ok = sqlcheck.main(a.input or scenario_path(a.scenario))
    return 0 if ok else 1


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(prog="stocktrace", description="Event-to-Posting Integrity Audit - cross-shift inventory reconciliation and data quality controls (synthetic data)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="run the engine and write the workbook and CSV outputs")
    b.add_argument("--scenario", choices=sorted(SCENARIOS), default="demo")
    b.add_argument("--input", help="folder with the raw CSV files (overrides --scenario)")
    b.add_argument("--output", default="output/demo")
    b.add_argument("--force", action="store_true", help="overwrite outputs even if they were modified")
    b.set_defaults(fn=cmd_build)
    g = sub.add_parser("generate", help="regenerate the deterministic synthetic scenarios (demo + clean)")
    g.set_defaults(fn=cmd_generate)
    s = sub.add_parser("sqlcheck", help="run the independent SQLite/SQL checks and compare with the Python engine")
    s.add_argument("--scenario", choices=sorted(SCENARIOS), default="demo")
    s.add_argument("--input")
    s.set_defaults(fn=cmd_sqlcheck)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

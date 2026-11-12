"""Independent SQL cross-check. Loads the raw CSV files into SQLite *as text*, runs sql/*.sql, and compares the issue
sets and the count balances with the Python engine. The only shared code is the CSV reader; all parsing, validation,
matching and balance logic comes from the SQL scripts."""
from __future__ import annotations

import csv
import os
import sqlite3
from decimal import Decimal
from typing import Dict, List, Set, Tuple

from .engine import run
from .loader import load_dataset

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_DIR = os.path.join(ROOT, "sql")
SQL_FILES = ["01_schema.sql", "02_validation.sql", "03_reconciliation.sql"]
LOAD = [("sku_master.csv", "r_sku", False), ("location_master.csv", "r_loc", False), ("uom_conversions.csv", "r_conv", False),
        ("opening_balances.csv", "r_open", False), ("business_events.csv", "r_ev", True),
        ("system_postings.csv", "r_po", True), ("physical_counts.csv", "r_cnt", True), ("shifts.csv", "r_shift", False),
        ("rule_parameters.csv", "r_param", False)]
# (rule, python subtype filter) pairs that the SQL layer reproduces
COVERED = {"R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08", "R10", "R12"}


def build_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    for f in SQL_FILES[:1]:
        con.executescript(open(os.path.join(SQL_DIR, f), encoding="utf-8").read())
    for fname, table, rownos in LOAD:
        with open(os.path.join(path, fname), newline="", encoding="utf-8-sig") as fh:
            rd = csv.reader(fh)
            header = [h.strip() for h in next(rd)]
            rows = [[c.strip() for c in r] for r in rd]       # same documented whitespace trim as the Python loader
        if rownos:
            q = "INSERT INTO %s VALUES (%s)" % (table, ",".join("?" * (len(header) + 1)))
            con.executemany(q, [[i] + r for i, r in enumerate(rows, start=1)])
        else:
            q = "INSERT INTO %s VALUES (%s)" % (table, ",".join("?" * len(header)))
            con.executemany(q, rows)
    for f in SQL_FILES[1:]:
        con.executescript(open(os.path.join(SQL_DIR, f), encoding="utf-8").read())
    return con


def sql_results(path: str) -> Tuple[Set[Tuple[str, str]], Dict[str, dict]]:
    con = build_db(path)
    issues = {(r, i) for r, i in con.execute("SELECT rule_id, record_id FROM sql_issues")}
    counts = {r[0]: {"sku": r[1], "loc": r[2], "C": r[3], "S": r[4], "E": r[5], "cls": r[6]} for r in con.execute(
        "SELECT count_id, sku_id, loc, c_qty, s_bal, e_bal, cls FROM sql_count_class")}
    return issues, counts


def python_results(path: str):
    res = run(load_dataset(path))
    keyset: Set[Tuple[str, str]] = set()
    for i in res.issues:
        if i.rule_id not in COVERED:
            continue
        if i.rule_id == "R10" and i.subtype != "LATE_POSTING":
            continue
        if i.rule_id == "R12" and i.subtype not in ("UNEXPLAINED_COUNT_VARIANCE",):
            continue
        if i.rule_id == "R01" and i.subtype == "INVALID_OPENING":
            continue
        keyset.add((i.rule_id, i.key_id))
    return keyset, res


def compare(path: str) -> Dict[str, object]:
    sql_set, sql_counts = sql_results(path)
    py_set, res = python_results(path)
    out: Dict[str, object] = {"only_sql": sorted(sql_set - py_set), "only_python": sorted(py_set - sql_set),
                              "n_sql": len(sql_set), "n_python": len(py_set), "balance_mismatches": [], "class_mismatches": []}
    map_cls = {"AGREES": {"AGREES"}, "PARTIAL_NOT_COMPARABLE": {"PARTIAL_NOT_COMPARABLE"},
               "UNEXPLAINED": {"UNEXPLAINED_COUNT_VARIANCE"},
               "MATCHES_EVENT_LEDGER": {"EXPLAINED_BY_OPEN_ISSUES", "IN_FLIGHT_POSTING_LAG", "EXPLAINED_BY_ISSUES_AND_POSTING_LAG",
                                        "SYSTEM_GAP_UNLINKED"}}
    for row in res.count_table:
        c = row["count"]
        if row["class"] == "VOID_EXCLUDED":
            continue
        q = sql_counts.get(c.id)
        if q is None:
            out["balance_mismatches"].append((c.id, "missing in SQL"))
            continue
        if abs(Decimal(str(q["S"])) - row["S"]) > Decimal("0.0006") or abs(Decimal(str(q["E"])) - row["E"]) > Decimal("0.0006") \
                or abs(Decimal(str(q["C"])) - c.qty) > Decimal("0.0006"):
            out["balance_mismatches"].append((c.id, "py S/E/C=%s/%s/%s sql=%s/%s/%s" % (row["S"], row["E"], c.qty, q["S"], q["E"], q["C"])))
        if row["class"] not in map_cls[q["cls"]]:
            out["class_mismatches"].append((c.id, row["class"], q["cls"]))
    out["n_counts_compared"] = len([r for r in res.count_table if r["class"] != "VOID_EXCLUDED"])
    out["ok"] = not (out["only_sql"] or out["only_python"] or out["balance_mismatches"] or out["class_mismatches"])
    return out


def main(path: str) -> bool:
    r = compare(path)
    print("SQL vs Python cross-check on %s" % os.path.basename(os.path.normpath(path)))
    print("  issue keys  SQL=%d  Python=%d  only-in-SQL=%d  only-in-Python=%d" % (r["n_sql"], r["n_python"], len(r["only_sql"]), len(r["only_python"])))
    print("  count balances compared=%d  balance mismatches=%d  class mismatches=%d" % (r["n_counts_compared"], len(r["balance_mismatches"]), len(r["class_mismatches"])))
    for k in ("only_sql", "only_python", "balance_mismatches", "class_mismatches"):
        for item in r[k][:15]:
            print("   ", k, item)
    print("  RESULT:", "PASS" if r["ok"] else "FAIL")
    return bool(r["ok"])

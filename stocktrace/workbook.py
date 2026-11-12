"""Excel workbook builder (xlsxwriter). Plain A1 formulas, native tables/filters, no macros or external links.

Every summary formula is written together with its cached value computed in Python, so the file is readable in
viewers that do not recalculate; Excel recalculates on open.

Print design (all sheets Letter, landscape): EXECUTIVE_SUMMARY and EVIDENCE_EXAMPLE are one-page documents; list sheets
print one page wide with repeating header rows and a limited, readable print area; raw DATA_* sheets are for screen use.
"""
from __future__ import annotations

import math
from collections import defaultdict
from decimal import Decimal
from typing import Dict, List, Optional

import xlsxwriter

from . import __version__
from .common import SHORT_RULE_NAMES, family, fmt_dec, fmt_local, headline
from .engine import Result
from .loader import Dataset
from .schema import SCHEMAS
from .simulate import APPROVER, SimResult
from .validate import F_COUNT, F_EVENTS, F_POST

SHEETS = ["START_HERE", "EXECUTIVE_SUMMARY", "EVIDENCE_EXAMPLE", "REVIEW_QUEUE", "EVIDENCE_DETAIL", "SHIFT_HANDOFF",
          "HANDOFF_EXCEPTIONS", "COUNT_RECONCILIATION", "COUNT_PRIORITY", "CORRECTION_SIMULATION", "RULE_CATALOG",
          "GLOSSARY_ASSUMPTIONS", "DATA_EVENTS", "DATA_POSTINGS", "DATA_COUNTS", "DATA_SKU"]
TAB = {"START_HERE": "#1F3864", "EXECUTIVE_SUMMARY": "#1F3864", "EVIDENCE_EXAMPLE": "#1F3864", "REVIEW_QUEUE": "#C00000",
       "EVIDENCE_DETAIL": "#C00000", "SHIFT_HANDOFF": "#ED7D31", "HANDOFF_EXCEPTIONS": "#ED7D31", "COUNT_RECONCILIATION": "#ED7D31",
       "COUNT_PRIORITY": "#ED7D31", "CORRECTION_SIMULATION": "#7030A0", "RULE_CATALOG": "#548235", "GLOSSARY_ASSUMPTIONS": "#548235"}
STATUSES = ["OPEN", "IN_REVIEW", "PROPOSED_FIX", "APPROVED_FOR_SIMULATION", "RETESTED", "CLOSED"]
SYNTH = "SYNTHETIC DEMONSTRATION DATA - fictional company, not an employer dataset, not a production deployment."
FOOTER = "&L&8SYNTHETIC DEMONSTRATION DATA - fictional company, not an employer dataset&C&8Integrity Audit v%s - &A&R&8Page &P of &N" % __version__

# REVIEW_QUEUE columns: the ones that print (A:J) come first
QUEUE_COLS = ["rank", "exception_id", "severity", "rule_id", "sku_id", "location_id", "reference_time_local", "reason",
              "issue_type", "primary_case", "observed", "expected", "difference_base_uom", "illustrative_value_cad_sim",
              "case_family", "owner_role", "status", "priority_score", "shift_id", "case_key", "proposed_review_action",
              "root_cause_hypothesis", "evidence_sources", "detection_run_id", "headline"]
QUEUE_WIDTHS = [6, 24, 10, 7, 10, 11, 19, 64, 34, 9, 22, 22, 14, 14, 16, 24, 20, 9, 14, 18, 62, 50, 80, 14, 62]
QL = {n: chr(65 + i) if i < 26 else "A" + chr(65 + i - 26) for i, n in enumerate(QUEUE_COLS)}
LETTER_LANDSCAPE_PT = 11 * 72 - 2 * 0.4 * 72        # printable width at 0.4in side margins


def _f(x):
    if x is None:
        return ""
    if isinstance(x, Decimal):
        return float(x)
    return x


def _num_or_text(s: str):
    s = (s or "").strip()
    if s == "":
        return ""
    try:
        d = Decimal(s)
        if d.is_finite() and all(ch in "0123456789.-+" for ch in s):
            return float(d)
    except Exception:
        pass
    return s


def col_letter(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def est_lines(text, width_units: float, chars_per_unit: float = 1.0) -> int:
    t = str(text or "")
    per = max(1, int(width_units * chars_per_unit))
    return max(1, sum(max(1, math.ceil(len(part) / per)) for part in t.split("\n")))


def width_pt(units: float) -> float:
    return (units * 7 + 5) * 0.75


class Book:
    def __init__(self, path: str):
        # Untrusted CSV text must stay text: never let a value such as "=1+1" become a live formula or a hyperlink.
        # Every real formula in this module is written explicitly with write_formula.
        self.wb = xlsxwriter.Workbook(path, {"strings_to_formulas": False, "strings_to_urls": False, "strings_to_numbers": False})
        self.wb.set_properties({"title": "Event-to-Posting Integrity Audit demo workbook (synthetic data)", "subject": "Cross-shift inventory reconciliation and data quality controls",
                                "author": "Integrity Audit portfolio project", "comments": SYNTH})
        self.wb.set_calc_mode("auto")
        self._cache: Dict[tuple, object] = {}
        w = self.wb
        base = {"font_name": "Calibri", "font_size": 10, "valign": "top"}
        self.base = base
        self.f = {
            "title": w.add_format(dict(base, bold=True, font_size=16, font_color="#1F3864")),
            "sub": w.add_format(dict(base, italic=True, font_color="#7F6000")),
            "banner": w.add_format(dict(base, bold=True, font_color="#9C0006", bg_color="#FFC7CE", text_wrap=True, border=1)),
            "h2": w.add_format(dict(base, bold=True, font_size=12, font_color="#1F3864", bottom=2)),
            "txt": w.add_format(dict(base, text_wrap=True)),
            "txtb": w.add_format(dict(base, text_wrap=True, bold=True)),
            "cell": w.add_format(dict(base)),
            "wrap": w.add_format(dict(base, text_wrap=True)),
            "num": w.add_format(dict(base, num_format="#,##0.###")),
            "int": w.add_format(dict(base, num_format="#,##0")),
            "money": w.add_format(dict(base, num_format="$#,##0.00")),
            "pct": w.add_format(dict(base, num_format="0.0%")),
            "subw": w.add_format(dict(base, italic=True, font_color="#7F6000", text_wrap=True)),
            "cint": w.add_format(dict(base, num_format="#,##0", align="center")),
            "cnum": w.add_format(dict(base, num_format="#,##0.###", align="center")),
            "cpct": w.add_format(dict(base, num_format="0.0%", align="center")),
            "ccell": w.add_format(dict(base, align="center")),
            "kpi": w.add_format(dict(base, bold=True, font_size=14, align="center", border=1, bg_color="#F2F2F2")),
            "kpi_pct": w.add_format(dict(base, bold=True, font_size=14, align="center", border=1, bg_color="#F2F2F2", num_format="0.0%")),
            "kpil": w.add_format(dict(base, text_wrap=True, align="center", font_color="#595959")),
            "hdr": w.add_format(dict(base, bold=True, font_color="#FFFFFF", bg_color="#1F3864", text_wrap=True, border=1)),
            "link": w.add_format(dict(base, font_color="#0563C1", underline=1)),
            "high": w.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"}),
            "med": w.add_format({"bg_color": "#FFEB9C", "font_color": "#7F6000"}),
            "low": w.add_format({"bg_color": "#E2EFDA", "font_color": "#375623"}),
            "good": w.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"}),
            "grey": w.add_format({"bg_color": "#EDEDED", "font_color": "#595959"}),
        }

    def fmt(self, **kw):
        """Cached ad-hoc format on top of the base font."""
        key = tuple(sorted(kw.items()))
        if key not in self._cache:
            self._cache[key] = self.wb.add_format(dict(self.base, **kw))
        return self._cache[key]

    def sheet(self, name: str):
        ws = self.wb.add_worksheet(name)
        ws.hide_gridlines(2)
        if name in TAB:
            ws.set_tab_color(TAB[name])
        return ws

    def table(self, ws, row: int, name: str, headers: List[str], rows: List[list], widths: List[float],
              fmts: Optional[Dict[int, str]] = None, style: str = "Table Style Medium 2"):
        """Write an Excel table whose header is at `row` (0-based). Returns (first_data_row, last_data_row) 0-based."""
        fmts = fmts or {}
        n = max(len(rows), 1)
        data = [[_f(c) for c in r] for r in rows] or [["" for _ in headers]]
        cols = [{"header": h, "format": self.f[fmts.get(ci, "cell")], "header_format": self.f["hdr"]} for ci, h in enumerate(headers)]
        ws.add_table(row, 0, row + n, len(headers) - 1, {"name": name, "columns": cols, "data": data, "style": style, "autofilter": True})
        for ci, wdt in enumerate(widths):
            ws.set_column(ci, ci, wdt)
        ws.set_row(row, 30)
        return row + 1, row + n


PAGE_H_PT = 8.5 * 72


def note(B, ws, row: int, text: str, last_col: int, widths: List[float]):
    """A subtitle line merged across the printed columns and wrapped, so it never runs past the print area."""
    ws.merge_range(row, 0, row, last_col, text, B.f["subw"])
    h = 12 * est_lines(text, sum(widths[:last_col + 1]), 1.25) + 2
    ws.set_row(row, h)
    return h


def paginate(row_heights: List[float], width_units: List[float], title_h: float, head_h: float, margins=(0.4, 0.4, 0.5, 0.55)):
    """Pick an integer print scale and balanced page breaks for a long list sheet (Excel ignores manual breaks under fit-to-page).
    row_heights: body-row heights (pt); head_h: everything above the body on page 1 (title lines + header row);
    title_h: the repeated header row on later pages. Returns (scale_percent, body_row_indexes_that_start_a_page, pages)."""
    avail_w = 11 * 72 - (margins[0] + margins[1]) * 72
    avail_h = (PAGE_H_PT - (margins[2] + margins[3]) * 72) * 0.97          # 3% safety margin
    sw = min(1.0, avail_w / sum(width_pt(w) for w in width_units))
    body = sum(row_heights)

    def pages_at(sc):
        n = 1
        while (head_h + body + (n - 1) * title_h) * sc > n * avail_h:
            n += 1
        return n
    sc = sw
    n = pages_at(sc)
    if n > 1:
        last_fill = ((head_h + body + (n - 1) * title_h) * sc - (n - 1) * avail_h) / avail_h
        if last_fill < 0.30:                               # an almost-empty last page: shrink a little to save it
            sc2 = (n - 1) * avail_h / (head_h + body + (n - 2) * title_h)
            if sc2 >= 0.88 * sw:
                sc = sc2
    sc = math.floor(sc * 100) / 100
    n = pages_at(sc)
    per_page = ((head_h + body + (n - 1) * title_h) * sc) / n          # balanced content per page
    breaks, acc, k = [], 0.0, 0
    for idx, h in enumerate(row_heights):
        cap = per_page - (head_h if k == 0 else title_h) * sc
        if len(breaks) < n - 1 and acc + h * sc / 2 > cap:
            breaks.append(idx)
            k += 1
            acc = 0.0
        acc += h * sc
    return int(sc * 100), breaks, n


def print_setup(ws, area: str, fit=(1, 0), titles: Optional[int] = None, margins=(0.4, 0.4, 0.5, 0.55), center=True,
                scale: Optional[int] = None, breaks: Optional[List[int]] = None):
    """Letter landscape, explicit print area, fit-to-width (or an explicit scale with balanced manual breaks), repeating header row(s),
    synthetic-data footer."""
    ws.set_landscape()
    ws.set_paper(1)
    ws.print_area(area)
    if scale is not None:
        ws.set_print_scale(scale)
        if breaks:
            ws.set_h_pagebreaks(breaks)
    else:
        ws.fit_to_pages(*fit)
    ws.set_margins(left=margins[0], right=margins[1], top=margins[2], bottom=margins[3])
    ws.set_footer(FOOTER)
    if center:
        ws.center_horizontally()
    if titles is not None:
        ws.repeat_rows(titles)


# ------------------------------------------------------------------ main
def build(path: str, res: Result, kpi: Dict[str, object], sim: Optional[SimResult], ds: Dataset,
          prio: List[dict], extra: Dict[str, str]) -> Dict[str, object]:
    B = Book(path)
    F = B.f
    snapshot = res.v.snapshot
    issues = sorted(res.issues, key=lambda i: (0 if i.is_primary else 1, i.priority_rank if i.is_primary else 10 ** 6, i.exception_id))
    prim = [i for i in issues if i.is_primary]
    prim_sorted = sorted(prim, key=lambda i: i.priority_rank)
    info: Dict[str, object] = {}
    ws = {name: B.sheet(name) for name in SHEETS}

    # ================================================================ REVIEW_QUEUE (built first: others reference its ranges)
    q = ws["REVIEW_QUEUE"]
    q.write(0, 0, "REVIEW QUEUE - what to review first, and why", F["title"])
    qh1 = note(B, q, 1, SYNTH + " Filter with the header arrows. Rank is assigned to the primary issue of each case only; "
         "secondary issues stay visible but are not counted as separate cases.", 7, QUEUE_WIDTHS)
    qh2 = note(B, q, 2, "Status is a working column (change it freely); it is NOT read back by the build. Values and CAD amounts are SIMULATED. "
         "Printing covers columns A:H (rank to reason); the remaining columns are for screen use.", 7, QUEUE_WIDTHS)
    qrows = []
    for i in issues:
        srcs = "; ".join("%s:%s (row %s)" % (s[0], s[1], s[2] if s[2] else "?") for s in i.sources) or "(derived from balances)"
        vals = {"rank": i.priority_rank if i.is_primary else "", "exception_id": i.exception_id, "severity": i.severity,
                "rule_id": i.rule_id, "issue_type": i.subtype, "primary_case": "Y" if i.is_primary else "N", "sku_id": i.sku_id,
                "location_id": i.location_id, "reference_time_local": fmt_local(i.ref_ts), "reason": i.reason, "observed": i.observed,
                "expected": i.expected, "difference_base_uom": _f(i.qty_diff), "illustrative_value_cad_sim": _f(i.value_cad_sim),
                "case_family": family(i.rule_id), "owner_role": i.owner_role, "status": i.status, "priority_score": i.priority_score,
                "shift_id": i.shift_id, "case_key": i.case_key, "proposed_review_action": i.review_action,
                "root_cause_hypothesis": i.hypothesis, "evidence_sources": srcs, "detection_run_id": res.run_id,
                "headline": headline(i.rule_id, i.subtype, i.key_id, i.sku_id, i.value_cad_sim)}
        qrows.append([vals[c] for c in QUEUE_COLS])
    wrap_idx = {QUEUE_COLS.index(n): "wrap" for n in ("reason", "proposed_review_action", "root_cause_hypothesis", "evidence_sources")}
    wrap_idx.update({QUEUE_COLS.index("difference_base_uom"): "num", QUEUE_COLS.index("illustrative_value_cad_sim"): "money", QUEUE_COLS.index("rank"): "cint"})
    B.table(q, 3, "tblQueue", QUEUE_COLS, qrows, QUEUE_WIDTHS, wrap_idx)
    q_heights = []
    for k, r_ in enumerate(qrows):                     # explicit heights: Excel does not reliably auto-fit wrapped table rows
        lines = max(est_lines(r_[QUEUE_COLS.index(n)], QUEUE_WIDTHS[QUEUE_COLS.index(n)]) for n in
                    ("reason", "proposed_review_action", "root_cause_hypothesis", "evidence_sources"))
        q.set_row(4 + k, 13 * lines + 2)
        q_heights.append(13 * lines + 2)
    q.freeze_panes(4, 2)
    nq = len(qrows)
    last = 4 + max(nq, 1)
    rng = lambda name: "REVIEW_QUEUE!$%s$5:$%s$%d" % (QL[name], QL[name], last)
    sev_c = QUEUE_COLS.index("severity")
    for val, fk in (("HIGH", "high"), ("MEDIUM", "med"), ("LOW", "low")):
        q.conditional_format(4, sev_c, 3 + max(nq, 1), sev_c, {"type": "cell", "criteria": "==", "value": '"%s"' % val, "format": F[fk]})
    st_c = QUEUE_COLS.index("status")
    q.data_validation(4, st_c, 3 + max(nq, 1), st_c, {"validate": "list", "source": STATUSES})
    q_scale, q_breaks, q_pages = paginate(q_heights or [15], QUEUE_WIDTHS[:8], 30, 22 + qh1 + qh2 + 30)
    print_setup(q, "A1:H%d" % last, titles=3, scale=q_scale, breaks=[4 + b for b in q_breaks])
    info["queue_pages"] = q_pages

    # ================================================================ COUNT_RECONCILIATION (needed for exec ranges)
    c = ws["COUNT_RECONCILIATION"]
    c.write(0, 0, "COUNT RECONCILIATION - independent counts vs system and event ledger", F["title"])
    CW = [10, 18, 10, 11, 10, 12, 12, 12, 10, 10, 9, 34, 50, 22, 22]
    ch1 = note(B, c, 1, SYNTH + " S = system balance by posting time; E = event-ledger balance by event time; C = counted. All as of observed_at, inclusive.", 11, CW)
    ch2 = note(B, c, 2, "A count that matches E but not S is explained by open posting issues - it is not an extra stock loss. Only counts that match neither raise R12. "
         "Printing covers columns A:L.", 11, CW)
    crows = []
    for rcd in res.count_table:
        cn = rcd["count"]
        crows.append([cn.id, fmt_local(cn.observed), cn.sku, cn.loc, cn.status, _f(cn.qty), _f(rcd["S"]), _f(rcd["E"]), "", "",
                      _f(rcd["tol"]), rcd["class"], ", ".join(sorted({x.exception_id for x in rcd["linked"]})), rcd["issue_id"], cn.evidence_ref])
    c0, c1 = B.table(c, 4, "tblCounts", ["count id", "observed (local)", "SKU", "location", "status", "counted (base UoM)",
                                         "system balance S", "event balance E", "C - S", "C - E", "tolerance", "classification",
                                         "linked issues", "R12 exception", "evidence ref"], crows, CW, {5: "num", 6: "num", 7: "num", 10: "cnum"})
    for k, rcd in enumerate(res.count_table):
        rw = c0 + k
        has = rcd["S"] is not None
        c.write_formula(rw, 8, '=IF(ISNUMBER(G%d),F%d-G%d,"")' % (rw + 1, rw + 1, rw + 1), F["cnum"], float(rcd["count"].qty - rcd["S"]) if has else "")
        c.write_formula(rw, 9, '=IF(ISNUMBER(H%d),F%d-H%d,"")' % (rw + 1, rw + 1, rw + 1), F["cnum"], float(rcd["count"].qty - rcd["E"]) if has else "")
    for k in range(max(len(crows), 1)):
        c.set_row(c0 + k, 13.5)
    c.freeze_panes(5, 1)
    for crit, val, fk in (("==", '"UNEXPLAINED_COUNT_VARIANCE"', "high"), ("==", '"SYSTEM_GAP_UNLINKED"', "high"), ("==", '"AGREES"', "good")):
        c.conditional_format(c0, 11, c1, 11, {"type": "cell", "criteria": crit, "value": val, "format": F[fk]})
    c.conditional_format(c0, 11, c1, 11, {"type": "text", "criteria": "begins with", "value": "EXPLAINED", "format": F["med"]})
    nrec = len(res.count_table)
    rec_last = 5 + max(nrec, 1)
    assert c0 + 1 == 6 and c1 + 1 == rec_last, (c0, c1, rec_last)
    info["count_rows"] = (c0 + 1, c1 + 1)
    crng = lambda col: "COUNT_RECONCILIATION!$%s$6:$%s$%d" % (col, col, rec_last)
    c_scale, c_breaks, c_pages = paginate([13.5] * max(len(crows), 1), CW[:12], 30, 22 + ch1 + ch2 + 15 + 30)
    print_setup(c, "A1:L%d" % rec_last, titles=4, scale=c_scale, breaks=[5 + b for b in c_breaks])
    info["count_pages"] = c_pages

    # ================================================================ EXECUTIVE_SUMMARY (one landscape Letter page)
    s = ws["EXECUTIVE_SUMMARY"]
    NCOL = 16                                    # B..Q, uniform width: tiles span two columns, panels span fixed groups
    s.set_column(0, 0, 1)
    s.set_column(1, NCOL, 7.5)
    heights: Dict[int, float] = {}

    def H(row, pt):
        heights[row] = pt
        s.set_row(row, pt)

    def sub(**kw):
        return B.fmt(font_size=8, **kw)

    cell9 = B.fmt(font_size=9)
    cell9b = B.fmt(font_size=9, bold=True)
    int9 = B.fmt(font_size=9, num_format="#,##0")
    pct9 = B.fmt(font_size=9, num_format="0.0%")
    money8 = B.fmt(font_size=8, num_format="$#,##0")
    int9c = B.fmt(font_size=9, num_format="#,##0", align="center")
    hdr9 = B.fmt(font_size=8, bold=True, font_color="#FFFFFF", bg_color="#1F3864", text_wrap=True, border=1, align="center", valign="vcenter")
    hdr9l = B.fmt(font_size=8, bold=True, font_color="#FFFFFF", bg_color="#1F3864", text_wrap=True, border=1, valign="vcenter")
    h2s = B.fmt(font_size=11, bold=True, font_color="#1F3864", bottom=2)
    wrap9 = B.fmt(font_size=9, text_wrap=True, valign="vcenter")
    top8 = B.fmt(font_size=9, text_wrap=True, valign="vcenter", border=1)
    rank8 = B.fmt(font_size=10, bold=True, align="center", valign="vcenter", border=1, bg_color="#F2F2F2")
    foot8 = B.fmt(font_size=7.5, italic=True, font_color="#595959", text_wrap=True)
    banner9 = B.fmt(font_size=9, bold=True, font_color="#9C0006", bg_color="#FFC7CE", text_wrap=True, border=1, valign="vcenter")
    kpi16 = B.fmt(font_size=16, bold=True, align="center", valign="vcenter", border=1, bg_color="#F2F2F2")
    kpi16p = B.fmt(font_size=16, bold=True, align="center", valign="vcenter", border=1, bg_color="#F2F2F2", num_format="0.0%")
    kpil8 = B.fmt(font_size=8, text_wrap=True, align="center", valign="top", font_color="#595959")

    s.merge_range(0, 1, 0, NCOL, "Event-to-Posting Integrity Audit - Executive Summary", B.fmt(font_size=16, bold=True, font_color="#1F3864"))
    H(0, 24)
    s.merge_range(1, 1, 1, NCOL, SYNTH + " All quantities, values and anomalies are simulated.", banner9)
    H(1, 18)
    s.merge_range(2, 1, 2, NCOL, "Data snapshot: %s (local, UTC-07:00)  |  Run: %s  |  Build: v%s" % (fmt_local(snapshot), res.run_id, __version__),
                  sub(italic=True, font_color="#7F6000"))
    H(2, 12)
    H(3, 4)

    # ---- values needed by the tiles (formulas written after the count panel fixes its cell addresses)
    counts_n = kpi["count_classes"]
    cc_ex = counts_n.get("EXPLAINED_BY_OPEN_ISSUES", 0) + counts_n.get("EXPLAINED_BY_ISSUES_AND_POSTING_LAG", 0) + counts_n.get("IN_FLIGHT_POSTING_LAG", 0)
    cc_un = counts_n.get("UNEXPLAINED_COUNT_VARIANCE", 0) + counts_n.get("SYSTEM_GAP_UNLINKED", 0)
    cc_ag = counts_n.get("AGREES", 0)
    cc_part, cc_void = counts_n.get("PARTIAL_NOT_COMPARABLE", 0), counts_n.get("VOID_EXCLUDED", 0)
    cmp_n = cc_ag + cc_ex + cc_un
    agree_pct = cc_ag / max(1, cmp_n)
    cover = kpi["counted_pairs"] / max(1, kpi["eligible_pairs"])
    n_rule = lambda rid: sum(1 for i in prim if i.rule_id == rid)
    n_high = sum(1 for i in prim if i.severity == "HIGH")
    resolved = len(sim.resolved) if sim is not None else 0
    ROW_T, ROW_L = 4, 5
    H(ROW_T, 28)
    H(ROW_L, 24)
    tiles = [("Unique cases\n(primary issues)", '=COUNTIF(%s,"Y")' % rng("primary_case"), len(prim), kpi16),
             ("HIGH severity\ncases", '=COUNTIFS(%s,"HIGH",%s,"Y")' % (rng("severity"), rng("primary_case")), n_high, kpi16),
             ("Count agreement\n(comparable counts)", None, agree_pct, kpi16p),
             ("Count coverage\n(eligible SKU-locations)", None, cover, kpi16p),
             ("Late-posting (timing)\ncases, R10", '=COUNTIFS(%s,"R10",%s,"Y")' % (rng("rule_id"), rng("primary_case")), n_rule("R10"), kpi16),
             ("Handoff cases,\nR11", '=COUNTIFS(%s,"R11",%s,"Y")' % (rng("rule_id"), rng("primary_case")), n_rule("R11"), kpi16),
             ("Unexplained count\nvariances, R12", '=COUNTIFS(%s,"R12",%s,"Y")' % (rng("rule_id"), rng("primary_case")), n_rule("R12"), kpi16),
             ("Resolved in dry run\n(hypothetical approval)", None, resolved, kpi16)]
    tile_cell = {}
    for ti, (label, formula, val, fm) in enumerate(tiles):
        col = 1 + ti * 2
        tile_cell[ti] = (ROW_T, col)
        s.merge_range(ROW_L, col, ROW_L, col + 1, label, kpil8)
        if formula:
            s.merge_range(ROW_T, col, ROW_T, col + 1, "", fm)
            s.write_formula(ROW_T, col, formula, fm, val)
    # split of the HIGH count by family (a timing or handoff case is not a stock problem) -> stays in B7
    fam_n = {fm: sum(1 for i in prim if i.severity == "HIGH" and family(i.rule_id) == fm) for fm in ("STOCK_IMPACT", "TIMING_PROCESS", "DATA_QUALITY")}
    parts = ['COUNTIFS(%s,"HIGH",%s,"%s",%s,"Y")' % (rng("severity"), rng("case_family"), fm, rng("primary_case")) for fm in ("STOCK_IMPACT", "TIMING_PROCESS", "DATA_QUALITY")]
    txt = "Of the HIGH cases: %d stock-impacting, %d timing/handoff, %d data-quality." % (fam_n["STOCK_IMPACT"], fam_n["TIMING_PROCESS"], fam_n["DATA_QUALITY"])
    s.merge_range(6, 1, 6, NCOL, "", B.fmt(font_size=9, italic=True, font_color="#7F6000", align="center"))
    s.write_formula(6, 1, '="Of the HIGH cases: "&%s&" stock-impacting, "&%s&" timing/handoff, "&%s&" data-quality."' % tuple(parts),
                    B.fmt(font_size=9, italic=True, font_color="#7F6000", align="center"), txt)
    H(6, 14)
    H(7, 5)

    # ---- key findings
    s.merge_range(8, 1, 8, NCOL, "Key findings", h2s)
    H(8, 16)
    top1 = prim_sorted[0] if prim_sorted else None
    bullets = [
        "Timing is kept apart from loss: %d late-posting (R10) and %d handoff (R11) cases are listed separately and propose no stock adjustment."
        % (n_rule("R10"), n_rule("R11")),
        "Count evidence: %.1f%% of comparable counts agree with the system; %d more are explained by open posting issues or normal posting lag; "
        "%d are unexplained and go to recount, not to adjustment." % (agree_pct * 100, cc_ex, cc_un),
        "Dry run on a copy (hypothetical approval): %d cases resolved, %d timing cases acknowledged, %d unexpected new exceptions. "
        "This shows the logic works, not that any real operation improved." % (resolved, len(sim.acknowledged) if sim else 0, len(sim.unexpected_new) if sim else 0),
    ]
    for k, b in enumerate(bullets):
        s.merge_range(9 + k, 1, 9 + k, NCOL, "• " + b, wrap9)
        H(9 + k, 12.5 * est_lines(b, 7.5 * NCOL, 1.35) + 1)
    H(12, 5)

    # ---- panels: cases by rule (left, B..J) and priority cases (right, L..Q)
    R0 = 13
    s.merge_range(R0, 1, R0, 9, "Cases by rule (primary issues only)", h2s)
    s.merge_range(R0, 11, R0, 16, "Review these first (top 8, live from REVIEW_QUEUE)", h2s)
    H(R0, 16)
    RH = R0 + 1
    s.write(RH, 1, "Rule", hdr9)
    s.merge_range(RH, 2, RH, 4, "What it finds", hdr9l)
    for k, hname in enumerate(["Cases", "HIGH", "MED", "LOW", "CAD value*"]):
        s.write(RH, 5 + k, hname, hdr9)
    s.write(RH, 11, "#", hdr9)
    s.merge_range(RH, 12, RH, 16, "Priority case (severity, rule, record, SKU, simulated value)", hdr9l)
    H(RH, 22)
    rr = RH + 1
    rule_first = rr
    for rid, cdef in sorted(res.catalog.items()):
        s.write(rr, 1, rid, cell9b)
        s.merge_range(rr, 2, rr, 4, SHORT_RULE_NAMES[rid], cell9)
        s.write_formula(rr, 5, '=COUNTIFS(%s,B%d,%s,"Y")' % (rng("rule_id"), rr + 1, rng("primary_case")), int9c, n_rule(rid))
        for k, sev in enumerate(("HIGH", "MEDIUM", "LOW")):
            s.write_formula(rr, 6 + k, '=COUNTIFS(%s,$B%d,%s,"%s",%s,"Y")' % (rng("rule_id"), rr + 1, rng("severity"), sev, rng("primary_case")), int9c,
                            sum(1 for i in prim if i.rule_id == rid and i.severity == sev))
        v = sum((i.value_cad_sim for i in prim if i.rule_id == rid), Decimal(0))
        s.write_formula(rr, 9, '=SUMIFS(%s,%s,B%d,%s,"Y")' % (rng("illustrative_value_cad_sim"), rng("rule_id"), rr + 1, rng("primary_case")), money8, float(v))
        H(rr, 13)
        rr += 1
    tot = B.fmt(font_size=9, bold=True, top=1, align="center", num_format="#,##0")
    s.write(rr, 1, "Total", B.fmt(font_size=9, bold=True, top=1))
    s.merge_range(rr, 2, rr, 4, "", B.fmt(font_size=9, top=1))
    s.write_formula(rr, 5, "=SUM(F%d:F%d)" % (rule_first + 1, rr), tot, len(prim))
    for k, sev in enumerate(("HIGH", "MEDIUM", "LOW")):
        col = col_letter(6 + k)
        s.write_formula(rr, 6 + k, "=SUM(%s%d:%s%d)" % (col, rule_first + 1, col, rr), tot, sum(1 for i in prim if i.severity == sev))
    tot_val = sum((i.value_cad_sim for i in prim), Decimal(0))
    s.write_formula(rr, 9, "=SUM(J%d:J%d)" % (rule_first + 1, rr), B.fmt(font_size=8, bold=True, top=1, num_format="$#,##0"), float(tot_val))
    H(rr, 13)
    s.merge_range(rr + 1, 1, rr + 1, 9, "* Simulated CAD indicator for cases that carry a quantity difference; not a loss (see note below).", foot8)
    H(rr + 1, 11)
    left_end = rr + 1
    # priority cases: two sheet rows per case
    for k in range(1, 9):
        item = prim_sorted[k - 1] if k <= len(prim_sorted) else None
        r_a = RH + 1 + (k - 1) * 2
        s.merge_range(r_a, 11, r_a + 1, 11, k, rank8)
        hl = ("[%s] %s" % (item.severity, headline(item.rule_id, item.subtype, item.key_id, item.sku_id, item.value_cad_sim))) if item else ""
        fml = '=IFERROR("["&INDEX(%s,MATCH($L%d,%s,0))&"] "&INDEX(%s,MATCH($L%d,%s,0)),"")' % (rng("severity"), r_a + 1, rng("rank"), rng("headline"), r_a + 1, rng("rank"))
        s.merge_range(r_a, 12, r_a + 1, 16, "", top8)
        s.write_formula(r_a, 12, fml, top8, hl)
    top_first, top_last = RH + 1, RH + 16
    for fmt_key, word in (("high", "[HIGH]"), ("med", "[MEDIUM]"), ("low", "[LOW]")):
        s.conditional_format(top_first, 12, top_last, 16, {"type": "formula", "criteria": '=LEFT($M%d,%d)="%s"' % (top_first + 1, len(word), word), "format": F[fmt_key]})
    panel_end = max(left_end, top_last)
    for r_ in range(rr + 2, panel_end + 1):
        H(r_, 13)
    H(panel_end + 1, 5)

    # ---- bottom: count evidence (left) and validation funnel + dry run (right)
    R1 = panel_end + 2
    s.merge_range(R1, 1, R1, 9, "Count evidence (independent point-in-time counts)", h2s)
    s.merge_range(R1, 11, R1, 16, "Data quality funnel and dry run", h2s)
    H(R1, 16)
    hr = R1 + 1                                   # header row shared with the funnel header (same height, no stray gap)
    cr = hr + 1
    crng_class = crng("L")
    s.merge_range(hr, 1, hr, 8, "Result of the independent counts", hdr9l)
    s.write(hr, 9, "Counts", hdr9)

    def count_row(r_, label, formula, val, fm=int9c, bold=False):
        s.merge_range(r_, 1, r_, 8, label, cell9b if bold else cell9)
        s.write_formula(r_, 9, formula, fm, val)
        H(r_, 13)

    ag_row, ex_row, un_row, cmp_row = cr, cr + 1, cr + 2, cr + 3
    count_row(ag_row, "Agrees with the system", '=COUNTIF(%s,"AGREES")' % crng_class, cc_ag)
    count_row(ex_row, "Explained by open posting issues or normal posting lag",
              '=COUNTIF(%s,"EXPLAINED_BY_OPEN_ISSUES")+COUNTIF(%s,"EXPLAINED_BY_ISSUES_AND_POSTING_LAG")+COUNTIF(%s,"IN_FLIGHT_POSTING_LAG")' % ((crng_class,) * 3), cc_ex)
    count_row(un_row, "Unexplained - queued for recount (R12)",
              '=COUNTIF(%s,"UNEXPLAINED_COUNT_VARIANCE")+COUNTIF(%s,"SYSTEM_GAP_UNLINKED")' % ((crng_class,) * 2), cc_un)
    count_row(cmp_row, "Comparable counts (partial and void excluded)", "=J%d+J%d+J%d" % (ag_row + 1, ex_row + 1, un_row + 1), cmp_n, bold=True)
    count_row(cr + 4, "Not comparable: partial counts / void counts",
              '=COUNTIF(%s,"PARTIAL_NOT_COMPARABLE")+COUNTIF(%s,"VOID_EXCLUDED")' % ((crng_class,) * 2), cc_part + cc_void)
    agr_row = cr + 5
    count_row(agr_row, "Count agreement = agrees / comparable", "=IF(J%d=0,0,J%d/J%d)" % (cmp_row + 1, ag_row + 1, cmp_row + 1), agree_pct, pct9, True)
    elig_row = cr + 6
    s.merge_range(elig_row, 1, elig_row, 7, "Eligible SKU-locations / counted at least once (complete counts)", cell9)
    s.write_number(elig_row, 8, kpi["eligible_pairs"], int9c)
    s.write_number(elig_row, 9, kpi["counted_pairs"], int9c)
    H(elig_row, 13)
    cov_row = cr + 7
    count_row(cov_row, "Count coverage = counted / eligible", "=IF(I%d=0,0,J%d/I%d)" % (elig_row + 1, elig_row + 1, elig_row + 1), cover, pct9, True)
    # tiles that depend on the panel
    for ti, addr_row, val, fm in ((2, agr_row, agree_pct, kpi16p), (3, cov_row, cover, kpi16p)):
        rw_, col_ = tile_cell[ti]
        s.merge_range(rw_, col_, rw_, col_ + 1, "", fm)
        s.write_formula(rw_, col_, "=J%d" % (addr_row + 1), fm, val)
    rw_, col_ = tile_cell[7]
    s.merge_range(rw_, col_, rw_, col_ + 1, "", kpi16)
    s.write_number(rw_, col_, resolved, kpi16)
    # right: funnel (header on the shared header row)
    s.merge_range(hr, 11, hr, 12, "File", hdr9l)
    for k, hname in enumerate(["Raw rows", "Valid", "Rejected", "Repeats ignored"]):
        s.write(hr, 13 + k, hname, hdr9)
    fr = cr
    for fname, f in kpi["funnel"].items():
        s.merge_range(fr, 11, fr, 12, fname.replace(".csv", ""), B.fmt(font_size=8))
        s.write_number(fr, 13, f["raw_rows"], int9c)
        s.write_number(fr, 14, f["valid"], int9c)
        s.write_number(fr, 15, f["quarantined"], int9c)
        s.write_number(fr, 16, f["identical_repeats_ignored"], int9c)
        fr += 1
    H(hr, 22)
    s.merge_range(fr, 11, fr, 16, "Dry run on a copy (hypothetical approval; no real change)", B.fmt(font_size=9, bold=True, font_color="#1F3864", top=1))
    dry = [("Resolved (primary cases)", resolved), ("Timing cases acknowledged (no data change)", len(sim.acknowledged) if sim else 0),
           ("Unexpected new exceptions (must be 0)", len(sim.unexpected_new) if sim else 0)]
    for k, (lab, val) in enumerate(dry):
        s.merge_range(fr + 1 + k, 11, fr + 1 + k, 15, lab, cell9)
        s.write_number(fr + 1 + k, 16, val, int9c)
    for r_ in range(cr + 8, fr + 4):
        H(r_, 13)
    end_bottom = max(cov_row, fr + len(dry))
    H(end_bottom + 1, 4)
    # ---- footnotes
    fn1 = ("Simulated CAD value = |quantity difference| x unit_value_cad_sim, shown only for cases that carry a quantity difference (R05-R09, R12, R13, R14; "
           "none for R01-R04, R10, R11). Each rule values a different quantity (R06 the unposted movement, R09 the misposted movement, R13 the negative balance), "
           "so the total is an exposure indicator, not a loss estimate.")
    fn2 = ("Denominators: agreement = valid COMPLETE (and comparable PARTIAL) counts only; coverage = SKU-locations with an opening balance or a completed movement, "
           "excluding the shipping staging bin. Timing (R10) and handoff (R11) cases are reported separately from stock-impacting cases. All data is synthetic.")
    s.merge_range(end_bottom + 2, 1, end_bottom + 2, NCOL, fn1, foot8)
    H(end_bottom + 2, 11 * est_lines(fn1, 7.5 * NCOL, 1.35) + 5)
    s.merge_range(end_bottom + 3, 1, end_bottom + 3, NCOL, fn2, foot8)
    H(end_bottom + 3, 11 * est_lines(fn2, 7.5 * NCOL, 1.35) + 5)
    exec_last = end_bottom + 4
    print_setup(s, "A1:Q%d" % exec_last, (1, 1), margins=(0.4, 0.4, 0.4, 0.5))
    exec_h = sum(heights.get(r_, 15) for r_ in range(exec_last))
    info["exec_height_pt"] = exec_h
    info["exec_width_pt"] = width_pt(1) + NCOL * width_pt(7.5)
    s.set_selection(0, 0, 0, 0)

    # ================================================================ EVIDENCE_EXAMPLE (one page, all fields of a case together)
    ex = ws["EVIDENCE_EXAMPLE"]
    example = next((i for i in prim_sorted if i.rule_id == "R08"), prim_sorted[0] if prim_sorted else None)
    n_cards = min(3, len(example.sources)) if example is not None else 2
    LAST = 3 * max(n_cards, 2) - 1                  # last used column: F for two cards, I for three
    ex_w = [1] + ([22, 25, 1.5] * 3)[:LAST]
    ex.set_column(0, 0, 1)
    for col in range(1, LAST + 1):
        ex.set_column(col, col, [22, 25, 1.5][(col - 1) % 3])
    inner_w = sum(ex_w[2:LAST + 1])                 # width of the merged value area (from column C)
    ex.merge_range(0, 1, 0, LAST, "Evidence example - one exception traced to its source records", B.fmt(font_size=16, bold=True, font_color="#1F3864"))
    ex.set_row(0, 24)
    ex_banner = SYNTH + " This page is a static example (as built); the interactive EVIDENCE_DETAIL sheet holds every evidence row."
    ex.merge_range(1, 1, 1, LAST, ex_banner, banner9)
    ex.set_row(1, 14 * est_lines(ex_banner, sum(ex_w[1:LAST + 1]), 1.15) + 4)
    info["evidence_example"] = example.exception_id if example else ""
    lab9 = B.fmt(font_size=9, bold=True, font_color="#1F3864", valign="top", border=1, bg_color="#F2F2F2")
    val9 = B.fmt(font_size=9, text_wrap=True, valign="top", border=1)
    ex_h = 24 + 28
    if example is not None:
        facts = [("Exception", "%s   (run %s)" % (example.exception_id, res.run_id)),
                 ("Rule and issue type", "%s - %s  [%s]" % (example.rule_id, res.catalog[example.rule_id]["rule_name"], example.subtype)),
                 ("Severity / family", "%s  /  %s  (priority rank %d)" % (example.severity, family(example.rule_id), example.priority_rank)),
                 ("Reference time", fmt_local(example.ref_ts) + " (local, UTC-07:00)"),
                 ("Reason", example.reason), ("Root cause (hypothesis)", example.hypothesis),
                 ("Proposed review action", example.review_action), ("Owner role / status", "%s  /  %s" % (example.owner_role, example.status))]
        r_ = 3
        for lab, val in facts:
            ex.write(r_, 1, lab, lab9)
            ex.merge_range(r_, 2, r_, LAST, val, val9)
            h_ = 13 * est_lines(val, inner_w, 1.2) + 3
            ex.set_row(r_, max(14, h_))
            ex_h += max(14, h_)
            r_ += 1
        ex.set_row(2, 6)
        ex_h += 6
        r_ += 1
        ex.merge_range(r_, 1, r_, LAST, "Source records cited by this exception (every field of each raw record)", h2s)
        ex.set_row(r_, 18)
        ex_h += 18 + 14
        r_ += 1
        raw_rows = {F_EVENTS: {x["_row"]: x for x in ds.rows(F_EVENTS)}, F_POST: {x["_row"]: x for x in ds.rows(F_POST)},
                    F_COUNT: {x["_row"]: x for x in ds.rows(F_COUNT)}}
        idcol = {F_EVENTS: "event_id", F_POST: "posting_id", F_COUNT: "count_id"}
        cards = []
        for fname, rid, rownum in example.sources[:3]:
            raw = raw_rows[fname].get(rownum) or next((x for x in ds.rows(fname) if x[idcol[fname]] == rid), None)
            if raw is not None:
                cards.append((fname, raw))
        n_rows = 0
        for ci, (fname, raw) in enumerate(cards):
            col = 1 + ci * 3
            ex.merge_range(r_, col, r_, col + 1, "%s  (CSV data row %d)" % (fname, raw["_row"]), hdr9l)
            fields = [(k, raw[k]) for k in SCHEMAS[fname]]
            for k, (fk, fv) in enumerate(fields):
                ex.write(r_ + 1 + k, col, fk, lab9)
                ex.write(r_ + 1 + k, col + 1, fv if fv != "" else "(blank)", B.fmt(font_size=9, border=1, font_color="#000000" if fv != "" else "#7F7F7F", italic=(fv == "")))
            n_rows = max(n_rows, len(fields))
        for k in range(n_rows + 1):
            ex.set_row(r_ + k, 13)
        ex_h += 13 * (n_rows + 1)
        r_ = r_ + n_rows + 2
        ex.merge_range(r_, 1, r_, LAST, "How to trace it yourself", h2s)
        ex.set_row(r_, 18)
        trace = ["1. In REVIEW_QUEUE filter exception_id = %s and read the reason." % example.exception_id,
                 "2. In EVIDENCE_DETAIL filter the same exception_id: each row names the source file, record ID and CSV row number.",
                 "3. Open the named CSV (data/raw/demo) in Excel: spreadsheet row = CSV data row + 1 because of the header row."]
        for k, t_ in enumerate(trace):
            ex.merge_range(r_ + 1 + k, 1, r_ + 1 + k, LAST, t_, B.fmt(font_size=9))
            ex.set_row(r_ + 1 + k, 13)
        ex_h += 18 + 13 * 3
        ex_last = r_ + 4
    else:
        ex.write(3, 1, "No exceptions in this scenario.", F["cell"])
        ex_last = 4
    print_setup(ex, "A1:%s%d" % (col_letter(LAST), ex_last), (1, 1), margins=(0.4, 0.4, 0.5, 0.55))
    info["example_height_pt"] = ex_h

    # ================================================================ EVIDENCE_DETAIL
    e = ws["EVIDENCE_DETAIL"]
    e.write(0, 0, "EVIDENCE DETAIL - source records behind every exception", F["title"])
    EW = [24, 6, 19, 11, 7, 24, 9, 9, 5, 9, 9, 25, 17]
    eh1 = note(B, e, 1, SYNTH + " Each row is one raw CSV record cited by an exception; csv row is the data-row number in the named file (header excluded).", 12, EW)
    eh2 = note(B, e, 2, "Interactive sheet: filter exception_id to see one case. For a printable single-case view use EVIDENCE_EXAMPLE.", 12, EW)
    by_row = {F_EVENTS: {r["_row"]: r for r in ds.rows(F_EVENTS)}, F_POST: {r["_row"]: r for r in ds.rows(F_POST)},
              F_COUNT: {r["_row"]: r for r in ds.rows(F_COUNT)}}
    by_id = {F_EVENTS: defaultdict(list), F_POST: defaultdict(list), F_COUNT: defaultdict(list)}
    for fname, idc in ((F_EVENTS, "event_id"), (F_POST, "posting_id"), (F_COUNT, "count_id")):
        for r_ in ds.rows(fname):
            by_id[fname][r_[idc]].append(r_)
    erows = []
    seen = set()
    for i in issues:
        for (fname, rid, rownum) in i.sources:
            raw = by_row[fname].get(rownum) if rownum else (by_id[fname].get(rid) or [None])[0]
            if raw is None:
                continue
            key = (i.exception_id, fname, raw["_row"])
            if key in seen:
                continue
            seen.add(key)
            if fname == F_EVENTS:
                rec = [raw["event_timestamp"], raw["sku_id"], _num_or_text(raw["quantity"]), raw["uom"], raw["from_location"],
                       raw["to_location"], "%s / %s" % (raw["event_type"], raw["status"]), raw["related_id"] or raw["handoff_to_shift"]]
            elif fname == F_POST:
                rec = [raw["system_post_timestamp"], raw["sku_id"], _num_or_text(raw["quantity"]), raw["uom"], raw["from_location"],
                       raw["to_location"], "%s / %s" % (raw["posting_type"], raw["posting_status"]),
                       raw["source_event_id"] or raw["related_posting_id"]]
            else:
                rec = [raw["observed_at"], raw["sku_id"], _num_or_text(raw["counted_qty"]), raw["uom"], "", raw["location_id"],
                       raw["count_status"], raw["evidence_ref"]]
            erows.append([i.exception_id, i.rule_id, fname, rid, raw["_row"]] + rec)
    B.table(e, 3, "tblEvidence", ["exception_id", "rule", "source_file", "record_id", "csv row", "record timestamp", "sku_id",
                                  "quantity", "uom", "from loc", "to loc", "type / status", "reference"], erows, EW, {4: "cint", 7: "num"})
    for k in range(max(len(erows), 1)):
        e.set_row(4 + k, 13.5)
    e.freeze_panes(4, 1)
    info["evidence_rows"] = len(erows)
    e_scale, e_breaks, e_pages = paginate([13.5] * max(len(erows), 1), EW, 30, 22 + eh1 + eh2 + 30)
    print_setup(e, "A1:M%d" % (4 + max(len(erows), 1)), titles=3, scale=e_scale, breaks=[4 + b for b in e_breaks])
    info["evidence_pages"] = e_pages

    # ================================================================ SHIFT_HANDOFF and HANDOFF_EXCEPTIONS
    h = ws["SHIFT_HANDOFF"]
    h.write(0, 0, "SHIFT CUTOFF DIAGNOSTICS - were completed events posted by their own shift's cutoff?", F["title"])
    SWD = [16, 9, 17, 17, 12, 11, 11, 12, 13, 13, 12, 10, 12]
    note(B, h, 1, SYNTH + " Cutoff = shift end; a posting at or before the cutoff is timely. Events in a shift that is still open at the snapshot are not yet due.", 12, SWD)
    note(B, h, 2, "Handoff exceptions (R11) are listed on the HANDOFF_EXCEPTIONS sheet.", 12, SWD)
    srows = []
    for sr in kpi["shift_rows"]:
        srows.append([sr["shift_id"], sr["name"], sr["start"], sr["cutoff"], sr["state"], sr["completed"],
                      sr["posted_by_cutoff"] if sr["posted_by_cutoff"] is not None else "",
                      "", sr["later_posted"] if sr["later_posted"] is not None else "",
                      sr["never_posted"] if sr["never_posted"] is not None else "", "", sr["pending"], sr["pending_unresolved"]])
    hdrs = ["shift_id", "shift", "start (local)", "cutoff (local)", "state at snapshot", "completed events", "posted by cutoff",
            "unposted at cutoff", "posted later (timing)", "never posted", "% posted by cutoff", "pending events", "pending unresolved"]
    f0, f1 = B.table(h, 3, "tblShift", hdrs, srows, SWD, {4: "ccell", 5: "cint", 6: "cint", 7: "cint", 8: "cint", 9: "cint", 10: "cpct", 11: "cint", 12: "cint"})
    for k, sr in enumerate(kpi["shift_rows"]):
        rw = f0 + k
        closed = sr["state"] == "CLOSED"
        unp = (sr["completed"] - sr["posted_by_cutoff"]) if closed else ""
        h.write_formula(rw, 7, '=IF(E%d="CLOSED",F%d-G%d,"")' % (rw + 1, rw + 1, rw + 1), F["cint"], unp)
        pctv = (sr["posted_by_cutoff"] / sr["completed"]) if closed and sr["completed"] else ""
        h.write_formula(rw, 10, '=IF(AND(E%d="CLOSED",F%d>0),G%d/F%d,"")' % (rw + 1, rw + 1, rw + 1, rw + 1), F["cpct"], pctv)
    print_setup(h, "A1:M%d" % (f1 + 1), (1, 1), titles=3)

    hx = ws["HANDOFF_EXCEPTIONS"]
    hx.write(0, 0, "CROSS-SHIFT HANDOFF EXCEPTIONS (R11)", F["title"])
    HW = [24, 10, 12, 10, 14, 11, 64, 36, 22, 44]
    note(B, hx, 1, SYNTH + " Aging = number of closed shifts since the event's shift. A handoff to a shift that is still open is not yet due.", 6, HW)
    hrows = [[i.exception_id, i.severity, i.key_id, i.sku_id, i.shift_id, i.aging_shifts, i.reason, i.subtype, i.owner_role, i.review_action]
             for i in prim if i.rule_id == "R11"]
    B.table(hx, 3, "tblHandoff", ["exception_id", "severity", "event_id", "sku_id", "shift_id", "aging (shifts)",
                                  "reason", "issue_type", "owner_role", "proposed_review_action"], hrows, HW, {5: "cint", 6: "wrap", 9: "wrap"})
    for k, r_ in enumerate(hrows):
        hx.set_row(4 + k, 13 * max(est_lines(r_[6], HW[6]), est_lines(r_[9], HW[9])) + 2)
    for val, fk in (("HIGH", "high"), ("MEDIUM", "med"), ("LOW", "low")):
        hx.conditional_format(4, 1, 3 + max(len(hrows), 1), 1, {"type": "cell", "criteria": "==", "value": '"%s"' % val, "format": F[fk]})
    note(B, hx, 2, "Printing covers columns A:G (to the reason); issue type, owner and review action follow on screen.", 6, HW)
    print_setup(hx, "A1:G%d" % (4 + max(len(hrows), 1)), (1, 0), titles=3)

    # ================================================================ COUNT_PRIORITY
    p = ws["COUNT_PRIORITY"]
    p.write(0, 0, "NEXT CYCLE-COUNT PRIORITIES (transparent score)", F["title"])
    PW = [7, 10, 12, 10, 19, 8, 80]
    note(B, p, 1, SYNTH + " Score = ABC points (A30/B20/C10, simulated pick value) + recency (never 30; >48h 20; >24h 10) + open stock-impacting cases (10 each, max 40) + unexplained variance (20).", 6, PW)
    note(B, p, 2, "Ties break by ABC class, then SKU, then location - the same input always gives the same list.", 6, PW)
    prows = [[r_["rank"], r_["sku_id"], r_["location_id"], r_["abc_class"], r_["last_counted"], r_["score"], r_["reason"]] for r_ in prio]
    B.table(p, 4, "tblPriority", ["rank", "sku_id", "location_id", "abc_class", "last counted (local)", "score", "why"], prows, PW, {0: "cint", 3: "ccell", 5: "cint", 6: "wrap"})
    for k, r_ in enumerate(prows):
        p.set_row(5 + k, 13 * est_lines(r_[6], PW[6]) + 2)
    print_setup(p, "A1:G%d" % (5 + max(len(prows), 1)), (1, 0), titles=4)

    # ================================================================ CORRECTION_SIMULATION
    m = ws["CORRECTION_SIMULATION"]
    m.write(0, 0, "CORRECTION SIMULATION - dry run on a working copy", F["title"])
    m.merge_range(1, 0, 1, 5, "SIMULATION ONLY. Approvals below are HYPOTHETICAL. Raw data is untouched. A cleaner simulated result does not prove any real operational improvement.", F["banner"])
    m.set_row(1, 28)
    MW = [26, 8, 36, 30, 56, 10, 14, 40]
    note(B, m, 2, SYNTH, 6, MW)
    for ci, wdt in enumerate(MW):
        m.set_column(ci, ci, wdt)
    sim_last = 4
    if sim is not None:
        n_prim = len(prim)
        rows_s = [("Primary cases detected (before)", n_prim), ("Resolved by simulated data correction", len(sim.resolved)),
                  ("Acknowledged as timing (no data change)", len(sim.acknowledged)), ("Remaining open after simulation (primary)", None),
                  ("Expected timing side-effects of late corrective postings", len(sim.side_effects)),
                  ("UNEXPECTED new exceptions (must be 0)", len(sim.unexpected_new))]
        m.merge_range(3, 0, 3, 2, "Metric", F["hdr"])
        m.write(3, 3, "Value", B.fmt(bold=True, font_color="#FFFFFF", bg_color="#1F3864", text_wrap=True, border=1, align="center"))
        remaining_prim = n_prim - len(sim.resolved) - len(sim.acknowledged)
        for k, (lab, val) in enumerate(rows_s):
            rw = 4 + k
            m.merge_range(rw, 0, rw, 2, lab, F["cell"])
            if val is None:
                m.write_formula(rw, 3, "=D5-D6-D7", F["cint"], remaining_prim)
            else:
                m.write_number(rw, 3, val, F["cint"])
        r_ = 4 + len(rows_s) + 1
        m.merge_range(r_, 0, r_, 3, "Count classification (before vs after)", F["h2"])
        m.merge_range(r_ + 1, 0, r_ + 1, 1, "Classification", F["hdr"])
        hdr_c = B.fmt(bold=True, font_color="#FFFFFF", bg_color="#1F3864", text_wrap=True, border=1, align="center")
        for k, hname in enumerate(["Before", "After", "Change"]):
            m.write(r_ + 1, 2 + k, hname, hdr_c)
        allc = sorted(set(sim.count_before) | set(sim.count_after))
        for k, cl in enumerate(allc):
            rw = r_ + 2 + k
            b_, a_ = sim.count_before.get(cl, 0), sim.count_after.get(cl, 0)
            m.merge_range(rw, 0, rw, 1, cl, F["cell"])
            m.write_number(rw, 2, b_, F["cint"])
            m.write_number(rw, 3, a_, F["cint"])
            m.write_formula(rw, 4, "=D%d-C%d" % (rw + 1, rw + 1), F["cint"], a_ - b_)
        r_ = r_ + 2 + len(allc) + 1
        m.write(r_, 0, "Correction plan (one row per simulated action)", F["h2"])
        outcome = {hrow["exception_id"]: hrow["status"] for hrow in sim.history if hrow["seq"] == 5}
        arows = [[a.exception_id, a.rule_id, a.subtype, a.action, a.description, "Y" if a.data_change else "N", outcome.get(a.exception_id, ""), APPROVER]
                 for a in sim.actions]
        a0, a1 = B.table(m, r_ + 1, "tblActions", ["exception_id", "rule_id", "issue_type", "action", "description", "data change",
                                                   "simulated outcome", "approval (hypothetical)"], arows, MW, {4: "wrap", 5: "ccell", 7: "wrap"})
        for k, r2 in enumerate(arows):
            m.set_row(a0 + k, 13 * max(est_lines(r2[4], MW[4]), est_lines(r2[7], MW[7])) + 2)
        sim_last = a1 + 1
    else:
        m.write(3, 0, "Simulation not run for this scenario.", F["cell"])
    print_setup(m, "A1:G%d" % sim_last, (1, 0))

    # ================================================================ RULE_CATALOG
    rc = ws["RULE_CATALOG"]
    rc.write(0, 0, "RULE CATALOG", F["title"])
    RW = [7, 28, 50, 11, 18, 50, 10, 10, 26]
    note(B, rc, 1, SYNTH + " Single source: config/rule_catalog.csv. Severity may be raised by value, aging or lag tiers as documented in docs/RULE_CATALOG.md.", 6, RW)
    rrows = []
    for rid, cdef in sorted(res.catalog.items()):
        rrows.append([rid, cdef["rule_name"], cdef["business_intent"], cdef["base_severity"], cdef["owner_role"],
                      cdef["proposed_review_action"], "", int(cdef["precedence"]), cdef["simulated_action"]])
    q0, q1 = B.table(rc, 3, "tblRules", ["rule_id", "rule_name", "business_intent", "base severity", "owner_role", "proposed_review_action",
                                         "cases in this run", "precedence", "simulated_action"], rrows, RW, {1: "wrap", 2: "wrap", 4: "wrap", 5: "wrap", 6: "cint"})
    for k, (rid, _c) in enumerate(sorted(res.catalog.items())):
        rw = q0 + k
        rc.write_formula(rw, 6, '=COUNTIFS(%s,A%d,%s,"Y")' % (rng("rule_id"), rw + 1, rng("primary_case")), F["cint"], n_rule(rid))
        rc.set_row(rw, 13 * max(est_lines(rrows[k][2], RW[2]), est_lines(rrows[k][5], RW[5]), est_lines(rrows[k][1], RW[1])) + 2)
    print_setup(rc, "A1:G%d" % (q1 + 1), (1, 0), titles=3)

    # ================================================================ GLOSSARY_ASSUMPTIONS
    g = ws["GLOSSARY_ASSUMPTIONS"]
    g.set_column(0, 0, 26)
    g.set_column(1, 1, 110)
    g.write(0, 0, "GLOSSARY & ASSUMPTIONS", F["title"])
    items = [
        ("SYNTHETIC", SYNTH),
        ("Event vs posting", "An EVENT is the floor record of something that physically happened (event time). A POSTING is the ERP/WMS record (posting time, stored in UTC). They are matched by source_event_id."),
        ("Shift & cutoff", "Day 06:00-14:00, Evening 14:00-22:00, Night 22:00-06:00 local. Intervals are half-open [start, end). Cutoff = shift end. A posting at or before the cutoff is timely."),
        ("System balance S", "Opening + posted, valid postings with post time <= as-of time (what the system shows)."),
        ("Event balance E", "Opening + completed, valid events with event time <= as-of time (what the floor log says)."),
        ("Count C", "Independent point-in-time count. COMPLETE counts compare fully; PARTIAL is a lower bound; VOID is ignored. Zero is a valid count; blank is rejected."),
        ("Primary vs secondary", "One real-world problem can break several rules. The primary issue ranks the case; secondary issues stay visible and are never added up as separate losses."),
        ("Quarantine", "Rows that fail R01-R03 or conflict on ID (R04) raise an issue and are excluded from matching and balances. Their counterparts are not double-flagged."),
        ("Timing vs loss", "R10 means the posting was late. It is not evidence of missing stock and no adjustment is proposed."),
        ("Root cause", "Every root cause is a HYPOTHESIS to be confirmed by the owner role. No cause is asserted as fact."),
        ("UoM", "Quantities are converted to the SKU base UoM only through explicit lookups (3-decimal, half-up rounding). Unknown conversions raise R03; nothing is guessed."),
        ("Illustrative CAD", "Uses unit_value_cad_sim, a synthetic value. It indicates relative exposure only; it is not a loss, saving or ROI."),
        ("Simulation", "Dry run on a working copy with hypothetical approval. Corrective postings are posted at the snapshot time, so they show up as late (expected timing side-effects)."),
        ("Status column", "REVIEW_QUEUE.status is a working column for the reader. Edits are not read back by the build."),
        ("Recalculation", "Summary cells are Excel formulas over REVIEW_QUEUE/COUNT_RECONCILIATION with cached values written at build time. Excel recalculates on open."),
        ("Printing", "All sheets are set to Letter landscape. EXECUTIVE_SUMMARY and EVIDENCE_EXAMPLE are one page each; list sheets print one page wide with repeating headers and a limited print area; DATA_* sheets are for screen use."),
        ("Not implemented", "Live ERP/WMS access, Power Query connection, macros, automatic stock adjustments, DST handling across a clock change."),
    ]
    for k, (a, b) in enumerate(items):
        g.write(2 + k, 0, a, F["txtb"])
        g.write(2 + k, 1, b, F["txt"])
        g.set_row(2 + k, 13 * est_lines(b, 110) + 3)
    print_setup(g, "A1:B%d" % (2 + len(items)), (1, 0))

    # ================================================================ DATA sheets (screen use)
    def data_sheet(name, tname, fname, note):
        d = ws[name]
        cols = SCHEMAS[fname]
        d.write(0, 0, SYNTH + " " + note + " Source file: %s (raw, unchanged; invalid values kept as text). For screen use; not designed for printing." % fname, F["sub"])
        rws = [[_num_or_text(r_[c_]) if c_ in ("quantity", "counted_qty", "quantity_in_base_uom", "unit_value_cad_sim", "count_tolerance_base", "to_base_factor") else r_[c_] for c_ in cols]
               for r_ in ds.rows(fname)]
        wd = [max(10, min(26, len(c_) + 3)) for c_ in cols]
        B.table(d, 2, tname, cols, rws, wd)
        d.freeze_panes(3, 1)
        print_setup(d, "A1:%s%d" % (col_letter(len(cols) - 1), 3 + max(len(rws), 1)), (1, 0), titles=2)

    data_sheet("DATA_EVENTS", "tblEvents", F_EVENTS, "Operational event log (floor record).")
    data_sheet("DATA_POSTINGS", "tblPostings", F_POST, "ERP/WMS posting extract (UTC timestamps).")
    data_sheet("DATA_COUNTS", "tblCountsRaw", F_COUNT, "Cycle-count sheet extract.")
    data_sheet("DATA_SKU", "tblSku", "sku_master.csv", "SKU master with simulated unit values.")

    # ================================================================ START_HERE
    sh = ws["START_HERE"]
    sh.set_column(0, 0, 3)
    sh.set_column(1, 1, 28)
    sh.set_column(2, 2, 105)
    sh.write(0, 1, "Event-to-Posting Integrity Audit", F["title"])
    sh.write(2, 1, "Cross-Shift Inventory Reconciliation & Data Quality Controls", F["sub"])
    sh.merge_range(1, 1, 1, 2, SYNTH, F["banner"])
    sh.set_row(1, 30)
    sh.write(3, 1, "What this is", F["h2"])
    sh.write(3, 2, "", F["h2"])
    sh.merge_range(4, 1, 4, 2, "A reproducible investigation tool: it compares the floor event log, the ERP posting extract and independent cycle counts, "
                   "separates timing/process problems from real count variances, and ranks what to review first with traceable evidence. "
                   "Built as a personal portfolio project with fictional data; it does not use any real company's system or data.", F["txt"])
    sh.set_row(4, 58)
    sh.write(6, 1, "60-second path", F["h2"])
    sh.write(6, 2, "", F["h2"])
    steps = [("1. Read the summary", "EXECUTIVE_SUMMARY - one page: KPIs, key findings, cases by rule, the top 8 cases to review first."),
             ("2. Open the queue", "REVIEW_QUEUE - filter by severity / rule / SKU. Read the 'reason' column; it is written to be explainable in two sentences."),
             ("3. Follow the evidence", "EVIDENCE_EXAMPLE shows one case end to end; EVIDENCE_DETAIL (filter exception_id) has every case with source file, record ID and CSV row."),
             ("4. Check shift timing", "SHIFT_HANDOFF - which events were posted by their own cutoff; HANDOFF_EXCEPTIONS - unresolved handoffs."),
             ("5. Check counts", "COUNT_RECONCILIATION - counts that match the event ledger but not the system are explained, not new losses."),
             ("6. See the dry run", "CORRECTION_SIMULATION - what a hypothetical approved fix would resolve on a COPY of the data.")]
    for k, (a, b) in enumerate(steps):
        sh.write(7 + k, 1, a, F["txtb"])
        sh.write(7 + k, 2, b, F["txt"])
        sh.set_row(7 + k, 13 * est_lines(b, 105) + 3)
    sh.write(14, 1, "Sheet index", F["h2"])
    sh.write(14, 2, "", F["h2"])
    desc = {"START_HERE": "This page", "EXECUTIVE_SUMMARY": "Manager view, one landscape Letter page",
            "EVIDENCE_EXAMPLE": "One case traced to its raw records, one landscape Letter page", "REVIEW_QUEUE": "Ranked, filterable exception queue (prints columns A:H)",
            "EVIDENCE_DETAIL": "Raw source records behind every exception (interactive)", "SHIFT_HANDOFF": "Cutoff diagnostics by shift",
            "HANDOFF_EXCEPTIONS": "Cross-shift handoff exceptions (R11)", "COUNT_RECONCILIATION": "Count vs system vs event ledger",
            "COUNT_PRIORITY": "Next cycle-count priorities", "CORRECTION_SIMULATION": "Dry-run before/after", "RULE_CATALOG": "R01-R14 definitions",
            "GLOSSARY_ASSUMPTIONS": "Terms, assumptions and printing notes", "DATA_EVENTS": "Raw events (screen use)",
            "DATA_POSTINGS": "Raw ERP postings (screen use)", "DATA_COUNTS": "Raw counts (screen use)", "DATA_SKU": "SKU master (screen use)"}
    for k, name in enumerate(SHEETS):
        sh.write_url(15 + k, 1, "internal:'%s'!A1" % name, F["link"], name)
        sh.write(15 + k, 2, desc[name], F["txt"])
        sh.set_row(15 + k, 13 * est_lines(desc[name], 105) + 3)
    r_ = 15 + len(SHEETS) + 1
    sh.write(r_, 1, "Build info", F["h2"])
    sh.write(r_, 2, "", F["h2"])
    meta = [("Version", __version__), ("Snapshot (local UTC-07:00)", fmt_local(snapshot)), ("Detection run", res.run_id),
            ("Input fingerprint", res.input_hash[:16]), ("Scenario", extra.get("scenario", "")),
            ("Printing", "Letter landscape on every sheet. To print the manager pack, print EXECUTIVE_SUMMARY and EVIDENCE_EXAMPLE (one page each). Use File > Print with 'Print Active Sheets'; avoid 'Entire Workbook' unless you want the raw DATA sheets."),
            ("Verification status", "Automated tests and a partial independent SQL cross-check (10 of 14 rules): see docs/UAT_PLAN_AND_RESULTS.md and docs/LIMITATIONS.md. Excel and print walkthrough: see the README."),
            ("Rebuild", "python -m stocktrace build --scenario demo --output output/demo   (an existing, user-modified workbook is not overwritten without --force)")]
    for k, (a, b) in enumerate(meta):
        sh.write(r_ + 1 + k, 1, a, F["txtb"])
        sh.write(r_ + 1 + k, 2, b, F["txt"])
        sh.set_row(r_ + 1 + k, 13 * est_lines(b, 105) + 3)
    print_setup(sh, "A1:C%d" % (r_ + 1 + len(meta)), scale=100, breaks=[14])

    ws["START_HERE"].activate()
    B.wb.close()
    info["queue_rows"] = nq
    info["queue_last_row"] = last
    return info

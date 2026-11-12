"""Print layout: every sheet is configured (Letter landscape, print area, footer); the one-page sheets really fit one page at a readable
scale; long sheets use an explicit scale with balanced manual page breaks (Excel ignores manual breaks under fit-to-page); no printed
header is wider than its column; the printed evidence example shows every field of each cited record together."""
import math
import os

import openpyxl
import pytest
from openpyxl.utils import get_column_letter, range_boundaries

from stocktrace.schema import SCHEMAS

LETTER_W, LETTER_H = 792.0, 612.0
SCREEN_ONLY = {"DATA_EVENTS", "DATA_POSTINGS", "DATA_COUNTS", "DATA_SKU"}
ONE_PAGE = {"EXECUTIVE_SUMMARY": 0.80, "EVIDENCE_EXAMPLE": 0.85}


@pytest.fixture(scope="module")
def wb(built_demo):
    return openpyxl.load_workbook(os.path.join(built_demo, "Integrity_Audit_Demo.xlsx"))


@pytest.fixture(scope="module")
def wbv(built_demo):
    return openpyxl.load_workbook(os.path.join(built_demo, "Integrity_Audit_Demo.xlsx"), data_only=True)


def geometry(ws):
    c1, r1, c2, r2 = range_boundaries(ws.print_area.split("!")[-1].replace("$", ""))
    widths = {}
    for dim in ws.column_dimensions.values():
        for c in range(dim.min or 0, (dim.max or 0) + 1):
            widths[c] = dim.width
    colw = {c: ((widths.get(c) or 8.43) * 7 + 5) * 0.75 for c in range(c1, c2 + 1)}
    rowh = {}
    for r in range(r1, r2 + 1):
        h = ws.row_dimensions[r].height
        if not h:
            h = max([15] + [(ws.cell(r, c).font.sz or 10) * 1.3 for c in range(c1, c2 + 1) if ws.cell(r, c).value not in (None, "")])
        rowh[r] = h
    return (c1, r1, c2, r2), colw, rowh, widths


def margins(ws):
    m = ws.page_margins
    return m.left * 72, m.right * 72, m.top * 72, m.bottom * 72


def test_every_sheet_is_configured_for_letter_landscape_with_a_print_area_and_footer(wb):
    for ws in wb:
        assert ws.page_setup.orientation == "landscape", ws.title
        assert ws.page_setup.paperSize == 1, ws.title                        # Letter
        assert ws.print_area, ws.title
        assert "SYNTHETIC" in (ws.oddFooter.left.text or ""), ws.title
        assert "&P" in (ws.oddFooter.right.text or "") or "Page" in (ws.oddFooter.right.text or ""), ws.title


@pytest.mark.parametrize("name,min_scale", sorted(ONE_PAGE.items()))
def test_one_page_sheets_fit_one_page_at_a_readable_scale(wb, name, min_scale):
    ws = wb[name]
    assert ws.sheet_properties.pageSetUpPr.fitToPage is True
    assert ws.page_setup.fitToHeight in (None, 1) and ws.page_setup.fitToWidth in (None, 1)
    (c1, r1, c2, r2), colw, rowh, _ = geometry(ws)
    ml, mr, mt, mb = margins(ws)
    scale = min(1.0, (LETTER_W - ml - mr) / sum(colw.values()), (LETTER_H - mt - mb) / sum(rowh.values()))
    assert scale >= min_scale, "%s would print at %.0f%%" % (name, scale * 100)


def test_print_area_contains_all_content_of_the_one_page_and_short_sheets(wb):
    for name in ("EXECUTIVE_SUMMARY", "EVIDENCE_EXAMPLE", "START_HERE", "GLOSSARY_ASSUMPTIONS", "SHIFT_HANDOFF", "COUNT_PRIORITY"):
        ws = wb[name]
        (c1, r1, c2, r2), *_ = geometry(ws)
        outside = [c.coordinate for row in ws.iter_rows() for c in row if c.value not in (None, "") and not (c1 <= c.column <= c2 and r1 <= c.row <= r2)]
        assert outside == [], (name, outside[:5])


def _pages(ws):
    """Simulate Excel's pagination for sheets that use an explicit scale and manual breaks. Returns (scale, [fill ratio per page])."""
    (c1, r1, c2, r2), colw, rowh, _ = geometry(ws)
    scale = (ws.page_setup.scale or 100) / 100.0
    ml, mr, mt, mb = margins(ws)
    avail = LETTER_H - mt - mb
    title_rows = []
    if ws.print_title_rows:
        a, b = ws.print_title_rows.replace("$", "").split(":")
        title_rows = list(range(int(a), int(b) + 1))
    th = sum(rowh[r] for r in title_rows if r in rowh) * scale
    breaks = sorted(b.id + 1 for b in ws.row_breaks.brk)
    pages, cur, used = [], [], 0.0
    for r in range(r1, r2 + 1):
        h = rowh[r] * scale
        if cur and (r in breaks or used + h > avail):
            pages.append(used)
            cur, used = [], th
        cur.append(r)
        used += h
    pages.append(used)
    return scale, [u / avail for u in pages]


@pytest.mark.parametrize("name", ["REVIEW_QUEUE", "EVIDENCE_DETAIL", "COUNT_RECONCILIATION", "START_HERE"])
def test_long_sheets_use_explicit_scale_and_manual_breaks_not_fit_to_page(wb, name):
    ws = wb[name]
    assert not (ws.sheet_properties.pageSetUpPr and ws.sheet_properties.pageSetUpPr.fitToPage), "manual page breaks are ignored under fit-to-page"
    assert ws.row_breaks.brk or name == "START_HERE"
    scale, fills = _pages(ws)
    if name != "START_HERE":
        assert scale >= 0.70, (name, scale)
    assert all(f > 0.25 for f in fills), (name, [round(f, 2) for f in fills])      # no nearly-empty page
    assert max(fills) <= 1.0001


def test_start_here_page_break_keeps_the_sheet_index_together(wb):
    ws = wb["START_HERE"]
    brk = [b.id for b in ws.row_breaks.brk]
    assert brk == [14]
    assert ws.cell(15, 2).value == "Sheet index"


def test_list_sheets_print_one_page_wide_at_a_readable_scale(wb):
    for name in ("HANDOFF_EXCEPTIONS", "COUNT_PRIORITY", "CORRECTION_SIMULATION", "RULE_CATALOG", "GLOSSARY_ASSUMPTIONS", "SHIFT_HANDOFF"):
        ws = wb[name]
        (c1, r1, c2, r2), colw, rowh, _ = geometry(ws)
        ml, mr, *_ = margins(ws)
        scale = min(1.0, (LETTER_W - ml - mr) / sum(colw.values()))
        assert scale >= 0.70, (name, round(scale, 2))
        assert ws.sheet_properties.pageSetUpPr.fitToPage is True and ws.page_setup.fitToHeight in (0, None, 1)


def test_no_printed_table_header_is_wider_than_its_column(wb):
    """Header names like 'posted_by_cutoff' cannot wrap and were clipped in print; every word must fit its column."""
    bad = []
    for name in ("REVIEW_QUEUE", "EVIDENCE_DETAIL", "SHIFT_HANDOFF", "HANDOFF_EXCEPTIONS", "COUNT_RECONCILIATION", "COUNT_PRIORITY",
                 "CORRECTION_SIMULATION", "RULE_CATALOG"):
        ws = wb[name]
        (c1, r1, c2, r2), colw, rowh, widths = geometry(ws)
        for t in ws.tables.values():
            hr = int(t.ref.split(":")[0][1:])
            for c in range(c1, min(c2, range_boundaries(t.ref)[2]) + 1):
                v = ws.cell(hr, c).value
                if not isinstance(v, str):
                    continue
                longest = len(v) if " " not in v else max(len(w) for w in v.split(" "))      # snake_case cannot wrap
                if longest > (widths.get(c) or 8.43) * 1.02:
                    bad.append((name, get_column_letter(c), v, widths.get(c)))
    assert bad == [], bad


def test_priority_list_and_kpis_are_on_the_executive_page(wbv):
    s = wbv["EXECUTIVE_SUMMARY"]
    text = " ".join(str(c.value) for row in s.iter_rows() for c in row if c.value not in (None, ""))
    for needle in ("SYNTHETIC DEMONSTRATION DATA", "Key findings", "Cases by rule", "Review these first", "Count evidence", "Unique cases",
                   "HIGH severity", "Count agreement", "Count coverage", "Dry run", "not a loss"):
        assert needle in text, needle
    heads = [c.value for row in s.iter_rows(min_col=13, max_col=13) for c in row if isinstance(c.value, str) and c.value.startswith("[")]
    assert len(heads) == 8


def test_evidence_example_shows_all_fields_of_every_cited_record_together(wbv, ds_demo, res_demo):
    ws = wbv["EVIDENCE_EXAMPLE"]
    example = next(i for i in sorted([i for i in res_demo.issues if i.is_primary], key=lambda x: x.priority_rank) if i.rule_id == "R08")
    facts = " ".join(str(c.value) for row in ws.iter_rows(min_row=1, max_row=12) for c in row if c.value)
    assert example.exception_id in facts and example.reason in facts and "SYNTHETIC" in facts
    cards = 0
    for fname, rid, rownum in example.sources[:3]:
        raw = next(r for r in ds_demo.rows(fname) if r["_row"] == rownum)
        header = [(c.row, c.column) for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith(fname)]
        assert len(header) == 1, fname
        hr, hc = header[0]
        shown = {}
        r = hr + 1
        while ws.cell(r, hc).value:
            shown[ws.cell(r, hc).value] = ws.cell(r, hc + 1).value
            r += 1
        assert list(shown) == SCHEMAS[fname], fname                       # every field, in schema order, contiguous (no page split inside a record)
        for k, v in shown.items():
            assert (v if v != "(blank)" else "") == raw[k], (fname, k)
        assert "CSV data row %d" % rownum in ws.cell(hr, hc).value
        cards += 1
    assert cards == len(example.sources[:3]) >= 2


def test_executive_footnote_rows_are_tall_enough_for_excel(wb):
    """Found in a real Excel PDF: two-line 7.5 pt footnotes were cut off because the rows were only ~18 pt high."""
    ws = wb["EXECUTIVE_SUMMARY"]
    rows = [c.row for row in ws.iter_rows(min_col=2, max_col=2) for c in row
            if isinstance(c.value, str) and (c.value.startswith("Simulated CAD value =") or c.value.startswith("Denominators:"))]
    assert len(rows) == 2
    for r in rows:
        lines = math.ceil(len(ws.cell(r, 2).value) / (120 * 1.8))        # generous chars-per-line for 7.5 pt Calibri over 120 width units
        assert ws.row_dimensions[r].height >= 10.5 * lines + 4 - 0.01 and ws.row_dimensions[r].height >= 25, (r, ws.row_dimensions[r].height)


def test_evidence_example_is_sized_to_the_cards_it_shows(wb):
    """Found in a real Excel PDF: reserving room for an unused third card shrank the page to ~81%."""
    ws = wb["EVIDENCE_EXAMPLE"]
    area = ws.print_area.split("!")[-1].replace("$", "")
    assert area.startswith("A1:F"), area                                  # two cards -> columns A:F
    (c1, r1, c2, r2), colw, rowh, _ = geometry(ws)
    ml, mr, mt, mb = margins(ws)
    scale = min(1.0, (LETTER_W - ml - mr) / sum(colw.values()), (LETTER_H - mt - mb) / sum(rowh.values()))
    assert scale >= 0.95, scale

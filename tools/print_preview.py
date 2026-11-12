"""OPTIONAL developer tool: layout preview of the workbook's print setup (NOT Excel's own output).

Renders the printable sheets page by page with its own renderer (Helvetica metrics scaled to approximate Calibri), honouring print area,
orientation, fit-to-page / explicit scale, manual page breaks and repeated title rows, and reports clipped text and near-empty pages.
It exists because Excel's PDF export cannot be driven from the build environment. It is evidence for the layout design, not a substitute
for printing from Excel.

    pip install pymupdf openpyxl
    python tools/print_preview.py output/demo/Integrity_Audit_Demo.xlsx output/demo/Integrity_Audit_Print_Preview.pdf
"""
import sys, math, os
import fitz, openpyxl
from openpyxl.utils import get_column_letter, range_boundaries

SEV = {"[HIGH]": (1, 0.78, 0.81), "[MEDIUM]": (1, 0.92, 0.61), "[LOW]": (0.89, 0.94, 0.85)}

def rgb(c, default=None):
    try:
        v = c.rgb
        if isinstance(v, str) and len(v) == 8: return tuple(int(v[i:i+2], 16)/255 for i in (2, 4, 6))
    except Exception: pass
    return default

def fmt_val(v, nf):
    if v is None: return ""
    if isinstance(v, (int, float)):
        if "%" in nf: return ("%.1f%%" % (v * 100)) if "0.0" in nf else ("%.0f%%" % (v * 100))
        if "$" in nf: return "$" + format(v, ",.2f" if ".00" in nf else ",.0f")
        if "#,##0" in nf and "." not in nf: return format(v, ",.0f")
        if isinstance(v, float) and v == int(v): return str(int(v))
        if isinstance(v, float): return ("%.3f" % v).rstrip("0").rstrip(".")
        return str(v)
    return str(v)

CAL = 0.85   # measured from Excel PDFs: Calibri is ~0.82x Helvetica; 0.85 keeps the check slightly strict. Line height ~1.3x font.
HELV, HELVB = fitz.Font("helv"), fitz.Font("hebo")

def sheet_geometry(ws):
    area = ws.print_area.split("!")[-1].replace("$", "")
    c1, r1, c2, r2 = range_boundaries(area)
    widths = {}
    for dim in ws.column_dimensions.values():
        for cc in range(dim.min or 0, (dim.max or 0) + 1): widths[cc] = dim.width
    colw = {c: ((widths.get(c) or 8.43) * 7 + 5) * 0.75 for c in range(c1, c2 + 1)}
    rowh = {}
    for r in range(r1, r2 + 1):
        h = ws.row_dimensions[r].height
        if not h:   # Excel auto-sizes unset rows to the largest font in the row
            h = max([15] + [(ws.cell(r, c).font.sz or 10) * 1.3 for c in range(c1, c2 + 1) if ws.cell(r, c).value not in (None, '')])
        rowh[r] = h
    return area, c1, r1, c2, r2, colw, rowh

def draw_rows(page, ws, rows, ys, xs, c1, c2, scale, merged, covered, clipped, sheet):
    for r in rows:
        for c in range(c1, c2 + 1):
            if (r, c) in covered: continue
            cell = ws.cell(r, c)
            ca, rb, cc, rd = merged.get((r, c), (c, r, c, r))
            if rb not in ys: continue
            x0, y0, x1 = xs[ca], ys[rb], xs[min(cc, c2) + 1]
            y1 = ys[min(rd, rows[-1]) + 1] if min(rd, rows[-1]) + 1 in ys else ys[rows[-1] + 1]
            val = cell.value
            fill = rgb(cell.fill.fgColor) if cell.fill and cell.fill.fill_type == "solid" else None
            if isinstance(val, str) and sheet == "EXECUTIVE_SUMMARY" and c >= 13:
                for tag, fc in SEV.items():
                    if val.startswith(tag): fill = fc
            if fill: page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None, fill=fill)
            b = cell.border
            for side, pts in (("left", ((x0, y0), (x0, y1))), ("right", ((x1, y0), (x1, y1))), ("top", ((x0, y0), (x1, y0))), ("bottom", ((x0, y1), (x1, y1)))):
                sd = getattr(b, side)
                if sd is not None and sd.style:
                    page.draw_line(pts[0], pts[1], color=(0.6, 0.6, 0.6), width=0.5 * (2 if sd.style in ("medium", "thick", "double") else 1))
            if val is None or val == "": continue
            txt = fmt_val(val, cell.number_format or "General")
            fs = (cell.font.sz or 10) * scale * CAL
            bold = bool(cell.font.b); font = HELVB if bold else HELV
            col = rgb(cell.font.color, (0, 0, 0)) if cell.font.color is not None else (0, 0, 0)
            al = cell.alignment; wrap = bool(al.wrap_text)
            pad = 2 * scale; avail = (x1 - x0) - 2 * pad
            lines = []
            for para in txt.split("\n"):
                if not wrap or font.text_length(para, fs) <= avail: lines.append(para); continue
                cur = ""
                for wd in para.split(" "):
                    t = (cur + " " + wd).strip()
                    if font.text_length(t, fs) <= avail: cur = t
                    else:
                        if cur: lines.append(cur)
                        cur = wd
                lines.append(cur)
            lh = fs / CAL * 1.3
            where = "%s%d" % (get_column_letter(c), r)
            if not wrap and font.text_length(lines[0], fs) > avail and not isinstance(val, (int, float)):
                # spill is only allowed into genuinely empty neighbours on the right (Excel behaviour for left-aligned text)
                spill_ok = all(ws.cell(r, cx).value in (None, "") for cx in range(cc + 1, min(cc + 3, c2) + 1)) and (al.horizontal in (None, "left", "general")) and c == cc
                if not spill_ok: clipped.append((sheet, where, "no-wrap text wider than its cell", txt[:60]))
            if len(lines) * lh > (y1 - y0) + 0.6 * scale:
                clipped.append((sheet, where, "wrapped text taller than the row (%d lines)" % len(lines), txt[:60]))
            total = len(lines) * lh; va = al.vertical or "bottom"
            ty = y0 + pad + fs * 0.9 if va == "top" else (y0 + ((y1 - y0) - total) / 2 + fs * 0.9 if va == "center" else y1 - total + fs * 0.9 - pad)
            for ln in lines:
                w_ = font.text_length(ln, fs); hz = al.horizontal or ("right" if isinstance(val, (int, float)) else "left")
                tx = x0 + pad if hz in ("left", "general") else (x0 + ((x1 - x0) - w_) / 2 if hz == "center" else x1 - pad - w_)
                page.insert_text((tx, ty), ln, fontsize=fs, fontname="hebo" if bold else "helv", color=col); ty += lh

def render_all(xlsx, sheet, out_prefix, dpi=110):
    wb = openpyxl.load_workbook(xlsx, data_only=True); ws = wb[sheet]
    area, c1, r1, c2, r2, colw, rowh = sheet_geometry(ws)
    W = sum(colw.values())
    ml, mr, mt, mb = [x * 72 for x in (ws.page_margins.left, ws.page_margins.right, ws.page_margins.top, ws.page_margins.bottom)]
    landscape = ws.page_setup.orientation == "landscape"
    pw, ph = (792, 612) if landscape else (612, 792)
    aw, ah = pw - ml - mr, ph - mt - mb - 8
    fit_h = ws.page_setup.fitToHeight; one_page = fit_h in (None, 1)
    total_h = sum(rowh.values())
    fit_on = bool(ws.sheet_properties.pageSetUpPr and ws.sheet_properties.pageSetUpPr.fitToPage)
    manual = sorted(b.id + 1 for b in ws.row_breaks.brk)            # first row (1-based) of each manual page
    if not fit_on:
        scale = (ws.page_setup.scale or 100) / 100.0      # Excel default is 100%
    else:
        scale = min(aw / W, (ah / total_h) if one_page else 9, 1.0)
    title_rows = []
    if ws.print_title_rows:
        a, b = ws.print_title_rows.replace("$", "").split(":"); title_rows = list(range(int(a), int(b) + 1))
    merged = {}; covered = set()
    for mr_ in ws.merged_cells.ranges:
        a, b, c, d = mr_.bounds; merged[(b, a)] = (a, b, c, d)
        for rr in range(b, d + 1):
            for cx in range(a, c + 1):
                if (rr, cx) != (b, a): covered.add((rr, cx))
    # paginate rows
    body = [r for r in range(r1, r2 + 1)]
    pages = []; cur = []; used = 0
    th = sum(rowh[r] for r in title_rows if r in rowh) * scale
    for r in body:
        h = rowh[r] * scale
        limit = ah - (th if pages or cur and cur[0] not in title_rows else 0)
        if cur and (r in manual or used + h > ah + (1.0 if one_page else 0)):
            pages.append(cur); cur = []; used = th if title_rows else 0
        cur.append(r); used += h
    if cur: pages.append(cur)
    doc = fitz.open(); clipped = []; pngs = []
    xs = {}; x = ml + (aw - W * scale) / 2
    for c in range(c1, c2 + 1): xs[c] = x; x += colw[c] * scale
    xs[c2 + 1] = x
    for pi, rows in enumerate(pages):
        page = doc.new_page(width=pw, height=ph)
        draw_list = ([t for t in title_rows if t not in rows] if pi > 0 else []) + rows
        ys = {}; y = mt
        for r in draw_list: ys[r] = y; y += rowh[r] * scale
        ys[draw_list[-1] + 1] = y
        # make y lookups work across the discontinuity between title rows and body rows
        for r in draw_list:
            ys.setdefault(r + 1, ys[r] + rowh[r] * scale) if (r + 1) not in draw_list else None
        draw_rows(page, ws, draw_list, ys, xs, c1, c2, scale, merged, covered, clipped, sheet)
        page.insert_text((ml, ph - 12), "LAYOUT PREVIEW (own renderer, wider-than-Calibri metrics) - NOT Excel output.  %s  page %d of %d  scale %.0f%%  area %s" % (sheet, pi + 1, len(pages), scale * 100, area), fontsize=6.5, color=(0.5, 0.5, 0.5))
        pix = page.get_pixmap(dpi=dpi); png = "%s_p%d.png" % (out_prefix, pi + 1); pix.save(png); pngs.append(png)
    doc.save(out_prefix + ".pdf")
    fills = []
    for rows in pages:
        h = sum(rowh[r] for r in rows) * scale + (th if (rows is not pages[0]) else 0)
        fills.append(h / ah)
    clipped.extend((sheet, "page %d" % (i + 1), "page only %.0f%% filled (mostly blank?)" % (f * 100), "") for i, f in enumerate(fills) if f < 0.2 and len(pages) > 1)
    return scale, len(pages), clipped, pngs


SHEET_ORDER = ["START_HERE", "EXECUTIVE_SUMMARY", "EVIDENCE_EXAMPLE", "REVIEW_QUEUE", "EVIDENCE_DETAIL", "SHIFT_HANDOFF", "HANDOFF_EXCEPTIONS",
               "COUNT_RECONCILIATION", "COUNT_PRIORITY", "CORRECTION_SIMULATION", "RULE_CATALOG", "GLOSSARY_ASSUMPTIONS"]


def main(xlsx, out_pdf):
    import tempfile
    merged = fitz.open()
    total_warn = 0
    with tempfile.TemporaryDirectory() as tmp:
        for sheet in SHEET_ORDER:
            scale, n, clipped, pngs = render_all(xlsx, sheet, os.path.join(tmp, sheet), dpi=60)
            part = fitz.open(os.path.join(tmp, sheet + ".pdf"))
            merged.insert_pdf(part)
            total_warn += len(clipped)
            print("%-22s scale %3.0f%%  pages %d  warnings %d" % (sheet, scale * 100, n, len(clipped)))
            for c in clipped:
                print("    ", c)
    merged.save(out_pdf)
    print("wrote %s (%d pages); warnings: %d" % (out_pdf, len(merged), total_warn))
    return total_warn


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1], sys.argv[2]) else 0)

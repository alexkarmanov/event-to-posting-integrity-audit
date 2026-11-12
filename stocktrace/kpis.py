"""Management metrics and the transparent next-count priority list. Formulas are documented in docs/KPI_DEFINITIONS.md."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Tuple

from .common import ZERO, fmt_local
from .engine import Result
from .validate import F_COUNT, F_EVENTS, F_POST

OPS_LOCS_EXCLUDED = ("SHP-01",)


def eligible_pairs(res: Result) -> List[Tuple[str, str]]:
    """SKU-location combinations with a positive opening balance or a valid completed movement."""
    v = res.v
    pairs = set()
    for ts, sku, loc, q in v.opening:
        if q > 0 and loc not in OPS_LOCS_EXCLUDED:
            pairs.add((sku, loc))
    for e in v.events:
        if e.status == "completed":
            for loc, _ in e.effects():
                if loc not in OPS_LOCS_EXCLUDED:
                    pairs.add((e.sku, loc))
    return sorted(pairs)


def compute(res: Result) -> Dict[str, object]:
    v = res.v
    prim = res.primary()
    k: Dict[str, object] = {}
    k["funnel"] = v.funnel
    ev_t = res.event_table
    k["events_valid"] = len(v.events)
    k["events_completed"] = sum(1 for e in v.events if e.status == "completed")
    k["event_match_status"] = dict(Counter(r["match_status"] for r in ev_t))
    k["issues_total"] = len(res.issues)
    k["cases_unique"] = len({i.case_key for i in res.issues})
    k["cases_by_rule"] = dict(Counter(i.rule_id for i in prim))
    k["cases_by_severity"] = dict(Counter(i.severity for i in prim))
    k["cases_by_status"] = dict(Counter(i.status for i in prim))
    k["affected_skus"] = len({i.sku_id for i in prim if i.sku_id})
    # counts
    elig = eligible_pairs(res)
    counted = {(c.sku, c.loc) for c in v.counts if c.status == "COMPLETE"}
    k["eligible_pairs"] = len(elig)
    k["counted_pairs"] = len([p for p in elig if p in counted])
    k["count_coverage_pct"] = (Decimal(k["counted_pairs"]) / Decimal(max(1, k["eligible_pairs"])) * 100).quantize(Decimal("0.1"))
    cls = Counter(r["class"] for r in res.count_table)
    k["count_classes"] = dict(cls)
    comparable = sum(n for c, n in cls.items() if c not in ("VOID_EXCLUDED", "PARTIAL_NOT_COMPARABLE"))
    k["counts_comparable"] = comparable
    k["counts_agree"] = cls.get("AGREES", 0)
    k["count_agreement_pct"] = (Decimal(cls.get("AGREES", 0)) / Decimal(max(1, comparable)) * 100).quantize(Decimal("0.1"))
    r12 = [i for i in prim if i.rule_id == "R12"]
    k["r12_cases"] = len(r12)
    k["r12_units_net"] = sum((i.qty_diff for i in r12 if i.qty_diff is not None), ZERO)
    k["r12_units_abs"] = sum((abs(i.qty_diff) for i in r12 if i.qty_diff is not None), ZERO)
    k["r12_value_cad_sim"] = sum((i.value_cad_sim for i in r12), ZERO)
    k["exposure_value_cad_sim"] = sum((i.value_cad_sim for i in prim), ZERO)
    k["timing_cases"] = sum(1 for i in prim if i.rule_id == "R10")
    k["handoff_open_high"] = sum(1 for i in prim if i.rule_id == "R11" and i.severity == "HIGH")
    k["handoff_cases"] = sum(1 for i in prim if i.rule_id == "R11")
    k["max_handoff_aging"] = max([i.aging_shifts for i in prim if i.rule_id == "R11"] or [0])
    k["shift_rows"] = shift_table(res)
    return k


def shift_table(res: Result) -> List[dict]:
    snap = res.v.snapshot
    rows = []
    ev_info = {r["event"].id: r for r in res.event_table}
    for s in res.v.master.shifts:
        evs = [r for r in res.event_table if r["event"].shift.shift_id == s.shift_id]
        comp = [r for r in evs if r["event"].status == "completed"]
        closed = s.end <= snap
        posted_cut = sum(1 for r in comp if r["posted_by_cutoff"])
        later = sum(1 for r in comp if (not r["posted_by_cutoff"]) and r["posted_by_snapshot"])
        still = sum(1 for r in comp if not r["posted_by_snapshot"] and closed and r["match_status"] in ("UNPOSTED", "POSSIBLE_MATCH", "PARTIAL_POSTING"))
        pend = sum(1 for r in evs if r["event"].status == "pending")
        pend_unres = sum(1 for i in res.issues if i.rule_id == "R11" and i.shift_id == s.shift_id and i.is_primary
                         and i.subtype in ("UNRESOLVED_HANDOFF_AFTER_CUTOFF", "PENDING_NO_HANDOFF"))
        rows.append({"shift_id": s.shift_id, "name": s.name, "start": fmt_local(s.start), "cutoff": fmt_local(s.end),
                     "state": "CLOSED" if closed else "OPEN", "completed": len(comp) if closed else len(comp),
                     "posted_by_cutoff": posted_cut if closed else None, "later_posted": later if closed else None,
                     "never_posted": still if closed else None, "pending": pend, "pending_unresolved": pend_unres})
    return rows


# ------------------------------------------------------------ next-count priority
ABC_POINTS = {"A": 30, "B": 20, "C": 10}


def count_priority(res: Result, top: int = 15) -> List[dict]:
    m = res.v.master
    snap = res.v.snapshot
    vol: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    for e in res.v.events:
        if e.status == "completed" and e.type == "pick":
            vol[e.sku] += e.qty * m.sku[e.sku]["unit_value"]
    total = sum(vol.values(), ZERO) or Decimal(1)
    ranked = sorted(vol.items(), key=lambda kv: (-kv[1], kv[0]))
    abc: Dict[str, str] = {}
    cum = ZERO
    for sku, val in ranked:
        cum += val
        share = cum / total
        abc[sku] = "A" if (share - val / total) < Decimal("0.80") else ("B" if (share - val / total) < Decimal("0.95") else "C")
    for sku in m.sku:
        abc.setdefault(sku, "C")
    last: Dict[Tuple[str, str], datetime] = {}
    for c in res.v.counts:
        if c.status == "COMPLETE":
            key = (c.sku, c.loc)
            if key not in last or c.observed > last[key]:
                last[key] = c.observed
    open_cases: Dict[Tuple[str, str], int] = defaultdict(int)
    var_cases: Dict[Tuple[str, str], int] = defaultdict(int)
    for i in res.primary():
        key = (i.sku_id, i.location_id)
        if i.rule_id == "R12":
            var_cases[key] += 1
        elif i.rule_id in ("R05", "R06", "R07", "R08", "R09", "R13"):
            open_cases[key] += 1
    rows = []
    for key in eligible_pairs(res):
        sku, loc = key
        a = abc[sku]
        pts_abc = ABC_POINTS[a]
        n_open = open_cases.get(key, 0)
        pts_open = min(40, 10 * n_open)
        pts_var = 20 if var_cases.get(key) else 0
        if key not in last:
            pts_rec, rec_txt = 30, "never counted"
        else:
            hrs = (snap - last[key]).total_seconds() / 3600.0
            if hrs > 48:
                pts_rec, rec_txt = 20, "last counted %d h ago" % hrs
            elif hrs > 24:
                pts_rec, rec_txt = 10, "last counted %d h ago" % hrs
            else:
                pts_rec, rec_txt = 0, "counted within 24 h"
        score = pts_abc + pts_open + pts_var + pts_rec
        why = "ABC %s (+%d); %s (+%d)" % (a, pts_abc, rec_txt, pts_rec)
        if n_open:
            why += "; %d open stock-impacting case(s) (+%d)" % (n_open, pts_open)
        if pts_var:
            why += "; unexplained count variance (+20)"
        rows.append({"sku_id": sku, "location_id": loc, "abc_class": a, "score": score, "reason": why,
                     "last_counted": fmt_local(last[key]) if key in last else "never"})
    rows.sort(key=lambda r: (-r["score"], r["abc_class"], r["sku_id"], r["location_id"]))
    for n, r in enumerate(rows, start=1):
        r["rank"] = n
    return rows[:top] if top else rows

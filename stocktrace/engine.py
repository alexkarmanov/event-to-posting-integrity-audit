"""Reconciliation engine: matching, rules R05-R14, count classification, severity and priority.

Input: a `Dataset` (raw CSV rows). Output: `Result`. The engine never modifies its input.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

from .common import ZERO, fmt_dec, fmt_local, fmt_utc, sev_points
from .loader import Dataset
from .model import Issue, NCount, NEvent, NPosting, Shift
from .schema import EXPECTED_POSTINGS
from .validate import F_COUNT, F_EVENTS, F_POST, Master, Validated, validate

CATALOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "rule_catalog.csv")


def load_catalog(path: str = CATALOG_PATH) -> Dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["rule_id"]: r for r in csv.DictReader(fh)}


@dataclass
class Item:
    kind: str                       # EV (event + its postings) or PO (orphan posting)
    ident: str
    e_eff: List[Tuple[datetime, str, str, Decimal]]   # (ts, sku, loc, delta)
    s_eff: List[Tuple[datetime, str, str, Decimal]]
    keys: Set[str]

    @property
    def skus(self) -> Set[str]:
        return {x[1] for x in self.e_eff} | {x[1] for x in self.s_eff}


@dataclass
class Result:
    ds_name: str
    v: Validated
    params: Dict[str, str]
    catalog: Dict[str, dict]
    issues: List[Issue] = field(default_factory=list)
    event_table: List[dict] = field(default_factory=list)
    count_table: List[dict] = field(default_factory=list)
    shift_table: List[dict] = field(default_factory=list)
    items: List[Item] = field(default_factory=list)
    run_id: str = ""
    input_hash: str = ""
    # ledger indices
    e_ledger: Dict[Tuple[str, str], List[Tuple[datetime, Decimal, str]]] = field(default_factory=dict)
    s_ledger: Dict[Tuple[str, str], List[Tuple[datetime, Decimal, str]]] = field(default_factory=dict)

    def bal_event(self, sku: str, loc: str, t: datetime) -> Decimal:
        return sum((d for ts, d, _ in self.e_ledger.get((sku, loc), []) if ts <= t), ZERO)

    def bal_system(self, sku: str, loc: str, t: datetime) -> Decimal:
        return sum((d for ts, d, _ in self.s_ledger.get((sku, loc), []) if ts <= t), ZERO)

    def primary(self) -> List[Issue]:
        return [i for i in self.issues if i.is_primary]


# ------------------------------------------------------------------ helpers
def _P(params: Dict[str, str], key: str, default: str) -> Decimal:
    return Decimal(params.get(key, default))


def _ed(e: NEvent) -> str:
    return "%s %s (%s %s %s%s%s)" % (e.type, e.id, e.sku, fmt_dec(e.qty_raw), e.uom,
                                    (" from " + e.from_loc) if e.from_loc else "",
                                    (" to " + e.to_loc) if e.to_loc else "")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _closed(shift: Shift, snapshot: datetime) -> bool:
    return shift.end <= snapshot


def run(ds: Dataset, catalog: Optional[Dict[str, dict]] = None) -> Result:
    catalog = catalog or load_catalog()
    v = validate(ds)
    params = ds.params
    m: Master = v.master
    snap = v.snapshot
    tol = _P(params, "match_qty_tolerance_base", "0.005")
    window = timedelta(minutes=int(params.get("possible_match_window_minutes", "60")))
    late_low = int(params.get("late_low_max_minutes", "60"))
    skew = timedelta(minutes=int(params.get("clock_skew_tolerance_minutes", "5")))
    med_age = int(params.get("handoff_medium_aging_shifts", "2"))
    high_age = int(params.get("handoff_high_aging_shifts", "4"))
    res = Result(ds.name, v, params, catalog)
    res.input_hash = _hash_dataset(ds)
    issues: List[Issue] = list(v.issues)

    events = sorted(v.events, key=lambda e: (e.ts, e.id))
    ev_by_id = {e.id: e for e in events}
    posted = [p for p in v.postings if p.status == "POSTED"]
    failed = [p for p in v.postings if p.status == "FAILED"]
    post_by_id = {p.id: p for p in posted}
    shifts = m.shifts
    closed_shifts = [s for s in shifts if _closed(s, snap)]

    def uv(sku: str) -> Decimal:
        return m.sku[sku]["unit_value"]

    def next_cutoff(s: Shift) -> Optional[datetime]:
        for x in shifts:
            if x.start >= s.end:
                return x.end
        return None

    def aging(s: Shift) -> int:
        return sum(1 for x in closed_shifts if x.end > s.end)

    # reversal postings without source inherit the source of the posting they reverse
    def eff_source(p: NPosting) -> str:
        if p.source:
            return p.source
        if p.type == "REVERSAL" and p.related in post_by_id:
            return post_by_id[p.related].source
        return ""

    groups: Dict[str, List[NPosting]] = defaultdict(list)
    for p in posted:
        s = eff_source(p)
        if s:
            groups[s].append(p)
    failed_by_src: Dict[str, List[NPosting]] = defaultdict(list)
    for p in failed:
        if p.source:
            failed_by_src[p.source].append(p)

    claimed: Set[str] = set()
    for e in events:
        for p in groups.get(e.id, []):
            claimed.add(p.id)

    def net(e: NEvent, grp: List[NPosting]) -> List[NPosting]:
        if e.type == "reversal":
            return list(grp)
        targets = {p.related for p in grp if p.type == "REVERSAL"}
        return [p for p in grp if p.type != "REVERSAL" and p.id not in targets]

    def src(file: str, rid: str, row: int) -> Tuple[str, str, int]:
        return (file, rid, row)

    # ---------------- shift-label check (any valid event)
    for e in events:
        if e.shift_label != e.shift.shift_id:
            issues.append(Issue("R10", "SHIFT_LABEL_MISMATCH",
                                "%s is labelled shift %s but its timestamp falls in shift %s; the timestamp rules."
                                % (_cap(_ed(e)), e.shift_label, e.shift.shift_id),
                                "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                [src(F_EVENTS, e.id, e.row)], observed=e.shift_label, expected=e.shift.shift_id,
                                forced_severity="LOW", related={e.id},
                                hypothesis="Handheld shift picker left on the previous shift (hypothesis)."))

    # ---------------- pairing
    info: Dict[str, dict] = {}
    uncovered: List[NEvent] = []
    unclaimed_nosrc = [p for p in posted if not eff_source(p) and p.id not in claimed]
    for e in events:
        grp = groups.get(e.id, [])
        due = _closed(e.shift, snap)
        row = {"event": e, "due": due, "match_status": "", "kept": [], "posted_by_cutoff": False,
               "posted_by_snapshot": False, "max_post_ts": None, "posting_ids": [p.id for p in grp]}
        info[e.id] = row
        if e.status == "cancelled":
            row["match_status"] = "CANCELLED"
            for p in net(e, grp):
                issues.append(Issue("R07", "POSTING_FOR_CANCELLED_EVENT",
                                    "Posting %s (%s %s %s) exists for event %s, which was cancelled; there is no reversal."
                                    % (p.id, p.type, fmt_dec(p.qty_raw), p.uom, e.id),
                                    "PO:%s" % p.id, p.id, p.sku, p.from_loc or p.to_loc, e.shift.shift_id, p.post_ts,
                                    [src(F_POST, p.id, p.row), src(F_EVENTS, e.id, e.row)],
                                    observed="posted", expected="not posted (event cancelled)",
                                    qty_diff=p.qty, related={p.id, e.id},
                                    hypothesis="Posting made before cancellation reached the ERP (hypothesis)."))
            continue
        if e.status == "pending":
            row["match_status"] = "PENDING"
            if net(e, grp):
                issues.append(Issue("R11", "PENDING_BUT_POSTED",
                                    "%s is still pending on the floor but the ERP already holds posting(s) %s."
                                    % (_cap(_ed(e)), ", ".join(p.id for p in net(e, grp))),
                                    "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                    [src(F_EVENTS, e.id, e.row)] + [src(F_POST, p.id, p.row) for p in net(e, grp)],
                                    observed="posted", expected="not posted until completed",
                                    forced_severity="MEDIUM", related={e.id} | {p.id for p in grp},
                                    hypothesis="Posted ahead of physical completion (hypothesis)."))
            continue
        # completed
        active = net(e, grp)
        exp = EXPECTED_POSTINGS[e.type]
        bytype: Dict[str, List[NPosting]] = defaultdict(list)
        for p in sorted(active, key=lambda x: (x.post_ts, x.id)):
            bytype[p.type].append(p)
        if not active:
            if e.id in v.quarantined_post_sources and not grp:
                row["match_status"] = "COUNTERPART_QUARANTINED"
            else:
                row["match_status"] = "NOT_DUE" if not due else "UNPOSTED"
                if due:
                    uncovered.append(e)
            continue
        kept: List[NPosting] = []
        for t in exp:
            lst = bytype.get(t, [])
            if lst:
                kept.append(lst[0])
            ambiguous = any(p.type == "REVERSAL" and p.related not in post_by_id for p in grp) if e.type != "reversal" else False
            if len(lst) > 1 and not ambiguous:
                extra = lst[1:]
                issues.append(Issue("R05", "DUPLICATE_POSTING",
                                    "%s has %d active %s postings (%s); expected one. Quantities add to system stock."
                                    % (_cap(_ed(e)), len(lst), t, ", ".join(p.id for p in lst)),
                                    "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                    [src(F_EVENTS, e.id, e.row)] + [src(F_POST, p.id, p.row) for p in lst],
                                    observed="%d postings" % len(lst), expected="1 posting",
                                    qty_diff=sum((p.qty for p in extra), ZERO), related={e.id} | {p.id for p in lst},
                                    hypothesis="Re-processed queue message or manual re-post (hypothesis)."))
        for t, lst in bytype.items():
            if t not in exp:
                for p in lst:
                    issues.append(Issue("R09", "MOVEMENT_TYPE_MISMATCH",
                                        "%s was posted as %s (%s), but a %s event expects %s."
                                        % (_cap(_ed(e)), p.type, p.id, e.type, "/".join(exp)),
                                        "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                        [src(F_EVENTS, e.id, e.row), src(F_POST, p.id, p.row)],
                                        observed=p.type, expected="/".join(exp), related={e.id, p.id},
                                        hypothesis="Wrong transaction code used at posting (hypothesis)."))
        missing = [t for t in exp if not bytype.get(t)]
        row["kept"] = kept
        if missing and len(exp) > 1 and kept and due:
            issues.append(Issue("R09", "MISSING_LEG",
                                "Transfer %s has no %s posting; only %s exist, so one location is not updated."
                                % (e.id, "/".join(missing), "/".join(p.type for p in kept)),
                                "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                [src(F_EVENTS, e.id, e.row)] + [src(F_POST, p.id, p.row) for p in kept],
                                observed="/".join(p.type for p in kept), expected="/".join(exp),
                                qty_diff=e.qty if kept else None, related={e.id} | {p.id for p in grp},
                                hypothesis="Second leg failed or was not released (hypothesis)."))
        # content comparison for kept postings
        sku_bad, loc_bad, qty_bad = [], [], []
        for p in kept:
            if p.sku != e.sku:
                sku_bad.append(p)
            if _loc_mismatch(e, p):
                loc_bad.append(p)
            if abs(p.qty - e.qty) > tol:
                qty_bad.append(p)
        if sku_bad or loc_bad:
            parts = []
            if sku_bad:
                parts.append("SKU %s on posting %s vs %s on event" % (sku_bad[0].sku, sku_bad[0].id, e.sku))
            if loc_bad:
                p = loc_bad[0]
                parts.append("posting %s locations %s->%s vs event %s->%s" % (
                    p.id, p.from_loc or "-", p.to_loc or "-", e.from_loc or "-", e.to_loc or "-"))
            bad = sku_bad + [p for p in loc_bad if p not in sku_bad]
            issues.append(Issue("R09", "SKU_MISMATCH" if sku_bad else "LOCATION_MISMATCH",
                                "%s does not match its posting: %s." % (_cap(_ed(e)), "; ".join(parts)),
                                "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                [src(F_EVENTS, e.id, e.row)] + [src(F_POST, p.id, p.row) for p in bad],
                                observed="; ".join(parts), expected="posting equals event",
                                qty_diff=bad[0].qty, related={e.id} | {p.id for p in bad},
                                hypothesis="Keying/scan error at posting, e.g. transposed digits or wrong bin (hypothesis)."))
        if qty_bad:
            p = qty_bad[0]
            diff = p.qty - e.qty
            issues.append(Issue("R08", "QUANTITY_MISMATCH",
                                "%s posted as %s %s base units on %s; the event is %s base units (difference %s)."
                                % (_cap(_ed(e)), fmt_dec(p.qty), m.sku[e.sku]["base_uom"], p.id,
                                   fmt_dec(e.qty), fmt_dec(diff)),
                                "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                [src(F_EVENTS, e.id, e.row)] + [src(F_POST, q.id, q.row) for q in qty_bad],
                                observed=fmt_dec(p.qty), expected=fmt_dec(e.qty), qty_diff=diff,
                                related={e.id} | {q.id for q in qty_bad},
                                hypothesis="Partial confirmation or wrong quantity keyed (hypothesis)."))
        # timing
        complete = not missing
        if complete and kept:
            pmax = max(p.post_ts for p in kept)
            row["max_post_ts"] = pmax
            early = [p for p in kept if p.post_ts < e.ts - skew]
            if early:
                issues.append(Issue("R10", "POSTED_BEFORE_EVENT",
                                    "Posting %s was recorded %d min BEFORE %s happened (%s); an ERP posting cannot precede the movement "
                                    "beyond clock skew (%d min)."
                                    % (early[0].id, round((e.ts - early[0].post_ts).total_seconds() / 60), e.id, fmt_local(e.ts),
                                       int(skew.total_seconds() // 60)),
                                    "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                    [src(F_EVENTS, e.id, e.row)] + [src(F_POST, p.id, p.row) for p in early],
                                    observed=fmt_utc(early[0].post_ts), expected=">= " + fmt_utc(e.ts),
                                    forced_severity="LOW", related={e.id} | {p.id for p in early},
                                    hypothesis="Handheld or server clock error, or a pre-posted movement (hypothesis)."))
            row["posted_by_snapshot"] = True
            row["posted_by_cutoff"] = pmax <= e.shift.end
            if due and not row["posted_by_cutoff"]:
                lag = (pmax - e.shift.end).total_seconds() / 60.0
                nxt = next_cutoff(e.shift)
                if nxt is not None and pmax > nxt:
                    sev, tier = "HIGH", "after the next shift's cutoff too"
                elif lag > late_low:
                    sev, tier = "MEDIUM", "more than %d minutes after cutoff" % late_low
                else:
                    sev, tier = "LOW", "within %d minutes of cutoff" % late_low
                issues.append(Issue("R10", "LATE_POSTING",
                                    "%s happened %s (shift %s); the ERP posting %s arrived %s, %d min after the %s cutoff (%s). "
                                    "This is a timing exception, not evidence of loss."
                                    % (_cap(_ed(e)), fmt_local(e.ts), e.shift.shift_id,
                                       ",".join(p.id for p in kept), fmt_local(pmax), round(lag),
                                       fmt_local(e.shift.end), tier),
                                    "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                    [src(F_EVENTS, e.id, e.row)] + [src(F_POST, p.id, p.row) for p in kept],
                                    observed=fmt_utc(pmax), expected="<= " + fmt_utc(e.shift.end),
                                    forced_severity=sev, related={e.id} | {p.id for p in kept},
                                    aging_shifts=0,
                                    hypothesis="Batch posting after shift close or handoff delay (hypothesis)."))
                row["match_status"] = "MATCHED_LATE"
            else:
                row["match_status"] = "MATCHED_TIMELY" if due else "NOT_DUE"
            for p in kept:
                if p.eff_ts is not None:
                    es = m.shift_of(p.eff_ts)
                    if es is None or es.shift_id != e.shift.shift_id:
                        issues.append(Issue("R10", "EFFECTIVE_SHIFT_MISMATCH",
                                            "Posting %s claims event time %s (shift %s) but %s occurred at %s in shift %s."
                                            % (p.id, fmt_local(p.eff_ts), es.shift_id if es else "none", e.id,
                                               fmt_local(e.ts), e.shift.shift_id),
                                            "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id,
                                            e.ts, [src(F_EVENTS, e.id, e.row), src(F_POST, p.id, p.row)],
                                            observed=fmt_utc(p.eff_ts), expected=fmt_utc(e.ts),
                                            forced_severity="LOW", related={e.id, p.id},
                                            hypothesis="Backdated or forward-dated effective time at posting (hypothesis)."))
        else:
            row["match_status"] = "PARTIAL_POSTING" if due else "NOT_DUE"

    # ---------------- possible matches for uncovered events (single-posting types only)
    cand: Dict[str, List[NPosting]] = {}
    for e in uncovered:
        exp = EXPECTED_POSTINGS[e.type]
        lst = []
        if len(exp) == 1:
            for p in unclaimed_nosrc:
                if (p.type == exp[0] and p.sku == e.sku and abs(p.qty - e.qty) <= tol
                        and abs(p.post_ts - e.ts) <= window and _loc_ok(e, p)):
                    lst.append(p)
        cand[e.id] = lst
    cand_count: Dict[str, int] = defaultdict(int)
    for lst in cand.values():
        for p in lst:
            cand_count[p.id] += 1
    consumed: Dict[str, NPosting] = {}
    for e in uncovered:
        lst = cand[e.id]
        failed_ids = [p.id for p in failed_by_src.get(e.id, [])]
        if len(lst) == 1 and cand_count[lst[0].id] == 1:
            p = lst[0]
            consumed[e.id] = p
            claimed.add(p.id)
            groups[e.id].append(p)
            info[e.id]["match_status"] = "POSSIBLE_MATCH"
            info[e.id]["posting_ids"] = [p.id]
            issues.append(Issue("R06", "POSSIBLE_MATCH",
                                "%s has no posting that references it. Posting %s has no source reference but the same SKU, "
                                "quantity and location within %d min: POSSIBLE_MATCH / NEEDS_REVIEW, not a confirmed link."
                                % (_cap(_ed(e)), p.id, int(window.total_seconds() // 60)),
                                "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                [src(F_EVENTS, e.id, e.row), src(F_POST, p.id, p.row)],
                                observed="candidate posting " + p.id, expected="posting referencing " + e.id,
                                qty_diff=-e.qty, forced_severity="MEDIUM", related={e.id, p.id},
                                hypothesis="Posting keyed without the source event reference (hypothesis)."))
        else:
            ev_txt = ("; ERP posting attempt(s) %s have status FAILED" % ", ".join(failed_ids)) if failed_ids else ""
            issues.append(Issue("R06", "FAILED_POSTING" if failed_ids else "UNPOSTED",
                                "%s was completed in shift %s but no ERP posting exists at the extract snapshot%s."
                                % (_cap(_ed(e)), e.shift.shift_id, ev_txt),
                                "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                [src(F_EVENTS, e.id, e.row)] + [src(F_POST, i, 0) for i in failed_ids],
                                observed="0 postings", expected="posting by cutoff %s" % fmt_utc(e.shift.end),
                                qty_diff=-e.qty, related={e.id} | set(failed_ids),
                                hypothesis="Posting not released or failed in ERP; physical movement not yet recorded (hypothesis)."))

    # ---------------- R07 orphan postings (an orphan that has been reversed is closed)
    reversed_ids = {p.related for p in posted if p.type == "REVERSAL" and p.related}
    for p in posted:
        if p.id in claimed:
            continue
        if p.id in reversed_ids or (p.type == "REVERSAL" and p.related in post_by_id and p.related not in claimed):
            continue
        s = eff_source(p)
        if s and (s in v.quarantined[F_EVENTS]):
            continue  # counterpart quarantined: covered by the validation issue
        if s and s in ev_by_id:
            continue
        sub = "ORPHAN_REFERENCE" if s else "NO_SOURCE_REFERENCE"
        why = ("references unknown event %s" % s) if s else "has no source event reference and no unique matching event"
        issues.append(Issue("R07", sub, "Posting %s (%s %s %s %s) %s; no supporting floor event."
                            % (p.id, p.type, p.sku, fmt_dec(p.qty_raw), p.uom, why),
                            "PO:%s" % p.id, p.id, p.sku, p.from_loc or p.to_loc, "", p.post_ts,
                            [src(F_POST, p.id, p.row)], observed="posted", expected="supported by an event",
                            qty_diff=p.qty, related={p.id} | ({s} if s else set()),
                            hypothesis="Manual posting, test transaction or missing floor log (hypothesis)."))

    # ---------------- R11 handoffs
    for e in events:
        if e.handoff_to and e.handoff_to in m.shift_by_id:
            tgt = m.shift_by_id[e.handoff_to]
            if tgt.start <= e.shift.start:
                issues.append(Issue("R11", "INVALID_HANDOFF_TARGET",
                                    "%s hands off to %s, which is not later than its own shift %s."
                                    % (_cap(_ed(e)), e.handoff_to, e.shift.shift_id),
                                    "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts,
                                    [src(F_EVENTS, e.id, e.row)], observed=e.handoff_to, expected="a later shift",
                                    forced_severity="LOW", related={e.id}))
                continue
        if e.status == "pending":
            tgt = m.shift_by_id.get(e.handoff_to) if e.handoff_to else None
            if tgt is not None and _closed(tgt, snap):
                age = aging(e.shift)
                issues.append(_handoff_issue(e, "UNRESOLVED_HANDOFF_AFTER_CUTOFF", age, med_age, high_age,
                                             "%s is still pending although receiving shift %s closed at %s."
                                             % (_cap(_ed(e)), tgt.shift_id, fmt_local(tgt.end))))
            elif tgt is None and _closed(e.shift, snap):
                age = aging(e.shift)
                issues.append(_handoff_issue(e, "PENDING_NO_HANDOFF", age, med_age, high_age,
                                             "%s is still pending after its shift %s closed and no receiving shift is named."
                                             % (_cap(_ed(e)), e.shift.shift_id)))
        elif e.status == "completed" and e.handoff_status == "OPEN":
            tgt = m.shift_by_id.get(e.handoff_to) if e.handoff_to else None
            if tgt is None and _closed(e.shift, snap):
                issues.append(_handoff_issue(e, "OPEN_HANDOFF_NO_TARGET", aging(e.shift), med_age, high_age,
                                             "%s is completed but its handoff entry is still OPEN with no receiving shift."
                                             % _cap(_ed(e))))
            elif tgt is not None and _closed(tgt, snap):
                issues.append(_handoff_issue(e, "STALE_OPEN_HANDOFF", aging(e.shift), med_age, high_age,
                                             "%s is completed but its handoff to %s is still OPEN after that shift closed."
                                             % (_cap(_ed(e)), tgt.shift_id), cap="MEDIUM"))

    # ---------------- R14 reversal integrity
    rev_events = [e for e in events if e.type == "reversal"]
    seen_target: Dict[str, str] = {}
    for e in rev_events:
        problems: List[Tuple[str, str]] = []
        orig = ev_by_id.get(e.related_id)
        if orig is None:
            problems.append(("BROKEN_REFERENCE", "related_id %s does not exist among valid events" % e.related_id))
        else:
            if orig.type not in ("receipt", "pick", "adjustment"):
                problems.append(("UNSUPPORTED_TARGET", "reversal of %s events is not supported" % orig.type))
            elif orig.status != "completed":
                problems.append(("TARGET_NOT_COMPLETED", "original %s is %s" % (orig.id, orig.status)))
            if orig.sku != e.sku:
                problems.append(("SKU_MISMATCH", "reversal SKU %s differs from original %s" % (e.sku, orig.sku)))
            if abs(orig.qty - e.qty) > tol:
                problems.append(("QUANTITY_MISMATCH", "reversal quantity %s differs from original %s"
                                 % (fmt_dec(e.qty), fmt_dec(orig.qty))))
            if not (e.from_loc == orig.to_loc and e.to_loc == orig.from_loc):
                problems.append(("NOT_INVERSE", "reversal movement is not the inverse of %s" % orig.id))
            if orig.id in seen_target:
                problems.append(("DOUBLE_REVERSAL", "%s was already reversed by %s" % (orig.id, seen_target[orig.id])))
            else:
                seen_target[orig.id] = e.id
        if problems:
            rel = {e.id} | ({orig.id} if orig else set())
            srcs = [src(F_EVENTS, e.id, e.row)] + ([src(F_EVENTS, orig.id, orig.row)] if orig else [])
            issues.append(Issue("R14", problems[0][0],
                                "Reversal %s is inconsistent: %s." % (e.id, "; ".join(p[1] for p in problems)),
                                "EV:%s" % e.id, e.id, e.sku, e.from_loc or e.to_loc, e.shift.shift_id, e.ts, srcs,
                                observed="; ".join(p[0] for p in problems), expected="valid inverse of original",
                                qty_diff=(e.qty - orig.qty) if orig and abs(orig.qty - e.qty) > tol else None,
                                related=rel,
                                hypothesis="Wrong original selected or amount keyed incorrectly (hypothesis)."))
    for p in posted:
        if p.type != "REVERSAL":
            continue
        probs: List[Tuple[str, str]] = []
        rel_p = post_by_id.get(p.related) if p.related else None
        if not p.related:
            probs.append(("BROKEN_POSTING_REFERENCE", "reversal posting has no related_posting_id"))
        elif rel_p is None:
            probs.append(("BROKEN_POSTING_REFERENCE", "related_posting_id %s does not exist" % p.related))
        else:
            if rel_p.sku != p.sku:
                probs.append(("POSTING_SKU_MISMATCH", "SKU differs from reversed posting %s" % rel_p.id))
            if abs(rel_p.qty - p.qty) > tol:
                probs.append(("POSTING_QTY_MISMATCH", "reversal %s vs reversed posting %s base units"
                              % (fmt_dec(p.qty), fmt_dec(rel_p.qty))))
        if probs:
            issues.append(Issue("R14", probs[0][0],
                                "Reversal posting %s is inconsistent: %s." % (p.id, "; ".join(x[1] for x in probs)),
                                "PO:%s" % p.id, p.id, p.sku, p.from_loc or p.to_loc, "", p.post_ts,
                                [src(F_POST, p.id, p.row)] + ([src(F_POST, rel_p.id, rel_p.row)] if rel_p else []),
                                observed="; ".join(x[0] for x in probs), expected="valid reference to the reversed posting",
                                qty_diff=(p.qty - rel_p.qty) if rel_p and abs(rel_p.qty - p.qty) > tol else None,
                                related={p.id} | ({rel_p.id} if rel_p else set()) | ({p.source} if p.source else set()),
                                hypothesis="Reference keyed incorrectly (hypothesis)."))

    # ---------------- ledgers and items
    open_by_key: Dict[Tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    e_led: Dict[Tuple[str, str], List[Tuple[datetime, Decimal, str]]] = defaultdict(list)
    s_led: Dict[Tuple[str, str], List[Tuple[datetime, Decimal, str]]] = defaultdict(list)
    for ts, sku, loc, q in v.opening:
        e_led[(sku, loc)].append((ts, q, "OPENING"))
        s_led[(sku, loc)].append((ts, q, "OPENING"))
    items: List[Item] = []
    for e in events:
        grp = groups.get(e.id, [])
        e_eff = [(e.ts, e.sku, loc, d) for loc, d in e.effects()] if e.status == "completed" else []
        s_eff = [(p.post_ts, p.sku, loc, d) for p in grp for loc, d in p.effects()]
        for ts, sk, loc, d in e_eff:
            e_led[(sk, loc)].append((ts, d, e.id))
        for ts, sk, loc, d in s_eff:
            s_led[(sk, loc)].append((ts, d, [p.id for p in grp if p.post_ts == ts and p.sku == sk][0]))
        items.append(Item("EV", e.id, e_eff, s_eff, {e.id} | {p.id for p in grp}))
    for p in posted:
        if p.id in claimed:
            continue
        eff = [(p.post_ts, p.sku, loc, d) for loc, d in p.effects()]
        for ts, sk, loc, d in eff:
            s_led[(sk, loc)].append((ts, d, p.id))
        keys = {p.id}
        if p.source:
            keys.add(p.source)
        items.append(Item("PO", p.id, [], eff, keys))
    for k in e_led:
        e_led[k].sort(key=lambda x: (x[0], x[1] < 0, x[2]))
    for k in s_led:
        s_led[k].sort(key=lambda x: (x[0], x[1] < 0, x[2]))
    res.e_ledger, res.s_ledger, res.items = dict(e_led), dict(s_led), items

    related_idx: Dict[str, List[Issue]] = defaultdict(list)
    for i in issues:
        for k in i.related:
            related_idx[k].append(i)

    def linked_issues(it: Item) -> List[Issue]:
        seen, out = set(), []
        for k in sorted(it.keys):
            for i in related_idx.get(k, []):
                if id(i) not in seen:
                    seen.add(id(i))
                    out.append(i)
        return out

    items_by_sku: Dict[str, List[Item]] = defaultdict(list)
    for it in items:
        for sk in it.skus:
            items_by_sku[sk].append(it)

    # ---------------- R13 negative balances
    for (sku, loc), led in sorted(res.e_ledger.items()):
        run_bal = ZERO
        for ts, d, rid in led:
            run_bal += d
            if run_bal < 0:
                e = ev_by_id.get(rid)
                issues.append(Issue("R13", "IMPOSSIBLE_BALANCE",
                                    "Event ledger for %s at %s goes negative (%s) after %s at %s: more stock was issued than the "
                                    "event log says existed." % (sku, loc, fmt_dec(run_bal), rid, fmt_local(ts)),
                                    "SL:%s|%s" % (sku, loc), "%s|%s" % (sku, loc), sku, loc,
                                    e.shift.shift_id if e else "", ts,
                                    [src(F_EVENTS, rid, e.row)] if e else [], observed=fmt_dec(run_bal),
                                    expected=">= 0", qty_diff=run_bal, forced_severity="HIGH",
                                    related={rid, "%s|%s" % (sku, loc)},
                                    hypothesis="Missing receipt/transfer in the event log or wrong source bin (hypothesis)."))
                break
    flagged_neg = {i.key_id for i in issues if i.rule_id == "R13"}
    for (sku, loc), led in sorted(res.s_ledger.items()):
        if "%s|%s" % (sku, loc) in flagged_neg:
            continue
        for sh in closed_shifts:
            bal = res.bal_system(sku, loc, sh.end)
            if bal < 0:
                gap_items = [it for it in items_by_sku.get(sku, []) if abs(_gap(it, sku, loc, sh.end)) > tol]
                lk = []
                for it in gap_items:
                    lk.extend(linked_issues(it))
                lk = [i for i in lk if i.rule_id not in ("R12", "R13")]
                iss = Issue("R13", "SYSTEM_NEGATIVE_AT_CUTOFF",
                            "System balance for %s at %s is %s at the %s cutoff (%s) while the event ledger is not negative."
                            % (sku, loc, fmt_dec(bal), sh.shift_id, fmt_local(sh.end)),
                            "SL:%s|%s" % (sku, loc), "%s|%s" % (sku, loc), sku, loc, sh.shift_id, sh.end, [],
                            observed=fmt_dec(bal), expected=">= 0", qty_diff=bal, forced_severity="LOW",
                            related={"%s|%s" % (sku, loc)} | {x for i in lk for x in i.related},
                            hypothesis="A posting that raises this bin is missing or late (hypothesis).")
                if lk:
                    iss.case_key = lk[0].case_key
                    iss.extra["linked_primary"] = "yes"
                    iss.extra["secondary_of"] = lk[0].case_key
                issues.append(iss)
                break

    # ---------------- R12 counts
    count_rows: List[dict] = []
    elig_cls = ("AGREES", "EXPLAINED_BY_OPEN_ISSUES", "IN_FLIGHT_POSTING_LAG", "EXPLAINED_BY_ISSUES_AND_POSTING_LAG",
                "UNEXPLAINED_COUNT_VARIANCE", "SYSTEM_GAP_UNLINKED")
    for c in sorted(v.counts, key=lambda x: (x.observed, x.id)):
        row = {"count": c, "class": "", "S": None, "E": None, "linked": [], "tol": m.sku[c.sku]["tol"],
               "issue_id": ""}
        count_rows.append(row)
        if c.status == "VOID":
            row["class"] = "VOID_EXCLUDED"
            continue
        S = res.bal_system(c.sku, c.loc, c.observed)
        E = res.bal_event(c.sku, c.loc, c.observed)
        row["S"], row["E"] = S, E
        t = row["tol"]
        C = c.qty
        if abs(C - S) <= t:
            row["class"] = "AGREES"
            continue
        if c.status == "PARTIAL" and C <= S + t and C <= E + t:
            row["class"] = "PARTIAL_NOT_COMPARABLE"
            continue
        if abs(C - E) <= t:
            linked: List[Issue] = []
            inflight = unlinked_other = False
            for it in items_by_sku.get(c.sku, []):
                g = _gap(it, c.sku, c.loc, c.observed)
                if abs(g) <= tol:      # differences inside the matching tolerance are treated as equal
                    continue
                li = [i for i in linked_issues(it) if i.rule_id not in ("R12", "R13")]
                if li:
                    linked.extend(li)
                elif it.kind == "EV":
                    inflight = True
                else:
                    unlinked_other = True
            uniq = {}
            for i in linked:
                uniq[id(i)] = i
            linked = list(uniq.values())
            row["linked"] = linked
            if unlinked_other or (not linked and not inflight):
                row["class"] = "SYSTEM_GAP_UNLINKED"
            elif linked and inflight:
                row["class"] = "EXPLAINED_BY_ISSUES_AND_POSTING_LAG"
            elif linked:
                row["class"] = "EXPLAINED_BY_OPEN_ISSUES"
            else:
                row["class"] = "IN_FLIGHT_POSTING_LAG"
            if row["class"] != "SYSTEM_GAP_UNLINKED":
                continue
        else:
            row["class"] = "UNEXPLAINED_COUNT_VARIANCE"
        # exception
        sub = row["class"]
        diff = C - E
        issues.append(Issue("R12", sub,
                            "Count %s of %s at %s on %s: counted %s, event ledger %s, system %s (count - ledger = %s). "
                            "Not explained by open posting issues."
                            % (c.id, c.sku, c.loc, fmt_local(c.observed), fmt_dec(C), fmt_dec(E), fmt_dec(S), fmt_dec(diff)),
                            "CC:%s" % c.id, c.id, c.sku, c.loc, (m.shift_of(c.observed).shift_id if m.shift_of(c.observed) else ""),
                            c.observed, [src(F_COUNT, c.id, c.row)], observed=fmt_dec(C), expected=fmt_dec(E),
                            qty_diff=diff, related={c.id, "%s|%s" % (c.sku, c.loc)},
                            evidence="system=%s; event_ledger=%s; counted=%s; status=%s; evidence_ref=%s"
                                     % (fmt_dec(S), fmt_dec(E), fmt_dec(C), c.status, c.evidence_ref),
                            hypothesis="Unrecorded movement, miscount or loss; recount before any adjustment (hypothesis)."))
        ctx = sorted({i.exception_id for i in issues if i is not issues[-1] and i.sku_id == c.sku and
                      (i.location_id == c.loc) and i.rule_id not in ("R12", "R13")})
        if ctx:
            issues[-1].evidence += "; other open issues on this SKU-location: " + ",".join(ctx)
        row["issue_obj"] = issues[-1]

    # ---------------- finalize
    _finalize(res, issues, count_rows, info, ev_by_id, uv, params)
    return res


def _hash_dataset(ds: Dataset) -> str:
    import hashlib
    h = hashlib.sha256()
    for fname in sorted(ds.tables):
        h.update(fname.encode())
        for r in ds.tables[fname]:
            h.update(repr(sorted((k, v) for k, v in r.items() if k != "_row")).encode())
    return h.hexdigest()


def _gap(it: Item, sku: str, loc: str, t: datetime) -> Decimal:
    e = sum((d for ts, k, l, d in it.e_eff if k == sku and l == loc and ts <= t), ZERO)
    s = sum((d for ts, k, l, d in it.s_eff if k == sku and l == loc and ts <= t), ZERO)
    return s - e


def _loc_ok(e: NEvent, p: NPosting) -> bool:
    if p.type == "RECEIPT":
        return p.to_loc == e.to_loc
    if p.type == "ISSUE":
        return p.from_loc == e.from_loc
    return p.from_loc == e.from_loc and p.to_loc == e.to_loc


def _loc_mismatch(e: NEvent, p: NPosting) -> bool:
    return not _loc_ok(e, p)


def _handoff_issue(e: NEvent, sub: str, age: int, med: int, high: int, reason: str, cap: str = "") -> Issue:
    sev = "HIGH" if age >= high else ("MEDIUM" if age >= med else "LOW")
    if cap == "MEDIUM" and sev == "HIGH":
        sev = "MEDIUM"
    return Issue("R11", sub, reason + " Aging: %d closed shift(s) since the event." % age, "EV:%s" % e.id, e.id, e.sku,
                 e.from_loc or e.to_loc, e.shift.shift_id, e.ts, [(F_EVENTS, e.id, e.row)],
                 observed="%s / %s" % (e.status, e.handoff_status or "no handoff status"),
                 expected="resolved by receiving shift cutoff", forced_severity=sev, aging_shifts=age,
                 related={e.id}, hypothesis="Handoff not acknowledged or verbal only (hypothesis).")


def _finalize(res: Result, issues: List[Issue], count_rows, info, ev_by_id, uv, params) -> None:
    cat = res.catalog
    hi = _P(params, "value_high_cad", "500")
    me = _P(params, "value_medium_cad", "100")
    # stable ordering and IDs
    issues.sort(key=lambda i: (i.rule_id, i.key_id, i.subtype))
    seen: Dict[str, int] = defaultdict(int)
    for i in issues:
        base = "EX-%s-%s" % (i.rule_id, i.key_id.replace("|", "~"))
        seen[base] += 1
        i.exception_id = base if seen[base] == 1 else "%s-%d" % (base, seen[base])
        c = cat[i.rule_id]
        i.owner_role, i.review_action = c["owner_role"], c["proposed_review_action"]
        if i.qty_diff is not None and i.sku_id:
            i.value_cad_sim = (abs(i.qty_diff) * uv(i.sku_id)).quantize(Decimal("0.01")) if i.rule_id != "R10" else ZERO
        if i.forced_severity:
            i.severity = i.forced_severity
        else:
            sev = c["base_severity"]
            if i.rule_id in ("R05", "R06", "R07", "R08", "R12"):
                if i.value_cad_sim >= hi:
                    sev = "HIGH"
                elif i.value_cad_sim >= me and sev == "LOW":
                    sev = "MEDIUM"
            if i.rule_id == "R04" and i.subtype == "CONFLICTING_CONTENT":
                sev = "HIGH"
            i.severity = sev
        i.status = "OPEN"
    # primary selection per case
    prec = {k: int(v["precedence"]) for k, v in cat.items()}
    by_case: Dict[str, List[Issue]] = defaultdict(list)
    for i in issues:
        by_case[i.case_key].append(i)
    for ck, lst in by_case.items():
        lst.sort(key=lambda x: (prec[x.rule_id], x.exception_id))
        for j, i in enumerate(lst):
            i.is_primary = j == 0
            i.extra["case_size"] = str(len(lst))
    # priority
    for i in issues:
        i.priority_score = sev_points(i.severity) * 1000 + min(int(i.value_cad_sim), 499) + min(i.aging_shifts, 9) * 10
    prim = sorted([i for i in issues if i.is_primary], key=lambda x: (-x.priority_score, x.exception_id))
    for n, i in enumerate(prim, start=1):
        i.priority_rank = n
    res.issues = issues
    for r in count_rows:
        if "issue_obj" in r:
            r["issue_id"] = r["issue_obj"].exception_id
    res.count_table = count_rows
    # event & shift tables
    res.event_table = [info[e.id] for e in sorted(ev_by_id.values(), key=lambda x: (x.ts, x.id))]
    res.run_id = "RUN-" + res.input_hash[:10].upper()

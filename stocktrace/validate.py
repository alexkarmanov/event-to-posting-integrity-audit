"""Validation gate: R04 (duplicate IDs), R01 (fields), R02 (references), R03 (UoM).

Invalid records are quarantined: they raise one issue and take no part in matching or balances.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

from .common import ZERO, fmt_dec, parse_qty, parse_ts, q3
from .loader import Dataset, SchemaError
from .model import Issue, NCount, NEvent, NPosting, Shift
from .schema import (COUNT_STATUSES, EVENT_STATUSES, EVENT_TYPES, HANDOFF_STATUSES, POSTING_STATUSES,
                     POSTING_TYPES)

F_EVENTS = "business_events.csv"
F_POST = "system_postings.csv"
F_COUNT = "physical_counts.csv"
F_OPEN = "opening_balances.csv"


@dataclass
class Master:
    sku: Dict[str, dict]
    loc: Dict[str, dict]
    conv: Dict[Tuple[str, str], Decimal]
    known_uoms: Set[str]
    shifts: List[Shift]
    shift_by_id: Dict[str, Shift]

    def shift_of(self, ts: datetime) -> Optional[Shift]:
        for s in self.shifts:
            if s.start <= ts < s.end:
                return s
        return None

    def to_base(self, sku: str, uom: str, qty: Decimal) -> Optional[Decimal]:
        s = self.sku[sku]
        if uom == s["base_uom"]:
            return q3(qty)
        f = self.conv.get((sku, uom))
        return None if f is None else q3(qty * f)

    def sku_valid_at(self, sku: str, ts: datetime) -> bool:
        s = self.sku.get(sku)
        return bool(s) and (s["from"] is None or ts >= s["from"]) and (s["to"] is None or ts < s["to"])

    def loc_valid_at(self, loc: str, ts: datetime) -> bool:
        s = self.loc.get(loc)
        return bool(s) and (s["from"] is None or ts >= s["from"]) and (s["to"] is None or ts < s["to"])


def _opt_ts(v: str) -> Optional[datetime]:
    return parse_ts(v) if v.strip() else None


def build_master(ds: Dataset) -> Master:
    try:
        return _build_master(ds)
    except (ValueError, ArithmeticError) as exc:
        raise SchemaError("master/configuration data is invalid (%s); fix the master file and rerun" % exc)


def _build_master(ds: Dataset) -> Master:
    sku = {}
    for r in ds.rows("sku_master.csv"):
        sku[r["sku_id"]] = dict(r, unit_value=Decimal(r["unit_value_cad_sim"] or "0"),
                                tol=Decimal(r["count_tolerance_base"] or "0"),
                                **{"from": _opt_ts(r["effective_from"]), "to": _opt_ts(r["effective_to"])})
    loc = {}
    for r in ds.rows("location_master.csv"):
        loc[r["location_id"]] = dict(r, **{"from": _opt_ts(r["effective_from"]), "to": _opt_ts(r["effective_to"])})
    conv = {(r["sku_id"], r["uom"]): Decimal(r["to_base_factor"]) for r in ds.rows("uom_conversions.csv")}
    known = {r["uom"] for r in ds.rows("uom_conversions.csv")} | {s["base_uom"] for s in sku.values()}
    shifts = [Shift(r["shift_id"], r["shift_name"], parse_ts(r["start_ts"]), parse_ts(r["end_ts"]))
              for r in ds.rows("shifts.csv")]
    shifts.sort(key=lambda s: s.start)
    return Master(sku, loc, conv, known, shifts, {s.shift_id: s for s in shifts})


@dataclass
class Validated:
    master: Master
    snapshot: datetime
    events: List[NEvent] = field(default_factory=list)
    postings: List[NPosting] = field(default_factory=list)
    counts: List[NCount] = field(default_factory=list)
    opening: List[Tuple[datetime, str, str, Decimal]] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)
    quarantined: Dict[str, Set[str]] = field(default_factory=lambda: {F_EVENTS: set(), F_POST: set(), F_COUNT: set()})
    quarantined_post_sources: Set[str] = field(default_factory=set)
    raw_ids: Dict[str, Set[str]] = field(default_factory=lambda: {F_EVENTS: set(), F_POST: set(), F_COUNT: set()})
    ignored_dups: Set[Tuple[str, int]] = field(default_factory=set)
    funnel: Dict[str, Dict[str, int]] = field(default_factory=dict)


def _rid(fname: str, row: dict, idcol: str) -> str:
    v = row.get(idcol, "").strip()
    return v if v else "%s#row%d" % (fname.replace(".csv", ""), row["_row"])


def _dups(rows: List[dict], idcol: str, fname: str, v: Validated, rel_col: str = "") -> Set[int]:
    """R04. Returns the set of row numbers to drop (identical repeats and conflicting groups)."""
    groups: Dict[str, List[dict]] = OrderedDict()
    for r in rows:
        k = r[idcol].strip()
        if k:
            groups.setdefault(k, []).append(r)
    drop: Set[int] = set()
    for k, grp in groups.items():
        if len(grp) < 2:
            continue
        sig = {tuple((c, r[c]) for c in sorted(r) if c != "_row") for r in grp}
        sources = [(fname, k, r["_row"]) for r in grp]
        rel = {k} | ({r[rel_col].strip() for r in grp if r[rel_col].strip()} if rel_col else set())
        if len(sig) == 1:
            for r in grp[1:]:
                drop.add(r["_row"])
                v.ignored_dups.add((fname, r["_row"]))
            v.issues.append(Issue("R04", "IDENTICAL_REDELIVERY",
                                  "ID %s appears %d times with identical content; first row kept, repeats ignored."
                                  % (k, len(grp)), "ID:%s" % k, k, sources=sources, related=rel,
                                  observed="%d rows" % len(grp), expected="1 row",
                                  hypothesis="Re-delivered or re-imported row (hypothesis)."))
        else:
            for r in grp:
                drop.add(r["_row"])
                if rel_col and r[rel_col].strip():
                    v.quarantined_post_sources.add(r[rel_col].strip())
            v.quarantined[fname].add(k)
            diff_cols = [c for c in sorted(grp[0]) if c != "_row" and len({r[c] for r in grp}) > 1]
            detail = "; ".join("%s: %s" % (c, " | ".join(r[c] or "(blank)" for r in grp)) for c in diff_cols)
            v.issues.append(Issue("R04", "CONFLICTING_CONTENT",
                                  "ID %s appears %d times with different content (%s); all rows quarantined." % (k, len(grp), detail),
                                  "ID:%s" % k, k, sources=sources, related=rel,
                                  observed="%d conflicting rows" % len(grp), expected="1 row",
                                  hypothesis="Two different records share one immutable ID (hypothesis)."))
    return drop


def _req_blank(row: dict, cols: List[str]) -> Optional[str]:
    for c in cols:
        if row[c].strip() == "":
            return c
    return None


def _r01(rule: str, sub: str, why: str):
    return (rule, sub, why)


def validate(ds: Dataset) -> Validated:
    m = build_master(ds)
    p = ds.params
    snapshot = parse_ts(p["snapshot_ts"])
    v = Validated(m, snapshot)

    # ---- opening balances
    for r in ds.rows(F_OPEN):
        rid = "opening#row%d" % r["_row"]
        try:
            ts = parse_ts(r["timestamp"])
            q = parse_qty(r["quantity_in_base_uom"])
            if q < 0:
                raise ValueError("negative quantity")
            if r["sku_id"] not in m.sku or r["location_id"] not in m.loc:
                raise KeyError("unknown ref")
            v.opening.append((ts, r["sku_id"], r["location_id"], q))
        except (ValueError, KeyError) as exc:
            v.issues.append(Issue("R01", "INVALID_OPENING", "Opening balance row rejected: %s" % exc,
                                  "OP:%s" % rid, rid, sku_id=r["sku_id"], location_id=r["location_id"],
                                  sources=[(F_OPEN, rid, r["_row"])], related={rid}))

    # ---- events
    ev_rows = ds.rows(F_EVENTS)
    drop = _dups(ev_rows, "event_id", F_EVENTS, v)
    funnel = {"raw_rows": len(ev_rows)}
    n_valid = 0
    for r in ev_rows:
        rid = _rid(F_EVENTS, r, "event_id")
        v.raw_ids[F_EVENTS].add(rid)
        if r["_row"] in drop:
            continue
        err = _check_event(r, m, v.snapshot)
        if err:
            rule, sub, why = err
            v.quarantined[F_EVENTS].add(rid)
            v.issues.append(Issue(rule, sub, "Event %s quarantined: %s" % (rid, why), "EV:%s" % rid, rid,
                                  sku_id=r["sku_id"], location_id=(r["from_location"] or r["to_location"]),
                                  shift_id=r["shift_id"], sources=[(F_EVENTS, rid, r["_row"])],
                                  related={rid}, observed=why, expected="valid record"))
            continue
        ts = parse_ts(r["event_timestamp"])
        qraw = parse_qty(r["quantity"])
        ev = NEvent(rid, r["_row"], ts, r["event_type"], r["sku_id"], m.to_base(r["sku_id"], r["uom"], qraw),
                    qraw, r["uom"], r["from_location"].strip(), r["to_location"].strip(), r["status"],
                    r["related_id"].strip(), r["handoff_to_shift"].strip(), r["handoff_status"].strip(),
                    r["shift_id"].strip(), m.shift_of(ts))
        if qraw == 0:
            v.issues.append(Issue("R01", "ZERO_QTY", "Event %s has zero quantity (accepted, no balance effect)." % rid,
                                  "EV:%s" % rid, rid, sku_id=ev.sku, location_id=ev.from_loc or ev.to_loc,
                                  shift_id=ev.shift.shift_id, ref_ts=ts, sources=[(F_EVENTS, rid, r["_row"])],
                                  related={rid}, observed="0", expected=">0", forced_severity="LOW",
                                  hypothesis="Placeholder or cancelled scan (hypothesis)."))
        v.events.append(ev)
        n_valid += 1
    funnel["valid"] = n_valid
    funnel["identical_repeats_ignored"] = sum(1 for (f_, _r) in v.ignored_dups if f_ == F_EVENTS)
    funnel["quarantined"] = len(ev_rows) - n_valid - funnel["identical_repeats_ignored"]   # failed checks + conflicting duplicate rows
    v.funnel[F_EVENTS] = funnel

    # ---- postings
    po_rows = ds.rows(F_POST)
    drop = _dups(po_rows, "posting_id", F_POST, v, "source_event_id")
    funnel = {"raw_rows": len(po_rows)}
    n_valid = 0
    for r in po_rows:
        rid = _rid(F_POST, r, "posting_id")
        v.raw_ids[F_POST].add(rid)
        if r["_row"] in drop:
            continue
        err = _check_posting(r, m, v.snapshot)
        if err:
            rule, sub, why = err
            v.quarantined[F_POST].add(rid)
            if r["source_event_id"].strip():
                v.quarantined_post_sources.add(r["source_event_id"].strip())
            rel = {rid}
            if r["source_event_id"].strip():
                rel.add(r["source_event_id"].strip())
            v.issues.append(Issue(rule, sub, "Posting %s quarantined: %s" % (rid, why), "PO:%s" % rid, rid,
                                  sku_id=r["sku_id"], location_id=(r["from_location"] or r["to_location"]),
                                  sources=[(F_POST, rid, r["_row"])], related=rel, observed=why,
                                  expected="valid record"))
            continue
        qraw = parse_qty(r["quantity"])
        po = NPosting(rid, r["_row"], r["source_event_id"].strip(), parse_ts(r["system_post_timestamp"]),
                      _opt_ts(r["event_effective_timestamp"]), r["posting_type"], r["sku_id"],
                      m.to_base(r["sku_id"], r["uom"], qraw), qraw, r["uom"], r["from_location"].strip(),
                      r["to_location"].strip(), r["posting_status"], r["related_posting_id"].strip())
        v.postings.append(po)
        n_valid += 1
    funnel["valid"] = n_valid
    funnel["identical_repeats_ignored"] = sum(1 for (f_, _r) in v.ignored_dups if f_ == F_POST)
    funnel["quarantined"] = len(po_rows) - n_valid - funnel["identical_repeats_ignored"]   # failed checks + conflicting duplicate rows
    v.funnel[F_POST] = funnel

    # ---- counts
    c_rows = ds.rows(F_COUNT)
    drop = _dups(c_rows, "count_id", F_COUNT, v)
    funnel = {"raw_rows": len(c_rows)}
    n_valid = 0
    for r in c_rows:
        rid = _rid(F_COUNT, r, "count_id")
        v.raw_ids[F_COUNT].add(rid)
        if r["_row"] in drop:
            continue
        err = _check_count(r, m, v.snapshot)
        if err:
            rule, sub, why = err
            v.quarantined[F_COUNT].add(rid)
            v.issues.append(Issue(rule, sub, "Count %s rejected: %s" % (rid, why), "CC:%s" % rid, rid,
                                  sku_id=r["sku_id"], location_id=r["location_id"],
                                  sources=[(F_COUNT, rid, r["_row"])], related={rid}, observed=why,
                                  expected="valid record"))
            continue
        qraw = parse_qty(r["counted_qty"]) if r["count_status"] != "VOID" or r["counted_qty"].strip() else ZERO
        v.counts.append(NCount(rid, r["_row"], parse_ts(r["observed_at"]), r["sku_id"], r["location_id"],
                               m.to_base(r["sku_id"], r["uom"], qraw) if r["uom"].strip() else q3(qraw), qraw,
                               r["uom"], r["count_status"], r["evidence_ref"]))
        n_valid += 1
    funnel["valid"] = n_valid
    funnel["identical_repeats_ignored"] = sum(1 for (f_, _r) in v.ignored_dups if f_ == F_COUNT)
    funnel["quarantined"] = len(c_rows) - n_valid - funnel["identical_repeats_ignored"]   # failed checks + conflicting duplicate rows
    v.funnel[F_COUNT] = funnel
    return v


# ---------------------------------------------------------------- row checks
def _check_ts(val: str, snapshot: datetime, label: str):
    try:
        ts = parse_ts(val)
    except ValueError as exc:
        sub = "MISSING_FIELD" if "missing" in str(exc) else "INVALID_TIMESTAMP"
        return None, ("R01", sub, "%s: %s" % (label, exc))
    if ts > snapshot:
        return None, ("R01", "FUTURE_TIMESTAMP", "%s is after the extract snapshot" % label)
    return ts, None


def _loc_rules(typ: str, kind: str, row: dict):
    f, t = row["from_location"].strip(), row["to_location"].strip()
    need = {"receipt": (False, True), "RECEIPT": (False, True), "pick": (True, False), "ISSUE": (True, False),
            "transfer": (True, True), "TRANSFER_OUT": (True, True), "TRANSFER_IN": (True, True)}
    if typ in need:
        nf, nt = need[typ]
        if nf and not f:
            return ("R01", "MISSING_FIELD", "from_location required for %s" % typ)
        if nt and not t:
            return ("R01", "MISSING_FIELD", "to_location required for %s" % typ)
        if typ in ("transfer", "TRANSFER_OUT", "TRANSFER_IN") and f == t:
            return ("R01", "INVALID_VALUE", "from_location equals to_location")
        if typ in ("receipt", "RECEIPT") and f:
            return ("R01", "INVALID_VALUE", "receipt must not have from_location")
        if typ in ("pick", "ISSUE") and t:
            return ("R01", "INVALID_VALUE", "pick/issue must not have to_location")
    else:  # adjustment / reversal: exactly one side
        if bool(f) == bool(t):
            return ("R01", "MISSING_FIELD", "%s needs exactly one of from_location/to_location" % typ)
    return None


def _check_event(r: dict, m: Master, snapshot: datetime):
    miss = _req_blank(r, ["event_id", "shift_id", "event_type", "sku_id", "uom", "status"])
    if miss:
        return _r01("R01", "MISSING_FIELD", "%s is blank" % miss)
    ts, e = _check_ts(r["event_timestamp"], snapshot, "event_timestamp")
    if e:
        return e
    if r["event_type"] not in EVENT_TYPES:
        return _r01("R01", "INVALID_VALUE", "event_type '%s' not allowed" % r["event_type"])
    if r["status"] not in EVENT_STATUSES:
        return _r01("R01", "INVALID_VALUE", "status '%s' not allowed" % r["status"])
    if r["handoff_status"].strip() not in HANDOFF_STATUSES:
        return _r01("R01", "INVALID_VALUE", "handoff_status '%s' not allowed" % r["handoff_status"])
    try:
        q = parse_qty(r["quantity"])
    except ValueError as exc:
        return _r01("R01", "MISSING_FIELD" if "missing" in str(exc) else "INVALID_QUANTITY", "quantity: %s" % exc)
    if q < 0:
        return _r01("R01", "NEGATIVE_QUANTITY", "negative quantity; direction is encoded by event type/locations")
    le = _loc_rules(r["event_type"], "event", r)
    if le:
        return le
    if r["event_type"] == "reversal" and not r["related_id"].strip():
        return _r01("R01", "MISSING_FIELD", "related_id required for reversal")
    if m.shift_of(ts) is None:
        return _r01("R01", "OUTSIDE_SHIFT_CALENDAR", "timestamp is outside the shift calendar")
    # R02
    if r["sku_id"] not in m.sku:
        return ("R02", "UNKNOWN_SKU", "sku_id '%s' not in sku_master" % r["sku_id"])
    if not m.sku_valid_at(r["sku_id"], ts):
        return ("R02", "SKU_NOT_EFFECTIVE", "sku_id '%s' not effective at event time" % r["sku_id"])
    for col in ("from_location", "to_location"):
        lv = r[col].strip()
        if lv:
            if lv not in m.loc:
                return ("R02", "UNKNOWN_LOCATION", "%s '%s' not in location_master" % (col, lv))
            if not m.loc_valid_at(lv, ts):
                return ("R02", "LOCATION_NOT_EFFECTIVE", "%s '%s' inactive/not effective at event time" % (col, lv))
    if r["shift_id"].strip() not in m.shift_by_id:
        return ("R02", "UNKNOWN_SHIFT", "shift_id '%s' not in shifts" % r["shift_id"])
    if r["handoff_to_shift"].strip() and r["handoff_to_shift"].strip() not in m.shift_by_id:
        return ("R02", "UNKNOWN_SHIFT", "handoff_to_shift '%s' not in shifts" % r["handoff_to_shift"])
    # R03
    if m.to_base(r["sku_id"], r["uom"], q) is None:
        sub = "NO_CONVERSION_FOR_SKU" if r["uom"] in m.known_uoms else "UNKNOWN_UOM_CODE"
        return ("R03", sub, "uom '%s' has no explicit conversion for %s" % (r["uom"], r["sku_id"]))
    return None


def _check_posting(r: dict, m: Master, snapshot: datetime):
    miss = _req_blank(r, ["posting_id", "posting_type", "sku_id", "uom", "posting_status"])
    if miss:
        return _r01("R01", "MISSING_FIELD", "%s is blank" % miss)
    ts, e = _check_ts(r["system_post_timestamp"], snapshot, "system_post_timestamp")
    if e:
        return e
    eff = None
    if r["event_effective_timestamp"].strip():
        eff, e = _check_ts(r["event_effective_timestamp"], snapshot, "event_effective_timestamp")
        if e:
            return e
    if r["posting_type"] not in POSTING_TYPES:
        return _r01("R01", "INVALID_VALUE", "posting_type '%s' not allowed" % r["posting_type"])
    if r["posting_status"] not in POSTING_STATUSES:
        return _r01("R01", "INVALID_VALUE", "posting_status '%s' not allowed" % r["posting_status"])
    try:
        q = parse_qty(r["quantity"])
    except ValueError as exc:
        return _r01("R01", "MISSING_FIELD" if "missing" in str(exc) else "INVALID_QUANTITY", "quantity: %s" % exc)
    if q < 0:
        return _r01("R01", "NEGATIVE_QUANTITY", "negative quantity; direction is encoded by posting_type")
    le = _loc_rules(r["posting_type"], "posting", r)
    if le:
        return le
    at = eff or ts
    if r["sku_id"] not in m.sku:
        return ("R02", "UNKNOWN_SKU", "sku_id '%s' not in sku_master" % r["sku_id"])
    if not m.sku_valid_at(r["sku_id"], at):
        return ("R02", "SKU_NOT_EFFECTIVE", "sku_id '%s' not effective at posting effective time" % r["sku_id"])
    for col in ("from_location", "to_location"):
        lv = r[col].strip()
        if lv:
            if lv not in m.loc:
                return ("R02", "UNKNOWN_LOCATION", "%s '%s' not in location_master" % (col, lv))
            if not m.loc_valid_at(lv, at):
                return ("R02", "LOCATION_NOT_EFFECTIVE", "%s '%s' inactive/not effective at posting time" % (col, lv))
    if m.to_base(r["sku_id"], r["uom"], q) is None:
        sub = "NO_CONVERSION_FOR_SKU" if r["uom"] in m.known_uoms else "UNKNOWN_UOM_CODE"
        return ("R03", sub, "uom '%s' has no explicit conversion for %s" % (r["uom"], r["sku_id"]))
    return None


def _check_count(r: dict, m: Master, snapshot: datetime):
    miss = _req_blank(r, ["count_id", "sku_id", "location_id", "count_status"])
    if miss:
        return _r01("R01", "MISSING_FIELD", "%s is blank" % miss)
    ts, e = _check_ts(r["observed_at"], snapshot, "observed_at")
    if e:
        return e
    if r["count_status"] not in COUNT_STATUSES:
        return _r01("R01", "INVALID_VALUE", "count_status '%s' not allowed" % r["count_status"])
    if r["count_status"] == "VOID" and r["counted_qty"].strip() == "":
        return None if r["sku_id"] in m.sku and r["location_id"] in m.loc else ("R02", "UNKNOWN_SKU", "unknown reference on void count")
    if r["uom"].strip() == "":
        return _r01("R01", "MISSING_FIELD", "uom is blank")
    try:
        q = parse_qty(r["counted_qty"])
    except ValueError as exc:
        return _r01("R01", "MISSING_FIELD" if "missing" in str(exc) else "INVALID_QUANTITY", "counted_qty: %s" % exc)
    if q < 0:
        return _r01("R01", "NEGATIVE_QUANTITY", "negative counted_qty")
    if r["sku_id"] not in m.sku:
        return ("R02", "UNKNOWN_SKU", "sku_id '%s' not in sku_master" % r["sku_id"])
    if not m.sku_valid_at(r["sku_id"], ts):
        return ("R02", "SKU_NOT_EFFECTIVE", "sku_id '%s' not effective at count time" % r["sku_id"])
    if r["location_id"] not in m.loc:
        return ("R02", "UNKNOWN_LOCATION", "location_id '%s' not in location_master" % r["location_id"])
    if not m.loc_valid_at(r["location_id"], ts):
        return ("R02", "LOCATION_NOT_EFFECTIVE", "location_id '%s' inactive at count time" % r["location_id"])
    if m.to_base(r["sku_id"], r["uom"], q) is None:
        sub = "NO_CONVERSION_FOR_SKU" if r["uom"] in m.known_uoms else "UNKNOWN_UOM_CODE"
        return ("R03", sub, "uom '%s' has no explicit conversion for %s" % (r["uom"], r["sku_id"]))
    return None

"""Dry-run correction simulation.

Builds a correction plan from the detected exceptions, applies *hypothetically approved* actions to an in-memory COPY of
the raw tables, reruns the engine on the copy and reports before/after. Raw inputs are never modified.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from .common import fmt_dec, fmt_utc
from .engine import Result, run
from .loader import Dataset
from .model import Issue
from .schema import EXPECTED_POSTINGS

APPROVER = "Inventory Control Manager (HYPOTHETICAL approval for simulation only)"
F_E, F_P, F_C = "business_events.csv", "system_postings.csv", "physical_counts.csv"


@dataclass
class Action:
    exception_id: str
    rule_id: str
    subtype: str
    action: str
    description: str
    changes: List[dict] = field(default_factory=list)   # {"op": add_posting|drop_row|set_field, ...}
    data_change: bool = True


@dataclass
class SimResult:
    actions: List[Action]
    before: Result
    after: Result
    resolved: List[str]
    acknowledged: List[str]
    remaining: List[str]
    side_effects: List[str]          # expected timing side effects of corrective postings
    unexpected_new: List[str]
    history: List[dict]
    count_before: Dict[str, int]
    count_after: Dict[str, int]


def _posting_row(ds: Dataset, **kw) -> dict:
    cols = ["posting_id", "source_event_id", "system_post_timestamp", "event_effective_timestamp", "posting_type",
            "sku_id", "quantity", "uom", "from_location", "to_location", "posting_status", "related_posting_id"]
    row = {c: kw.get(c, "") for c in cols}
    row["posting_status"] = row["posting_status"] or "POSTED"
    row["_row"] = max([r["_row"] for r in ds.rows(F_P)] + [0]) + 1 + kw.get("_n", 0)
    return row


def build_plan(res: Result, ds: Dataset) -> List[Action]:
    snap = fmt_utc(res.v.snapshot)
    ev_rows = {r["event_id"]: r for r in ds.rows(F_E)}
    po_rows = {r["posting_id"]: r for r in ds.rows(F_P)}
    actions: List[Action] = []
    seq = [0]

    def new_id() -> str:
        seq[0] += 1
        return "SIM-PO-%04d" % seq[0]

    def standard_postings(e: dict, types: Optional[List[str]] = None) -> List[dict]:
        rows = []
        exp = EXPECTED_POSTINGS[e["event_type"]]
        for t in (types or list(exp)):
            frm, to = e["from_location"], e["to_location"]
            rows.append({"posting_id": new_id(), "source_event_id": e["event_id"], "system_post_timestamp": snap,
                         "event_effective_timestamp": fmt_utc(_ts(e["event_timestamp"])), "posting_type": t,
                         "sku_id": e["sku_id"], "quantity": e["quantity"], "uom": e["uom"],
                         "from_location": frm, "to_location": to if t != "RECEIPT" or True else "",
                         "posting_status": "POSTED", "related_posting_id": ""})
            if t == "RECEIPT":
                rows[-1]["from_location"] = ""
            if t == "ISSUE":
                rows[-1]["to_location"] = ""
        return rows

    def reversal_of(p: dict, source: str) -> dict:
        rv = {"posting_id": new_id(), "source_event_id": source, "system_post_timestamp": snap,
              "event_effective_timestamp": "", "posting_type": "REVERSAL", "sku_id": p["sku_id"],
              "quantity": p["quantity"], "uom": p["uom"], "from_location": "", "to_location": "",
              "posting_status": "POSTED", "related_posting_id": p["posting_id"]}
        if p["posting_type"] in ("RECEIPT", "TRANSFER_IN") or (p["posting_type"] in ("ADJUSTMENT", "REVERSAL") and p["to_location"]):
            rv["from_location"] = p["to_location"]
        else:
            rv["to_location"] = p["from_location"]
        return rv

    for i in sorted(res.issues, key=lambda x: x.exception_id):
        if not i.is_primary:
            continue
        sim = res.catalog[i.rule_id]["simulated_action"]
        if sim == "none":
            continue
        post_ids = [s[1] for s in i.sources if s[0] == F_P]
        ev_ids = [s[1] for s in i.sources if s[0] == F_E]
        if i.rule_id == "R04" and i.subtype == "IDENTICAL_REDELIVERY" and i.sources and i.sources[0][0] in (F_P, F_E, F_C):
            f, rid = i.sources[0][0], i.sources[0][1]
            rows = [s[2] for s in i.sources[1:]]
            actions.append(Action(i.exception_id, i.rule_id, i.subtype, "remove_identical_duplicate_row",
                                  "Drop repeated row(s) %s of %s in the working copy." % (rows, rid),
                                  [{"op": "drop_rows", "file": f, "rows": rows}]))
        elif i.rule_id == "R06" and i.subtype in ("UNPOSTED", "FAILED_POSTING") and ev_ids:
            e = ev_rows[ev_ids[0]]
            new = standard_postings(e)
            actions.append(Action(i.exception_id, i.rule_id, i.subtype, "repost_event",
                                  "Post %d missing posting(s) for %s in the working copy (late by construction)." % (len(new), e["event_id"]),
                                  [{"op": "add_postings", "rows": new}]))
        elif i.rule_id == "R06" and i.subtype == "POSSIBLE_MATCH" and ev_ids and post_ids:
            actions.append(Action(i.exception_id, i.rule_id, i.subtype, "link_source_reference",
                                  "After confirmation, set source_event_id=%s on %s in the working copy." % (ev_ids[0], post_ids[0]),
                                  [{"op": "set_field", "file": F_P, "id_col": "posting_id", "id": post_ids[0],
                                    "col": "source_event_id", "value": ev_ids[0]}]))
        elif i.rule_id == "R05" and ev_ids and len(post_ids) >= 2:
            ch = []
            for pid in post_ids[1:]:
                ch.append({"op": "add_postings", "rows": [reversal_of(po_rows[pid], ev_ids[0])]})
            actions.append(Action(i.exception_id, i.rule_id, i.subtype, "reverse_extra_posting",
                                  "Reverse extra posting(s) %s with linked REVERSAL(s) in the working copy." % ", ".join(post_ids[1:]), ch))
        elif i.rule_id == "R07" and post_ids:
            p = po_rows[post_ids[0]]
            src = ev_ids[0] if ev_ids else p["source_event_id"]
            actions.append(Action(i.exception_id, i.rule_id, i.subtype, "reverse_orphan_posting",
                                  "Reverse unsupported posting %s with a linked REVERSAL (not deleted)." % p["posting_id"],
                                  [{"op": "add_postings", "rows": [reversal_of(p, src)]}]))
        elif i.rule_id == "R08" and ev_ids and post_ids:
            e = ev_rows[ev_ids[0]]
            ch = []
            types = []
            for pid in post_ids:
                p = po_rows[pid]
                ch.append({"op": "add_postings", "rows": [reversal_of(p, e["event_id"])]})
                types.append(p["posting_type"])
            ch.append({"op": "add_postings", "rows": standard_postings(e, types)})
            actions.append(Action(i.exception_id, i.rule_id, i.subtype, "reverse_and_repost_posting",
                                  "Reverse %s and repost the event quantity (%s %s)." % (", ".join(post_ids), e["quantity"], e["uom"]), ch))
        elif i.rule_id == "R09" and ev_ids:
            e = ev_rows[ev_ids[0]]
            if i.subtype == "MISSING_LEG":
                present = {po_rows[pid]["posting_type"] for pid in post_ids}
                missing = [t for t in EXPECTED_POSTINGS[e["event_type"]] if t not in present]
                actions.append(Action(i.exception_id, i.rule_id, i.subtype, "repost_missing_leg",
                                      "Post the missing %s leg for %s." % ("/".join(missing), e["event_id"]),
                                      [{"op": "add_postings", "rows": standard_postings(e, missing)}]))
            else:
                ch, types = [], []
                for pid in post_ids:
                    p = po_rows[pid]
                    ch.append({"op": "add_postings", "rows": [reversal_of(p, e["event_id"])]})
                    types.append(EXPECTED_POSTINGS[e["event_type"]][0] if i.subtype == "MOVEMENT_TYPE_MISMATCH" else p["posting_type"])
                ch.append({"op": "add_postings", "rows": standard_postings(e, types)})
                actions.append(Action(i.exception_id, i.rule_id, i.subtype, "reverse_and_repost_posting",
                                      "Reverse %s and repost matching the event." % ", ".join(post_ids), ch))
        elif i.rule_id == "R10":
            actions.append(Action(i.exception_id, i.rule_id, i.subtype, "acknowledge_timing",
                                  "Acknowledge as timing; no data change. The historic fact remains detectable.", [], False))
    return actions


def _ts(s: str):
    from .common import parse_ts
    return parse_ts(s)


def apply_actions(ds: Dataset, actions: List[Action]) -> Dataset:
    copy = ds.clone()
    n = 0
    for a in actions:
        for ch in a.changes:
            if ch["op"] == "drop_rows":
                keep = [r for r in copy.tables[ch["file"]] if r["_row"] not in set(ch["rows"])]
                copy.tables[ch["file"]] = keep
            elif ch["op"] == "set_field":
                for r in copy.tables[ch["file"]]:
                    if r[ch["id_col"]] == ch["id"]:
                        r[ch["col"]] = ch["value"]
            elif ch["op"] == "add_postings":
                for row in ch["rows"]:
                    n += 1
                    r = dict(row)
                    r["_row"] = max([x["_row"] for x in copy.tables[F_P]] + [0]) + 1
                    copy.tables[F_P].append(r)
    return copy


def simulate(ds: Dataset, before: Result) -> SimResult:
    actions = build_plan(before, ds)
    work = apply_actions(ds, actions)
    after = run(work)
    key = lambda i: (i.rule_id, i.key_id, i.subtype)
    b = {key(i): i for i in before.issues}
    a = {key(i): i for i in after.issues}
    ack = {x.exception_id for x in actions if x.action == "acknowledge_timing"}
    resolved, acknowledged, remaining, side, unexpected = [], [], [], [], []
    for k, i in b.items():
        if k in a:
            (acknowledged if i.exception_id in ack else remaining).append(i.exception_id)
        elif i.is_primary:
            resolved.append(i.exception_id)
    repost_events = set()
    for x in actions:
        if x.action in ("repost_event", "reverse_and_repost_posting", "repost_missing_leg"):
            repost_events.add(x.exception_id.split("-", 2)[-1])
    for k, i in a.items():
        if k in b:
            continue
        if i.rule_id == "R10" and i.subtype == "LATE_POSTING":
            side.append(i.exception_id)
        elif i.rule_id == "R13" and i.subtype == "SYSTEM_NEGATIVE_AT_CUTOFF":
            side.append(i.exception_id)
        else:
            unexpected.append(i.exception_id)
    # ids resolved only if primary in before and absent after; secondary resolved are not counted twice
    hist = []
    for i in sorted(before.issues, key=lambda x: x.exception_id):
        hist.append({"exception_id": i.exception_id, "seq": 1, "status": "OPEN", "by_role": "detection engine",
                     "note": "detected in run %s" % before.run_id})
    act_by_ex = {x.exception_id: x for x in actions}
    for ex, x in sorted(act_by_ex.items()):
        hist.append({"exception_id": ex, "seq": 2, "status": "PROPOSED_FIX", "by_role": "Inventory Control Analyst",
                     "note": x.action})
        hist.append({"exception_id": ex, "seq": 3, "status": "APPROVED_FOR_SIMULATION", "by_role": APPROVER,
                     "note": "hypothetical approval"})
        hist.append({"exception_id": ex, "seq": 4, "status": "RETESTED", "by_role": "simulation retest",
                     "note": "engine rerun on working copy"})
        if ex in resolved:
            outcome = "CLOSED"
            note = "no longer detected on the working copy (simulation only)"
        elif ex in acknowledged:
            outcome = "CLOSED"
            note = "acknowledged as timing; still detectable historically"
        else:
            outcome = "OPEN"
            note = "still detected after simulated action"
        hist.append({"exception_id": ex, "seq": 5, "status": outcome, "by_role": "simulation retest", "note": note})
    return SimResult(actions, before, after, sorted(resolved), sorted(acknowledged), sorted(remaining), sorted(side),
                     sorted(unexpected), hist,
                     _cls(before), _cls(after))


def _cls(res: Result) -> Dict[str, int]:
    out: Dict[str, int] = defaultdict(int)
    for r in res.count_table:
        out[r["class"]] += 1
    return dict(out)

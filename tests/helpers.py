from collections import defaultdict
from datetime import timedelta

from stocktrace import engine
from stocktrace.common import fmt_utc, parse_ts


def run_mutated(ds, fn):
    d = ds.clone()
    fn(d)
    return engine.run(d)


def primary_keys(res):
    return {(i.rule_id, i.key_id) for i in res.issues if i.is_primary}


def plain_picks(ds):
    """Ordinary completed picks (closed shift) with exactly one ISSUE posting, unique timestamp, posted timely."""
    ev = ds.rows("business_events.csv")
    po = defaultdict(list)
    for p in ds.rows("system_postings.csv"):
        po[p["source_event_id"]].append(p)
    ts_count = defaultdict(int)
    for e in ev:
        ts_count[e["event_timestamp"]] += 1
    shifts = {s["shift_id"]: s for s in ds.rows("shifts.csv")}
    out = []
    for e in ev:
        ps = po.get(e["event_id"], [])
        if e["event_type"] != "pick" or e["status"] != "completed" or len(ps) != 1 or ps[0]["posting_type"] != "ISSUE":
            continue
        if ts_count[e["event_timestamp"]] > 1 or e["handoff_to_shift"] or ps[0]["event_effective_timestamp"]:
            continue
        sh = shifts[e["shift_id"]]
        if parse_ts(sh["end_ts"]) > parse_ts(ds.params["snapshot_ts"]):
            continue
        out.append((e, ps[0], sh))
    return out

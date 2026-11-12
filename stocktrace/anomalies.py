"""Plants labelled anomalies into the clean world to produce the demo scenario.

The planter works on CSV-style row dicts. It returns a manifest (what was planted, which records, which rule is
expected) which is written to data/fixtures/, never to data/raw/. The detection engine never sees it.
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .common import parse_ts
from .generator import (LOCAL, RCV, SNAPSHOT, World, dstr, f_local, f_utc, make_counts, q3)

F_E, F_P, F_C = "business_events.csv", "system_postings.csv", "physical_counts.csv"


class Planter:
    def __init__(self, w: World, ev: List[dict], po: List[dict], seed: int):
        self.w, self.ev, self.po = w, ev, po
        self.rng = random.Random(seed)
        self.manifest: List[dict] = []
        self.near: List[dict] = []
        self.used = set()
        self.touched = set()      # (sku, loc) pairs affected by pairing anomalies (informational)
        self.avoid_counts = set()
        self.shifts = [(s["shift_id"], parse_ts(s["start_ts"]), parse_ts(s["end_ts"])) for s in w.shifts]
        self.ev_by_id = {e["event_id"]: e for e in ev}
        self.po_by_id = {p["posting_id"]: p for p in po}
        self.next_e = max(int(e["event_id"][3:]) for e in ev) + 1
        self.next_p = max(int(p["posting_id"][3:]) for p in po) + 1
        self.ea = [m["id"] for m in w.meta if m["base"] == "EA"]
        self.kg = [m["id"] for m in w.meta if m["base"] == "KG"]
        self.reserved = set(self.kg[-2:])   # kept clean so count-variance cases have an untouched KG bin
        self._index()
        self._near_misses()

    # ---- indexing
    def _index(self) -> None:
        self.posts_of = defaultdict(list)
        for p in self.po:
            if p["source_event_id"]:
                self.posts_of[p["source_event_id"]].append(p)
        self.sidx = {}
        for e in self.ev:
            self.sidx[e["event_id"]] = self.shift_index(parse_ts(e["event_timestamp"]))

    def shift_index(self, dt: datetime) -> int:
        for i, (_, a, b) in enumerate(self.shifts):
            if a <= dt < b:
                return i
        return -1

    def _near_misses(self) -> None:
        by_ts = defaultdict(list)
        for e in self.ev:
            by_ts[(e["event_timestamp"], e["sku_id"], e["quantity"])].append(e)
        for k, grp in by_ts.items():
            if len(grp) == 2 and grp[0]["event_type"] == "pick":
                for e in grp:
                    self._nm(F_E, e["event_id"], ["R04", "R05"], "twin picks: same SKU/qty/time, distinct events and postings")
                    self.used.add(e["event_id"])
        ends = {f_utc(b) for _, _, b in self.shifts}
        for p in self.po:
            if p["system_post_timestamp"] in ends:
                self._nm(F_E, p["source_event_id"], ["R10", "R06"], "posting exactly at shift cutoff is timely")
                self.used.add(p["source_event_id"])
            if p["quantity"] == "4.5359":
                self._nm(F_E, p["source_event_id"], ["R08"], "10 LB vs 4.5359 KG rounds to the same 3-dp base quantity")
                self.used.add(p["source_event_id"])
        starts = {f_local(a) for _, a, _ in self.shifts}
        for e in self.ev:
            if e["event_timestamp"] in starts and e["event_type"] == "pick":
                self._nm(F_E, e["event_id"], ["R10"], "event exactly at shift start belongs to the new shift (label matches)")
                self.used.add(e["event_id"])
            if e["status"] == "pending" and e["handoff_to_shift"] == self.shifts[-1][0]:
                self._nm(F_E, e["event_id"], ["R11"], "pending handoff to a shift that is still open is not yet due")
                self.used.add(e["event_id"])
            if e["status"] == "cancelled" and not self.posts_of.get(e["event_id"]):
                self._nm(F_E, e["event_id"], ["R06", "R07"], "cancelled event with no posting is consistent")
                self.used.add(e["event_id"])
            if e["event_type"] == "reversal":
                self.used.add(e["event_id"])
                self.used.add(e["related_id"])
        for pid in [p["posting_id"] for p in self.po if p["event_effective_timestamp"]]:
            p = self.po_by_id[pid]
            if p["event_effective_timestamp"] in {f_utc(b - timedelta(seconds=1)) for _, _, b in self.shifts}:
                self._nm(F_E, p["source_event_id"], ["R10"], "effective time one second before cutoff is still in the same shift")
                self.used.add(p["source_event_id"])
        for eid, ps in self.posts_of.items():
            if len(ps) >= 3:
                self.used.add(eid)
        for e in self.ev:
            if e["uom"] == "CS" and e["event_type"] == "pick" and len(self.posts_of.get(e["event_id"], [])) == 1 \
                    and self.posts_of[e["event_id"]][0]["uom"] == self.w.sku_meta[e["sku_id"]]["base"] \
                    and len(self.near) < 40:
                self._nm(F_E, e["event_id"], ["R08"], "case-quantity event vs base-UoM posting: equal after normalization")

    def _nm(self, f: str, rid: str, rules: List[str], why: str) -> None:
        self.near.append({"file": f, "record_id": rid, "rules_not_expected": rules, "why": why})

    # ---- builders
    def mark(self, rule: str, sub: str, f: str, rid: str, note: str) -> None:
        self.manifest.append({"rule_id": rule, "subtype": sub, "file": f, "record_id": rid, "note": note})

    def new_e(self, ts: str, typ: str, sku: str, qty: str, uom: str, frm: str, to: str, status: str = "completed",
              shift: Optional[str] = None, related: str = "", hto: str = "", hst: str = "") -> dict:
        eid = "EV-%06d" % self.next_e
        self.next_e += 1
        if shift is None:
            try:
                i = self.shift_index(parse_ts(ts))
                shift = self.shifts[i][0] if i >= 0 else self.shifts[-1][0]
            except ValueError:
                shift = self.shifts[0][0]
        row = {"event_id": eid, "event_timestamp": ts, "shift_id": shift, "event_type": typ, "sku_id": sku,
               "quantity": qty, "uom": uom, "from_location": frm, "to_location": to, "status": status,
               "related_id": related, "handoff_to_shift": hto, "handoff_status": hst}
        self.ev.append(row)
        self.ev_by_id[eid] = row
        self.sidx[eid] = self.shift_index(parse_ts(ts)) if ts and ts.endswith(("-07:00", "Z")) else -1
        return row

    def new_p(self, src: str, ts: str, typ: str, sku: str, qty: str, uom: str, frm: str, to: str, eff: str = "",
              status: str = "POSTED", related: str = "") -> dict:
        pid = "PO-%06d" % self.next_p
        self.next_p += 1
        row = {"posting_id": pid, "source_event_id": src, "system_post_timestamp": ts, "event_effective_timestamp": eff,
               "posting_type": typ, "sku_id": sku, "quantity": qty, "uom": uom, "from_location": frm,
               "to_location": to, "posting_status": status, "related_posting_id": related}
        self.po.append(row)
        self.po_by_id[pid] = row
        if src:
            self.posts_of[src].append(row)
        return row

    def choose(self, typ: str, sidx: int, pred=None, nposts: int = 1, uom: Optional[str] = None) -> dict:
        c = []
        for e in sorted(self.ev, key=lambda x: x["event_id"]):
            if (e["event_type"] != typ or e["status"] != "completed" or e["event_id"] in self.used
                    or self.sidx.get(e["event_id"]) != sidx):
                continue
            ps = self.posts_of.get(e["event_id"], [])
            if len(ps) != nposts or (uom and e["uom"] != uom):
                continue
            if pred and not pred(e, ps):
                continue
            if e["sku_id"] in self.reserved:
                continue
            if any((e["sku_id"], l) in self.touched for l in (e["from_location"], e["to_location"]) if l):
                continue  # keep planted cases on separate SKU-locations so each story stays readable
            c.append(e)
        if not c:
            raise RuntimeError("no candidate for %s in shift %d" % (typ, sidx))
        e = self.rng.choice(c)
        self.used.add(e["event_id"])
        self.touch(e)
        return e

    def touch(self, e: dict) -> None:
        for loc in (e["from_location"], e["to_location"]):
            if loc:
                self.touched.add((e["sku_id"], loc))

    def tl(self, day: int, hh: int, mm: int, ss: int = 0) -> str:
        return f_local(datetime(2026, 2, day, hh, mm, ss, tzinfo=LOCAL))

    def base_qty(self, e: dict) -> Decimal:
        return self.w.to_base(e["sku_id"], e["uom"], Decimal(e["quantity"]))

    # ---- anomaly families
    def plant_rows(self) -> None:
        w = self.w
        ea = self.ea
        # ---------------- R04
        e = self.choose("pick", 2)
        p = self.posts_of[e["event_id"]][0]
        self.po.append(dict(p))
        self.mark("R04", "IDENTICAL_REDELIVERY", F_P, p["posting_id"], "exact repeat of a posting row")
        e = self.choose("pick", 4)
        dup = dict(e)
        dup["quantity"] = str(int(Decimal(e["quantity"])) + 1)
        self.ev.append(dup)
        self.mark("R04", "CONFLICTING_CONTENT", F_E, e["event_id"], "same event_id, different quantity")
        e = self.choose("pick", 6)
        p = self.posts_of[e["event_id"]][0]
        dup = dict(p)
        dup["quantity"] = str(int(Decimal(p["quantity"])) + 1) if "." not in p["quantity"] else "999"
        self.po.append(dup)
        self.mark("R04", "CONFLICTING_CONTENT", F_P, p["posting_id"], "same posting_id, different quantity; its event has a quarantined counterpart")
        # ---------------- R01
        x = self.new_e(self.tl(10, 9, 10), "pick", ea[0], "", "EA", "PK-A01", "")
        self.mark("R01", "MISSING_FIELD", F_E, x["event_id"], "blank quantity (rejected, not zero)")
        x = self.new_e(self.tl(10, 9, 20), "receipt", ea[1], "12x", "CS", "", RCV)
        self.mark("R01", "INVALID_QUANTITY", F_E, x["event_id"], "non-numeric quantity")
        x = self.new_e(self.tl(10, 9, 30), "pick", "", "4", "EA", "PK-A01", "")
        self.mark("R01", "MISSING_FIELD", F_E, x["event_id"], "blank sku_id")
        x = self.new_p("", "", "ISSUE", ea[2], "5", "EA", "PK-A02", "")
        self.mark("R01", "MISSING_FIELD", F_P, x["posting_id"], "blank system_post_timestamp")
        x = self.new_p("", "2026-02-10T17:00:00Z", "ISSUE", ea[3], "-5", "EA", "PK-A02", "")
        self.mark("R01", "NEGATIVE_QUANTITY", F_P, x["posting_id"], "negative posting quantity")
        x = self.new_e("2026-02-12T10:30:00-07:00", "pick", ea[4], "3", "EA", "PK-A01", "")
        self.mark("R01", "FUTURE_TIMESTAMP", F_E, x["event_id"], "event after extract snapshot")
        x = self.new_e("2026-02-10T09:30:00", "pick", ea[5], "3", "EA", "PK-A01", "")
        self.mark("R01", "INVALID_TIMESTAMP", F_E, x["event_id"], "timestamp without offset")
        r = self.new_e(self.tl(10, 11, 0), "receipt", ea[6], "0", "CS", "", RCV)
        self.new_p(r["event_id"], "2026-02-10T18:05:00Z", "RECEIPT", ea[6], "0", "CS", "", RCV)
        self.mark("R01", "ZERO_QTY", F_E, r["event_id"], "valid zero-quantity receipt with zero posting (warning only)")
        # ---------------- R02
        x = self.new_e(self.tl(10, 10, 5), "pick", ea[7], "4", "EA", "PK-X77", "")
        self.mark("R02", "UNKNOWN_LOCATION", F_E, x["event_id"], "location not in master")
        x = self.new_e(self.tl(10, 8, 0), "pick", "SKU-1041", "4", "EA", "PK-A01", "")
        self.mark("R02", "SKU_NOT_EFFECTIVE", F_E, x["event_id"], "SKU effective only from 2026-02-11 12:00")
        x = self.new_e(self.tl(11, 8, 0), "pick", "SKU-1042", "4", "EA", "PK-A01", "")
        self.mark("R02", "SKU_NOT_EFFECTIVE", F_E, x["event_id"], "SKU retired 2026-02-10 00:00")
        x = self.new_p("", "2026-02-10T20:00:00Z", "ISSUE", "SKU-9999", "3", "EA", "PK-A01", "")
        self.mark("R02", "UNKNOWN_SKU", F_P, x["posting_id"], "unknown SKU on posting")
        x = self.new_p("", "2026-02-10T20:10:00Z", "ISSUE", ea[8], "3", "EA", "PK-Z99", "")
        self.mark("R02", "LOCATION_NOT_EFFECTIVE", F_P, x["posting_id"], "location inactive since 2026-01-15")
        x = self.new_e(self.tl(10, 10, 15), "pick", ea[9], "2", "EA", "PK-A02", "", shift="2026-02-10-X")
        self.mark("R02", "UNKNOWN_SHIFT", F_E, x["event_id"], "shift label not in shift calendar")
        x = self.new_e(self.tl(10, 10, 25), "transfer", ea[10], "20", "EA", "BLK-01", "BLK-03")
        self.mark("R02", "LOCATION_NOT_EFFECTIVE", F_E, x["event_id"], "BLK-03 becomes effective only on 2026-02-15")
        # ---------------- R03
        x = self.new_e(self.tl(10, 10, 35), "receipt", ea[11], "2", "PLT", "", RCV)
        self.mark("R03", "UNKNOWN_UOM_CODE", F_E, x["event_id"], "PLT is not a defined UoM")
        x = self.new_e(self.tl(10, 10, 45), "pick", ea[12], "2", "KG", "PK-A01", "")
        self.mark("R03", "NO_CONVERSION_FOR_SKU", F_E, x["event_id"], "KG exists but not for this EA-based SKU")
        x = self.new_p("", "2026-02-10T20:30:00Z", "ISSUE", ea[13], "2", "BX", "PK-A01", "")
        self.mark("R03", "UNKNOWN_UOM_CODE", F_P, x["posting_id"], "BX is not a defined UoM")
        # ---------------- R05
        e = self.choose("pick", 3)
        p = self.posts_of[e["event_id"]][0]
        d = dict(p, posting_id=self._pid(), system_post_timestamp=self._shift_ts(p["system_post_timestamp"], 120))
        self._add_p(d)
        self.mark("R05", "DUPLICATE_POSTING", F_E, e["event_id"], "second ISSUE posting for one pick")
        e = self.choose("receipt", 4)
        p = self.posts_of[e["event_id"]][0]
        self._add_p(dict(p, posting_id=self._pid(), system_post_timestamp=self._shift_ts(p["system_post_timestamp"], 90)))
        self.mark("R05", "DUPLICATE_POSTING", F_E, e["event_id"], "second RECEIPT posting")
        e = self.choose("transfer", 5, nposts=2)
        p = [q for q in self.posts_of[e["event_id"]] if q["posting_type"] == "TRANSFER_OUT"][0]
        self._add_p(dict(p, posting_id=self._pid(), system_post_timestamp=self._shift_ts(p["system_post_timestamp"], 60)))
        self.mark("R05", "DUPLICATE_POSTING", F_E, e["event_id"], "second TRANSFER_OUT leg")
        # ---------------- R06
        e = self.choose("pick", 1)
        self._drop_postings(e)
        self.mark("R06", "UNPOSTED", F_E, e["event_id"], "pick never posted")
        e = self.choose("receipt", 3)
        self._drop_postings(e)
        self.mark("R06", "UNPOSTED", F_E, e["event_id"], "receipt never posted")
        e = self.choose("pick", 8)
        self._drop_postings(e)
        self.mark("R06", "UNPOSTED", F_E, e["event_id"], "pick in last closed shift never posted")
        e = self.choose("pick", 6)
        self.posts_of[e["event_id"]][0]["posting_status"] = "FAILED"
        self.mark("R06", "FAILED_POSTING", F_E, e["event_id"], "ERP posting attempt FAILED")
        e = self.choose("pick", 5, pred=self._unique_candidate)
        p = self.posts_of[e["event_id"]][0]
        p["source_event_id"] = ""
        self.posts_of[e["event_id"]].remove(p)
        self.mark("R06", "POSSIBLE_MATCH", F_E, e["event_id"], "posting lost its source reference; unique candidate")
        # ---------------- R07
        x = self.new_p("EV-999999", "2026-02-10T21:00:00Z", "ISSUE", ea[14], "3", "EA", "PK-A01" if True else "", "")
        self.mark("R07", "ORPHAN_REFERENCE", F_P, x["posting_id"], "references an event that does not exist")
        x = self.new_p("", "2026-02-10T21:20:00Z", "RECEIPT", ea[15], "24", "EA", "", RCV)
        self.mark("R07", "NO_SOURCE_REFERENCE", F_P, x["posting_id"], "no source and no candidate event")
        e = self.choose("pick", 2)
        e["status"] = "cancelled"
        self.w.truth_extra.append((parse_ts(e["event_timestamp"]), e["sku_id"], e["from_location"], self.base_qty(e)))
        self.mark("R07", "POSTING_FOR_CANCELLED_EVENT", F_P, self.posts_of[e["event_id"]][0]["posting_id"], "event cancelled but posting remains")
        # ---------------- R08
        e = self.choose("pick", 3, uom="EA", pred=lambda e, ps: Decimal(e["quantity"]) >= 14 and ps[0]["uom"] == "EA")
        p = self.posts_of[e["event_id"]][0]
        p["quantity"] = str(int(Decimal(e["quantity"])) - 10)
        self.mark("R08", "QUANTITY_MISMATCH", F_E, e["event_id"], "posted 10 base units short")
        e = self.choose("pick", 4, uom="CS", pred=lambda e, ps: ps[0]["uom"] == self.w.sku_meta[e["sku_id"]]["base"])
        p = self.posts_of[e["event_id"]][0]
        base = self.base_qty(e)
        p["quantity"] = dstr(base - 5)
        self.mark("R08", "QUANTITY_MISMATCH", F_E, e["event_id"], "case-quantity event; posting 5 base units short after normalization")
        e = self.choose("transfer", 2, nposts=2, uom="EA")
        out = [q for q in self.posts_of[e["event_id"]] if q["posting_type"] == "TRANSFER_OUT"][0]
        out["uom"] = "EA"
        out["quantity"] = str(int(Decimal(e["quantity"])) - 5)
        self.mark("R08", "QUANTITY_MISMATCH", F_E, e["event_id"], "TRANSFER_OUT leg 5 short")
        e = self.choose("pick", 7, uom="KG", pred=lambda e, ps: ps[0]["uom"] == "KG")
        self.posts_of[e["event_id"]][0]["quantity"] = str(int(Decimal(e["quantity"])) + 3)
        self.mark("R08", "QUANTITY_MISMATCH", F_E, e["event_id"], "KG posting 3 kg over")
        # ---------------- R09
        e = self.choose("pick", 4, pred=lambda e, ps: True)
        p = self.posts_of[e["event_id"]][0]
        other = [l for l in ("PK-A01", "PK-A02", "PK-B01") if l != e["from_location"]][0]
        p["from_location"] = other
        self.touched.add((e["sku_id"], other))
        self.mark("R09", "LOCATION_MISMATCH", F_E, e["event_id"], "posting from a different pick bin")
        e = self.choose("pick", 5, uom="EA", pred=lambda e, ps: ps[0]["uom"] == "EA")
        p = self.posts_of[e["event_id"]][0]
        other_sku = [s for s in ea if s != e["sku_id"]][5]
        p["sku_id"] = other_sku
        self.touched.add((other_sku, e["from_location"]))
        self.mark("R09", "SKU_MISMATCH", F_E, e["event_id"], "posting SKU differs from event SKU")
        e = self.choose("transfer", 6, nposts=2)
        ins = [q for q in self.posts_of[e["event_id"]] if q["posting_type"] == "TRANSFER_IN"][0]
        self.po.remove(ins)
        self.posts_of[e["event_id"]].remove(ins)
        self.mark("R09", "MISSING_LEG", F_E, e["event_id"], "TRANSFER_IN leg missing")
        e = self.choose("transfer", 7, nposts=2)
        ins = [q for q in self.posts_of[e["event_id"]] if q["posting_type"] == "TRANSFER_IN"][0]
        wrong = [l for l in ("BLK-01", "BLK-02", "PK-A01", "PK-A02", "PK-B01") if l not in (e["to_location"], e["from_location"])][0]
        ins["to_location"] = wrong
        self.touched.add((e["sku_id"], wrong))
        self.mark("R09", "LOCATION_MISMATCH", F_E, e["event_id"], "TRANSFER_IN lands in a different bin")
        e = self.choose("pick", 7, pred=lambda e, ps: ps[0]["posting_type"] == "ISSUE")
        self.posts_of[e["event_id"]][0]["posting_type"] = "ADJUSTMENT"
        self.mark("R09", "MOVEMENT_TYPE_MISMATCH", F_E, e["event_id"], "pick posted as ADJUSTMENT")
        # ---------------- R10
        for sidx, minutes, label in ((0, 12, "LOW"), (3, 95, "MEDIUM")):
            e = self.choose("pick", sidx)
            p = self.posts_of[e["event_id"]][0]
            p["system_post_timestamp"] = f_utc(self.shifts[sidx][2] + timedelta(minutes=minutes))
            self.mark("R10", "LATE_POSTING", F_E, e["event_id"], "posted %d min after cutoff (%s)" % (minutes, label))
        e = self.choose("pick", 2)
        p = self.posts_of[e["event_id"]][0]
        p["system_post_timestamp"] = f_utc(self.shifts[3][2] + timedelta(minutes=30))
        self.mark("R10", "LATE_POSTING", F_E, e["event_id"], "posted after the next cutoff too (HIGH)")
        e = self.choose("pick", 4)
        p = self.posts_of[e["event_id"]][0]
        p["event_effective_timestamp"] = f_utc(self.shifts[4][2] + timedelta(seconds=1))
        self.mark("R10", "EFFECTIVE_SHIFT_MISMATCH", F_E, e["event_id"], "effective time one second into the next shift")
        e = self.choose("pick", 7)
        e["shift_id"] = self.shifts[6][0]
        self.mark("R10", "SHIFT_LABEL_MISMATCH", F_E, e["event_id"], "label says previous shift")
        # ---------------- R11
        for sidx, tgt, sub in ((1, 2, "UNRESOLVED_HANDOFF_AFTER_CUTOFF"), (5, 6, "UNRESOLVED_HANDOFF_AFTER_CUTOFF"),
                               (7, 8, "UNRESOLVED_HANDOFF_AFTER_CUTOFF")):
            a = self.shifts[sidx][1] + timedelta(hours=2, minutes=sidx * 3)
            x = self.new_e(f_local(a), "pick", ea[16 + sidx % 3], "9", "EA", "PK-A01", "", status="pending",
                           hto=self.shifts[tgt][0], hst="OPEN")
            self.mark("R11", sub, F_E, x["event_id"], "pending, handed to shift %d which has closed" % tgt)
        a = self.shifts[3][1] + timedelta(hours=3)
        x = self.new_e(f_local(a), "pick", ea[20], "6", "EA", "PK-A02", "", status="pending")
        self.mark("R11", "PENDING_NO_HANDOFF", F_E, x["event_id"], "pending, no receiving shift named")
        e = self.choose("pick", 4)
        e["handoff_to_shift"], e["handoff_status"] = self.shifts[5][0], "OPEN"
        self.mark("R11", "STALE_OPEN_HANDOFF", F_E, e["event_id"], "completed, handoff entry still OPEN after the receiving shift closed")
        e = self.choose("pick", 6)
        e["status"] = "pending"
        self.w.truth_extra.append((parse_ts(e["event_timestamp"]), e["sku_id"], e["from_location"], self.base_qty(e)))
        self.mark("R11", "PENDING_BUT_POSTED", F_E, e["event_id"], "pending on the floor, already posted in ERP")
        # ---------------- R14
        sku = ea[21]
        x = self.new_e(self.tl(10, 12, 0), "receipt", sku, "4", "CS", "", RCV)
        px = self.new_p(x["event_id"], "2026-02-10T19:10:00Z", "RECEIPT", sku, "4", "CS", "", RCV)
        rv = self.new_e(self.tl(10, 12, 10), "reversal", sku, "4", "CS", RCV, "", related="EV-999998")
        self.new_p(rv["event_id"], "2026-02-10T19:20:00Z", "REVERSAL", sku, "4", "CS", RCV, "", related=px["posting_id"])
        self.mark("R14", "BROKEN_REFERENCE", F_E, rv["event_id"], "reversal points to a non-existent event")
        self._truth_pair(x, rv)
        sku = ea[22]
        y = self.new_e(self.tl(10, 13, 0), "receipt", sku, "4", "CS", "", RCV)
        py = self.new_p(y["event_id"], "2026-02-10T20:10:00Z", "RECEIPT", sku, "4", "CS", "", RCV)
        rv2 = self.new_e(self.tl(10, 13, 10), "reversal", sku, "3", "CS", RCV, "", related=y["event_id"])
        rp = self.new_p(rv2["event_id"], "2026-02-10T20:20:00Z", "REVERSAL", sku, "3", "CS", RCV, "", related=py["posting_id"])
        self.mark("R14", "QUANTITY_MISMATCH", F_E, rv2["event_id"], "reversal quantity 3 CS vs original 4 CS")
        self.mark("R14", "POSTING_QTY_MISMATCH", F_P, rp["posting_id"], "reversal posting 3 CS vs reversed posting 4 CS")
        self._truth_pair(y, rv2)
        chains = [eid for eid, ps in self.posts_of.items() if len(ps) == 3 and
                  sorted(q["posting_type"] for q in ps) == ["ISSUE", "ISSUE", "REVERSAL"]]
        if chains:
            rvp = [q for q in self.posts_of[chains[0]] if q["posting_type"] == "REVERSAL"][0]
            rvp["related_posting_id"] = "PO-999999"
            self.mark("R14", "BROKEN_POSTING_REFERENCE", F_P, rvp["posting_id"], "reversal posting references a missing posting")
        # ---------------- R13 (impossible balance)
        t = datetime(2026, 2, 12, 1, 30, 0, tzinfo=LOCAL)
        best = None
        for s in ea:
            home = w.sku_meta[s]["home"]
            if (s, home) in self.touched or s in ea[:23]:
                continue
            tq = w.truth(s, home, t)
            if best is None or tq < best[0]:
                best = (tq, s, home)
        tq, s, home = best
        qty = tq + 25
        x = self.new_e(f_local(t), "pick", s, dstr(qty), "EA", home, "")
        self.new_p(x["event_id"], f_utc(t + timedelta(minutes=10)), "ISSUE", s, dstr(qty), "EA", home, "")
        self.mark("R13", "IMPOSSIBLE_BALANCE", F_E, "%s|%s" % (s, home), "pick of %s from a bin holding %s" % (dstr(qty), dstr(tq)))
        self.avoid_counts.add((s, home))

    # ---- helpers
    def _truth_pair(self, a: dict, b: dict) -> None:
        for e, sign in ((a, 1), (b, 1)):
            base = self.base_qty(e)
            ts = parse_ts(e["event_timestamp"])
            if e["to_location"]:
                self.w.truth_extra.append((ts, e["sku_id"], e["to_location"], base))
            if e["from_location"]:
                self.w.truth_extra.append((ts, e["sku_id"], e["from_location"], -base))

    def _pid(self) -> str:
        pid = "PO-%06d" % self.next_p
        self.next_p += 1
        return pid

    def _add_p(self, row: dict) -> None:
        self.po.append(row)
        self.po_by_id[row["posting_id"]] = row
        if row["source_event_id"]:
            self.posts_of[row["source_event_id"]].append(row)

    @staticmethod
    def _shift_ts(ts: str, seconds: int) -> str:
        return f_utc(parse_ts(ts) + timedelta(seconds=seconds))

    def _drop_postings(self, e: dict) -> None:
        for p in list(self.posts_of[e["event_id"]]):
            self.po.remove(p)
        self.posts_of[e["event_id"]] = []

    def _unique_candidate(self, e: dict, ps: List[dict]) -> bool:
        p = ps[0]
        pt = parse_ts(e["event_timestamp"])
        for o in self.ev:
            if o is e or o["sku_id"] != e["sku_id"] or o["event_type"] != "pick":
                continue
            try:
                ot = parse_ts(o["event_timestamp"])
            except ValueError:
                continue
            if abs(ot - pt) <= timedelta(minutes=120) and o["from_location"] == e["from_location"]:
                return False
        return p["uom"] == "EA" and e["uom"] == "EA"

    # ---- counts phase
    def plant_counts(self, counts: List[dict]) -> None:
        rng = self.rng
        w = self.w
        meta = w.sku_meta
        cand = [c for c in counts if c["count_status"] == "COMPLETE" and c["uom"] == meta[c["sku_id"]]["base"]
                and (c["sku_id"], c["location_id"]) not in self.touched and Decimal(c["counted_qty"]) > 12]
        ea = [c for c in cand if meta[c["sku_id"]]["base"] == "EA"]
        kg = [c for c in cand if meta[c["sku_id"]]["base"] == "KG"]
        rng.shuffle(ea)
        rng.shuffle(kg)
        used = set()

        def take(lst, pred=lambda c: True):
            for c in lst:
                if c["count_id"] not in used and (c["sku_id"], c["location_id"]) not in used and pred(c):
                    used.add(c["count_id"])
                    used.add((c["sku_id"], c["location_id"]))
                    return c
            raise RuntimeError("no count candidate")

        c = take(ea)
        c["counted_qty"] = dstr(Decimal(c["counted_qty"]) - 6)
        self.mark("R12", "UNEXPLAINED_COUNT_VARIANCE", F_C, c["count_id"], "counted 6 below ledger and system")
        hv = [x for x in ea if meta[x["sku_id"]]["value"] >= 90]
        if hv:
            c = take(hv)
        else:
            sku = [m["id"] for m in w.meta if m["base"] == "EA" and m["value"] >= 90][0]
            home = meta[sku]["home"]
            cut = self.shifts[7][2]
            n = len(counts) + 1
            c = {"count_id": "CC-%04d" % (900 + n), "observed_at": f_local(cut), "sku_id": sku, "location_id": home,
                 "counted_qty": dstr(w.truth(sku, home, cut)), "uom": "EA", "count_status": "COMPLETE",
                 "evidence_ref": "CC-SHEET-099 line 1"}
            counts.append(c)
        drop = int(Decimal(600) / meta[c["sku_id"]]["value"]) + 1
        c["counted_qty"] = dstr(Decimal(c["counted_qty"]) - drop)
        self.mark("R12", "UNEXPLAINED_COUNT_VARIANCE", F_C, c["count_id"], "high-value SKU short by %d (simulated value above HIGH threshold)" % drop)
        c = take(ea)
        c["counted_qty"] = dstr(Decimal(c["counted_qty"]) + 4)
        self.mark("R12", "UNEXPLAINED_COUNT_VARIANCE", F_C, c["count_id"], "overage of 4")
        # KG counts: use existing untouched ones, else add dedicated counts at the last closed cutoff
        cut = self.shifts[8][2]
        for k, sku in enumerate(self.kg):
            if len(kg) >= 2:
                break
            home = meta[sku]["home"]
            if (sku, home) in self.touched or (sku, home) in used or (sku, home) in self.avoid_counts:
                continue
            if any(x["sku_id"] == sku and x["location_id"] == home and x["observed_at"] == f_local(cut) for x in counts):
                continue
            n_new = len(counts) + 1
            c = {"count_id": "CC-%04d" % (800 + n_new), "observed_at": f_local(cut), "sku_id": sku, "location_id": home,
                 "counted_qty": dstr(w.truth(sku, home, cut)), "uom": "KG", "count_status": "COMPLETE",
                 "evidence_ref": "CC-SHEET-098 line %d" % (k + 1)}
            counts.append(c)
            kg.append(c)
        c = take(kg)
        c["counted_qty"] = dstr(Decimal(c["counted_qty"]) + 3)
        self.mark("R12", "UNEXPLAINED_COUNT_VARIANCE", F_C, c["count_id"], "KG count 3 kg over (tolerance 0.5)")
        c = take(kg)
        c["counted_qty"] = dstr(Decimal(c["counted_qty"]) + Decimal("0.3"))
        self._nm(F_C, c["count_id"], ["R12"], "0.3 kg difference is inside the 0.5 kg tolerance")
        # R01/R03/R02/R04 on counts
        n = len(counts)
        base = cand[0]
        counts.append(dict(base, count_id="CC-%04d" % (n + 51), counted_qty=""))
        self.mark("R01", "MISSING_FIELD", F_C, "CC-%04d" % (n + 51), "blank counted_qty on a COMPLETE count (rejected; not the same as zero)")
        counts.append(dict(base, count_id="CC-%04d" % (n + 52), uom="DZ"))
        self.mark("R03", "UNKNOWN_UOM_CODE", F_C, "CC-%04d" % (n + 52), "DZ is not a defined UoM")
        counts.append(dict(base, count_id="CC-%04d" % (n + 53), location_id="PK-Z99"))
        self.mark("R02", "LOCATION_NOT_EFFECTIVE", F_C, "CC-%04d" % (n + 53), "count against an inactive location")
        counts.append(dict(counts[0]))
        self.mark("R04", "IDENTICAL_REDELIVERY", F_C, counts[0]["count_id"], "count row delivered twice")


def plant_anomalies(w: World, ev_rows: List[dict], po_rows: List[dict], seed: int):
    P = Planter(w, ev_rows, po_rows, seed + 3)
    P.plant_rows()
    counts = make_counts(w, ev_rows, random.Random(seed + 7), P.avoid_counts)
    P.plant_counts(counts)
    touched_ids = {p["record_id"] for p in P.manifest}
    near = [n for n in P.near if n["record_id"] not in touched_ids]      # a record later mutated is no longer a near miss
    return counts, {"seed": seed, "planted": P.manifest, "near_misses": near,
                    "note": "Generator-side labels. NOT an input to the detection engine."}


def generate_demo(path: str, manifest_path: str, seed: int = 20260209) -> dict:
    """Write the demo scenario (clean world + planted anomalies) and its generator-side manifest."""
    import json
    import os
    from .generator import build_clean_world, world_rows, write_scenario
    w = build_clean_world(seed)
    ev, po = world_rows(w)
    counts, manifest = plant_anomalies(w, ev, po, seed)
    write_scenario(path, w, ev, po, counts)
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return manifest

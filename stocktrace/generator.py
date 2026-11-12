"""Deterministic synthetic data generator.

Builds a *clean* warehouse world (events, ERP postings, counts) from a fixed seed. `plant_anomalies`
then mutates copies of the rows to create the demo scenario and returns a manifest of what was planted.
The manifest is written under data/fixtures/, never into data/raw/. The generator keeps its own simple
ledger ("truth") and does not import the engine.
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple

from .loader import write_csv
from .schema import SCHEMAS

SEED = 20260209
LOCAL = timezone(timedelta(hours=-7))
UTC = timezone.utc
T0 = datetime(2026, 2, 9, 6, 0, 0, tzinfo=LOCAL)
SNAPSHOT = datetime(2026, 2, 12, 9, 0, 0, tzinfo=LOCAL)
Q3 = Decimal("0.001")

ITEMS = [
    ("FASTENERS", "EA", ["Hex Bolt M8x40", "Wood Screw #8 x 2in", "Anchor Sleeve 3/8in", "Machine Nut M10",
                         "Flat Washer 1/2in", "Lag Screw 1/2 x 4in", "Drywall Screw #6 x 1-5/8in"]),
    ("HAND TOOLS", "EA", ["Claw Hammer 16oz", "Utility Knife Retractable", "Adjustable Wrench 10in",
                          "Tape Measure 25ft", "Box Level 24in", "Screwdriver Set 6pc"]),
    ("PACKAGING", "EA", ["Stretch Wrap 18in", "Packing Tape 48mm", "Corrugated Box 12x12x12", "Poly Mailer 10x13",
                         "Bubble Wrap Roll 12in", "Pallet Strap 1/2in"]),
    ("SAFETY", "EA", ["Safety Glasses Clear", "Nitrile Gloves Large", "Hi-Vis Vest Class 2", "Ear Plugs Corded",
                      "Hard Hat White", "Dust Mask N95"]),
    ("CLEANING", "EA", ["Shop Towels Roll", "Floor Cleaner 4L", "Trash Bags 55gal", "Degreaser 1L", "Mop Head Loop"]),
    ("ELECTRICAL", "EA", ["LED Bulb A19", "Extension Cord 15m", "Power Bar 6-Outlet", "Wire Nut Assortment",
                          "Electrical Tape Black", "Outlet Cover Plate"]),
    ("BULK", "KG", ["Granular Absorbent", "Rock Salt", "Concrete Mix", "Fill Sand"]),
]
PICK_LOCS = ["PK-A01", "PK-A02", "PK-B01"]
BULK_LOCS = ["BLK-01", "BLK-02"]
RCV = "RCV-01"


def q3(d: Decimal) -> Decimal:
    return d.quantize(Q3, rounding=ROUND_HALF_UP)


def dstr(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "") else s


def f_local(dt: datetime) -> str:
    return dt.astimezone(LOCAL).strftime("%Y-%m-%dT%H:%M:%S-07:00")


def f_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- masters
def build_shifts() -> List[dict]:
    rows = []
    specs = [("D", 6, 14), ("E", 14, 22), ("N", 22, 30)]
    day = datetime(2026, 2, 9, tzinfo=LOCAL)
    for d in range(4):
        for code, a, b in specs:
            start = day + timedelta(days=d, hours=a)
            end = day + timedelta(days=d, hours=b)
            if start >= datetime(2026, 2, 12, 14, 0, tzinfo=LOCAL):
                break
            name = {"D": "Day", "E": "Evening", "N": "Night"}[code]
            rows.append({"shift_id": "%s-%s" % (start.strftime("%Y-%m-%d"), code), "shift_name": name,
                         "start_ts": f_local(start), "end_ts": f_local(end)})
    return rows[:10]


def build_masters(rng: random.Random) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
    skus, conv, meta = [], [], []
    n = 0
    names = []
    for cat, base, lst in ITEMS:
        for nm in lst:
            names.append((cat, base, nm))
    for idx, (cat, base, nm) in enumerate(names):
        n += 1
        sid = "SKU-%d" % (1000 + n)
        if base == "EA":
            value = Decimal(str(round(rng.uniform(0.35, 38.0), 2)))
            if idx % 9 == 4:
                value = Decimal(str(round(rng.uniform(95.0, 240.0), 2)))
            tol = Decimal("0")
            cs = rng.choice([6, 10, 12, 20, 24, 48])
            conv.append({"sku_id": sid, "uom": "CS", "to_base_factor": str(cs)})
            pal = None
            if idx % 5 in (0, 1, 2):
                pal = cs * rng.choice([20, 30, 40])
                conv.append({"sku_id": sid, "uom": "PAL", "to_base_factor": str(pal)})
        else:
            value = Decimal(str(round(rng.uniform(0.40, 2.20), 2)))
            tol = Decimal("0.5")
            cs, pal = 25, None
            conv.append({"sku_id": sid, "uom": "BAG25", "to_base_factor": "25"})
            conv.append({"sku_id": sid, "uom": "LB", "to_base_factor": "0.45359237"})
        skus.append({"sku_id": sid, "description": nm, "base_uom": base, "category": cat,
                     "unit_value_cad_sim": dstr(value), "count_tolerance_base": dstr(tol),
                     "effective_from": "", "effective_to": ""})
        meta.append({"id": sid, "base": base, "cs": cs if base == "EA" else None, "pal": pal,
                     "bag": 25 if base == "KG" else None, "value": value,
                     "home": PICK_LOCS[idx % 3], "bulk": BULK_LOCS[idx % 2]})
    # two special master records used only by anomaly scenarios
    skus.append({"sku_id": "SKU-1041", "description": "Seasonal Ice Melt Scoop (new item)", "base_uom": "EA",
                 "category": "CLEANING", "unit_value_cad_sim": "7.5", "count_tolerance_base": "0",
                 "effective_from": "2026-02-11T12:00:00-07:00", "effective_to": ""})
    skus.append({"sku_id": "SKU-1042", "description": "Discontinued Work Gloves Small (retired)", "base_uom": "EA",
                 "category": "SAFETY", "unit_value_cad_sim": "4.25", "count_tolerance_base": "0",
                 "effective_from": "", "effective_to": "2026-02-10T00:00:00-07:00"})
    conv.append({"sku_id": "SKU-1041", "uom": "CS", "to_base_factor": "12"})
    conv.append({"sku_id": "SKU-1042", "uom": "CS", "to_base_factor": "10"})
    locs = [
        {"location_id": "RCV-01", "zone": "RECEIVING", "active_flag": "Y", "effective_from": "", "effective_to": ""},
        {"location_id": "BLK-01", "zone": "BULK", "active_flag": "Y", "effective_from": "", "effective_to": ""},
        {"location_id": "BLK-02", "zone": "BULK", "active_flag": "Y", "effective_from": "", "effective_to": ""},
        {"location_id": "PK-A01", "zone": "PICK-A", "active_flag": "Y", "effective_from": "", "effective_to": ""},
        {"location_id": "PK-A02", "zone": "PICK-A", "active_flag": "Y", "effective_from": "", "effective_to": ""},
        {"location_id": "PK-B01", "zone": "PICK-B", "active_flag": "Y", "effective_from": "", "effective_to": ""},
        {"location_id": "SHP-01", "zone": "SHIPPING", "active_flag": "Y", "effective_from": "", "effective_to": ""},
        {"location_id": "PK-Z99", "zone": "PICK-Z", "active_flag": "N", "effective_from": "",
         "effective_to": "2026-01-15T00:00:00-07:00"},
        {"location_id": "BLK-03", "zone": "BULK", "active_flag": "N", "effective_from": "2026-02-15T00:00:00-07:00",
         "effective_to": ""},
    ]
    return skus, conv, locs, meta


def default_params() -> List[dict]:
    return [
        {"key": "snapshot_ts", "value": f_local(SNAPSHOT), "description": "Extract time; later records are invalid; shifts ending after it are open"},
        {"key": "timezone_note", "value": "UTC-07:00 fixed (no DST change in window)", "description": "Local display only; comparisons use UTC"},
        {"key": "match_qty_tolerance_base", "value": "0.005", "description": "Event vs posting quantity tolerance in base UoM"},
        {"key": "possible_match_window_minutes", "value": "60", "description": "Max |posting - event| time for a POSSIBLE_MATCH"},
        {"key": "clock_skew_tolerance_minutes", "value": "5", "description": "A posting earlier than its event by more than this is R10 POSTED_BEFORE_EVENT"},
        {"key": "late_low_max_minutes", "value": "60", "description": "Late posting up to this many minutes after cutoff is LOW"},
        {"key": "handoff_medium_aging_shifts", "value": "2", "description": "Handoff aging (closed shifts) for MEDIUM"},
        {"key": "handoff_high_aging_shifts", "value": "4", "description": "Handoff aging (closed shifts) for HIGH"},
        {"key": "value_high_cad", "value": "500", "description": "Simulated CAD value at/above which value-driven rules are HIGH"},
        {"key": "value_medium_cad", "value": "100", "description": "Simulated CAD value at/above which LOW is raised to MEDIUM"},
    ]


# ---------------------------------------------------------------- world
class World:
    """Holds clean events/postings (internal form) plus a separate truth ledger."""

    def __init__(self, seed: int = SEED):
        self.rng = random.Random(seed)
        self.seed = seed
        self.skus, self.conv, self.locs, self.meta = build_masters(self.rng)
        self.shifts = build_shifts()
        self.sku_meta = {m["id"]: m for m in self.meta}
        self.factor = {}
        for c in self.conv:
            self.factor[(c["sku_id"], c["uom"])] = Decimal(c["to_base_factor"])
        weights = list(range(1, len(self.meta) + 1))
        self.rng.shuffle(weights)
        self.weight = {m["id"]: 1.0 / (w ** 0.75) for m, w in zip(self.meta, weights)}
        self.opening: List[dict] = []
        self.bal: Dict[Tuple[str, str], Decimal] = {}
        self.events: List[dict] = []
        self.postings: List[dict] = []
        self._ek = 0
        self._pk = 0
        self.truth_extra: List[Tuple[datetime, str, str, Decimal]] = []
        self._build_opening()

    # -- helpers
    def to_base(self, sku: str, uom: str, raw: Decimal) -> Decimal:
        if uom == self.sku_meta[sku]["base"]:
            return q3(raw)
        return q3(raw * self.factor[(sku, uom)])

    def _build_opening(self) -> None:
        for m in self.meta:
            if m["base"] == "EA":
                pk, bk = self.rng.randint(150, 450), self.rng.randint(600, 2400)
            else:
                pk, bk = self.rng.randint(400, 1200), self.rng.randint(1500, 4000)
            for loc, q in ((m["home"], pk), (m["bulk"], bk)):
                self.bal[(m["id"], loc)] = Decimal(q)
                self.opening.append({"timestamp": f_local(T0), "sku_id": m["id"], "location_id": loc,
                                     "quantity_in_base_uom": str(q)})

    def new_event(self, ts: datetime, etype: str, sku: str, raw: Decimal, uom: str, frm: str, to: str,
                  status: str = "completed", related_key: Optional[int] = None, handoff_to: str = "",
                  handoff_status: str = "", fmt: str = "local", apply: bool = True) -> dict:
        self._ek += 1
        base = self.to_base(sku, uom, raw)
        ev = {"key": self._ek, "ts": ts, "type": etype, "sku": sku, "raw": raw, "uom": uom, "from": frm, "to": to,
              "status": status, "related_key": related_key, "handoff_to": handoff_to,
              "handoff_status": handoff_status, "fmt": fmt, "base": base}
        self.events.append(ev)
        if apply and status == "completed":
            if frm:
                self.bal[(sku, frm)] = self.bal.get((sku, frm), Decimal(0)) - base
            if to:
                self.bal[(sku, to)] = self.bal.get((sku, to), Decimal(0)) + base
        return ev

    def new_post(self, ev: Optional[dict], ts: datetime, ptype: str, sku: str, raw: Decimal, uom: str, frm: str,
                 to: str, eff: Optional[datetime] = None, status: str = "POSTED", related_pkey: Optional[int] = None) -> dict:
        self._pk += 1
        p = {"key": self._pk, "event_key": ev["key"] if ev else None, "ts": ts, "type": ptype, "sku": sku,
             "raw": raw, "uom": uom, "from": frm, "to": to, "eff": eff, "status": status, "related_pkey": related_pkey}
        self.postings.append(p)
        return p

    def post_standard(self, ev: dict, lags: Tuple[int, int], eff: bool = False, as_event_uom: Optional[bool] = None) -> List[dict]:
        """Create the normal posting(s) for a completed event. Lags in seconds."""
        rng = self.rng
        sku, base = ev["sku"], self.sku_meta[ev["sku"]]["base"]
        if as_event_uom is None:
            as_event_uom = rng.random() < 0.3
        if as_event_uom or ev["uom"] == base:
            raw, uom = ev["raw"], ev["uom"]
        else:
            raw, uom = ev["base"], base
        effts = ev["ts"] if (eff or rng.random() < 0.2) else None
        t = ev["type"]
        out = []
        if t == "receipt":
            out.append(self.new_post(ev, ev["ts"] + timedelta(seconds=lags[0]), "RECEIPT", sku, raw, uom, "", ev["to"], effts))
        elif t == "pick":
            out.append(self.new_post(ev, ev["ts"] + timedelta(seconds=lags[0]), "ISSUE", sku, raw, uom, ev["from"], "", effts))
        elif t == "transfer":
            out.append(self.new_post(ev, ev["ts"] + timedelta(seconds=lags[0]), "TRANSFER_OUT", sku, raw, uom, ev["from"], ev["to"], effts))
            out.append(self.new_post(ev, ev["ts"] + timedelta(seconds=lags[1]), "TRANSFER_IN", sku, raw, uom, ev["from"], ev["to"], effts))
        return out

    # -- truth (independent of engine)
    def truth(self, sku: str, loc: str, t: datetime) -> Decimal:
        total = Decimal(0)
        for o in self.opening:
            if o["sku_id"] == sku and o["location_id"] == loc:
                total += Decimal(o["quantity_in_base_uom"])
        for e in self.events:
            if e["status"] == "completed" and e["ts"] <= t and e["sku"] == sku:
                if e["to"] == loc:
                    total += e["base"]
                if e["from"] == loc:
                    total -= e["base"]
        for ts, sk, lc, d in self.truth_extra:
            if sk == sku and lc == loc and ts <= t:
                total += d
        return total

    # -- flow
    def pick_sku(self, pred) -> Optional[str]:
        cands = [m["id"] for m in self.meta if pred(m["id"])]
        if not cands:
            return None
        w = [self.weight[c] for c in cands]
        return self.rng.choices(cands, weights=w, k=1)[0]

    def run_flow(self) -> None:
        rng = self.rng
        shifts = build_shifts()
        from .common import parse_ts
        for si, sh in enumerate(shifts):
            start = parse_ts(sh["start_ts"]).astimezone(LOCAL)
            end = parse_ts(sh["end_ts"]).astimezone(LOCAL)
            open_shift = end > SNAPSHOT
            last = (SNAPSHOT - timedelta(minutes=35)) if open_shift else (end - timedelta(minutes=35))
            n = 17 if open_shift else rng.randint(62, 72)
            span = int((last - start).total_seconds())
            secs = sorted(rng.sample(range(0, span), n))
            if si in (5, 7):
                secs[0] = 0  # event exactly at shift start (belongs to the new shift)
            for k, s in enumerate(secs):
                ts = start + timedelta(seconds=s)
                self._slot(si, k, ts, open_shift)
            if si == 4:
                self._post_at_cutoff(end)
            if si == 8:
                self._handoff_events(si, shifts, end)

    def _lag(self) -> Tuple[int, int]:
        a = self.rng.randint(60, 1500)
        return a, a + self.rng.randint(0, 240)

    def _slot(self, si: int, k: int, ts: datetime, open_shift: bool) -> None:
        rng = self.rng
        special = {(1, 8): "twin", (2, 12): "revchain", (3, 6): "repost", (3, 15): "lb", (6, 9): "lb2",
                   (6, 5): "effedge"}.get((si, k))
        if special and self._special(special, si, ts):
            return
        kind = rng.choices(["pick", "putaway", "replen", "receipt"], weights=[60, 13, 10, 12], k=1)[0]
        for _ in range(12):
            if self._try_kind(kind, ts):
                return
            kind = rng.choice(["pick", "pick", "receipt"])

    def _fmt(self) -> str:
        return "z" if self.rng.random() < 0.10 else "local"

    def _try_kind(self, kind: str, ts: datetime) -> bool:
        rng = self.rng
        if kind == "receipt":
            sku = self.pick_sku(lambda s: True)
            m = self.sku_meta[sku]
            if m["base"] == "KG":
                uom, raw = "BAG25", Decimal(rng.randint(6, 30))
            elif m["pal"] and rng.random() < 0.25:
                uom, raw = "PAL", Decimal(rng.randint(1, 2))
            else:
                uom, raw = "CS", Decimal(rng.randint(2, 14))
            ev = self.new_event(ts, "receipt", sku, raw, uom, "", RCV, fmt=self._fmt())
            self.post_standard(ev, self._lag())
            return True
        if kind == "putaway":
            sku = self.pick_sku(lambda s: self.bal.get((s, RCV), 0) > 0)
            if not sku:
                return False
            m = self.sku_meta[sku]
            have = self.bal[(sku, RCV)]
            qty = have if (m["base"] == "KG" or rng.random() < 0.6) else max(Decimal(1), (have * Decimal("0.5")).to_integral_value(rounding="ROUND_FLOOR"))
            dest = m["bulk"] if qty >= (200 if m["base"] == "EA" else 400) else m["home"]
            uom, raw = m["base"], qty
            if m["base"] == "EA" and qty % m["cs"] == 0 and rng.random() < 0.5:
                uom, raw = "CS", qty / Decimal(m["cs"])
            elif m["base"] == "KG" and qty % 25 == 0 and rng.random() < 0.5:
                uom, raw = "BAG25", qty / Decimal(25)
            ev = self.new_event(ts, "transfer", sku, raw, uom, RCV, dest, fmt=self._fmt())
            self.post_standard(ev, self._lag())
            return True
        if kind == "replen":
            sku = self.pick_sku(lambda s: self.bal.get((s, self.sku_meta[s]["home"]), 0) < (160 if self.sku_meta[s]["base"] == "EA" else 400)
                                and self.bal.get((s, self.sku_meta[s]["bulk"]), 0) > 100)
            if not sku:
                return False
            m = self.sku_meta[sku]
            qty = min(self.bal[(sku, m["bulk"])], Decimal(rng.randint(150, 400) if m["base"] == "EA" else rng.randint(400, 900)))
            ev = self.new_event(ts, "transfer", sku, qty, m["base"], m["bulk"], m["home"], fmt=self._fmt())
            self.post_standard(ev, self._lag())
            return True
        # pick
        sku = self.pick_sku(lambda s: self.bal.get((s, self.sku_meta[s]["home"]), 0) >= 40)
        if not sku:
            return False
        m = self.sku_meta[sku]
        home = m["home"]
        if m["base"] == "KG":
            if rng.random() < 0.35:
                uom, raw = "LB", Decimal(rng.randint(12, 120))
            else:
                uom, raw = "KG", Decimal(rng.randint(5, 60))
        elif m["cs"] and rng.random() < 0.2 and self.bal[(sku, home)] >= m["cs"] * 3:
            uom, raw = "CS", Decimal(rng.randint(1, 3))
        else:
            uom, raw = "EA", Decimal(rng.randint(1, 36))
        if self.to_base(sku, uom, raw) > self.bal[(sku, home)]:
            return False
        ev = self.new_event(ts, "pick", sku, raw, uom, home, "", fmt=self._fmt())
        self.post_standard(ev, self._lag())
        return True

    # -- scripted benign patterns (must not raise exceptions)
    def _special(self, kind: str, si: int, ts: datetime) -> bool:
        rng = self.rng
        if kind == "twin":
            sku = self.pick_sku(lambda s: self.sku_meta[s]["base"] == "EA" and self.bal.get((s, self.sku_meta[s]["home"]), 0) >= 40)
            m = self.sku_meta[sku]
            evs = [self.new_event(ts, "pick", sku, Decimal(5), "EA", m["home"], "") for _ in range(2)]
            pts = ts + timedelta(seconds=240)
            for ev in evs:
                self.new_post(ev, pts, "ISSUE", sku, Decimal(5), "EA", m["home"], "")
            return True
        if kind == "revchain":
            sku = self.pick_sku(lambda s: self.sku_meta[s]["base"] == "EA" and self.sku_meta[s]["cs"])
            m = self.sku_meta[sku]
            r = self.new_event(ts, "receipt", sku, Decimal(3), "CS", "", RCV)
            rp = self.new_post(r, ts + timedelta(seconds=120), "RECEIPT", sku, Decimal(3), "CS", "", RCV)
            rv = self.new_event(ts + timedelta(seconds=60), "reversal", sku, Decimal(3), "CS", RCV, "", related_key=r["key"])
            self.new_post(rv, ts + timedelta(seconds=300), "REVERSAL", sku, Decimal(3), "CS", RCV, "", related_pkey=rp["key"])
            return True
        if kind == "repost":
            sku = self.pick_sku(lambda s: self.sku_meta[s]["base"] == "EA" and self.bal.get((s, self.sku_meta[s]["home"]), 0) >= 40)
            m = self.sku_meta[sku]
            ev = self.new_event(ts, "pick", sku, Decimal(7), "EA", m["home"], "")
            p1 = self.new_post(ev, ts + timedelta(seconds=180), "ISSUE", sku, Decimal(7), "EA", m["home"], "")
            self.new_post(ev, ts + timedelta(seconds=360), "REVERSAL", sku, Decimal(7), "EA", "", m["home"], related_pkey=p1["key"])
            self.new_post(ev, ts + timedelta(seconds=540), "ISSUE", sku, Decimal(7), "EA", m["home"], "")
            return True
        if kind == "lb":
            kgs = [m["id"] for m in self.meta if m["base"] == "KG" and self.bal.get((m["id"], m["home"]), 0) > 100]
            sku = kgs[0]
            m = self.sku_meta[sku]
            ev = self.new_event(ts, "pick", sku, Decimal(10), "LB", m["home"], "")
            self.new_post(ev, ts + timedelta(seconds=200), "ISSUE", sku, Decimal(10), "LB", m["home"], "")
            return True
        if kind == "lb2":
            kgs = [m["id"] for m in self.meta if m["base"] == "KG" and self.bal.get((m["id"], m["home"]), 0) > 100]
            sku = kgs[-1]
            m = self.sku_meta[sku]
            ev = self.new_event(ts, "pick", sku, Decimal(10), "LB", m["home"], "")
            self.new_post(ev, ts + timedelta(seconds=200), "ISSUE", sku, Decimal("4.5359"), "KG", m["home"], "")
            return True
        if kind == "effedge":
            sku = self.pick_sku(lambda s: self.sku_meta[s]["base"] == "EA" and self.bal.get((s, self.sku_meta[s]["home"]), 0) >= 40)
            m = self.sku_meta[sku]
            ev = self.new_event(ts, "pick", sku, Decimal(4), "EA", m["home"], "")
            from .common import parse_ts
            sh = [s for s in self.shifts if parse_ts(s["start_ts"]) <= ts < parse_ts(s["end_ts"])][0]
            end = parse_ts(sh["end_ts"])
            self.new_post(ev, ts + timedelta(seconds=200), "ISSUE", sku, Decimal(4), "EA", m["home"], "",
                          eff=end - timedelta(seconds=1))
            return True
        return False

    def _post_at_cutoff(self, end: datetime) -> None:
        sku = self.pick_sku(lambda s: self.sku_meta[s]["base"] == "EA" and self.bal.get((s, self.sku_meta[s]["home"]), 0) >= 40)
        m = self.sku_meta[sku]
        ts = end - timedelta(seconds=600)
        ev = self.new_event(ts, "pick", sku, Decimal(6), "EA", m["home"], "")
        self.new_post(ev, end, "ISSUE", sku, Decimal(6), "EA", m["home"], "")

    def _handoff_events(self, si: int, shifts: List[dict], end: datetime) -> None:
        rng = self.rng
        open_id = shifts[9]["shift_id"]
        start = end - timedelta(hours=8)
        for j in range(2):  # pending handoffs to the still-open shift: legitimate, not due
            sku = self.pick_sku(lambda s: self.sku_meta[s]["base"] == "EA")
            m = self.sku_meta[sku]
            self.new_event(start + timedelta(hours=7, minutes=10 + j * 7), "pick", sku, Decimal(rng.randint(8, 20)), "EA",
                           m["home"], "", status="pending", handoff_to=open_id, handoff_status="OPEN", apply=False)
        for j in range(2):  # cancelled, never posted
            sku = self.pick_sku(lambda s: self.sku_meta[s]["base"] == "EA")
            m = self.sku_meta[sku]
            self.new_event(start + timedelta(hours=3, minutes=20 + j * 11), "pick", sku, Decimal(rng.randint(2, 9)), "EA",
                           m["home"], "", status="cancelled", apply=False)

    def mark_closed_handoffs(self) -> None:
        """A few completed events carry a handoff that was closed properly (benign)."""
        shifts = build_shifts()
        done = 0
        for e in self.events:
            if e["status"] == "completed" and e["type"] == "pick" and e["handoff_to"] == "" and e["key"] % 97 == 0 and done < 3:
                from .common import parse_ts
                for i, s in enumerate(shifts[:-1]):
                    if parse_ts(s["start_ts"]) <= e["ts"] < parse_ts(s["end_ts"]):
                        e["handoff_to"] = shifts[i + 1]["shift_id"]
                        e["handoff_status"] = "CLOSED"
                        done += 1
                        break


# ---------------------------------------------------------------- rows
def _ts_str(dt: datetime, fmt: str) -> str:
    return f_utc(dt) if fmt == "z" else f_local(dt)


def world_rows(w: World) -> Tuple[List[dict], List[dict]]:
    from .common import parse_ts
    shifts = w.shifts
    evs = sorted(w.events, key=lambda e: (e["ts"], e["key"]))
    id_of = {}
    for i, e in enumerate(evs, start=1):
        id_of[e["key"]] = "EV-%06d" % i
    ps = sorted(w.postings, key=lambda p: (p["ts"], p["key"]))
    pid = {}
    for i, p in enumerate(ps, start=1):
        pid[p["key"]] = "PO-%06d" % i
    ev_rows = []
    for e in evs:
        sh = [s for s in shifts if parse_ts(s["start_ts"]) <= e["ts"] < parse_ts(s["end_ts"])][0]
        ev_rows.append({
            "event_id": id_of[e["key"]], "event_timestamp": _ts_str(e["ts"], e["fmt"]), "shift_id": sh["shift_id"],
            "event_type": e["type"], "sku_id": e["sku"], "quantity": dstr(e["raw"]), "uom": e["uom"],
            "from_location": e["from"], "to_location": e["to"], "status": e["status"],
            "related_id": id_of[e["related_key"]] if e["related_key"] else "", "handoff_to_shift": e["handoff_to"],
            "handoff_status": e["handoff_status"]})
    po_rows = []
    for p in ps:
        po_rows.append({
            "posting_id": pid[p["key"]], "source_event_id": id_of[p["event_key"]] if p["event_key"] else "",
            "system_post_timestamp": f_utc(p["ts"]), "event_effective_timestamp": f_utc(p["eff"]) if p["eff"] else "",
            "posting_type": p["type"], "sku_id": p["sku"], "quantity": dstr(p["raw"]), "uom": p["uom"],
            "from_location": p["from"], "to_location": p["to"], "posting_status": p["status"],
            "related_posting_id": pid[p["related_pkey"]] if p["related_pkey"] else ""})
    return ev_rows, po_rows


def make_counts(w: World, ev_rows: List[dict], rng: random.Random, avoid: set, extra_truth_ok: bool = True) -> List[dict]:
    """Counts at each closed shift cutoff plus three in-flight mid-shift counts. Counted value = truth."""
    from .common import parse_ts
    rows: List[dict] = []
    n = 0
    shifts = w.shifts
    closed = [s for s in shifts if parse_ts(s["end_ts"]) <= SNAPSHOT]
    pairs = sorted({(o["sku_id"], o["location_id"]) for o in w.opening} |
                   {(e["sku"], l) for e in w.events for l in (e["from"], e["to"]) if l and e["status"] == "completed"})
    base_of = {m["id"]: m["base"] for m in w.meta}
    cs_of = {m["id"]: (m["cs"] or 0) for m in w.meta}

    def add(ts: datetime, sku: str, loc: str, qty: Decimal, status: str = "COMPLETE", fmt: str = "local", uom: Optional[str] = None):
        nonlocal n
        n += 1
        u = uom or base_of[sku]
        raw = qty
        if uom == "CS":
            raw = qty / Decimal(cs_of[sku])
        rows.append({"count_id": "CC-%04d" % n, "observed_at": _ts_str(ts, fmt), "sku_id": sku, "location_id": loc,
                     "counted_qty": dstr(raw) if status != "VOID" or qty else "", "uom": u, "count_status": status,
                     "evidence_ref": "CC-SHEET-%03d line %d" % (1 + (n - 1) // 12, 1 + (n - 1) % 12)})

    for si, sh in enumerate(closed):
        cut = parse_ts(sh["end_ts"])
        pool = [pr for pr in pairs if pr not in avoid and pr[1] not in ("SHP-01",)]
        rng.shuffle(pool)
        picked = 0
        zero_done = False
        for sku, loc in pool:
            tq = w.truth(sku, loc, cut)
            if tq == 0 and not zero_done and loc == RCV:
                add(cut, sku, loc, Decimal(0), "COMPLETE")
                zero_done = True
                picked += 1
            elif tq > 0:
                st = "COMPLETE"
                r = rng.random()
                if r < 0.07:
                    st = "PARTIAL"
                uom = None
                if cs_of[sku] and base_of[sku] == "EA" and tq % cs_of[sku] == 0 and rng.random() < 0.3:
                    uom = "CS"
                if st == "PARTIAL":
                    add(cut, sku, loc, (tq * Decimal("0.6")).to_integral_value(rounding="ROUND_FLOOR"), "PARTIAL", "local")
                else:
                    add(cut, sku, loc, tq, st, "z" if rng.random() < 0.15 else "local", uom)
                picked += 1
            if picked >= 13:
                break
        if si in (2, 6):
            sku, loc = next(((s, l) for s, l in pool if w.truth(s, l, cut) > 0), (None, None))
            if sku:
                add(cut, sku, loc, Decimal(0), "VOID")
    # in-flight counts: a pick posted >= 6 min after the count time, counted 2 min after the pick
    done = 0
    for e in sorted(w.events, key=lambda x: (x["ts"], x["key"])):
        if done >= 3 or e["type"] != "pick" or e["status"] != "completed":
            continue
        ps = [p for p in w.postings if p["event_key"] == e["key"]]
        if not ps or (e["sku"], e["from"]) in avoid:
            continue
        if min(p["ts"] for p in ps) - e["ts"] >= timedelta(minutes=8) and e["ts"].day in (9, 10, 11) and e["key"] % 11 == 0:
            ct = e["ts"] + timedelta(seconds=120)
            if ct < min(p["ts"] for p in ps) and ct <= SNAPSHOT - timedelta(hours=1):
                # make sure no other event/posting for this sku-loc lands within the window
                clash = any(o is not e and o["sku"] == e["sku"] and (o["from"] == e["from"] or o["to"] == e["from"]) and
                            e["ts"] - timedelta(minutes=30) <= o["ts"] <= ct + timedelta(minutes=30) for o in w.events)
                clash = clash or any(p["sku"] == e["sku"] and (p["from"] == e["from"] or p["to"] == e["from"]) and
                                     p["event_key"] != e["key"] and ct - timedelta(minutes=30) <= p["ts"] <= ct + timedelta(minutes=30)
                                     for p in w.postings)
                if not clash:
                    add(ct, e["sku"], e["from"], w.truth(e["sku"], e["from"], ct), "COMPLETE")
                    done += 1
    return rows


def build_clean_world(seed: int = SEED) -> World:
    w = World(seed)
    w.run_flow()
    w.mark_closed_handoffs()
    return w


def write_scenario(path: str, w: World, ev_rows, po_rows, count_rows, params: Optional[List[dict]] = None) -> None:
    os.makedirs(path, exist_ok=True)
    write_csv(os.path.join(path, "sku_master.csv"), SCHEMAS["sku_master.csv"], w.skus)
    write_csv(os.path.join(path, "location_master.csv"), SCHEMAS["location_master.csv"], w.locs)
    write_csv(os.path.join(path, "uom_conversions.csv"), SCHEMAS["uom_conversions.csv"], w.conv)
    write_csv(os.path.join(path, "opening_balances.csv"), SCHEMAS["opening_balances.csv"], w.opening)
    write_csv(os.path.join(path, "business_events.csv"), SCHEMAS["business_events.csv"], ev_rows)
    write_csv(os.path.join(path, "system_postings.csv"), SCHEMAS["system_postings.csv"], po_rows)
    write_csv(os.path.join(path, "physical_counts.csv"), SCHEMAS["physical_counts.csv"], count_rows)
    write_csv(os.path.join(path, "shifts.csv"), SCHEMAS["shifts.csv"], w.shifts)
    write_csv(os.path.join(path, "rule_parameters.csv"), SCHEMAS["rule_parameters.csv"], params or default_params())


def generate_clean(path: str, seed: int = SEED) -> World:
    w = build_clean_world(seed)
    ev_rows, po_rows = world_rows(w)
    counts = make_counts(w, ev_rows, random.Random(seed + 7), set())
    write_scenario(path, w, ev_rows, po_rows, counts)
    return w

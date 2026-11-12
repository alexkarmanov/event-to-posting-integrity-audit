"""Plain data classes used across the engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class Shift:
    shift_id: str
    name: str
    start: datetime   # UTC, inclusive
    end: datetime     # UTC, exclusive == cutoff


@dataclass
class NEvent:
    id: str
    row: int
    ts: datetime
    type: str
    sku: str
    qty: Decimal          # base UoM
    qty_raw: Decimal
    uom: str
    from_loc: str
    to_loc: str
    status: str
    related_id: str
    handoff_to: str
    handoff_status: str
    shift_label: str
    shift: Shift

    def effects(self) -> List[Tuple[str, Decimal]]:
        out = []
        if self.to_loc:
            out.append((self.to_loc, self.qty))
        if self.from_loc:
            out.append((self.from_loc, -self.qty))
        return out


@dataclass
class NPosting:
    id: str
    row: int
    source: str
    post_ts: datetime
    eff_ts: Optional[datetime]
    type: str
    sku: str
    qty: Decimal          # base UoM
    qty_raw: Decimal
    uom: str
    from_loc: str
    to_loc: str
    status: str
    related: str

    def effects(self) -> List[Tuple[str, Decimal]]:
        t = self.type
        if t in ("RECEIPT", "TRANSFER_IN"):
            return [(self.to_loc, self.qty)]
        if t in ("ISSUE", "TRANSFER_OUT"):
            return [(self.from_loc, -self.qty)]
        out = []  # ADJUSTMENT / REVERSAL: whichever side is present
        if self.to_loc:
            out.append((self.to_loc, self.qty))
        if self.from_loc:
            out.append((self.from_loc, -self.qty))
        return out


@dataclass
class NCount:
    id: str
    row: int
    observed: datetime
    sku: str
    loc: str
    qty: Decimal          # base UoM
    qty_raw: Decimal
    uom: str
    status: str
    evidence_ref: str


@dataclass
class Issue:
    rule_id: str
    subtype: str
    reason: str
    case_key: str
    key_id: str                                   # primary record id (event/posting/count/SKU|LOC)
    sku_id: str = ""
    location_id: str = ""
    shift_id: str = ""
    ref_ts: Optional[datetime] = None
    sources: List[Tuple[str, str, int]] = field(default_factory=list)   # (file, record_id, row)
    observed: str = ""
    expected: str = ""
    qty_diff: Optional[Decimal] = None            # signed, base UoM (observed - expected)
    severity: str = ""
    forced_severity: str = ""
    hypothesis: str = ""
    aging_shifts: int = 0
    related: Set[str] = field(default_factory=set)
    evidence: str = ""
    # filled by finalize
    exception_id: str = ""
    is_primary: bool = True
    value_cad_sim: Decimal = Decimal("0")
    priority_score: int = 0
    priority_rank: int = 0
    owner_role: str = ""
    review_action: str = ""
    status: str = "OPEN"
    extra: Dict[str, str] = field(default_factory=dict)

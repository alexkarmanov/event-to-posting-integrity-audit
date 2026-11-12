"""Time, quantity and formatting helpers shared by the engine (not by the SQL cross-check)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

UTC = timezone.utc
LOCAL_OFFSET = timezone(timedelta(hours=-7))  # display only; data window has no DST change
Q3 = Decimal("0.001")
ZERO = Decimal("0")


def parse_ts(value: Optional[str]) -> datetime:
    """Parse an ISO-8601 timestamp that MUST carry an explicit offset; return aware UTC."""
    if value is None or str(value).strip() == "":
        raise ValueError("missing timestamp")
    s = str(value).strip()
    if s[-1] in "zZ":
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError("unparseable timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp lacks timezone offset")
    return dt.astimezone(UTC)


def fmt_utc(dt: Optional[datetime]) -> str:
    return "" if dt is None else dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_local(dt: Optional[datetime]) -> str:
    return "" if dt is None else dt.astimezone(LOCAL_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


def fmt_local_iso(dt: datetime) -> str:
    return dt.astimezone(LOCAL_OFFSET).strftime("%Y-%m-%dT%H:%M:%S-07:00")


def parse_qty(value: Optional[str]) -> Decimal:
    """Strict decimal parse: no commas, no units, no NaN/Infinity."""
    if value is None:
        raise ValueError("missing quantity")
    s = str(value).strip()
    if s == "":
        raise ValueError("missing quantity")
    if not all(ch in "0123456789.-" for ch in s):
        raise ValueError("non-numeric quantity")
    try:
        d = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError("non-numeric quantity") from exc
    if not d.is_finite():
        raise ValueError("non-finite quantity")
    return d


def q3(d: Decimal) -> Decimal:
    return d.quantize(Q3, rounding=ROUND_HALF_UP)


def fmt_dec(d: Optional[Decimal]) -> str:
    if d is None:
        return ""
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "") else s


def sev_points(sev: str) -> int:
    return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(sev, 0)


FAMILY = {"R05": "STOCK_IMPACT", "R06": "STOCK_IMPACT", "R07": "STOCK_IMPACT", "R08": "STOCK_IMPACT", "R09": "STOCK_IMPACT",
          "R12": "STOCK_IMPACT", "R13": "STOCK_IMPACT", "R10": "TIMING_PROCESS", "R11": "TIMING_PROCESS",
          "R01": "DATA_QUALITY", "R02": "DATA_QUALITY", "R03": "DATA_QUALITY", "R04": "DATA_QUALITY", "R14": "DATA_QUALITY"}


def family(rule_id: str) -> str:
    """STOCK_IMPACT = may change what the system says is on hand; TIMING_PROCESS = timing/handoff; DATA_QUALITY = bad records."""
    return FAMILY[rule_id]


SHORT_RULE_NAMES = {
    "R01": "Invalid or missing field", "R02": "Unknown / inactive reference", "R03": "Unit of measure", "R04": "Duplicate ID",
    "R05": "Duplicate posting", "R06": "Event never posted", "R07": "Posting without event", "R08": "Quantity mismatch",
    "R09": "SKU / bin / leg mismatch", "R10": "Late posting (timing)", "R11": "Handoff unresolved",
    "R12": "Unexplained count variance", "R13": "Negative balance", "R14": "Reversal reference",
}


def headline(rule_id: str, subtype: str, key_id: str, sku_id: str, value) -> str:
    """One-line description for print: '<rule> <subtype> | <record> | <sku> | $value' (about 60 characters)."""
    key = key_id.replace("|", "/") if "|" in key_id else key_id
    parts = ["%s %s" % (rule_id, subtype.replace("_", " ").lower()), key]
    if sku_id and sku_id not in key:
        parts.append(sku_id)
    if value and float(value) > 0:
        parts.append("$%s" % format(int(round(float(value))), ","))
    return " · ".join(parts)

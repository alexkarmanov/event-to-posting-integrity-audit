"""Unit tests for time, quantity and shift primitives."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from stocktrace.common import fmt_utc, parse_qty, parse_ts, q3
from stocktrace.validate import build_master


def test_parse_ts_requires_offset():
    with pytest.raises(ValueError):
        parse_ts("2026-02-10T09:30:00")
    with pytest.raises(ValueError):
        parse_ts("")


def test_parse_ts_normalises_offsets_to_utc():
    a = parse_ts("2026-02-10T06:00:00-07:00")
    b = parse_ts("2026-02-10T13:00:00Z")
    assert a == b == datetime(2026, 2, 10, 13, 0, tzinfo=timezone.utc)
    assert fmt_utc(a) == "2026-02-10T13:00:00Z"


@pytest.mark.parametrize("bad", ["", "  ", "12x", "1,000", "NaN", "Infinity", "1e3", "abc"])
def test_parse_qty_rejects_non_numeric(bad):
    with pytest.raises(ValueError):
        parse_qty(bad)


def test_parse_qty_zero_is_valid_not_missing():
    assert parse_qty("0") == Decimal("0")
    assert parse_qty("0.0") == Decimal("0.0")


@pytest.mark.parametrize("raw,exp", [("4.5359", "4.536"), ("4.5355", "4.536"), ("4.5354", "4.535"), ("0.0005", "0.001")])
def test_q3_rounds_half_up(raw, exp):
    assert q3(Decimal(raw)) == Decimal(exp)


def test_shift_boundaries_are_half_open(ds_boundary):
    m = build_master(ds_boundary)
    end_d = parse_ts("2026-03-02T14:00:00-07:00")
    assert m.shift_of(end_d).shift_id == "2026-03-02-E"                 # exactly at the cutoff belongs to the next shift
    assert m.shift_of(parse_ts("2026-03-02T13:59:59-07:00")).shift_id == "2026-03-02-D"
    assert m.shift_of(parse_ts("2026-03-02T05:59:59-07:00")) is None    # before the calendar


def test_uom_conversion_is_explicit_only(ds_boundary):
    m = build_master(ds_boundary)
    assert m.to_base("SKU-B1", "CS", Decimal("2")) == Decimal("24.000")
    assert m.to_base("SKU-B2", "LB", Decimal("10")) == Decimal("4.536")
    assert m.to_base("SKU-B1", "DZ", Decimal("1")) is None              # no guessing
    assert m.to_base("SKU-B1", "LB", Decimal("1")) is None              # known code, but not defined for this SKU

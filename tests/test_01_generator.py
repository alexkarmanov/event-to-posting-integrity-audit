"""Deterministic generation, schema, size and label-free raw data."""
import csv
import hashlib
import os

from stocktrace import anomalies, generator
from stocktrace.schema import SCHEMAS

from conftest import path


def _hashes(folder):
    out = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".csv"):
            out[f] = hashlib.sha256(open(os.path.join(folder, f), "rb").read()).hexdigest()
    return out


def test_clean_regeneration_is_byte_identical(tmp_path):
    generator.generate_clean(str(tmp_path / "a"))
    generator.generate_clean(str(tmp_path / "b"))
    assert _hashes(str(tmp_path / "a")) == _hashes(str(tmp_path / "b")) == _hashes(path("data", "fixtures", "clean"))


def test_demo_regeneration_matches_committed_files(tmp_path):
    anomalies.generate_demo(str(tmp_path / "demo"), str(tmp_path / "m.json"))
    assert _hashes(str(tmp_path / "demo")) == _hashes(path("data", "raw", "demo"))
    assert open(str(tmp_path / "m.json")).read() == open(path("data", "fixtures", "demo_expected_anomalies.json")).read()


def test_raw_files_have_exact_schema_and_no_label_columns():
    for scen in (("data", "raw", "demo"), ("data", "fixtures", "clean"), ("data", "fixtures", "boundary")):
        for fname, cols in SCHEMAS.items():
            with open(path(*scen, fname), newline="") as fh:
                header = next(csv.reader(fh))
            assert header == cols, (scen, fname)
            assert not [c for c in header if "anomal" in c.lower() or "expected" in c.lower() or "label" in c.lower()]


def test_demo_size_matches_spec(ds_demo):
    assert 30 <= len(ds_demo.rows("sku_master.csv")) <= 60
    assert 5 <= len(ds_demo.rows("location_master.csv")) <= 12
    assert 500 <= len(ds_demo.rows("business_events.csv")) + len(ds_demo.rows("system_postings.csv")) <= 1500 * 2
    assert len(ds_demo.rows("shifts.csv")) >= 9                         # three shifts over at least two days
    assert len(ds_demo.rows("physical_counts.csv")) > 50


def test_manifest_is_outside_raw_dir_and_has_30_to_70_planted():
    import json
    m = json.load(open(path("data", "fixtures", "demo_expected_anomalies.json")))
    assert 30 <= len(m["planted"]) <= 70
    assert not os.path.exists(path("data", "raw", "demo", "demo_expected_anomalies.json"))
    assert {p["rule_id"] for p in m["planted"]} >= {"R%02d" % i for i in range(1, 15)}

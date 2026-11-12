"""CSV loading with schema checks. Rows keep their 1-based data-row number in `_row`."""
from __future__ import annotations

import csv
import re
import os
from dataclasses import dataclass, field
from typing import Dict, List

from .schema import SCHEMAS


class SchemaError(Exception):
    pass


@dataclass
class Dataset:
    name: str
    path: str
    tables: Dict[str, List[dict]] = field(default_factory=dict)

    def rows(self, fname: str) -> List[dict]:
        return self.tables[fname]

    @property
    def params(self) -> Dict[str, str]:
        return {r["key"]: r["value"] for r in self.tables["rule_parameters.csv"]}

    def clone(self) -> "Dataset":
        return Dataset(self.name, self.path,
                       {k: [dict(r) for r in v] for k, v in self.tables.items()})


def read_csv(path: str, expected_cols: List[str]) -> List[dict]:
    if not os.path.exists(path):
        raise SchemaError("missing required file: %s" % os.path.basename(path))
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = [c for c in expected_cols if c not in header]
        if missing:
            raise SchemaError("%s missing columns: %s" % (os.path.basename(path), ", ".join(missing)))
        forbidden = [c for c in header if c.lower() in ("expected_issue", "anomaly", "anomaly_type",
                                                          "is_anomaly", "ground_truth", "planted")]
        if forbidden:
            raise SchemaError("%s contains label-like columns: %s" % (os.path.basename(path), forbidden))
        rows = []
        for i, row in enumerate(reader, start=1):
            clean = {c: (row.get(c) or "").strip() for c in expected_cols}   # the only normalisation: surrounding whitespace
            clean["_row"] = i
            rows.append(clean)
    return rows


def load_dataset(path: str, name: str = "") -> Dataset:
    ds = Dataset(name or os.path.basename(os.path.normpath(path)), path)
    for fname, cols in SCHEMAS.items():
        ds.tables[fname] = read_csv(os.path.join(path, fname), cols)
    return ds


_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"^[+-]?[0-9]+(\.[0-9]+)?$")


def csv_safe(value):
    """Neutralise spreadsheet formula injection in one exported CSV cell.

    A text value that begins with = + - @ (or a tab/CR) can be executed as a formula when the file is opened in Excel
    or LibreOffice. Such values get a leading apostrophe, the usual mitigation. Plain numbers (for example -5 or +2.5)
    and non-text values are left alone, so ordinary output is unchanged. Use it for generated reports, never for the
    raw source files, which must stay exactly as supplied.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_LEAD) and not _PLAIN_NUMBER.match(value):
        return "'" + value
    return value


def write_csv(path: str, cols: List[str], rows: List[dict], safe: bool = False) -> None:
    """Write rows to CSV. safe=True neutralises formula-injection prefixes (see csv_safe); report outputs use it."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            vals = [r.get(c, "") for c in cols]
            w.writerow([csv_safe(v) for v in vals] if safe else vals)

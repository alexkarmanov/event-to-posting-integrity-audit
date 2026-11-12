"""Raw file schemas (single source for generator, loader and docs)."""
from __future__ import annotations

SCHEMAS = {
    "sku_master.csv": ["sku_id", "description", "base_uom", "category", "unit_value_cad_sim",
                       "count_tolerance_base", "effective_from", "effective_to"],
    "location_master.csv": ["location_id", "zone", "active_flag", "effective_from", "effective_to"],
    "uom_conversions.csv": ["sku_id", "uom", "to_base_factor"],
    "opening_balances.csv": ["timestamp", "sku_id", "location_id", "quantity_in_base_uom"],
    "business_events.csv": ["event_id", "event_timestamp", "shift_id", "event_type", "sku_id", "quantity",
                           "uom", "from_location", "to_location", "status", "related_id",
                           "handoff_to_shift", "handoff_status"],
    "system_postings.csv": ["posting_id", "source_event_id", "system_post_timestamp",
                            "event_effective_timestamp", "posting_type", "sku_id", "quantity", "uom",
                            "from_location", "to_location", "posting_status", "related_posting_id"],
    "physical_counts.csv": ["count_id", "observed_at", "sku_id", "location_id", "counted_qty", "uom",
                            "count_status", "evidence_ref"],
    "shifts.csv": ["shift_id", "shift_name", "start_ts", "end_ts"],
    "rule_parameters.csv": ["key", "value", "description"],
}

EVENT_TYPES = ("receipt", "pick", "transfer", "adjustment", "reversal")
EVENT_STATUSES = ("completed", "pending", "cancelled")
POSTING_TYPES = ("RECEIPT", "ISSUE", "TRANSFER_OUT", "TRANSFER_IN", "ADJUSTMENT", "REVERSAL")
POSTING_STATUSES = ("POSTED", "FAILED")
COUNT_STATUSES = ("COMPLETE", "PARTIAL", "VOID")
HANDOFF_STATUSES = ("", "OPEN", "CLOSED")
EXPECTED_POSTINGS = {
    "receipt": ("RECEIPT",),
    "pick": ("ISSUE",),
    "transfer": ("TRANSFER_OUT", "TRANSFER_IN"),
    "adjustment": ("ADJUSTMENT",),
    "reversal": ("REVERSAL",),
}

# Primary-issue precedence inside one case (lower number wins). Mirrors config/rule_catalog.csv.
FILES_RAW_ORDER = list(SCHEMAS.keys())

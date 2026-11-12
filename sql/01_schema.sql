-- Integrity Audit SQL layer, step 1: raw tables (all TEXT, exactly as in the CSV files) and parameter views.
-- This layer is an independent implementation: it re-derives validation, matching and balances in SQL
-- and is compared with the Python engine by `python -m stocktrace sqlcheck`.
DROP TABLE IF EXISTS r_sku;  DROP TABLE IF EXISTS r_loc;  DROP TABLE IF EXISTS r_conv; DROP TABLE IF EXISTS r_open;
DROP TABLE IF EXISTS r_ev;   DROP TABLE IF EXISTS r_po;   DROP TABLE IF EXISTS r_cnt;  DROP TABLE IF EXISTS r_shift;
DROP TABLE IF EXISTS r_param;

CREATE TABLE r_sku  (sku_id TEXT, description TEXT, base_uom TEXT, category TEXT, unit_value_cad_sim TEXT,
                     count_tolerance_base TEXT, effective_from TEXT, effective_to TEXT);
CREATE TABLE r_loc  (location_id TEXT, zone TEXT, active_flag TEXT, effective_from TEXT, effective_to TEXT);
CREATE TABLE r_conv (sku_id TEXT, uom TEXT, to_base_factor TEXT);
CREATE TABLE r_open (timestamp TEXT, sku_id TEXT, location_id TEXT, quantity_in_base_uom TEXT);
CREATE TABLE r_ev   (row_no INTEGER, event_id TEXT, event_timestamp TEXT, shift_id TEXT, event_type TEXT, sku_id TEXT,
                     quantity TEXT, uom TEXT, from_location TEXT, to_location TEXT, status TEXT, related_id TEXT,
                     handoff_to_shift TEXT, handoff_status TEXT);
CREATE TABLE r_po   (row_no INTEGER, posting_id TEXT, source_event_id TEXT, system_post_timestamp TEXT,
                     event_effective_timestamp TEXT, posting_type TEXT, sku_id TEXT, quantity TEXT, uom TEXT,
                     from_location TEXT, to_location TEXT, posting_status TEXT, related_posting_id TEXT);
CREATE TABLE r_cnt  (row_no INTEGER, count_id TEXT, observed_at TEXT, sku_id TEXT, location_id TEXT, counted_qty TEXT,
                     uom TEXT, count_status TEXT, evidence_ref TEXT);
CREATE TABLE r_shift(shift_id TEXT, shift_name TEXT, start_ts TEXT, end_ts TEXT);
CREATE TABLE r_param(key TEXT, value TEXT, description TEXT);

-- UTC-normalised text timestamps compare correctly as strings: YYYY-MM-DDTHH:MM:SSZ
DROP VIEW IF EXISTS p_snapshot; DROP VIEW IF EXISTS p_tol; DROP VIEW IF EXISTS p_window;
CREATE VIEW p_snapshot AS SELECT strftime('%Y-%m-%dT%H:%M:%SZ', value) AS ts FROM r_param WHERE key = 'snapshot_ts';
CREATE VIEW p_tol      AS SELECT CAST(value AS REAL) AS tol FROM r_param WHERE key = 'match_qty_tolerance_base';
CREATE VIEW p_window   AS SELECT CAST(value AS INTEGER) * 60 AS secs FROM r_param WHERE key = 'possible_match_window_minutes';

DROP VIEW IF EXISTS m_sku; DROP VIEW IF EXISTS m_loc; DROP VIEW IF EXISTS m_shift; DROP VIEW IF EXISTS u_factor;
CREATE VIEW m_sku AS
SELECT sku_id, base_uom, CAST(count_tolerance_base AS REAL) AS ctol,
       CASE WHEN effective_from = '' THEN NULL ELSE strftime('%Y-%m-%dT%H:%M:%SZ', effective_from) END AS efrom,
       CASE WHEN effective_to   = '' THEN NULL ELSE strftime('%Y-%m-%dT%H:%M:%SZ', effective_to)   END AS eto
FROM r_sku;
CREATE VIEW m_loc AS
SELECT location_id,
       CASE WHEN effective_from = '' THEN NULL ELSE strftime('%Y-%m-%dT%H:%M:%SZ', effective_from) END AS efrom,
       CASE WHEN effective_to   = '' THEN NULL ELSE strftime('%Y-%m-%dT%H:%M:%SZ', effective_to)   END AS eto
FROM r_loc;
CREATE VIEW m_shift AS
SELECT shift_id, strftime('%Y-%m-%dT%H:%M:%SZ', start_ts) AS st, strftime('%Y-%m-%dT%H:%M:%SZ', end_ts) AS en FROM r_shift;
-- explicit UoM lookup only: base UoM has factor 1, everything else must be listed in r_conv
CREATE VIEW u_factor AS
SELECT sku_id, base_uom AS uom, 1.0 AS f FROM r_sku
UNION ALL
SELECT sku_id, uom, CAST(to_base_factor AS REAL) FROM r_conv;

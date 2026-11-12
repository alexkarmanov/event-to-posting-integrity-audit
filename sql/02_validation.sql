-- Step 2: validation gate R04 (duplicate IDs), R01 (fields), R02 (references), R03 (UoM). Mirrors docs/RULE_CATALOG.md.
DROP VIEW IF EXISTS e_norm; DROP VIEW IF EXISTS e_dup; DROP VIEW IF EXISTS e_keep; DROP VIEW IF EXISTS e_chk; DROP VIEW IF EXISTS e_valid;
DROP VIEW IF EXISTS p_norm; DROP VIEW IF EXISTS p_dup; DROP VIEW IF EXISTS p_keep; DROP VIEW IF EXISTS p_chk; DROP VIEW IF EXISTS p_valid;
DROP VIEW IF EXISTS c_norm; DROP VIEW IF EXISTS c_dup; DROP VIEW IF EXISTS c_keep; DROP VIEW IF EXISTS c_chk; DROP VIEW IF EXISTS c_valid;
DROP VIEW IF EXISTS o_valid;

------------------------------------------------------------------ events
CREATE VIEW e_norm AS
SELECT row_no, event_id, event_type, status, sku_id, uom, from_location AS frm, to_location AS tto, shift_id,
       related_id, handoff_status, handoff_to_shift,
       CASE WHEN event_timestamp GLOB '*[0-9]Z' OR event_timestamp GLOB '*[0-9][+-][0-9][0-9]:[0-9][0-9]'
            THEN strftime('%Y-%m-%dT%H:%M:%SZ', event_timestamp) END AS ts,
       CASE WHEN quantity <> '' AND quantity <> '.' AND quantity NOT GLOB '*[^0-9.]*' AND quantity NOT GLOB '*.*.*'
            THEN CAST(quantity AS REAL) END AS q,
       event_timestamp, quantity
FROM r_ev;

CREATE VIEW e_dup AS
SELECT event_id, COUNT(*) AS n, MIN(row_no) AS first_row,
       COUNT(DISTINCT event_timestamp||'|'||shift_id||'|'||event_type||'|'||sku_id||'|'||quantity||'|'||uom||'|'||from_location||'|'||
                      to_location||'|'||status||'|'||related_id||'|'||handoff_to_shift||'|'||handoff_status) AS nd
FROM r_ev WHERE event_id <> '' GROUP BY event_id HAVING COUNT(*) > 1;

-- identical repeats: keep the first row; conflicting groups: drop every row
CREATE VIEW e_keep AS
SELECT n.* FROM e_norm n
WHERE n.event_id = '' OR n.event_id NOT IN (SELECT event_id FROM e_dup)
   OR (n.event_id IN (SELECT event_id FROM e_dup WHERE nd = 1) AND n.row_no = (SELECT first_row FROM e_dup d WHERE d.event_id = n.event_id));

CREATE VIEW e_chk AS
SELECT n.*,
CASE
  WHEN event_id = '' OR shift_id = '' OR event_type = '' OR sku_id = '' OR uom = '' OR status = '' THEN 'R01'
  WHEN ts IS NULL THEN 'R01'
  WHEN ts > (SELECT ts FROM p_snapshot) THEN 'R01'
  WHEN event_type NOT IN ('receipt','pick','transfer','adjustment','reversal') THEN 'R01'
  WHEN status NOT IN ('completed','pending','cancelled') THEN 'R01'
  WHEN handoff_status NOT IN ('','OPEN','CLOSED') THEN 'R01'
  WHEN q IS NULL THEN 'R01'
  WHEN event_type = 'receipt'  AND (tto = '' OR frm <> '') THEN 'R01'
  WHEN event_type = 'pick'     AND (frm = '' OR tto <> '') THEN 'R01'
  WHEN event_type = 'transfer' AND (frm = '' OR tto = '' OR frm = tto) THEN 'R01'
  WHEN event_type IN ('adjustment','reversal') AND ((frm = '') = (tto = '')) THEN 'R01'
  WHEN event_type = 'reversal' AND related_id = '' THEN 'R01'
  WHEN NOT EXISTS (SELECT 1 FROM m_shift s WHERE n.ts >= s.st AND n.ts < s.en) THEN 'R01'
  WHEN sku_id NOT IN (SELECT sku_id FROM m_sku) THEN 'R02'
  WHEN EXISTS (SELECT 1 FROM m_sku s WHERE s.sku_id = n.sku_id AND ((s.efrom IS NOT NULL AND n.ts < s.efrom) OR (s.eto IS NOT NULL AND n.ts >= s.eto))) THEN 'R02'
  WHEN frm <> '' AND frm NOT IN (SELECT location_id FROM m_loc) THEN 'R02'
  WHEN frm <> '' AND EXISTS (SELECT 1 FROM m_loc l WHERE l.location_id = n.frm AND ((l.efrom IS NOT NULL AND n.ts < l.efrom) OR (l.eto IS NOT NULL AND n.ts >= l.eto))) THEN 'R02'
  WHEN tto <> '' AND tto NOT IN (SELECT location_id FROM m_loc) THEN 'R02'
  WHEN tto <> '' AND EXISTS (SELECT 1 FROM m_loc l WHERE l.location_id = n.tto AND ((l.efrom IS NOT NULL AND n.ts < l.efrom) OR (l.eto IS NOT NULL AND n.ts >= l.eto))) THEN 'R02'
  WHEN shift_id NOT IN (SELECT shift_id FROM m_shift) THEN 'R02'
  WHEN handoff_to_shift <> '' AND handoff_to_shift NOT IN (SELECT shift_id FROM m_shift) THEN 'R02'
  WHEN NOT EXISTS (SELECT 1 FROM u_factor f WHERE f.sku_id = n.sku_id AND f.uom = n.uom) THEN 'R03'
  ELSE NULL END AS fail
FROM e_keep n;

-- valid events with base-UoM quantity (3 dp, half away from zero) and the shift the TIMESTAMP falls in
CREATE VIEW e_valid AS
SELECT c.row_no, c.event_id, c.event_type, c.status, c.sku_id, c.uom, c.frm, c.tto, c.shift_id AS label, c.related_id,
       c.handoff_status, c.handoff_to_shift, c.ts, c.q,
       ROUND(c.q * (SELECT f FROM u_factor f WHERE f.sku_id = c.sku_id AND f.uom = c.uom), 3) AS qb,
       (SELECT s.shift_id FROM m_shift s WHERE c.ts >= s.st AND c.ts < s.en) AS shift_id,
       (SELECT s.en FROM m_shift s WHERE c.ts >= s.st AND c.ts < s.en) AS cutoff
FROM e_chk c WHERE c.fail IS NULL;

------------------------------------------------------------------ postings
CREATE VIEW p_norm AS
SELECT row_no, posting_id, source_event_id AS src, posting_type, sku_id, uom, from_location AS frm, to_location AS tto,
       posting_status, related_posting_id AS rel,
       CASE WHEN system_post_timestamp GLOB '*[0-9]Z' OR system_post_timestamp GLOB '*[0-9][+-][0-9][0-9]:[0-9][0-9]'
            THEN strftime('%Y-%m-%dT%H:%M:%SZ', system_post_timestamp) END AS ts,
       CASE WHEN event_effective_timestamp = '' THEN '' WHEN event_effective_timestamp GLOB '*[0-9]Z' OR event_effective_timestamp GLOB '*[0-9][+-][0-9][0-9]:[0-9][0-9]'
            THEN strftime('%Y-%m-%dT%H:%M:%SZ', event_effective_timestamp) END AS eff,
       CASE WHEN quantity <> '' AND quantity <> '.' AND quantity NOT GLOB '*[^0-9.]*' AND quantity NOT GLOB '*.*.*'
            THEN CAST(quantity AS REAL) END AS q,
       system_post_timestamp, event_effective_timestamp, quantity
FROM r_po;

CREATE VIEW p_dup AS
SELECT posting_id, COUNT(*) AS n, MIN(row_no) AS first_row,
       COUNT(DISTINCT source_event_id||'|'||system_post_timestamp||'|'||event_effective_timestamp||'|'||posting_type||'|'||sku_id||'|'||
                      quantity||'|'||uom||'|'||from_location||'|'||to_location||'|'||posting_status||'|'||related_posting_id) AS nd
FROM r_po WHERE posting_id <> '' GROUP BY posting_id HAVING COUNT(*) > 1;

CREATE VIEW p_keep AS
SELECT n.* FROM p_norm n
WHERE n.posting_id = '' OR n.posting_id NOT IN (SELECT posting_id FROM p_dup)
   OR (n.posting_id IN (SELECT posting_id FROM p_dup WHERE nd = 1) AND n.row_no = (SELECT first_row FROM p_dup d WHERE d.posting_id = n.posting_id));

CREATE VIEW p_chk AS
SELECT n.*, COALESCE(NULLIF(n.eff, ''), n.ts) AS at_ts,
CASE
  WHEN posting_id = '' OR posting_type = '' OR sku_id = '' OR uom = '' OR posting_status = '' THEN 'R01'
  WHEN ts IS NULL THEN 'R01'
  WHEN ts > (SELECT ts FROM p_snapshot) THEN 'R01'
  WHEN eff IS NULL THEN 'R01'
  WHEN eff <> '' AND eff > (SELECT ts FROM p_snapshot) THEN 'R01'
  WHEN posting_type NOT IN ('RECEIPT','ISSUE','TRANSFER_OUT','TRANSFER_IN','ADJUSTMENT','REVERSAL') THEN 'R01'
  WHEN posting_status NOT IN ('POSTED','FAILED') THEN 'R01'
  WHEN q IS NULL THEN 'R01'
  WHEN posting_type = 'RECEIPT' AND (tto = '' OR frm <> '') THEN 'R01'
  WHEN posting_type = 'ISSUE'   AND (frm = '' OR tto <> '') THEN 'R01'
  WHEN posting_type IN ('TRANSFER_OUT','TRANSFER_IN') AND (frm = '' OR tto = '' OR frm = tto) THEN 'R01'
  WHEN posting_type IN ('ADJUSTMENT','REVERSAL') AND ((frm = '') = (tto = '')) THEN 'R01'
  WHEN sku_id NOT IN (SELECT sku_id FROM m_sku) THEN 'R02'
  WHEN EXISTS (SELECT 1 FROM m_sku s WHERE s.sku_id = n.sku_id AND ((s.efrom IS NOT NULL AND COALESCE(NULLIF(n.eff,''), n.ts) < s.efrom) OR (s.eto IS NOT NULL AND COALESCE(NULLIF(n.eff,''), n.ts) >= s.eto))) THEN 'R02'
  WHEN frm <> '' AND frm NOT IN (SELECT location_id FROM m_loc) THEN 'R02'
  WHEN frm <> '' AND EXISTS (SELECT 1 FROM m_loc l WHERE l.location_id = n.frm AND ((l.efrom IS NOT NULL AND COALESCE(NULLIF(n.eff,''), n.ts) < l.efrom) OR (l.eto IS NOT NULL AND COALESCE(NULLIF(n.eff,''), n.ts) >= l.eto))) THEN 'R02'
  WHEN tto <> '' AND tto NOT IN (SELECT location_id FROM m_loc) THEN 'R02'
  WHEN tto <> '' AND EXISTS (SELECT 1 FROM m_loc l WHERE l.location_id = n.tto AND ((l.efrom IS NOT NULL AND COALESCE(NULLIF(n.eff,''), n.ts) < l.efrom) OR (l.eto IS NOT NULL AND COALESCE(NULLIF(n.eff,''), n.ts) >= l.eto))) THEN 'R02'
  WHEN NOT EXISTS (SELECT 1 FROM u_factor f WHERE f.sku_id = n.sku_id AND f.uom = n.uom) THEN 'R03'
  ELSE NULL END AS fail
FROM p_keep n;

CREATE VIEW p_valid AS
SELECT c.row_no, c.posting_id, c.src, c.posting_type, c.sku_id, c.uom, c.frm, c.tto, c.posting_status, c.rel, c.ts, c.eff, c.q,
       ROUND(c.q * (SELECT f FROM u_factor f WHERE f.sku_id = c.sku_id AND f.uom = c.uom), 3) AS qb
FROM p_chk c WHERE c.fail IS NULL;

------------------------------------------------------------------ counts
CREATE VIEW c_norm AS
SELECT row_no, count_id, sku_id, location_id AS loc, uom, count_status AS st, counted_qty,
       CASE WHEN observed_at GLOB '*[0-9]Z' OR observed_at GLOB '*[0-9][+-][0-9][0-9]:[0-9][0-9]'
            THEN strftime('%Y-%m-%dT%H:%M:%SZ', observed_at) END AS ts,
       CASE WHEN counted_qty <> '' AND counted_qty <> '.' AND counted_qty NOT GLOB '*[^0-9.]*' AND counted_qty NOT GLOB '*.*.*'
            THEN CAST(counted_qty AS REAL) END AS q,
       observed_at, evidence_ref
FROM r_cnt;

CREATE VIEW c_dup AS
SELECT count_id, COUNT(*) AS n, MIN(row_no) AS first_row,
       COUNT(DISTINCT observed_at||'|'||sku_id||'|'||location_id||'|'||counted_qty||'|'||uom||'|'||count_status||'|'||evidence_ref) AS nd
FROM r_cnt WHERE count_id <> '' GROUP BY count_id HAVING COUNT(*) > 1;

CREATE VIEW c_keep AS
SELECT n.* FROM c_norm n
WHERE n.count_id = '' OR n.count_id NOT IN (SELECT count_id FROM c_dup)
   OR (n.count_id IN (SELECT count_id FROM c_dup WHERE nd = 1) AND n.row_no = (SELECT first_row FROM c_dup d WHERE d.count_id = n.count_id));

CREATE VIEW c_chk AS
SELECT n.*,
CASE
  WHEN count_id = '' OR sku_id = '' OR loc = '' OR st = '' THEN 'R01'
  WHEN ts IS NULL THEN 'R01'
  WHEN ts > (SELECT ts FROM p_snapshot) THEN 'R01'
  WHEN st NOT IN ('COMPLETE','PARTIAL','VOID') THEN 'R01'
  WHEN st = 'VOID' AND counted_qty = '' THEN CASE WHEN sku_id IN (SELECT sku_id FROM m_sku) AND loc IN (SELECT location_id FROM m_loc) THEN NULL ELSE 'R02' END
  WHEN uom = '' THEN 'R01'
  WHEN q IS NULL THEN 'R01'
  WHEN sku_id NOT IN (SELECT sku_id FROM m_sku) THEN 'R02'
  WHEN EXISTS (SELECT 1 FROM m_sku s WHERE s.sku_id = n.sku_id AND ((s.efrom IS NOT NULL AND n.ts < s.efrom) OR (s.eto IS NOT NULL AND n.ts >= s.eto))) THEN 'R02'
  WHEN loc NOT IN (SELECT location_id FROM m_loc) THEN 'R02'
  WHEN EXISTS (SELECT 1 FROM m_loc l WHERE l.location_id = n.loc AND ((l.efrom IS NOT NULL AND n.ts < l.efrom) OR (l.eto IS NOT NULL AND n.ts >= l.eto))) THEN 'R02'
  WHEN NOT EXISTS (SELECT 1 FROM u_factor f WHERE f.sku_id = n.sku_id AND f.uom = n.uom) THEN 'R03'
  ELSE NULL END AS fail
FROM c_keep n;

CREATE VIEW c_valid AS
SELECT c.row_no, c.count_id, c.sku_id, c.loc, c.st, c.ts,
       CASE WHEN c.st = 'VOID' AND c.counted_qty = '' THEN 0.0
            ELSE ROUND(c.q * (SELECT f FROM u_factor f WHERE f.sku_id = c.sku_id AND f.uom = c.uom), 3) END AS qb
FROM c_chk c WHERE c.fail IS NULL;

CREATE VIEW o_valid AS
SELECT strftime('%Y-%m-%dT%H:%M:%SZ', timestamp) AS ts, sku_id, location_id AS loc, CAST(quantity_in_base_uom AS REAL) AS q
FROM r_open
WHERE sku_id IN (SELECT sku_id FROM m_sku) AND location_id IN (SELECT location_id FROM m_loc) AND quantity_in_base_uom NOT GLOB '*[^0-9.]*' AND quantity_in_base_uom <> '';

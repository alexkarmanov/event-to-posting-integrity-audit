-- Step 3: matching, issue sets and balances. Result tables: sql_issues(rule_id, record_id) and sql_counts.
DROP TABLE IF EXISTS x_exp; DROP TABLE IF EXISTS sql_issues; DROP TABLE IF EXISTS sql_counts;
DROP VIEW IF EXISTS po_ok; DROP VIEW IF EXISTS po_grp; DROP VIEW IF EXISTS po_active; DROP VIEW IF EXISTS po_kept;
DROP VIEW IF EXISTS ev_c; DROP VIEW IF EXISTS q_src; DROP VIEW IF EXISTS q_ev; DROP VIEW IF EXISTS uncov; DROP VIEW IF EXISTS nosrc;
DROP VIEW IF EXISTS pm_cand; DROP VIEW IF EXISTS pm_pairs; DROP VIEW IF EXISTS claimed; DROP VIEW IF EXISTS e_eff; DROP VIEW IF EXISTS s_eff;
DROP VIEW IF EXISTS sql_count_class;

CREATE TABLE x_exp (event_type TEXT, posting_type TEXT);   -- which ERP postings each event type must produce
INSERT INTO x_exp VALUES ('receipt','RECEIPT'),('pick','ISSUE'),('transfer','TRANSFER_OUT'),('transfer','TRANSFER_IN'),
                         ('adjustment','ADJUSTMENT'),('reversal','REVERSAL');

CREATE TABLE sql_issues (rule_id TEXT, record_id TEXT);

------------------------------------------------------------------ validation issues (R01-R04)
INSERT INTO sql_issues SELECT fail, CASE WHEN event_id = '' THEN 'business_events#row'||row_no ELSE event_id END FROM e_chk WHERE fail IS NOT NULL;
INSERT INTO sql_issues SELECT fail, CASE WHEN posting_id = '' THEN 'system_postings#row'||row_no ELSE posting_id END FROM p_chk WHERE fail IS NOT NULL;
INSERT INTO sql_issues SELECT fail, CASE WHEN count_id = '' THEN 'physical_counts#row'||row_no ELSE count_id END FROM c_chk WHERE fail IS NOT NULL;
INSERT INTO sql_issues SELECT 'R04', event_id FROM e_dup;
INSERT INTO sql_issues SELECT 'R04', posting_id FROM p_dup;
INSERT INTO sql_issues SELECT 'R04', count_id FROM c_dup;
INSERT INTO sql_issues SELECT 'R01', event_id FROM e_valid WHERE q = 0;               -- zero quantity: accepted, warning only

------------------------------------------------------------------ grouping postings under their source event
CREATE VIEW po_ok AS SELECT * FROM p_valid WHERE posting_status = 'POSTED';
-- a reversal without a source inherits the source of the posting it reverses
CREATE VIEW po_grp AS
SELECT p.*, CASE WHEN p.src <> '' THEN p.src
                 WHEN p.posting_type = 'REVERSAL' THEN COALESCE((SELECT x.src FROM po_ok x WHERE x.posting_id = p.rel), '')
                 ELSE '' END AS esrc
FROM po_ok p;
CREATE VIEW ev_c AS SELECT * FROM e_valid WHERE status = 'completed';
CREATE VIEW claimed AS SELECT g.posting_id FROM po_grp g JOIN e_valid e ON e.event_id = g.esrc;
-- active = not a reversal and not reversed inside the same group (reversal events keep their own REVERSAL posting)
CREATE VIEW po_active AS
SELECT g.* FROM po_grp g JOIN e_valid e ON e.event_id = g.esrc
WHERE e.event_type = 'reversal'
   OR (g.posting_type <> 'REVERSAL'
       AND g.posting_id NOT IN (SELECT r.rel FROM po_grp r WHERE r.posting_type = 'REVERSAL' AND r.esrc = g.esrc AND r.rel <> ''));
CREATE VIEW po_kept AS
SELECT * FROM (
  SELECT a.*, ROW_NUMBER() OVER (PARTITION BY a.esrc, a.posting_type ORDER BY a.ts, a.posting_id) AS rn
  FROM po_active a JOIN e_valid e ON e.event_id = a.esrc JOIN x_exp x ON x.event_type = e.event_type AND x.posting_type = a.posting_type
) WHERE rn = 1;

------------------------------------------------------------------ R05 more than one active posting of an expected type
INSERT INTO sql_issues
SELECT 'R05', e.event_id FROM ev_c e
WHERE EXISTS (SELECT 1 FROM po_active a JOIN x_exp x ON x.event_type = e.event_type AND x.posting_type = a.posting_type
              WHERE a.esrc = e.event_id GROUP BY a.posting_type HAVING COUNT(*) > 1)
  AND NOT (e.event_type <> 'reversal' AND EXISTS (SELECT 1 FROM po_grp r WHERE r.esrc = e.event_id AND r.posting_type = 'REVERSAL'
                                                  AND r.rel NOT IN (SELECT posting_id FROM po_ok)));

------------------------------------------------------------------ R06 completed, closed shift, nothing posted
CREATE VIEW q_src AS
SELECT src AS s FROM p_chk WHERE fail IS NOT NULL AND src <> ''
UNION SELECT source_event_id FROM r_po WHERE posting_id IN (SELECT posting_id FROM p_dup WHERE nd > 1) AND source_event_id <> '';
CREATE VIEW q_ev AS
SELECT event_id AS s FROM e_chk WHERE fail IS NOT NULL AND event_id <> ''
UNION SELECT event_id FROM e_dup WHERE nd > 1;
CREATE VIEW uncov AS
SELECT e.* FROM ev_c e
WHERE e.cutoff <= (SELECT ts FROM p_snapshot)
  AND NOT EXISTS (SELECT 1 FROM po_active a WHERE a.esrc = e.event_id)
  AND NOT (e.event_id IN (SELECT s FROM q_src) AND NOT EXISTS (SELECT 1 FROM po_grp g WHERE g.esrc = e.event_id));
INSERT INTO sql_issues SELECT 'R06', event_id FROM uncov;

------------------------------------------------------------------ possible matches (never a confident link)
CREATE VIEW nosrc AS SELECT * FROM po_grp WHERE esrc = '';
CREATE VIEW pm_cand AS
SELECT e.event_id, p.posting_id FROM uncov e
JOIN (SELECT event_type, MIN(posting_type) AS posting_type FROM x_exp GROUP BY event_type HAVING COUNT(*) = 1) x ON x.event_type = e.event_type
JOIN nosrc p ON p.posting_type = x.posting_type AND p.sku_id = e.sku_id
 AND ABS(p.qb - e.qb) <= (SELECT tol FROM p_tol) + 1e-9
 AND ABS(CAST(strftime('%s', p.ts) AS INTEGER) - CAST(strftime('%s', e.ts) AS INTEGER)) <= (SELECT secs FROM p_window)
 AND (CASE p.posting_type WHEN 'RECEIPT' THEN p.tto = e.tto WHEN 'ISSUE' THEN p.frm = e.frm ELSE (p.frm = e.frm AND p.tto = e.tto) END);
CREATE VIEW pm_pairs AS
SELECT event_id, posting_id FROM pm_cand
WHERE event_id IN (SELECT event_id FROM pm_cand GROUP BY event_id HAVING COUNT(*) = 1)
  AND posting_id IN (SELECT posting_id FROM pm_cand GROUP BY posting_id HAVING COUNT(*) = 1);

------------------------------------------------------------------ R07 postings with no supporting event
INSERT INTO sql_issues
SELECT 'R07', p.posting_id FROM po_grp p
WHERE p.posting_id NOT IN (SELECT posting_id FROM claimed)
  AND p.posting_id NOT IN (SELECT posting_id FROM pm_pairs)
  AND p.posting_id NOT IN (SELECT rel FROM po_ok WHERE posting_type = 'REVERSAL' AND rel <> '')
  AND NOT (p.posting_type = 'REVERSAL' AND p.rel IN (SELECT posting_id FROM po_ok)
           AND p.rel NOT IN (SELECT posting_id FROM claimed) AND p.rel NOT IN (SELECT posting_id FROM pm_pairs))
  AND NOT (p.esrc <> '' AND p.esrc IN (SELECT s FROM q_ev));
INSERT INTO sql_issues
SELECT 'R07', a.posting_id FROM po_active a JOIN e_valid e ON e.event_id = a.esrc WHERE e.status = 'cancelled';

------------------------------------------------------------------ R08 quantity mismatch after normalisation
INSERT INTO sql_issues
SELECT DISTINCT 'R08', e.event_id FROM ev_c e JOIN po_kept k ON k.esrc = e.event_id
WHERE ABS(k.qb - e.qb) > (SELECT tol FROM p_tol) + 1e-9;

------------------------------------------------------------------ R10 late posting (only the LATE_POSTING flavour is cross-checked)
INSERT INTO sql_issues
SELECT 'R10', e.event_id FROM ev_c e
WHERE e.cutoff <= (SELECT ts FROM p_snapshot)
  AND (SELECT COUNT(*) FROM po_kept k WHERE k.esrc = e.event_id) > 0
  AND (SELECT COUNT(*) FROM po_kept k WHERE k.esrc = e.event_id) = (SELECT COUNT(*) FROM x_exp x WHERE x.event_type = e.event_type)
  AND (SELECT MAX(k.ts) FROM po_kept k WHERE k.esrc = e.event_id) > e.cutoff;

------------------------------------------------------------------ balances as of each count time (both bases)
CREATE VIEW e_eff AS
SELECT sku_id, tto AS loc, ts, qb AS d FROM e_valid WHERE status = 'completed' AND tto <> ''
UNION ALL SELECT sku_id, frm, ts, -qb FROM e_valid WHERE status = 'completed' AND frm <> '';
CREATE VIEW s_eff AS
SELECT sku_id, tto AS loc, ts, qb AS d FROM po_ok WHERE posting_type IN ('RECEIPT','TRANSFER_IN')
UNION ALL SELECT sku_id, frm, ts, -qb FROM po_ok WHERE posting_type IN ('ISSUE','TRANSFER_OUT')
UNION ALL SELECT sku_id, tto, ts, qb FROM po_ok WHERE posting_type IN ('ADJUSTMENT','REVERSAL') AND tto <> ''
UNION ALL SELECT sku_id, frm, ts, -qb FROM po_ok WHERE posting_type IN ('ADJUSTMENT','REVERSAL') AND frm <> '';

CREATE TABLE sql_counts AS
SELECT c.count_id, c.sku_id, c.loc, c.st, c.qb AS c_qty,
       (SELECT COALESCE(SUM(o.q), 0) FROM o_valid o WHERE o.sku_id = c.sku_id AND o.loc = c.loc AND o.ts <= c.ts)
     + (SELECT COALESCE(SUM(s.d), 0) FROM s_eff s WHERE s.sku_id = c.sku_id AND s.loc = c.loc AND s.ts <= c.ts) AS s_bal,
       (SELECT COALESCE(SUM(o.q), 0) FROM o_valid o WHERE o.sku_id = c.sku_id AND o.loc = c.loc AND o.ts <= c.ts)
     + (SELECT COALESCE(SUM(e.d), 0) FROM e_eff e WHERE e.sku_id = c.sku_id AND e.loc = c.loc AND e.ts <= c.ts) AS e_bal,
       (SELECT ctol FROM m_sku m WHERE m.sku_id = c.sku_id) AS tol
FROM c_valid c WHERE c.st <> 'VOID';

CREATE VIEW sql_count_class AS
SELECT count_id, sku_id, loc, c_qty, s_bal, e_bal,
  CASE WHEN ABS(c_qty - s_bal) <= tol + 1e-9 THEN 'AGREES'
       WHEN st = 'PARTIAL' AND c_qty <= s_bal + tol + 1e-9 AND c_qty <= e_bal + tol + 1e-9 THEN 'PARTIAL_NOT_COMPARABLE'
       WHEN ABS(c_qty - e_bal) <= tol + 1e-9 THEN 'MATCHES_EVENT_LEDGER'
       ELSE 'UNEXPLAINED' END AS cls
FROM sql_counts;
INSERT INTO sql_issues SELECT 'R12', count_id FROM sql_count_class WHERE cls = 'UNEXPLAINED';

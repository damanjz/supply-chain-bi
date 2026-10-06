-- Marts: one long table for Tableau, sc_workbench, with a record type per subject, so one set of
-- parameters reaches every view:
--   Lane      outbound lane (centre to city) x category x month, two rows per lane: point 1 at the centre,
--             point 2 at the city, for drawing the line; measures sit on point 1 only, so nothing double counts
--   Node      centre x category x day: stock value, cost of goods shipped, carrying cost
--   Supplier  supplier x centre x month: purchase orders and how they arrived
--   Snapshot  centre x product x week: stock, reorder point, cover, stockout risk and its reasons

CREATE OR REPLACE TABLE mart_lanes AS
SELECT
    l.dc_id || '-' || l.zone_id AS lane_id, l.dc_id, l.zone_id, l.category,
    date_trunc('month', l.order_date)::DATE AS month,
    count(*) AS lines, sum(l.otif::INTEGER) AS otif_lines, sum(l.on_time::INTEGER) AS ontime_lines,
    sum(l.in_full::INTEGER) AS infull_lines, sum(l.lead_days) AS lead_days_sum,
    sum(l.qty_ordered) AS units_ordered, sum(l.qty_shipped) AS units_shipped
FROM fact_order_lines l GROUP BY ALL;

CREATE OR REPLACE TABLE mart_nodes AS
WITH stock AS (
    -- rounded to 4 decimals: parallel float sums otherwise differ in the last digit from run to run
    SELECT dc_id, category, date, round(sum(stock_value), 4) AS stock_value_days, 1 AS days,
           round(sum(carrying_cost), 4) AS carrying_cost
    FROM fact_inventory_daily GROUP BY ALL
),
cogs AS (
    SELECT dc_id, category, ship_date AS date, round(sum(cogs), 4) AS cogs FROM fact_order_lines GROUP BY ALL
)
SELECT s.*, coalesce(c.cogs, 0) AS cogs FROM stock s LEFT JOIN cogs c USING (dc_id, category, date);

CREATE OR REPLACE TABLE mart_suppliers AS
SELECT
    po.supplier_id, po.dc_id, p.category, date_trunc('month', po.last_received)::DATE AS month,
    count(*) AS pos, sum(po.on_time::INTEGER) AS pos_on_time, sum(po.in_full::INTEGER) AS pos_in_full,
    sum(po.lead_days) AS po_lead_days_sum, sum(po.quoted_lead_days) AS po_quoted_days_sum,
    sum(po.qty_ordered) AS po_units_ordered, sum(po.qty_received) AS units_received, sum(po.qty_rejected) AS units_rejected
FROM fact_purchase_orders po JOIN dim_product p USING (sku)
WHERE po.last_received IS NOT NULL
GROUP BY ALL;

-- The map draws two layers from these rows. Lines: one per lane, through point 1 (the centre) and point 2 (the
-- city). Circles: one per map_point, the centre for warehouse rows and lane starts, the city for lane ends.
-- Warehouse rows join the start point of one of the centre's own lanes, so neither layer meets an empty group
-- (an empty group draws as a stray mark).
CREATE OR REPLACE TABLE sc_workbench AS
WITH fy AS (SELECT DISTINCT month, fiscal_year FROM dim_date),
anchor AS (SELECT dc_id, min(lane_id) AS anchor_lane FROM mart_lanes GROUP BY dc_id)
-- Lane, point 1 (the centre) carries the measures
SELECT 'Lane' AS record_type, fy.fiscal_year, m.month AS period_date, m.dc_id, w.dc_city, m.lane_id, z.city,
       1 AS point_order, w.dc_city AS map_point, w.lat, w.lon, m.category, NULL AS supplier_id, NULL AS supplier, NULL AS sku,
       m.lines, m.otif_lines, m.ontime_lines, m.infull_lines, m.lead_days_sum, m.units_ordered, m.units_shipped,
       NULL::DOUBLE AS stock_value_days, NULL::INTEGER AS days, NULL::DOUBLE AS cogs, NULL::DOUBLE AS carrying_cost,
       NULL::INTEGER AS pos, NULL::INTEGER AS pos_on_time, NULL::INTEGER AS pos_in_full, NULL::INTEGER AS po_lead_days_sum,
       NULL::INTEGER AS po_quoted_days_sum, NULL::INTEGER AS po_units_ordered, NULL::INTEGER AS units_received,
       NULL::INTEGER AS units_rejected,
       NULL::INTEGER AS on_hand, NULL::INTEGER AS reorder_point, NULL::DOUBLE AS cover_days, NULL::DOUBLE AS risk_score,
       NULL AS risk_band, NULL AS reasons, NULL::INTEGER AS stockout_14d
FROM mart_lanes m JOIN fy USING (month) JOIN dim_dc w USING (dc_id) JOIN dim_zone z USING (zone_id)
UNION ALL
-- Lane, point 2 (the city): location only
SELECT 'Lane', fy.fiscal_year, m.month, m.dc_id, w.dc_city, m.lane_id, z.city, 2, z.city, z.lat, z.lon, m.category, NULL, NULL, NULL,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM mart_lanes m JOIN fy USING (month) JOIN dim_dc w USING (dc_id) JOIN dim_zone z USING (zone_id)
UNION ALL
SELECT 'Node', d.fiscal_year, n.date, n.dc_id, w.dc_city, a.anchor_lane, NULL, 1, w.dc_city, w.lat, w.lon, n.category, NULL, NULL, NULL,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, n.stock_value_days, n.days, n.cogs, n.carrying_cost,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM mart_nodes n JOIN dim_date d USING (date) JOIN dim_dc w USING (dc_id) JOIN anchor a USING (dc_id)
UNION ALL
SELECT 'Supplier', fy.fiscal_year, s.month, s.dc_id, w.dc_city, NULL, NULL, NULL, NULL, NULL, NULL, s.category, s.supplier_id,
       sp.supplier, NULL,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       s.pos, s.pos_on_time, s.pos_in_full, s.po_lead_days_sum, s.po_quoted_days_sum, s.po_units_ordered,
       s.units_received, s.units_rejected, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM mart_suppliers s JOIN fy USING (month) JOIN dim_dc w USING (dc_id) JOIN dim_supplier sp USING (supplier_id)
UNION ALL
SELECT 'Snapshot', d.fiscal_year, s.snapshot_date, s.dc_id, w.dc_city, a.anchor_lane, NULL, 1, w.dc_city, w.lat, w.lon, s.category,
       s.supplier_id, sp.supplier, s.sku,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       s.on_hand, s.reorder_point, s.cover_days, c.risk_score, c.risk_band, c.reasons, s.stockout_14d
FROM stockout_snapshots s
JOIN stockout_scores c USING (snapshot_date, dc_id, sku)
JOIN dim_date d ON d.date = s.snapshot_date
JOIN dim_dc w USING (dc_id)
JOIN anchor a USING (dc_id)
JOIN dim_supplier sp ON sp.supplier_id = s.supplier_id;

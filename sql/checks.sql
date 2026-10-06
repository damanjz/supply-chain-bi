-- Each check returns the rows that break a rule. A clean build returns zero rows for every check.

-- name: supplier names are clean
SELECT supplier_id, supplier FROM dim_supplier
WHERE supplier <> trim(supplier) OR supplier = upper(supplier) OR supplier LIKE '%  %';

-- name: every order has exactly one shipment, and dates run in order
SELECT o.order_id FROM stg_sales_orders o
LEFT JOIN (SELECT order_id, count(*) AS n, min(ship_date) AS ship_date, min(delivered_date) AS delivered_date
           FROM stg_shipments GROUP BY order_id) s USING (order_id)
WHERE s.n IS DISTINCT FROM 1 OR s.ship_date < o.order_date OR s.delivered_date < s.ship_date;

-- name: orders go to the centre that serves their city
SELECT o.order_id FROM stg_sales_orders o JOIN dim_zone z USING (zone_id) WHERE o.dc_id <> z.dc_id;

-- name: shipped quantities are between zero and the quantity ordered
SELECT order_id, sku FROM stg_order_lines WHERE qty_shipped < 0 OR qty_shipped > qty_ordered OR qty_ordered <= 0;

-- name: stock never goes negative
SELECT date, dc_id, sku FROM fact_inventory_daily WHERE on_hand < 0;

-- name: stock balances day to day (yesterday + accepted receipts - shipments = today)
WITH rec AS (
    SELECT po.dc_id, po.sku, r.received_date AS date, sum(r.qty_received - r.qty_rejected) AS accepted
    FROM stg_goods_receipts r JOIN stg_purchase_orders po USING (po_id) GROUP BY ALL
),
shp AS (
    SELECT dc_id, sku, ship_date AS date, sum(qty_shipped) AS shipped FROM fact_order_lines GROUP BY ALL
),
bal AS (
    SELECT i.date, i.dc_id, i.sku, i.on_hand,
           lag(i.on_hand) OVER (PARTITION BY i.dc_id, i.sku ORDER BY i.date) AS prev,
           coalesce(rec.accepted, 0) AS accepted, coalesce(shp.shipped, 0) AS shipped
    FROM fact_inventory_daily i
    LEFT JOIN rec USING (dc_id, sku, date)
    LEFT JOIN shp USING (dc_id, sku, date)
)
SELECT date, dc_id, sku FROM bal WHERE prev IS NOT NULL AND on_hand <> prev + accepted - shipped;

-- name: receipts never exceed the purchase order, rejects never exceed receipts
SELECT po_id FROM fact_purchase_orders WHERE qty_received > qty_ordered OR qty_rejected > qty_received;

-- name: every purchase order goes to the product's own supplier
SELECT po.po_id FROM stg_purchase_orders po JOIN dim_product p USING (sku) WHERE po.supplier_id <> p.supplier_id;

-- name: ABC classes split value at 80% and 95%
WITH v AS (SELECT p.abc_class, sum(l.cogs) AS value FROM fact_order_lines l JOIN dim_product p USING (sku) GROUP BY 1),
s AS (SELECT abc_class, value / sum(value) OVER () AS share FROM v)
SELECT * FROM s WHERE (abc_class = 'A' AND share NOT BETWEEN 0.79 AND 0.81)
                   OR (abc_class = 'C' AND share > 0.06);

-- name: carrying cost is stock value at 24% a year
SELECT date, dc_id, sku FROM fact_inventory_daily WHERE abs(carrying_cost - stock_value * 0.24 / 365) > 1e-6;

-- name: stockout label matches a direct recount
SELECT s.snapshot_date, s.dc_id, s.sku FROM stockout_snapshots s
WHERE s.outcome_known AND dayofmonth(s.snapshot_date) <= 7 AND s.stockout_14d <> (
    SELECT (count(*) > 0)::INTEGER FROM fact_inventory_daily i
    WHERE i.dc_id = s.dc_id AND i.sku = s.sku AND i.out_of_stock
      AND i.date > s.snapshot_date AND i.date <= s.snapshot_date + 14);

-- name: snapshots without 14 days of follow-up carry no outcome
SELECT snapshot_date, dc_id, sku FROM stockout_snapshots
WHERE (snapshot_date + 14 > DATE '2026-03-31') <> (stockout_14d IS NULL);

-- name: supplier reliability features only use deliveries received before the snapshot
SELECT s.snapshot_date, s.supplier_id FROM (SELECT DISTINCT snapshot_date, supplier_id, supplier_on_time_180d FROM stockout_snapshots) s
WHERE dayofmonth(s.snapshot_date) <= 7 AND abs(s.supplier_on_time_180d - coalesce((
    SELECT avg(on_time::INTEGER) FROM fact_purchase_orders po
    WHERE po.supplier_id = s.supplier_id AND po.last_received <= s.snapshot_date
      AND po.last_received > s.snapshot_date - 180), 0.8)) > 1e-9;

-- name: every snapshot has one risk score between 0 and 1
SELECT s.snapshot_date, s.dc_id, s.sku FROM stockout_snapshots s
LEFT JOIN stockout_scores c USING (snapshot_date, dc_id, sku)
WHERE c.risk_score IS NULL OR c.risk_score NOT BETWEEN 0 AND 1;

-- name: the Tableau table reconciles to its sources, record type by record type
SELECT * FROM (
    SELECT 'Lane lines' AS what, (SELECT sum(lines) FROM sc_workbench WHERE record_type = 'Lane') AS got,
           (SELECT count(*) FROM fact_order_lines) AS expected
    UNION ALL SELECT 'Lane points', (SELECT count(*) FILTER (WHERE point_order = 2) FROM sc_workbench WHERE record_type = 'Lane'),
           (SELECT count(*) FILTER (WHERE point_order = 1) FROM sc_workbench WHERE record_type = 'Lane')
    UNION ALL SELECT 'Map anchors belong to their own centre', (SELECT count(*) FROM sc_workbench
           WHERE record_type IN ('Node', 'Snapshot') AND (point_order <> 1 OR NOT starts_with(lane_id, dc_id || '-'))), 0
    UNION ALL SELECT 'Every map row has a map point', (SELECT count(*) FROM sc_workbench
           WHERE record_type <> 'Supplier' AND (map_point IS NULL OR lat IS NULL)), 0
    UNION ALL SELECT 'Node cost of goods', (SELECT round(sum(cogs)) FROM sc_workbench WHERE record_type = 'Node'),
           (SELECT round(sum(cogs)) FROM fact_order_lines)
    UNION ALL SELECT 'Node carrying cost', (SELECT round(sum(carrying_cost)) FROM sc_workbench WHERE record_type = 'Node'),
           (SELECT round(sum(carrying_cost)) FROM fact_inventory_daily)
    UNION ALL SELECT 'Supplier orders', (SELECT sum(pos) FROM sc_workbench WHERE record_type = 'Supplier'),
           (SELECT count(*) FROM fact_purchase_orders WHERE last_received IS NOT NULL)
    UNION ALL SELECT 'Snapshots', (SELECT count(*) FROM sc_workbench WHERE record_type = 'Snapshot'),
           (SELECT count(*) FROM stockout_snapshots)
) WHERE got IS DISTINCT FROM expected;

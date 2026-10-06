-- Model: dimensions and facts. The data covers 1 April 2023 to 31 March 2026.
-- OTIF is measured per order line, as most FMCG distributors report it: a line counts when its order was
-- delivered by the promised date and the line shipped in full.

CREATE OR REPLACE MACRO carry_rate() AS 0.24;          -- a year, as a share of inventory value

CREATE OR REPLACE TABLE dim_date AS
SELECT
    d::DATE AS date,
    date_trunc('week', d)::DATE AS week_start,
    date_trunc('month', d)::DATE AS month,
    'FY ' || (year(d) - CASE WHEN month(d) < 4 THEN 1 ELSE 0 END)::VARCHAR || '-'
        || right((year(d) + CASE WHEN month(d) < 4 THEN 0 ELSE 1 END)::VARCHAR, 2) AS fiscal_year
FROM range(DATE '2023-04-01', DATE '2026-04-01', INTERVAL 1 DAY) t(d);

CREATE OR REPLACE TABLE dim_dc AS SELECT * FROM stg_warehouses;
CREATE OR REPLACE TABLE dim_zone AS SELECT * FROM stg_zones;
CREATE OR REPLACE TABLE dim_supplier AS SELECT * FROM stg_suppliers;

-- ABC class by value shipped over the whole period: A = the products making the first 80%, B the next 15%.
CREATE OR REPLACE TABLE dim_product AS
WITH v AS (
    SELECT p.sku, sum(l.qty_shipped * p.unit_cost) AS value
    FROM stg_products p LEFT JOIN stg_order_lines l USING (sku) GROUP BY p.sku
),
c AS (
    SELECT sku, value, sum(value) OVER (ORDER BY value DESC, sku ROWS UNBOUNDED PRECEDING) / sum(value) OVER () AS cum
    FROM v
)
SELECT p.*, CASE WHEN c.cum - c.value / (SELECT sum(value) FROM v) < 0.80 THEN 'A'
                 WHEN c.cum - c.value / (SELECT sum(value) FROM v) < 0.95 THEN 'B' ELSE 'C' END AS abc_class
FROM stg_products p JOIN c USING (sku);

CREATE OR REPLACE TABLE fact_order_lines AS
SELECT
    o.order_id, o.dc_id, o.zone_id, l.sku, p.category,
    o.order_date, o.promised_date, s.ship_date, s.delivered_date, s.carrier,
    l.qty_ordered, l.qty_shipped,
    l.qty_ordered * p.unit_cost AS value_ordered,
    l.qty_shipped * p.unit_cost AS cogs,
    s.delivered_date <= o.promised_date AS on_time,
    l.qty_shipped = l.qty_ordered AS in_full,
    s.delivered_date <= o.promised_date AND l.qty_shipped = l.qty_ordered AS otif,
    date_diff('day', o.order_date, s.delivered_date) AS lead_days
FROM stg_sales_orders o
JOIN stg_shipments s USING (order_id)
JOIN stg_order_lines l USING (order_id)
JOIN stg_products p USING (sku);

CREATE OR REPLACE TABLE fact_orders AS
SELECT order_id, dc_id, zone_id, order_date, promised_date, ship_date, delivered_date, carrier,
       any_value(lead_days) AS lead_days, bool_and(on_time) AS on_time, bool_and(in_full) AS in_full,
       count(*) AS lines, sum(otif::INTEGER) AS otif_lines
FROM fact_order_lines GROUP BY ALL;

CREATE OR REPLACE TABLE fact_purchase_orders AS
WITH r AS (
    SELECT po_id, min(received_date) AS first_received, max(received_date) AS last_received, count(*) AS deliveries,
           sum(qty_received) AS qty_received, sum(qty_rejected) AS qty_rejected
    FROM stg_goods_receipts GROUP BY po_id
)
SELECT
    po.po_id, po.supplier_id, po.dc_id, po.sku, po.order_date, po.promised_date, po.qty_ordered,
    r.first_received, r.last_received, r.deliveries, r.qty_received, r.qty_rejected,
    s.quoted_lead_days,
    date_diff('day', po.order_date, r.last_received) AS lead_days,
    r.last_received <= po.promised_date AS on_time,
    r.qty_received >= po.qty_ordered AS in_full,
    r.qty_received * p.unit_cost AS value_received
FROM stg_purchase_orders po
JOIN stg_suppliers s USING (supplier_id)
JOIN stg_products p USING (sku)
LEFT JOIN r USING (po_id);

-- Daily demand per centre and product (zero-filled), for features and checks.
CREATE OR REPLACE TABLE fact_demand_daily AS
WITH grid AS (
    SELECT d.date, w.dc_id, p.sku FROM dim_date d CROSS JOIN dim_dc w CROSS JOIN dim_product p
),
dem AS (
    SELECT dc_id, sku, order_date AS date, sum(qty_ordered) AS qty_ordered, sum(qty_shipped) AS qty_shipped
    FROM fact_order_lines GROUP BY ALL
)
SELECT g.date, g.dc_id, g.sku, coalesce(dem.qty_ordered, 0) AS qty_ordered, coalesce(dem.qty_shipped, 0) AS qty_shipped
FROM grid g LEFT JOIN dem USING (date, dc_id, sku);

CREATE OR REPLACE TABLE fact_inventory_daily AS
SELECT
    i.stock_date AS date, i.dc_id, i.sku, p.category, i.on_hand, i.reorder_point,
    i.on_hand * p.unit_cost AS stock_value,
    i.on_hand * p.unit_cost * carry_rate() / 365 AS carrying_cost,
    i.on_hand <= 0 AS out_of_stock,
    i.on_hand <= i.reorder_point AS below_reorder_point
FROM stg_inventory i JOIN stg_products p USING (sku);

-- Features for the stockout risk model: one snapshot per centre and product every Monday.
-- Every feature uses only what was known by the end of the snapshot day.
-- Label: the product runs out (end-of-day stock at zero) at that centre on any of the next 14 days.
-- Snapshots in the last 14 days of data have no outcome yet; they are scored but not labelled.
-- Baseline the model must beat: the reorder-point rule (stock at or below the reorder point).

CREATE OR REPLACE TABLE model_features (feature VARCHAR, kind VARCHAR);
INSERT INTO model_features VALUES
    ('cover_days', 'numeric'), ('pipeline_cover_days', 'numeric'), ('stock_to_reorder_point', 'numeric'),
    ('demand_trend', 'numeric'), ('demand_cv', 'numeric'), ('supplier_on_time_180d', 'numeric'),
    ('supplier_lead_sd_180d', 'numeric'), ('days_to_diwali', 'numeric'), ('category', 'categorical'),
    ('abc_class', 'categorical'), ('dc_id', 'categorical');

CREATE OR REPLACE TABLE stockout_snapshots AS
WITH inv AS (
    SELECT i.*,
           avg(d.qty_ordered) OVER w28 AS demand_28d,
           avg(d.qty_ordered) OVER w7 AS demand_7d,
           stddev_pop(d.qty_ordered) OVER w28 AS demand_sd_28d,
           bool_or(i.out_of_stock) OVER (PARTITION BY i.dc_id, i.sku ORDER BY i.date
                                         ROWS BETWEEN 1 FOLLOWING AND 14 FOLLOWING) AS out_next_14d
    FROM fact_inventory_daily i
    JOIN fact_demand_daily d USING (date, dc_id, sku)
    WINDOW w28 AS (PARTITION BY i.dc_id, i.sku ORDER BY i.date ROWS BETWEEN 27 PRECEDING AND CURRENT ROW),
           w7 AS (PARTITION BY i.dc_id, i.sku ORDER BY i.date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
),
snap AS (
    SELECT * FROM inv WHERE isodow(date) = 1 AND date >= DATE '2023-05-01'
),
pipeline AS (
    SELECT s.date, s.dc_id, s.sku, coalesce(sum(po.qty_ordered), 0) AS open_po_units
    FROM snap s
    LEFT JOIN fact_purchase_orders po
      ON po.dc_id = s.dc_id AND po.sku = s.sku AND po.order_date <= s.date
     AND coalesce(po.last_received, DATE '2099-01-01') > s.date
    GROUP BY ALL
),
sup AS (
    SELECT d.date, p.supplier_id,
           avg(po.on_time::INTEGER) AS supplier_on_time_180d,
           stddev_pop(po.lead_days) AS supplier_lead_sd_180d
    FROM (SELECT DISTINCT date FROM snap) d
    CROSS JOIN dim_supplier p
    LEFT JOIN fact_purchase_orders po
      ON po.supplier_id = p.supplier_id AND po.last_received <= d.date AND po.last_received > d.date - 180
    GROUP BY ALL
),
diwali(day) AS (VALUES (DATE '2023-11-12'), (DATE '2024-11-01'), (DATE '2025-10-21'), (DATE '2026-11-08'))
SELECT
    s.date AS snapshot_date, s.dc_id, s.sku, pr.category, pr.abc_class, pr.supplier_id,
    s.on_hand, s.reorder_point, s.below_reorder_point,
    least(s.on_hand / nullif(s.demand_28d, 0), 120) AS cover_days,
    least((s.on_hand + pl.open_po_units) / nullif(s.demand_28d, 0), 120) AS pipeline_cover_days,
    least(s.on_hand / nullif(s.reorder_point, 0), 10) AS stock_to_reorder_point,
    s.demand_7d / nullif(s.demand_28d, 0) AS demand_trend,
    s.demand_sd_28d / nullif(s.demand_28d, 0) AS demand_cv,
    coalesce(su.supplier_on_time_180d, 0.8) AS supplier_on_time_180d,
    coalesce(su.supplier_lead_sd_180d, 2) AS supplier_lead_sd_180d,
    least((SELECT min(day) FROM diwali WHERE day >= s.date) - s.date, 60) AS days_to_diwali,
    s.date + 14 <= DATE '2026-03-31' AS outcome_known,
    CASE WHEN s.date + 14 <= DATE '2026-03-31' THEN s.out_next_14d::INTEGER END AS stockout_14d
FROM snap s
JOIN dim_product pr USING (sku)
JOIN pipeline pl USING (date, dc_id, sku)
JOIN sup su ON su.date = s.date AND su.supplier_id = pr.supplier_id;

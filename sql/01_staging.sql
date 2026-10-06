-- Staging: read the raw exports as text, fix types and formats, clean the hand-kept supplier names.

CREATE OR REPLACE TABLE stg_warehouses AS
SELECT dc_id, dc_city, CAST(lat AS DOUBLE) AS lat, CAST(lon AS DOUBLE) AS lon
FROM read_csv('data/raw/warehouses.csv', all_varchar = true);

CREATE OR REPLACE TABLE stg_zones AS
SELECT zone_id, city, state, CAST(lat AS DOUBLE) AS lat, CAST(lon AS DOUBLE) AS lon, serving_dc AS dc_id,
       CAST(road_km AS DOUBLE) AS road_km, CAST(std_transit_days AS INTEGER) AS std_transit_days
FROM read_csv('data/raw/delivery_zones.csv', all_varchar = true);

-- Supplier names are typed by hand: some are in capitals with trailing spaces. Trim, and restore title case
-- word by word, keeping FMCG as an acronym.
CREATE OR REPLACE TABLE stg_suppliers AS
SELECT
    supplier_id,
    array_to_string(list_transform(string_split(trim(supplier), ' '),
        w -> CASE WHEN upper(w) = 'FMCG' THEN 'FMCG' ELSE upper(left(w, 1)) || lower(substr(w, 2)) END), ' ') AS supplier,
    category, hub, state, CAST(lat AS DOUBLE) AS lat, CAST(lon AS DOUBLE) AS lon,
    CAST(quoted_lead_days AS INTEGER) AS quoted_lead_days
FROM read_csv('data/raw/suppliers.csv', all_varchar = true);

CREATE OR REPLACE TABLE stg_products AS
SELECT sku, category, supplier_id, CAST(unit_cost AS DOUBLE) AS unit_cost, CAST(unit_price AS DOUBLE) AS unit_price,
       CAST(case_qty AS INTEGER) AS case_qty
FROM read_csv('data/raw/products.csv', all_varchar = true);

CREATE OR REPLACE TABLE stg_sales_orders AS
SELECT order_id, zone_id, dc_id, CAST(order_date AS DATE) AS order_date, CAST(promised_date AS DATE) AS promised_date
FROM read_csv('data/raw/sales_orders.csv', all_varchar = true);

CREATE OR REPLACE TABLE stg_order_lines AS
SELECT order_id, sku, CAST(qty_ordered AS INTEGER) AS qty_ordered, CAST(qty_shipped AS INTEGER) AS qty_shipped
FROM read_csv('data/raw/order_lines.csv', all_varchar = true);

CREATE OR REPLACE TABLE stg_shipments AS
SELECT order_id, CAST(ship_date AS DATE) AS ship_date, CAST(delivered_date AS DATE) AS delivered_date, carrier
FROM read_csv('data/raw/shipments.csv', all_varchar = true);

-- The ERP writes purchase-order dates day first.
CREATE OR REPLACE TABLE stg_purchase_orders AS
SELECT po_id, supplier_id, dc_id, sku,
       strptime(order_date, '%d/%m/%Y')::DATE AS order_date,
       strptime(promised_date, '%d/%m/%Y')::DATE AS promised_date,
       CAST(qty_ordered AS INTEGER) AS qty_ordered
FROM read_csv('data/raw/purchase_orders.csv', all_varchar = true);

-- A purchase order can arrive in two deliveries.
CREATE OR REPLACE TABLE stg_goods_receipts AS
SELECT po_id, CAST(received_date AS DATE) AS received_date, CAST(qty_received AS INTEGER) AS qty_received,
       CAST(qty_rejected AS INTEGER) AS qty_rejected
FROM read_csv('data/raw/goods_receipts.csv', all_varchar = true);

CREATE OR REPLACE TABLE stg_inventory AS
SELECT CAST(stock_date AS DATE) AS stock_date, dc_id, sku, CAST(on_hand AS INTEGER) AS on_hand,
       CAST(reorder_point AS INTEGER) AS reorder_point
FROM read_csv('data/raw/inventory_daily.csv', all_varchar = true);

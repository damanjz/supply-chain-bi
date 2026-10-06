"""Break the built data on purpose, one rule at a time, and confirm the matching check fails.

Works on a copy of data/sc.duckdb, so the real build is untouched. Run after src/pipeline.py.
"""
import re
import shutil
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB, COPY = ROOT / "data" / "sc.duckdb", ROOT / "data" / "mutation.duckdb"
FIRST_LINE = "(SELECT min(order_id) FROM stg_order_lines)"

MUTATIONS = {
    "supplier names are clean":
        "UPDATE dim_supplier SET supplier = upper(supplier) || '  ' WHERE supplier_id = 'S001'",
    "every order has exactly one shipment, and dates run in order":
        "UPDATE stg_shipments SET delivered_date = ship_date - 1 WHERE order_id = (SELECT min(order_id) FROM stg_shipments)",
    "orders go to the centre that serves their city":
        "UPDATE stg_sales_orders SET dc_id = CASE dc_id WHEN 'DC-HYD' THEN 'DC-KOL' ELSE 'DC-HYD' END "
        "WHERE order_id = (SELECT min(order_id) FROM stg_sales_orders)",
    "shipped quantities are between zero and the quantity ordered":
        f"UPDATE stg_order_lines SET qty_shipped = qty_ordered + 1 WHERE order_id = {FIRST_LINE} "
        f"AND sku = (SELECT min(sku) FROM stg_order_lines WHERE order_id = {FIRST_LINE})",
    "stock never goes negative":
        "UPDATE fact_inventory_daily SET on_hand = -1 WHERE date = DATE '2024-06-03' AND dc_id = 'DC-HYD' AND sku = 'BE-001'",
    "stock balances day to day (yesterday + accepted receipts - shipments = today)":
        "UPDATE fact_inventory_daily SET on_hand = on_hand + 1 WHERE date = DATE '2024-06-03' AND dc_id = 'DC-HYD' AND sku = 'BE-001'",
    "receipts never exceed the purchase order, rejects never exceed receipts":
        "UPDATE fact_purchase_orders SET qty_rejected = qty_received + 1 WHERE po_id = (SELECT min(po_id) FROM fact_purchase_orders)",
    "every purchase order goes to the product's own supplier":
        "UPDATE stg_purchase_orders SET supplier_id = CASE supplier_id WHEN 'S001' THEN 'S002' ELSE 'S001' END "
        "WHERE po_id = (SELECT min(po_id) FROM stg_purchase_orders)",
    "ABC classes split value at 80% and 95%":
        "UPDATE dim_product SET abc_class = 'A' WHERE abc_class = 'C'",
    "carrying cost is stock value at 24% a year":
        "UPDATE fact_inventory_daily SET carrying_cost = carrying_cost * 1.01 WHERE date = DATE '2024-06-03' AND dc_id = 'DC-HYD' AND sku = 'BE-001'",
    "stockout label matches a direct recount":
        "UPDATE stockout_snapshots SET stockout_14d = 1 - stockout_14d WHERE snapshot_date = DATE '2024-06-03' AND dc_id = 'DC-HYD' AND sku = 'BE-001'",
    "snapshots without 14 days of follow-up carry no outcome":
        "UPDATE stockout_snapshots SET stockout_14d = 0 WHERE snapshot_date = (SELECT max(snapshot_date) FROM stockout_snapshots)",
    "supplier reliability features only use deliveries received before the snapshot":
        "UPDATE stockout_snapshots SET supplier_on_time_180d = supplier_on_time_180d + 0.05 WHERE snapshot_date = DATE '2024-06-03'",
    "every snapshot has one risk score between 0 and 1":
        "DELETE FROM stockout_scores WHERE snapshot_date = DATE '2024-06-03' AND dc_id = 'DC-HYD' AND sku = 'BE-001'",
    "the Tableau table reconciles to its sources, record type by record type":
        "DELETE FROM sc_workbench WHERE rowid = (SELECT min(rowid) FROM sc_workbench WHERE record_type = 'Supplier')",
}


def main():
    text = (ROOT / "sql" / "checks.sql").read_text(encoding="utf-8")
    checks = dict(re.findall(r"-- name: ([^\n]+)\n(.*?;)", text, flags=re.S))
    assert set(checks) == set(MUTATIONS), set(checks) ^ set(MUTATIONS)
    missed = 0
    for name, mutation in MUTATIONS.items():
        shutil.copy(DB, COPY)
        con = duckdb.connect(str(COPY))
        assert not con.sql(checks[name]).fetchall(), f"check fails before mutation: {name}"
        con.execute(mutation)
        caught = bool(con.sql(checks[name]).fetchall())
        missed += not caught
        print(f"  {'caught' if caught else 'MISSED'}  {name}")
        con.close()
    COPY.unlink()
    print(f"{len(MUTATIONS) - missed} of {len(MUTATIONS)} mutations caught")
    sys.exit(1 if missed else 0)


if __name__ == "__main__":
    main()

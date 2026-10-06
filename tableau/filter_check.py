"""Print the numbers the dashboard should show for a set of parameter values, computed directly in DuckDB.

Written independently of the workbook: same definitions, different code. Compare its output with the
dashboard opened with the same values (tableau/build_twb.py --preset ...).
Usage: python tableau/filter_check.py [Warehouse=Kolkata] [Period="FY 2024-25"] [Category=Snacks] [As-of week=2024-10-28]
"""
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]


def main(args):
    p = {"Warehouse": "All", "Period": "All", "Category": "All", "As-of week": None}
    for a in args:
        k, v = a.split("=", 1)
        p[k] = v
    con = duckdb.connect(str(ROOT / "data" / "sc.duckdb"), read_only=True)
    week = p["As-of week"] or con.sql("SELECT max(snapshot_date) FROM stockout_snapshots").fetchone()[0]
    dc = "TRUE" if p["Warehouse"] == "All" else f"w.dc_city = '{p['Warehouse']}'"
    cat = "TRUE" if p["Category"] == "All" else f"x.category = '{p['Category']}'"
    fy = "TRUE" if p["Period"] == "All" else f"d.fiscal_year = '{p['Period']}'"

    lanes = con.sql(f"""
        SELECT avg(x.otif::INTEGER), avg(x.lead_days) FROM fact_order_lines x
        JOIN dim_dc w USING (dc_id) JOIN dim_date d ON d.date = x.order_date WHERE {dc} AND {cat} AND {fy}""").fetchone()
    stock = con.sql(f"""
        SELECT sum(x.stock_value), sum(x.carrying_cost) FROM fact_inventory_daily x
        JOIN dim_dc w USING (dc_id) JOIN dim_date d USING (date) WHERE {dc} AND {cat} AND {fy}""").fetchone()
    cogs = con.sql(f"""
        SELECT sum(x.cogs) FROM fact_order_lines x
        JOIN dim_dc w USING (dc_id) JOIN dim_date d ON d.date = x.ship_date WHERE {dc} AND {cat} AND {fy}""").fetchone()[0]
    print(f"parameters: {p}, as-of week {week}")
    print(f"  OTIF, order lines      {lanes[0]:.1%}")
    print(f"  Order to delivery      {lanes[1]:.1f} days")
    print(f"  Inventory turns        {365 * cogs / stock[0]:.1f}")
    print(f"  Carrying cost          {stock[1] / 1e5:,.1f} lakh")
    gauge = con.sql(f"""
        SELECT w.dc_city, count(*) FILTER (WHERE c.risk_band = 'Watch') AS watch, count(*) FILTER (WHERE c.risk_band = 'High') AS high
        FROM stockout_scores c JOIN dim_dc w USING (dc_id) JOIN dim_product x USING (sku)
        WHERE c.snapshot_date = DATE '{week}' AND {dc} AND {cat} GROUP BY 1 ORDER BY 1""").fetchall()
    print("  Products at risk (watch / high):", ", ".join(f"{r[0]} {r[1]}/{r[2]}" for r in gauge))
    top = con.sql(f"""
        SELECT c.sku, w.dc_city, round(c.risk_score, 2) FROM stockout_scores c JOIN dim_dc w USING (dc_id) JOIN dim_product x USING (sku)
        WHERE c.snapshot_date = DATE '{week}' AND c.risk_band = 'High' AND {dc} AND {cat}
        ORDER BY c.risk_score DESC LIMIT 3""").fetchall()
    print("  Watch list, top 3:", top)
    sup = con.sql(f"""
        SELECT s.supplier, round(avg(po.on_time::INTEGER), 2) AS on_time
        FROM fact_purchase_orders po JOIN dim_supplier s USING (supplier_id) JOIN dim_dc w USING (dc_id)
        JOIN dim_product x USING (sku) JOIN dim_date d ON d.date = date_trunc('month', po.last_received)
        WHERE po.last_received IS NOT NULL AND {dc} AND {cat} AND {fy}
        GROUP BY 1 ORDER BY 2, 1 LIMIT 3""").fetchall()
    print("  Scorecard, least on time:", sup)


if __name__ == "__main__":
    main(sys.argv[1:])

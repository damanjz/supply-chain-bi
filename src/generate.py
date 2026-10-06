"""Generate three years of synthetic exports for an Indian FMCG distributor, April 2023 to March 2026.

A day-by-day simulation of four distribution centres (Hyderabad, Mumbai, Delhi, Kolkata) holding 200 products
from 40 suppliers and serving retailers in 36 cities:
- Demand: retailer orders twice a week per city, shaped by weekday, summer (beverages), the three weeks before
  Diwali (snacks, beverages, home and personal care) and 8% growth a year.
- Fulfilment: each centre ships what it has (short lines are cut, not back-ordered) up to its daily dispatch
  capacity; Kolkata's is tight, so orders queue there at peaks. Transit time follows road distance; monsoon
  rain slows some lanes, most of all the north-east and the Konkan coast.
- Replenishment: a reorder point per product and centre from trailing 28-day demand, the supplier's quoted lead
  time and a safety stock. Trailing demand lags the Diwali ramp, so festive weeks run short. From September 2025
  the distributor pre-builds stock for Diwali (reorder points raised on the festive forecast).
- Suppliers: each has its own on-time habit, defect rate and short-shipping; one slips badly from mid-2024.

Writes ten raw exports to data/raw, shaped like ERP and warehouse system extracts.
"""
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 7
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
START, END = date(2023, 4, 1), date(2026, 4, 1)
DAYS = (END - START).days
rng = np.random.default_rng(SEED)

DIWALI = [date(2023, 11, 12), date(2024, 11, 1), date(2025, 10, 21)]
PREBUILD_FROM = date(2025, 9, 1)            # festive pre-build policy starts
CARRY_RATE = 0.24                          # capital 12%, storage 8%, shrink and obsolescence 4% a year

WAREHOUSES = [  # id, city, lat, lon, dispatch capacity as a multiple of first-year average daily volume
    ("DC-HYD", "Hyderabad", 17.4850, 78.4867, 1.9),
    ("DC-MUM", "Mumbai (Bhiwandi)", 19.2967, 73.0631, 1.9),
    ("DC-DEL", "Delhi NCR (Gurugram)", 28.4595, 77.0266, 1.9),
    ("DC-KOL", "Kolkata", 22.5726, 88.3639, 1.45),
]
CITIES = [  # city, state, lat, lon, weight (relative demand), monsoon exposure (extra delay days in Jul-Sep)
    ("Hyderabad", "Telangana", 17.385, 78.487, 9, 0.2), ("Vijayawada", "Andhra Pradesh", 16.506, 80.648, 3, 0.5),
    ("Visakhapatnam", "Andhra Pradesh", 17.687, 83.218, 3, 0.7), ("Warangal", "Telangana", 17.968, 79.594, 2, 0.3),
    ("Bengaluru", "Karnataka", 12.972, 77.595, 9, 0.3), ("Chennai", "Tamil Nadu", 13.083, 80.271, 8, 0.3),
    ("Coimbatore", "Tamil Nadu", 11.017, 76.956, 3, 0.4), ("Kochi", "Kerala", 9.931, 76.267, 3, 1.4),
    ("Madurai", "Tamil Nadu", 9.925, 78.120, 2, 0.3), ("Nagpur", "Maharashtra", 21.146, 79.088, 3, 0.5),
    ("Mumbai", "Maharashtra", 19.076, 72.878, 10, 0.8), ("Pune", "Maharashtra", 18.520, 73.857, 6, 0.6),
    ("Nashik", "Maharashtra", 19.998, 73.790, 2, 0.6), ("Ahmedabad", "Gujarat", 23.023, 72.571, 6, 0.3),
    ("Surat", "Gujarat", 21.170, 72.831, 4, 0.5), ("Vadodara", "Gujarat", 22.307, 73.181, 2, 0.3),
    ("Goa", "Goa", 15.491, 73.828, 2, 1.6), ("Indore", "Madhya Pradesh", 22.720, 75.858, 3, 0.4),
    ("Aurangabad", "Maharashtra", 19.877, 75.343, 2, 0.4), ("Delhi", "Delhi", 28.704, 77.102, 10, 0.3),
    ("Jaipur", "Rajasthan", 26.912, 75.787, 4, 0.2), ("Lucknow", "Uttar Pradesh", 26.847, 80.946, 5, 0.5),
    ("Chandigarh", "Chandigarh", 30.733, 76.779, 3, 0.4), ("Ludhiana", "Punjab", 30.901, 75.857, 3, 0.4),
    ("Agra", "Uttar Pradesh", 27.177, 78.008, 2, 0.3), ("Kanpur", "Uttar Pradesh", 26.449, 80.331, 3, 0.5),
    ("Dehradun", "Uttarakhand", 30.317, 78.032, 2, 1.2), ("Bhopal", "Madhya Pradesh", 23.260, 77.413, 3, 0.4),
    ("Kolkata", "West Bengal", 22.573, 88.364, 8, 0.8), ("Bhubaneswar", "Odisha", 20.296, 85.825, 3, 0.9),
    ("Patna", "Bihar", 25.594, 85.137, 4, 1.0), ("Ranchi", "Jharkhand", 23.344, 85.310, 2, 0.8),
    ("Guwahati", "Assam", 26.144, 91.736, 3, 2.4), ("Siliguri", "West Bengal", 26.727, 88.395, 2, 2.0),
    ("Raipur", "Chhattisgarh", 21.251, 81.630, 2, 0.6), ("Varanasi", "Uttar Pradesh", 25.318, 82.974, 2, 0.6),
]
HUBS = [  # supplier manufacturing hubs
    ("Baddi", "Himachal Pradesh", 30.957, 76.791), ("Haridwar", "Uttarakhand", 29.945, 78.164),
    ("Chakan", "Maharashtra", 18.760, 73.861), ("Sanand", "Gujarat", 22.992, 72.381),
    ("Sriperumbudur", "Tamil Nadu", 12.968, 79.947), ("Hosur", "Tamil Nadu", 12.740, 77.825),
    ("Howrah", "West Bengal", 22.596, 88.264), ("Patancheru", "Telangana", 17.533, 78.265),
    ("Silvassa", "Dadra and Nagar Haveli", 20.274, 73.008), ("Pithampur", "Madhya Pradesh", 22.611, 75.676),
]
CATEGORIES = {  # category: (products, suppliers, unit cost range in rupees, festive lift, summer lift)
    "Personal care": (50, 10, (35, 420), 1.35, 1.0),
    "Home care": (40, 8, (40, 380), 1.45, 1.0),
    "Packaged foods": (50, 10, (25, 300), 1.20, 1.0),
    "Beverages": (30, 6, (20, 220), 1.50, 1.45),
    "Snacks": (30, 6, (10, 150), 1.70, 1.05),
}
CARRIERS = [("Sahyadri Roadlines", 0.0), ("Ganga Freight", 0.15), ("Deccan Express Logistics", 0.05)]
NAMES = ["Aarav", "Bharat", "Chetak", "Devi", "Eshan", "Ganga", "Himalaya", "Indra", "Jyoti", "Kaveri", "Lakshmi",
         "Meera", "Narmada", "Om", "Prakash", "Rajat", "Sagar", "Tara", "Uday", "Vasant"]
SUFFIX = ["Consumer Products", "Foods", "Industries", "Hygiene", "Agro", "Beverages", "Home Products", "FMCG"]


def km(a, b):
    """Road distance: great-circle distance times 1.3."""
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    d = math.acos(min(1, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(math.radians(lo2 - lo1))))
    return 6371 * d * 1.3


def festive_factor(d, lift):
    """Demand lift in the 24 days before Diwali, easing off over the week after."""
    best = 1.0
    for dw in DIWALI:
        delta = (dw - d).days
        if 0 <= delta <= 24:
            best = max(best, 1 + (lift - 1) * (1 - delta / 30))
        elif -7 <= delta < 0:
            best = max(best, 1 + (lift - 1) * 0.4 * (1 + delta / 7))
    return best


def summer_factor(d, lift):
    return lift if d.month in (4, 5, 6) else 1.0


def masters():
    whs = pd.DataFrame(WAREHOUSES, columns=["dc_id", "dc_city", "lat", "lon", "capacity"])
    cities = pd.DataFrame(CITIES, columns=["city", "state", "lat", "lon", "weight", "monsoon"])
    cities["zone_id"] = [f"Z{i + 1:02d}" for i in range(len(cities))]
    dist = np.array([[km((c.lat, c.lon), (w.lat, w.lon)) for w in whs.itertuples()] for c in cities.itertuples()])
    cities["dc_idx"] = dist.argmin(axis=1)
    cities["road_km"] = dist.min(axis=1).round()
    cities["std_transit_days"] = np.maximum(1, np.ceil(cities.road_km / 450)).astype(int)

    sup_rows, prod_rows = [], []
    sid = 0
    for cat, (n_prod, n_sup, (lo, hi), _, _) in CATEGORIES.items():
        cat_sups = []
        for _ in range(n_sup):
            sid += 1
            hub = HUBS[rng.integers(len(HUBS))]
            name = f"{NAMES[rng.integers(len(NAMES))]} {SUFFIX[rng.integers(len(SUFFIX))]}"
            on_time = float(np.clip(rng.beta(9, 1.6), 0.55, 0.98))
            sup_rows.append(dict(supplier_id=f"S{sid:03d}", supplier=name, category=cat, hub=hub[0], state=hub[1],
                                 lat=hub[2], lon=hub[3], on_time=on_time,
                                 defect=float(np.clip(rng.lognormal(math.log(0.012), 0.8), 0.001, 0.08)),
                                 short=float(np.clip(rng.beta(1.2, 18), 0.0, 0.25))))
            cat_sups.append(sid - 1)
        for k in range(n_prod):
            cost = round(float(np.exp(rng.uniform(math.log(lo), math.log(hi)))), 2)
            prod_rows.append(dict(sku=f"{cat[:2].upper()}-{k + 1:03d}", category=cat,
                                  supplier_idx=cat_sups[rng.integers(len(cat_sups))], unit_cost=cost,
                                  unit_price=round(cost * rng.uniform(1.18, 1.35), 2),
                                  case_qty=int(rng.choice([6, 12, 24, 48])),
                                  popularity=float(rng.lognormal(0, 0.9))))
    sups = pd.DataFrame(sup_rows)
    prods = pd.DataFrame(prod_rows)
    # one supplier, reliable until June 2024, slips badly afterwards: the snacks supplier with the most products
    slip = int(prods[prods.category == "Snacks"].supplier_idx.value_counts().idxmax())
    sups.loc[slip, "on_time"] = 0.93
    # quoted lead time: travel to the farthest centre plus production time
    sups["quoted_lead_days"] = [int(5 + max(km((s.lat, s.lon), (w.lat, w.lon)) for w in whs.itertuples()) // 600)
                                for s in sups.itertuples()]
    return whs, cities, sups, prods, slip


def simulate():
    whs, cities, sups, prods, slip = masters()
    n_dc, n_sku = len(whs), len(prods)
    cat_of = prods.category.to_numpy()
    sup_of = prods.supplier_idx.to_numpy()
    cost = prods.unit_cost.to_numpy()
    # daily units per city per product at the start: popularity x city weight, scaled to a cheap-good FMCG volume
    base = np.outer(cities.weight.to_numpy(), prods.popularity.to_numpy()) * 0.9 / np.sqrt(cost / 50)
    zone_dc = cities.dc_idx.to_numpy()
    # a retailer order carries a product with probability p (popular lines nearly always), in units scaled by 1/p
    include_p = np.clip(prods.popularity.to_numpy() / 2.5, 0.12, 1.0)
    order_days = {z: set(rng.choice(6, 2, replace=False)) for z in range(len(cities))}   # two weekdays Mon-Sat

    # starting stock: 30 days of expected demand
    dc_rate = np.zeros((n_dc, n_sku))
    for z in range(len(cities)):
        dc_rate[zone_dc[z]] += base[z]
    on_hand = np.round(dc_rate * 30)
    capacity = dc_rate.sum(axis=1) * 7 / 6 * whs.capacity.to_numpy()     # units dispatched a working day
    history = [dc_rate.copy() for _ in range(28)]         # trailing demand, newest last
    on_order = np.zeros((n_dc, n_sku))
    arrivals = defaultdict(list)                          # day -> receipts
    queue = [[] for _ in range(n_dc)]                     # orders waiting for dispatch

    orders, lines, shipments, pos, receipts, inventory = [], [], [], [], [], []
    oid = poid = 0
    last_order = {z: -3 for z in range(len(cities))}

    for t in range(DAYS):
        d = START + timedelta(days=t)
        years = t / 365.25
        season = np.array([festive_factor(d, CATEGORIES[c][3]) * summer_factor(d, CATEGORIES[c][4]) for c in CATEGORIES])
        cat_idx = {c: i for i, c in enumerate(CATEGORIES)}
        sku_season = season[[cat_idx[c] for c in cat_of]]
        monsoon = d.month in (7, 8, 9)

        # 1. goods receipts land in the morning; quality control rejects defects
        for (dc, k, qty, po_id) in arrivals.pop(t, []):
            s = sup_of[k]
            rejected = rng.binomial(int(qty), sups.defect.iat[s]) if qty > 0 else 0
            on_hand[dc, k] += qty - rejected
            on_order[dc, k] -= qty
            receipts.append((po_id, d, int(qty), int(rejected)))

        # 2. retailer orders arrive (no orders on Sunday)
        demand_today = np.zeros((n_dc, n_sku))
        if d.weekday() < 6:
            for z in range(len(cities)):
                if d.weekday() not in order_days[z]:
                    continue
                gap = t - last_order[z]
                last_order[z] = t
                carried = rng.random(n_sku) < include_p
                qty = rng.poisson(base[z] * sku_season * (1.08 ** years) * gap * rng.uniform(0.85, 1.15)
                                  / include_p) * carried
                if qty.sum() == 0:
                    continue
                oid += 1
                dc = zone_dc[z]
                order = dict(order_id=f"SO{oid:07d}", zone=z, dc=dc, order_date=d,
                             promised=d + timedelta(days=1 + int(cities.std_transit_days.iat[z])), qty=qty)
                queue[dc].append(order)
                demand_today[dc] += qty

        # 3. dispatch (Monday to Saturday): first come first served, up to the day's capacity in units;
        #    short lines are cut, not back-ordered
        for dc in range(n_dc):
            if d.weekday() == 6:
                continue
            budget, n_out = capacity[dc] * (1.08 ** years) * rng.uniform(0.9, 1.1), 0   # capacity grows with the business
            for order in queue[dc]:
                if n_out and budget < order["qty"].sum():
                    break
                budget -= order["qty"].sum()
                n_out += 1
                qty = order["qty"]
                shipped = np.minimum(qty, np.maximum(on_hand[dc], 0))
                on_hand[dc] -= shipped
                z = order["zone"]
                transit = int(cities.std_transit_days.iat[z])
                carrier = int(rng.integers(len(CARRIERS)))
                delay = rng.poisson(0.25 + (cities.monsoon.iat[z] * (1 + CARRIERS[carrier][1] * 4) if monsoon else 0))
                delivered = d + timedelta(days=transit + int(delay) + int(rng.random() < 0.15))
                orders.append((order["order_id"], cities.zone_id.iat[z], whs.dc_id.iat[dc], order["order_date"],
                               order["promised"]))
                shipments.append((order["order_id"], d, delivered, CARRIERS[carrier][0]))
                for k in np.nonzero(qty)[0]:
                    lines.append((order["order_id"], prods.sku.iat[k], int(qty[k]), int(shipped[k])))
            queue[dc] = queue[dc][n_out:]

        # 4. replenishment review at end of day
        history.append(demand_today / 1.0)
        history.pop(0)
        trailing = np.mean(history, axis=0)                     # units a day, last 28 days
        sd = np.std(history, axis=0)
        lead = sups.quoted_lead_days.to_numpy()[sup_of]
        rop = trailing * lead + 1.0 * sd * np.sqrt(lead)
        if d >= PREBUILD_FROM:
            # festive pre-build: plan on the expected Diwali lift for the next lead time
            ahead = np.array([max(festive_factor(d + timedelta(days=i), CATEGORIES[c][3]) for i in range(0, 22, 3))
                              for c in CATEGORIES])
            rop = rop * ahead[[cat_idx[c] for c in cat_of]]
        target = rop + trailing * 30
        position = on_hand + on_order
        need = np.nonzero(position <= rop)
        for dc, k in zip(*need):
            qty = float(np.ceil(max(target[dc, k] - position[dc, k], 0) / prods.case_qty.iat[k]) * prods.case_qty.iat[k])
            if qty <= 0:
                continue
            s = sup_of[k]
            poid += 1
            po_id = f"PO{poid:07d}"
            quoted = int(sups.quoted_lead_days.iat[s])
            on_time_p = sups.on_time.iat[s]
            if s == slip and d >= date(2024, 7, 1):
                on_time_p = 0.45
            late = 0 if rng.random() < on_time_p else int(rng.gamma(2, 2.5)) + 1
            if monsoon:
                late += int(rng.poisson(1.2))
            actual = max(2, quoted - int(rng.integers(0, 3)) + late)
            delivered_qty = qty if rng.random() > sups.short.iat[s] else float(np.floor(qty * rng.uniform(0.5, 0.9)))
            pos.append((po_id, sups.supplier_id.iat[s], whs.dc_id.iat[dc], prods.sku.iat[k], d,
                        d + timedelta(days=quoted), int(qty)))
            on_order[dc, k] += qty
            if rng.random() < 0.15 and delivered_qty >= 2:      # split delivery
                first = float(np.floor(delivered_qty / 2))
                arrivals[t + actual].append((dc, k, first, po_id))
                arrivals[t + actual + int(rng.integers(1, 5))].append((dc, k, delivered_qty - first, po_id))
            else:
                arrivals[t + actual].append((dc, k, delivered_qty, po_id))
            on_order[dc, k] -= qty - delivered_qty          # the short-shipped part never arrives

        # 5. end-of-day stock record, as the warehouse system exports it
        for dc in range(n_dc):
            inventory.append((d, dc, on_hand[dc].copy(), np.round(rop[dc]).copy()))

    return whs, cities, sups, prods, orders, lines, shipments, pos, receipts, inventory


def export(whs, cities, sups, prods, orders, lines, shipments, pos, receipts, inventory):
    RAW.mkdir(parents=True, exist_ok=True)
    whs[["dc_id", "dc_city", "lat", "lon"]].to_csv(RAW / "warehouses.csv", index=False)
    z = cities.assign(serving_dc=whs.dc_id.to_numpy()[cities.dc_idx])
    z[["zone_id", "city", "state", "lat", "lon", "serving_dc", "road_km", "std_transit_days"]] \
        .to_csv(RAW / "delivery_zones.csv", index=False)
    # the supplier master is maintained by hand: names carry stray spaces and mixed case
    s = sups.copy()
    s["supplier"] = [n.upper() + "  " if i % 7 == 0 else n for i, n in enumerate(s.supplier)]
    s[["supplier_id", "supplier", "category", "hub", "state", "lat", "lon", "quoted_lead_days"]] \
        .to_csv(RAW / "suppliers.csv", index=False)
    p = prods.assign(supplier_id=sups.supplier_id.to_numpy()[prods.supplier_idx])
    p[["sku", "category", "supplier_id", "unit_cost", "unit_price", "case_qty"]].to_csv(RAW / "products.csv", index=False)
    pd.DataFrame(orders, columns=["order_id", "zone_id", "dc_id", "order_date", "promised_date"]) \
        .to_csv(RAW / "sales_orders.csv", index=False)
    pd.DataFrame(lines, columns=["order_id", "sku", "qty_ordered", "qty_shipped"]).to_csv(RAW / "order_lines.csv", index=False)
    pd.DataFrame(shipments, columns=["order_id", "ship_date", "delivered_date", "carrier"]) \
        .to_csv(RAW / "shipments.csv", index=False)
    # the ERP exports purchase orders with day-first dates
    po = pd.DataFrame(pos, columns=["po_id", "supplier_id", "dc_id", "sku", "order_date", "promised_date", "qty_ordered"])
    for c in ("order_date", "promised_date"):
        po[c] = pd.to_datetime(po[c]).dt.strftime("%d/%m/%Y")
    po.to_csv(RAW / "purchase_orders.csv", index=False)
    pd.DataFrame(receipts, columns=["po_id", "received_date", "qty_received", "qty_rejected"]) \
        .to_csv(RAW / "goods_receipts.csv", index=False)
    rows = []
    skus = prods.sku.to_numpy()
    for d, dc, stock, rop in inventory:
        rows.append(pd.DataFrame({"stock_date": d, "dc_id": whs.dc_id.iat[dc], "sku": skus,
                                  "on_hand": stock.astype(int), "reorder_point": rop.astype(int)}))
    pd.concat(rows).to_csv(RAW / "inventory_daily.csv", index=False)


def main():
    res = simulate()
    whs, cities, sups, prods, orders, lines, shipments, pos, receipts, inventory = res
    print(f"orders {len(orders):,}  lines {len(lines):,}  purchase orders {len(pos):,}  receipts {len(receipts):,}  "
          f"stock rows {len(inventory) * len(prods):,}")
    export(*res)
    print(f"wrote raw exports to {RAW}")


if __name__ == "__main__":
    main()

"""Build the DuckDB model, train the stockout model, run the checks, export for Tableau."""
import os
import re
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "sc.duckdb"
OUT = ROOT / "data" / "model"
EXPORT = ["dim_dc", "dim_zone", "dim_supplier", "dim_product", "model_metrics", "model_calibration",
          "model_importance", "sc_workbench"]


def run_sql(con, name):
    con.execute((ROOT / "sql" / name).read_text(encoding="utf-8"))
    print(f"built {name}")


def run_checks(con):
    text = (ROOT / "sql" / "checks.sql").read_text(encoding="utf-8")
    failed = 0
    for name, sql in re.findall(r"-- name: ([^\n]+)\n(.*?;)", text, flags=re.S):
        bad = con.sql(sql).fetchall()
        failed += bool(bad)
        print(f"  {'FAIL' if bad else 'pass'}  {name}" + (f"  ({len(bad)} rows, e.g. {bad[0]})" if bad else ""))
    return failed


def main():
    os.chdir(ROOT)
    DB.unlink(missing_ok=True)
    con = duckdb.connect(str(DB))
    for name in ("01_staging.sql", "02_model.sql", "03_features.sql"):
        run_sql(con, name)
    metrics = model.run(con)
    chosen = metrics[metrics.chosen].iloc[0]
    rule = metrics[metrics.model == "Reorder-point rule"].iloc[0]
    print(f"trained model: {chosen.model}, test ROC AUC {chosen.roc_auc:.3f} (reorder-point rule {rule.roc_auc:.3f}), "
          f"stockouts caught at the rule's flag rate {chosen.capture_at_rule_share:.1%} vs {rule.capture_at_rule_share:.1%}")
    run_sql(con, "04_marts.sql")
    print("checks:")
    failed = run_checks(con)
    if failed:
        print(f"{failed} checks failed; nothing exported")
        con.close()
        sys.exit(1)
    OUT.mkdir(parents=True, exist_ok=True)
    for t in EXPORT:
        con.execute(f"COPY (SELECT * FROM {t} ORDER BY ALL) TO '{(OUT / (t + '.csv')).as_posix()}' (HEADER)")
    print(f"exported {len(EXPORT)} tables to data/model")
    con.close()


if __name__ == "__main__":
    main()

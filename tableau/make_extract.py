"""Write the Tableau extract (.hyper) for the workbench with Tableau's Hyper API.

Tableau Public only accepts extracts, so the packaged workbook carries this file instead of a live CSV.
Usage telemetry to Tableau is switched off.
"""
from pathlib import Path

import duckdb
from tableauhyperapi import (Connection, CreateMode, HyperProcess, Inserter, SqlType, TableDefinition, TableName,
                             Telemetry)

ROOT = Path(__file__).resolve().parents[1]
HYPER_TYPES = {"VARCHAR": SqlType.text(), "DATE": SqlType.date(), "DOUBLE": SqlType.double(),
               "BIGINT": SqlType.big_int(), "INTEGER": SqlType.big_int(), "HUGEINT": SqlType.big_int(),
               "BOOLEAN": SqlType.bool()}
TABLE = TableName("Extract", "Extract")


def write_extract(out_path, table="sc_workbench"):
    con = duckdb.connect(str(ROOT / "data" / "sc.duckdb"), read_only=True)
    cols = [(c, t.split("(")[0]) for c, t, *_ in con.sql(f"DESCRIBE {table}").fetchall()]
    rows = con.sql(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
    con.close()
    definition = TableDefinition(TABLE, [TableDefinition.Column(c, HYPER_TYPES[t]) for c, t in cols])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,
                      parameters={"log_dir": str(out_path.parent)}) as hyper:
        with Connection(hyper.endpoint, str(out_path), CreateMode.CREATE_AND_REPLACE) as conn:
            conn.catalog.create_schema("Extract")
            conn.catalog.create_table(definition)
            with Inserter(conn, definition) as ins:
                ins.add_rows(rows)
                ins.execute()
            count = conn.execute_scalar_query(f"SELECT COUNT(*) FROM {TABLE}")
    for log in out_path.parent.glob("hyperd*.log"):
        log.unlink()
    return cols, count


if __name__ == "__main__":
    cols, n = write_extract(ROOT / ".captures" / "test.hyper")
    print(f"{n} rows, {len(cols)} columns")

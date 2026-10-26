"""SQLite warehouse: machine-day production facts plus the raw event logs."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path("data/oee.db")


def machine_day(h: pd.DataFrame) -> pd.DataFrame:
    g = h.groupby(["date", "machineID"])
    d = g.agg(planned_hours=("planned_hours", "sum"), run_hours=("run_hours", "sum"), ideal_output=("ideal_output", "sum"),
              output=("output", "sum"), good_output=("good_output", "sum"), scrap=("scrap", "sum"),
              slow_hours=("slow_running", "sum"), out_of_spec_hours=("out_of_spec", "sum")).reset_index()
    st = h.pivot_table(index=["date", "machineID"], columns="state", values="datetime", aggfunc="count", fill_value=0)
    st.columns = [c.lower().replace(" ", "_") + "_hours" for c in st.columns]
    out = d.merge(st.reset_index(), on=["date", "machineID"], how="left")
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(3)
    return out


def build(h: pd.DataFrame, t: dict, path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    md = machine_day(h)
    md["date"] = md["date"].dt.strftime("%Y-%m-%d")
    md.to_sql("machine_day", con, index=False)
    t["machines"].to_sql("machines", con, index=False)
    for name in ("failures", "errors"):
        e = t[name].copy()
        e["datetime"] = e["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
        e.to_sql(name, con, index=False)
    m = t["maint"].copy()
    m["datetime"] = m["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    m["after_failure"] = m["after_failure"].astype(int)
    m.to_sql("maintenance", con, index=False)
    con.commit()
    return con


def run_queries(con: sqlite3.Connection, sql_dir: Path = Path("sql")) -> dict[str, pd.DataFrame]:
    return {f.stem[3:]: pd.read_sql_query(f.read_text(), con) for f in sorted(sql_dir.glob("*.sql"))}

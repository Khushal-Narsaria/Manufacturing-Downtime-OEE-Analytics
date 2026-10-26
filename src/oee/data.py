"""Load the machine logs and build an hourly production table with OEE components.

The source has hourly sensor readings, error events, maintenance and failures
for 100 machines over 2015. It does not record repair durations, output or
scrap, so these are derived with the assumptions in ASSUMPTIONS (one place,
easy to change and re-run).
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

BASE_URL = "https://raw.githubusercontent.com/AbdelhafidhMahouel/predictive-maintenance-db/main/data/"
RAW = Path("data/raw")
FILES = ["PdM_telemetry", "PdM_errors", "PdM_maint", "PdM_failures", "PdM_machines"]
SENSORS = ["volt", "rotate", "pressure", "vibration"]

ASSUMPTIONS = {
    "repair_hours_per_failure": 8,        # unplanned breakdown: machine stopped while the component is replaced
    "stop_hours_per_error": 1,            # minor stoppage: an error alarm stops the machine for an hour
    "planned_maintenance_hours": 2,       # scheduled component replacement (not after a failure)
    "units_per_hour_at_rated_speed": 100, # ideal rate when the spindle runs at rated speed
    "rated_speed_percentile": 50,         # rated speed = median of the model's healthy running speed
    "slow_running_percentile": 5,         # speed loss counts only below the 5th percentile of healthy speed
    "out_of_spec_sigma": 3,               # an hour is scrap when a process sensor is beyond 3 sigma of normal
    "healthy_gap_hours": 72,              # 'healthy' hours are at least 3 days away from any failure
}


def download(raw: Path = RAW) -> Path:
    raw.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        p = raw / f"{f}.csv"
        if not p.exists():
            print(f"Downloading {f}.csv ...")
            urllib.request.urlretrieve(BASE_URL + f + ".csv", p)
    return raw


def load(raw: Path = RAW) -> dict[str, pd.DataFrame]:
    out = {}
    for f in FILES:
        df = pd.read_csv(raw / f"{f}.csv")
        if "datetime" in df:
            df["datetime"] = pd.to_datetime(df["datetime"])
        out[f.replace("PdM_", "")] = df
    return out


def clean(t: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], dict]:
    q = {}
    tel = t["telemetry"]
    q["telemetry_rows"] = int(len(tel))
    q["telemetry_duplicates"] = int(tel.duplicated(["datetime", "machineID"]).sum())
    tel = tel.drop_duplicates(["datetime", "machineID"]).sort_values(["machineID", "datetime"])
    q["telemetry_missing_values"] = int(tel[SENSORS].isna().sum().sum())
    expected = tel.groupby("machineID")["datetime"].agg(lambda s: (s.max() - s.min()) / pd.Timedelta(hours=1) + 1)
    q["telemetry_hours_missing"] = int((expected - tel.groupby("machineID").size()).sum())

    start, end = tel["datetime"].min(), tel["datetime"].max()
    maint = t["maint"]
    q["maintenance_records"] = int(len(maint))
    q["maintenance_before_telemetry_starts"] = int((maint["datetime"] < start).sum())
    fail = t["failures"].drop_duplicates()
    err = t["errors"].drop_duplicates()
    q["failures"] = int(len(fail))
    q["errors"] = int(len(err))

    # A maintenance record at the same hour as a failure of that component is the repair, not planned work.
    m = maint.merge(fail.rename(columns={"failure": "comp"}).assign(after_failure=True),
                    on=["datetime", "machineID", "comp"], how="left")
    m["after_failure"] = m["after_failure"].fillna(False).astype(bool)
    q["maintenance_after_failure"] = int(m["after_failure"].sum())
    q["planned_maintenance"] = int((~m["after_failure"]).sum())
    q["machines"] = int(t["machines"]["machineID"].nunique())
    q["machines_without_failures"] = int(q["machines"] - fail["machineID"].nunique())
    q["period"] = f"{start:%Y-%m-%d} to {end:%Y-%m-%d}"
    return {"telemetry": tel, "errors": err, "maint": m, "failures": fail, "machines": t["machines"]}, q


def _window_hours(events: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Expand each event into the hourly slots it covers."""
    rows = [events.assign(datetime=events["datetime"] + pd.Timedelta(hours=h)) for h in range(hours)]
    return pd.concat(rows, ignore_index=True)


def hourly(t: dict[str, pd.DataFrame], a: dict = ASSUMPTIONS) -> pd.DataFrame:
    """One row per machine-hour with state, output, scrap and the cause of any lost time."""
    h = t["telemetry"].merge(t["machines"], on="machineID", how="left").copy()
    key = ["machineID", "datetime"]

    planned = _window_hours(t["maint"][~t["maint"]["after_failure"]][key], a["planned_maintenance_hours"]).drop_duplicates(key)
    breakdown = _window_hours(t["failures"][key + ["failure"]], a["repair_hours_per_failure"]) \
        .sort_values(key).drop_duplicates(key)
    stops = t["errors"][key + ["errorID"]].sort_values(key).drop_duplicates(key)

    h = h.merge(planned.assign(planned_stop=True), on=key, how="left")
    h = h.merge(breakdown.rename(columns={"failure": "breakdown_comp"}), on=key, how="left")
    h = h.merge(stops.rename(columns={"errorID": "stop_error"}), on=key, how="left")
    h["planned_stop"] = h["planned_stop"].fillna(False).astype(bool)

    # Priority when slots overlap: planned stop > breakdown > minor stop.
    h["state"] = np.select(
        [h["planned_stop"], h["breakdown_comp"].notna(), h["stop_error"].notna()],
        ["Planned maintenance", "Breakdown", "Minor stop"], default="Running")
    h["loss_cause"] = np.select(
        [h["state"] == "Planned maintenance", h["state"] == "Breakdown", h["state"] == "Minor stop"],
        ["Planned maintenance", "Breakdown: " + h["breakdown_comp"].fillna(""), "Minor stop: " + h["stop_error"].fillna("")],
        default="")

    # Healthy running hours set the rated speed and the normal process window per model.
    fails = t["failures"][key]
    near = _window_hours(fails.assign(datetime=fails["datetime"] - pd.Timedelta(hours=a["healthy_gap_hours"])),
                         2 * a["healthy_gap_hours"]).drop_duplicates(key)
    h = h.merge(near.assign(near_failure=True), on=key, how="left")
    h["near_failure"] = h["near_failure"].fillna(False).astype(bool)
    healthy = h[(h["state"] == "Running") & ~h["near_failure"]]
    rated = healthy.groupby("model")["rotate"].quantile(a["rated_speed_percentile"] / 100)
    slow = healthy.groupby("model")["rotate"].quantile(a["slow_running_percentile"] / 100)
    mu = healthy.groupby("model")[["volt", "pressure", "vibration"]].mean()
    sd = healthy.groupby("model")[["volt", "pressure", "vibration"]].std()

    run = h["state"].eq("Running")
    h["planned_hours"] = (~h["planned_stop"]).astype(float)
    h["run_hours"] = run.astype(float)
    rate = a["units_per_hour_at_rated_speed"]
    h["ideal_output"] = h["run_hours"] * rate
    # Normal speed variation is not a loss; only abnormally slow hours lose output.
    is_slow = h["rotate"] < h["model"].map(slow)
    h["slow_running"] = is_slow & run
    speed = np.where(is_slow, (h["rotate"] / h["model"].map(rated)).clip(upper=1.0), 1.0)
    h["output"] = np.where(run, rate * speed, 0.0)
    z = pd.DataFrame({c: (h[c] - h["model"].map(mu[c])) / h["model"].map(sd[c]) for c in ["volt", "pressure", "vibration"]})
    h["out_of_spec"] = (z.abs() > a["out_of_spec_sigma"]).any(axis=1) & run
    h["scrap"] = np.where(h["out_of_spec"], h["output"], 0.0)
    h["good_output"] = h["output"] - h["scrap"]
    h["month"] = h["datetime"].dt.to_period("M").dt.to_timestamp()
    h["date"] = h["datetime"].dt.normalize()
    return h

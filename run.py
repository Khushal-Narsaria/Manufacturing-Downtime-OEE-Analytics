"""Run everything: clean -> hourly OEE -> SQL -> losses & Pareto -> reliability -> statistics -> risk forecast -> exports."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))
from oee import analysis as an, data, forecast as fc, warehouse  # noqa: E402

DOCS, RESULTS, POWERBI = Path("docs"), Path("results"), Path("powerbi/data")


def rec(df: pd.DataFrame) -> list[dict]:
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%d")
    return json.loads(d.to_json(orient="records"))


def main() -> None:
    for p in (DOCS, RESULTS, POWERBI):
        p.mkdir(parents=True, exist_ok=True)
    t, quality = data.clean(data.load(data.download()))
    h = data.hourly(t)
    rate = data.ASSUMPTIONS["units_per_hour_at_rated_speed"]
    quality["failure_hours_logged"] = {str(k): int(v) for k, v in t["failures"]["datetime"].dt.hour.value_counts().items()}
    print("Quality:", quality)

    con = warehouse.build(h, t)
    sql = warehouse.run_queries(con)
    con.close()

    head = an.headline(h, t)
    losses = an.loss_tree(h, rate)
    down = an.downtime_pareto(h)
    machines = an.machine_pareto(h, rate)
    rel = an.mtbf(h, t)
    lift = an.errors_before_failures(t)
    rep = an.replacement_age(t)
    pat = an.patterns(h, t)
    tests = an.tests(h, t, rep)
    reg, pseudo_r2 = an.failure_regression(t, h)
    monthly = an.oee_by(h, "month")
    by_model = an.oee_by(h, "model")
    print(head)
    print(losses.to_string(index=False))
    print(down)

    X = fc.features(t)
    sensors = fc.train_and_test(X, t, use_alarms=False)
    alarms = fc.train_and_test(X, t, use_alarms=True)
    risk = fc.current_risk(sensors["model"], X, t, sensors["cols"])
    print("Sensors-only:", sensors["metrics"])
    print("With alarms:", alarms["metrics"])

    machine_info = t["machines"].set_index("machineID")
    nf = t["failures"].groupby("machineID").size().reindex(machine_info.index, fill_value=0)
    top_machines = machines.head(15).join(machine_info).join(nf.rename("failures"))
    top_machines["share_of_machines_pct"] = 100 / len(machine_info)
    n50 = int((machines["cumulative_pct"] < 50).sum() + 1)
    by_age = pd.DataFrame({"age": machine_info["age"], "failures": nf}).groupby("age")["failures"].mean().round(2)
    best_lift = lift.loc[lift.groupby("component")["lift"].idxmax()].sort_values("component")
    rep_med = rep.groupby("after_failure")["days_since_replacement"].median()
    breakdown_share = float(down[down.index.str.startswith("Breakdown")]["share_pct"].sum())

    headline = {
        **head,
        "breakdown_share_of_downtime_pct": round(breakdown_share, 1),
        "top_downtime_cause": down.index[0], "top_downtime_cause_pct": float(down["share_pct"].iloc[0]),
        "losses_top": losses.sort_values("lost_units", ascending=False).iloc[0]["loss"],
        "machines_for_half_of_losses": n50,
        "mtbf_by_component_hours": {k: float(v) for k, v in rel["by_component"].items()},
        "mtbf_by_model_hours": {k: float(v) for k, v in rel["by_model"].items()},
        "median_hours_between_failures": rel["gap_median"],
        "days_since_replacement_at_failure": float(rep_med.get(True)),
        "days_since_replacement_planned": float(rep_med.get(False)),
        "scrap_rate_normal_pct": pat["scrap_rate_normal_pct"], "scrap_rate_near_failure_pct": pat["scrap_rate_near_failure_pct"],
        "repeat_failure_share_pct": pat["repeat_failure_share_pct"],
        "risk_sensors": sensors["metrics"], "risk_alarms": alarms["metrics"],
        "risk_threshold": sensors["threshold"],
        "poisson_pseudo_r2": pseudo_r2,
        "reactive_share_pct": round(float(t["maint"][t["maint"]["datetime"] >= "2015-01-01"]["after_failure"].mean() * 100), 1),
        "assumptions": data.ASSUMPTIONS,
    }

    run = h[h["run_hours"] > 0]
    dash = {
        "headline": headline, "quality": quality,
        "monthly": rec(monthly.rename_axis("month").reset_index()),
        "by_model": rec(by_model.rename_axis("model").reset_index()),
        "losses": rec(losses),
        "downtime_pareto": rec(down.rename_axis("cause").reset_index()),
        "machine_pareto_curve": [{"machines_pct": round((i + 1) / len(machines) * 100, 1), "cum_pct": float(c)}
                                 for i, c in enumerate(machines["cumulative_pct"])],
        "top_machines": rec(top_machines.rename_axis("machineID").reset_index()),
        "mtbf_component": rec(rel["by_component"].rename("mtbf_hours").rename_axis("component").reset_index()),
        "mtbf_model": rec(rel["by_model"].rename("mtbf_hours").rename_axis("model").reset_index()),
        "failures_by_age": rec(by_age.rename("avg_failures").rename_axis("age").reset_index()),
        "failures_by_month": rec(pat["failures_by_month"].rename("failures").rename_axis("month").reset_index()),
        "error_lift": rec(best_lift),
        "error_lift_all": rec(lift),
        "replacement_age": {"failed": rep[rep["after_failure"]]["days_since_replacement"].round(0).tolist(),
                            "planned": rep[~rep["after_failure"]]["days_since_replacement"].round(0).tolist()},
        "sensors_near_failure": rec(pat["sensors_near_failure"].rename_axis("sensor").reset_index()),
        "tests": tests, "regression": rec(reg.rename_axis("factor").reset_index()),
        "importance": [{"feature": k, "auc_drop": float(v)} for k, v in sensors["importance"].items()],
        "risk_now": rec(risk.head(15)),
        "sql_maintenance": rec(sql["maintenance_mix"]),
        "sql_model": rec(sql["oee_by_model"]),
    }
    (DOCS / "data.json").write_text(json.dumps(dash, indent=1, default=str))
    (RESULTS / "summary.json").write_text(json.dumps({"headline": headline, "quality": quality, "tests": tests}, indent=2, default=str))
    losses.to_csv(RESULTS / "loss_tree.csv", index=False)
    lift.to_csv(RESULTS / "errors_before_failures.csv", index=False)
    reg.to_csv(RESULTS / "poisson_regression_failures.csv")

    # ---------- Power BI ----------
    md = warehouse.machine_day(h)
    md.to_csv(POWERBI / "fact_machine_day.csv", index=False, float_format="%.2f")
    dim = t["machines"].copy()
    dim["failures_2015"] = dim["machineID"].map(nf)
    dim["mtbf_hours"] = dim["machineID"].map(rel["per_machine"])
    dim["risk_score_now"] = dim["machineID"].map(risk.set_index("machineID")["risk"])
    dim.to_csv(POWERBI / "dim_machine.csv", index=False)
    ev = pd.concat([
        t["failures"].rename(columns={"failure": "detail"}).assign(event="Breakdown"),
        t["errors"].rename(columns={"errorID": "detail"}).assign(event="Error alarm"),
        t["maint"].rename(columns={"comp": "detail"}).assign(event=np.where(t["maint"]["after_failure"], "Repair", "Planned replacement"))
                  .drop(columns="after_failure"),
    ])
    ev[ev["datetime"] >= "2015-01-01"].to_csv(POWERBI / "fact_events.csv", index=False)
    losses.to_csv(POWERBI / "loss_tree.csv", index=False)
    down.rename_axis("cause").to_csv(POWERBI / "downtime_pareto.csv")
    risk.to_csv(POWERBI / "risk_scores.csv", index=False)
    pd.DataFrame(tests).to_csv(POWERBI / "hypothesis_tests.csv", index=False)
    lift.to_csv(POWERBI / "errors_before_failures.csv", index=False)
    print(json.dumps({k: headline[k] for k in ("oee", "availability", "performance", "quality", "downtime_hours", "mtbf_hours")}, indent=1))


if __name__ == "__main__":
    main()

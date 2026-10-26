"""OEE, the loss tree, downtime Pareto, reliability (MTBF), recurring patterns and statistics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats


def oee(df: pd.DataFrame) -> dict:
    planned, run = df["planned_hours"].sum(), df["run_hours"].sum()
    ideal, out, good = df["ideal_output"].sum(), df["output"].sum(), df["good_output"].sum()
    A = run / planned if planned else np.nan
    P = out / ideal if ideal else np.nan
    Q = good / out if out else np.nan
    return {"availability": A, "performance": P, "quality": Q, "oee": A * P * Q,
            "planned_hours": planned, "run_hours": run, "output": out, "good_output": good,
            "scrap": out - good, "defect_rate": 1 - Q}


def oee_by(h: pd.DataFrame, key) -> pd.DataFrame:
    return pd.DataFrame({k: oee(g) for k, g in h.groupby(key)}).T.astype(float)


def headline(h: pd.DataFrame, t: dict) -> dict:
    o = oee(h)
    down = h["state"].isin(["Breakdown", "Minor stop"])
    machine_hours = h.groupby("machineID")["run_hours"].sum()
    fails = t["failures"]
    return {
        **{k: round(float(v), 4) for k, v in o.items()},
        "downtime_hours": int(down.sum()),
        "breakdown_hours": int((h["state"] == "Breakdown").sum()),
        "minor_stop_hours": int((h["state"] == "Minor stop").sum()),
        "planned_maintenance_hours": int((h["state"] == "Planned maintenance").sum()),
        "failures": int(len(fails)),
        "mtbf_hours": round(float(machine_hours.sum() / len(fails)), 0),
        "machines": int(h["machineID"].nunique()),
    }


# ---------------- Loss tree and Pareto ----------------
def loss_tree(h: pd.DataFrame, rate: float) -> pd.DataFrame:
    """Lost production in units, split the classic OEE way."""
    planned_ideal = h["planned_hours"].sum() * rate
    rows = [
        ("Availability", "Breakdowns", (h["state"] == "Breakdown").sum() * rate),
        ("Availability", "Minor stops", (h["state"] == "Minor stop").sum() * rate),
        ("Performance", "Reduced speed", (h["ideal_output"] - h["output"]).sum()),
        ("Quality", "Scrap (out-of-spec running)", h["scrap"].sum()),
    ]
    t = pd.DataFrame(rows, columns=["oee_factor", "loss", "lost_units"])
    t["share_of_planned_pct"] = (t["lost_units"] / planned_ideal * 100).round(2)
    t["share_of_losses_pct"] = (t["lost_units"] / t["lost_units"].sum() * 100).round(1)
    return t


def pareto(values: pd.Series, name: str) -> pd.DataFrame:
    s = values.sort_values(ascending=False)
    df = pd.DataFrame({name: s, "share_pct": (s / s.sum() * 100).round(1)})
    df["cumulative_pct"] = df["share_pct"].cumsum().round(1)
    return df


def downtime_pareto(h: pd.DataFrame) -> pd.DataFrame:
    lost = h[h["state"].isin(["Breakdown", "Minor stop"])]
    return pareto(lost["loss_cause"].value_counts().astype(float), "downtime_hours")


def machine_pareto(h: pd.DataFrame, rate: float) -> pd.DataFrame:
    """Lost units per machine (all losses), worst first."""
    g = h.groupby("machineID")
    lost = (g["planned_hours"].sum() * rate - g["good_output"].sum())
    return pareto(lost, "lost_units")


# ---------------- Reliability ----------------
def mtbf(h: pd.DataFrame, t: dict) -> dict:
    run = h.groupby("machineID")["run_hours"].sum()
    f = t["failures"].merge(t["machines"], on="machineID")
    by_comp = (run.sum() / f["failure"].value_counts()).round(0)
    per_machine = (run / f.groupby("machineID").size()).dropna()
    model_runs = h.groupby("model")["run_hours"].sum()
    by_model = (model_runs / f.groupby("model").size()).round(0)
    # time between consecutive failures of the same machine
    fs = f.sort_values(["machineID", "datetime"])
    gaps = fs.groupby("machineID")["datetime"].diff().dt.total_seconds().dropna() / 3600
    return {"by_component": by_comp, "by_model": by_model, "per_machine": per_machine.round(0),
            "gap_hours": gaps, "gap_median": float(gaps.median()), "gap_mean": float(gaps.mean())}


def errors_before_failures(t: dict, window_h: int = 24) -> pd.DataFrame:
    """How much more often each error type appears in the 24 h before a failure of each component."""
    err, fail = t["errors"], t["failures"]
    total_hours = 100 * 8761
    rows = []
    for comp, fc in fail.groupby("failure"):
        for eid, ec in err.groupby("errorID"):
            m = fc.merge(ec, on="machineID", suffixes=("_f", "_e"))
            dt = (m["datetime_f"] - m["datetime_e"]).dt.total_seconds() / 3600
            hit = m[(dt >= 0) & (dt <= window_h)].groupby(["machineID", "datetime_f"]).size()
            share = len(hit) / len(fc)
            base = 1 - (1 - len(ec) / total_hours) ** (window_h + 1)   # chance of that error in any 25-h window
            rows.append({"component": comp, "error": eid, "failures": len(fc), "share_with_error_pct": round(share * 100, 1),
                         "baseline_pct": round(base * 100, 1), "lift": round(share / base, 1) if base else np.nan})
    return pd.DataFrame(rows)


def replacement_age(t: dict) -> pd.DataFrame:
    """Days since that component was last replaced, at each failure vs at planned replacements."""
    m = t["maint"].sort_values("datetime")
    rows = []
    for (mid, comp), g in m.groupby(["machineID", "comp"]):
        g = g.sort_values("datetime")
        prev = g["datetime"].shift()
        days = (g["datetime"] - prev).dt.total_seconds() / 86400
        rows.append(pd.DataFrame({"machineID": mid, "comp": comp, "days_since_replacement": days,
                                  "after_failure": g["after_failure"].values}))
    return pd.concat(rows).dropna()


def patterns(h: pd.DataFrame, t: dict) -> dict:
    f = t["failures"].copy()
    f["weekday"] = f["datetime"].dt.day_name()
    f["hour"] = f["datetime"].dt.hour
    f["month"] = f["datetime"].dt.to_period("M").dt.to_timestamp()
    repeat = f.groupby(["machineID", "failure"]).size()
    run = h[h["run_hours"] > 0]
    near = run.groupby("near_failure")[["rotate", "vibration", "pressure", "volt"]].mean().T
    near.columns = ["normal", "within_3_days_of_failure"]
    scrap_near = run.groupby("near_failure")["out_of_spec"].mean()
    return {
        "failures_by_hour": f["hour"].value_counts().sort_index(),
        "failures_by_month": f.groupby("month").size(),
        "machines_with_repeat_component_failures": int((repeat >= 3).groupby(level=0).any().sum()),
        "repeat_failure_share_pct": round(float(repeat[repeat >= 2].sum() / len(f) * 100), 1),
        "sensors_near_failure": near.round(2),
        "scrap_rate_normal_pct": round(float(scrap_near.get(False, 0)) * 100, 2),
        "scrap_rate_near_failure_pct": round(float(scrap_near.get(True, 0)) * 100, 2),
    }


# ---------------- Statistics ----------------
def tests(h: pd.DataFrame, t: dict, rep: pd.DataFrame) -> list[dict]:
    out = []
    mach = t["machines"].set_index("machineID")
    nf = t["failures"].groupby("machineID").size().reindex(mach.index, fill_value=0)
    rho = stats.spearmanr(mach["age"], nf)
    out.append({"hypothesis": "Older machines fail more often", "test": "Spearman rank correlation",
                "result": f"rho = {rho.statistic:.2f}", "p_value": float(rho.pvalue),
                "conclusion": "Supported" if rho.pvalue < 0.05 and rho.statistic > 0 else "Not supported"})

    groups = [nf[mach["model"] == m].to_numpy() for m in sorted(mach["model"].unique())]
    kw = stats.kruskal(*groups)
    med = nf.groupby(mach["model"]).mean().round(1)
    out.append({"hypothesis": "Failure frequency differs by machine model", "test": "Kruskal-Wallis H",
                "result": ", ".join(f"{k}: {v}/yr" for k, v in med.items()), "p_value": float(kw.pvalue),
                "conclusion": "Supported" if kw.pvalue < 0.05 else "Not supported"})

    a = rep[rep["after_failure"]]["days_since_replacement"]
    b = rep[~rep["after_failure"]]["days_since_replacement"]
    u = stats.mannwhitneyu(a, b, alternative="greater")
    out.append({"hypothesis": "Components that fail have gone longer since their last replacement",
                "test": "Mann-Whitney U (one-sided)",
                "result": f"median {a.median():.0f} days before a failure vs {b.median():.0f} at planned replacement",
                "p_value": float(u.pvalue), "conclusion": "Supported" if u.pvalue < 0.05 else "Not supported"})

    run = h[h["run_hours"] > 0]
    ct = pd.crosstab(run["near_failure"], run["out_of_spec"])
    chi = stats.chi2_contingency(ct)
    r1, r0 = run.loc[run["near_failure"], "out_of_spec"].mean(), run.loc[~run["near_failure"], "out_of_spec"].mean()
    out.append({"hypothesis": "Scrap is more likely in the 3 days around a failure", "test": "Chi-square test of independence",
                "result": f"{r1 * 100:.1f}% vs {r0 * 100:.1f}% of hours ({r1 / r0:.1f}x)", "p_value": float(chi.pvalue),
                "conclusion": "Supported" if chi.pvalue < 0.05 else "Not supported"})

    return out


def failure_regression(t: dict, h: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Poisson regression: yearly failures per machine on age, model and error count."""
    mach = t["machines"].set_index("machineID").copy()
    mach["failures"] = t["failures"].groupby("machineID").size().reindex(mach.index, fill_value=0)
    mach["errors"] = t["errors"].groupby("machineID").size().reindex(mach.index, fill_value=0)
    mach["avg_vibration"] = h.groupby("machineID")["vibration"].mean()
    m = smf.glm("failures ~ age + errors + C(model, Treatment('model1'))", data=mach.reset_index(),
                family=sm.families.Poisson()).fit()
    ci = m.conf_int()
    tab = pd.DataFrame({"rate_ratio": np.exp(m.params), "ci_low": np.exp(ci[0]), "ci_high": np.exp(ci[1]), "p_value": m.pvalues})
    tab = tab.drop(index="Intercept")
    tab.index = [i.replace("C(model, Treatment('model1'))[T.", "Model ").replace("]", " vs model1")
                 .replace("age", "Machine age (per year)").replace("errors", "Error alarms (each)") for i in tab.index]
    pseudo_r2 = 1 - m.deviance / m.null_deviance
    return tab.round(4), round(float(pseudo_r2), 3)

"""Forecast downtime risk: will a machine break down in the next 24 hours?

Trained on January-August 2015, threshold chosen on September, tested once on
October-December. Every feature uses only readings up to the forecast hour.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from .data import SENSORS

HORIZON_H = 24
STEP_H = 3
TRAIN_END, VALID_END = pd.Timestamp("2015-09-01"), pd.Timestamp("2015-10-01")


def features(t: dict) -> pd.DataFrame:
    tel = t["telemetry"].sort_values(["machineID", "datetime"]).set_index("datetime")
    parts = []
    for mid, g in tel.groupby("machineID"):
        s = g[SENSORS]
        f = pd.concat([s.rolling("3h").mean().add_suffix("_mean_3h"), s.rolling("24h").mean().add_suffix("_mean_24h"),
                       s.rolling("24h").std().add_suffix("_std_24h")], axis=1)
        f["machineID"] = mid
        parts.append(f.iloc[::STEP_H])
    X = pd.concat(parts).reset_index()

    # error alarms in the last 24 hours, by type
    err = t["errors"].copy()
    err["n"] = 1
    e = err.pivot_table(index=["machineID", "datetime"], columns="errorID", values="n", aggfunc="sum", fill_value=0)
    e = e.reindex(pd.MultiIndex.from_frame(t["telemetry"][["machineID", "datetime"]]), fill_value=0).sort_index()
    e24 = e.groupby(level=0).rolling(24, min_periods=1).sum().droplevel(0)
    e24.columns = [f"{c}_24h" for c in e24.columns]
    X = X.merge(e24.reset_index(), on=["machineID", "datetime"], how="left")

    # days since each component was last replaced (any maintenance, including repairs)
    m = t["maint"][["machineID", "datetime", "comp"]].sort_values("datetime")
    for comp in sorted(m["comp"].unique()):
        mc = m[m["comp"] == comp][["machineID", "datetime"]].rename(columns={"datetime": "last"})
        X = X.sort_values("datetime")
        X = pd.merge_asof(X, mc.sort_values("last"), left_on="datetime", right_on="last", by="machineID", direction="backward")
        X[f"days_since_{comp}"] = (X["datetime"] - X["last"]).dt.total_seconds() / 86400
        X = X.drop(columns="last")

    mach = t["machines"].copy()
    mach["model_no"] = mach["model"].str.replace("model", "").astype(int)
    X = X.merge(mach[["machineID", "age", "model_no"]], on="machineID", how="left")

    # label: a failure in the next 24 hours (any component)
    f = t["failures"][["machineID", "datetime"]].sort_values("datetime").rename(columns={"datetime": "next_failure"})
    X = X.sort_values("datetime")
    X = pd.merge_asof(X, f, left_on="datetime", right_on="next_failure", by="machineID", direction="forward",
                      allow_exact_matches=False)
    hours_to = (X["next_failure"] - X["datetime"]).dt.total_seconds() / 3600
    X["fails_next_24h"] = (hours_to <= HORIZON_H).astype(int)
    X["hours_to_failure"] = hours_to
    return X.sort_values(["machineID", "datetime"]).reset_index(drop=True)


FEATURE_DROP = ["datetime", "machineID", "next_failure", "fails_next_24h", "hours_to_failure"]


def train_and_test(X: pd.DataFrame, t: dict, use_alarms: bool = True) -> dict:
    X = X[X["datetime"] >= X["datetime"].min() + pd.Timedelta(days=1)]   # rolling windows need a day of history
    tr, va, te = X[X["datetime"] < TRAIN_END], X[(X["datetime"] >= TRAIN_END) & (X["datetime"] < VALID_END)], X[X["datetime"] >= VALID_END]
    cols = [c for c in X.columns if c not in FEATURE_DROP]
    all_cols = cols
    if not use_alarms:   # sensors, age and maintenance history only
        cols = [c for c in cols if not c.startswith("error")]
    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, random_state=0)
    model.fit(tr[cols], tr["fails_next_24h"])

    pv = model.predict_proba(va[cols])[:, 1]
    best, thr = -1, 0.5
    for th in np.linspace(0.05, 0.95, 91):   # threshold with the best F1 on September
        pred = pv >= th
        tp = (pred & (va["fails_next_24h"] == 1)).sum()
        prec = tp / max(pred.sum(), 1)
        rec = tp / max((va["fails_next_24h"] == 1).sum(), 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        if f1 > best:
            best, thr = f1, th

    p = model.predict_proba(te[cols])[:, 1]
    te = te.assign(risk=p, alert=p >= thr)
    y = te["fails_next_24h"]

    # Baseline rule: alert whenever any error alarm fired in the last 24 hours.
    err_cols = [c for c in all_cols if c.startswith("error")]
    rule = te[err_cols].sum(axis=1) > 0

    def scores(alert):
        tp = int((alert & (y == 1)).sum())
        return {"precision": round(tp / max(int(alert.sum()), 1), 3), "recall": round(tp / max(int(y.sum()), 1), 3)}

    # Failure-level view: was there at least one alert in the 24 h before each test failure?
    fails = t["failures"][t["failures"]["datetime"] >= VALID_END]
    caught, lead = 0, []
    for _, r in fails.iterrows():
        w = te[(te["machineID"] == r["machineID"]) & (te["datetime"] < r["datetime"]) &
               (te["datetime"] >= r["datetime"] - pd.Timedelta(hours=HORIZON_H))]
        hits = w[w["alert"]]
        if len(hits):
            caught += 1
            lead.append((r["datetime"] - hits["datetime"].min()).total_seconds() / 3600)
    false_alerts = int((te["alert"] & (y == 0)).sum())
    machine_months = te["machineID"].nunique() * 3

    imp = feature_importance(model, te[cols], y)
    return {
        "model": model, "cols": cols, "threshold": round(float(thr), 2), "test": te,
        "metrics": {
            "roc_auc": round(float(roc_auc_score(y, p)), 4),
            "pr_auc": round(float(average_precision_score(y, p)), 4),
            "base_rate_pct": round(float(y.mean() * 100), 2),
            **{f"model_{k}": v for k, v in scores(te["alert"]).items()},
            **{f"rule_{k}": v for k, v in scores(rule).items()},
            "test_failures": int(len(fails)), "failures_caught": caught,
            "failures_caught_pct": round(caught / max(len(fails), 1) * 100, 1),
            "median_warning_hours": round(float(np.median(lead)), 1) if lead else None,
            "false_alerts_per_machine_month": round(false_alerts / machine_months, 2),
            "train_rows": int(len(tr)), "test_rows": int(len(te)),
        },
        "importance": imp,
    }


def feature_importance(model, X: pd.DataFrame, y: pd.Series, n: int = 12) -> pd.Series:
    """Drop in ROC AUC when one feature is shuffled (on a 30k-row sample of the test set)."""
    rng = np.random.default_rng(0)
    idx = rng.choice(len(X), size=min(30000, len(X)), replace=False)
    Xs, ys = X.iloc[idx].copy(), y.iloc[idx]
    base = roc_auc_score(ys, model.predict_proba(Xs)[:, 1])
    out = {}
    for c in X.columns:
        Xp = Xs.copy()
        Xp[c] = rng.permutation(Xp[c].to_numpy())
        out[c] = base - roc_auc_score(ys, model.predict_proba(Xp)[:, 1])
    return pd.Series(out).sort_values(ascending=False).head(n).round(4)


def current_risk(model, X: pd.DataFrame, t: dict, cols: list) -> pd.DataFrame:
    """Each machine's latest risk score: the inspection list for the next shift."""
    last = X.sort_values("datetime").groupby("machineID").tail(1).copy()
    last["risk"] = model.predict_proba(last[cols])[:, 1]
    keep = ["machineID", "datetime", "risk", "age", "model_no"] + [c for c in X.columns if c.startswith("days_since") or (c.endswith("_24h") and c.startswith("error"))]
    return last[keep].sort_values("risk", ascending=False).round({"risk": 3})

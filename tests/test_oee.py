import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from oee import analysis as an, data, forecast as fc, warehouse  # noqa: E402


def fake(days: int = 60, machines: int = 3, seed: int = 0) -> dict[str, pd.DataFrame]:
    """Small fleet with one breakdown, one alarm and one planned replacement at known hours."""
    rng = np.random.default_rng(seed)
    hours = pd.date_range("2015-01-01 06:00", periods=days * 24, freq="h")
    tel = []
    for m in range(1, machines + 1):
        tel.append(pd.DataFrame({"datetime": hours, "machineID": m,
                                 "volt": rng.normal(170, 5, len(hours)), "rotate": rng.normal(450, 10, len(hours)),
                                 "pressure": rng.normal(100, 3, len(hours)), "vibration": rng.normal(40, 2, len(hours))}))
    tel = pd.concat(tel, ignore_index=True)
    fail_t = hours[24 * 30]
    return {
        "telemetry": tel,
        "errors": pd.DataFrame({"datetime": [fail_t - pd.Timedelta(hours=10), hours[24 * 50]], "machineID": [1, 2], "errorID": ["error1", "error2"]}),
        "maint": pd.DataFrame({"datetime": [hours[0] - pd.Timedelta(days=40), fail_t, hours[24 * 10]], "machineID": [1, 1, 3], "comp": ["comp1", "comp1", "comp2"]}),
        "failures": pd.DataFrame({"datetime": [fail_t], "machineID": [1], "failure": ["comp1"]}),
        "machines": pd.DataFrame({"machineID": range(1, machines + 1), "model": ["model1", "model2", "model1"][:machines], "age": [10, 3, 5][:machines]}),
    }


@pytest.fixture(scope="module")
def built():
    t, q = data.clean(fake())
    return t, q, data.hourly(t)


def test_cleaning_links_repairs_to_failures(built):
    t, q, _ = built
    assert q["maintenance_after_failure"] == 1
    assert q["planned_maintenance"] == 2
    assert q["telemetry_hours_missing"] == 0


def test_states_and_downtime_hours(built):
    t, _, h = built
    a = data.ASSUMPTIONS
    assert (h["state"] == "Breakdown").sum() == a["repair_hours_per_failure"]
    assert (h["state"] == "Minor stop").sum() == 2 * a["stop_hours_per_error"]
    assert (h["state"] == "Planned maintenance").sum() == a["planned_maintenance_hours"]   # only the in-period planned job


def test_oee_is_product_of_components(built):
    _, _, h = built
    o = an.oee(h)
    assert o["oee"] == pytest.approx(o["availability"] * o["performance"] * o["quality"])
    for k in ("availability", "performance", "quality"):
        assert 0 < o[k] <= 1


def test_no_output_while_stopped(built):
    _, _, h = built
    stopped = h["state"] != "Running"
    assert (h.loc[stopped, "output"] == 0).all()
    assert (h["scrap"] <= h["output"] + 1e-9).all()
    assert (h["output"] <= h["ideal_output"] + 1e-9).all()


def test_planned_maintenance_not_counted_as_planned_time(built):
    _, _, h = built
    pm = h["state"] == "Planned maintenance"
    assert (h.loc[pm, "planned_hours"] == 0).all()


def test_loss_tree_adds_up(built):
    _, _, h = built
    rate = data.ASSUMPTIONS["units_per_hour_at_rated_speed"]
    lt = an.loss_tree(h, rate)
    total_lost = h["planned_hours"].sum() * rate - h["good_output"].sum()
    assert lt["lost_units"].sum() == pytest.approx(total_lost, rel=1e-6)


def test_pareto_cumulative():
    p = an.pareto(pd.Series({"a": 6.0, "b": 3.0, "c": 1.0}), "hours")
    assert list(p.index) == ["a", "b", "c"] and p["cumulative_pct"].iloc[-1] == pytest.approx(100.0)


def test_alarm_before_failure_is_found(built):
    t, _, _ = built
    lift = an.errors_before_failures(t)
    row = lift[(lift["component"] == "comp1") & (lift["error"] == "error1")].iloc[0]
    assert row["share_with_error_pct"] == 100.0


def test_forecast_label_and_no_future_features(built):
    t, _, _ = built
    X = fc.features(t)
    fail_t = t["failures"]["datetime"].iloc[0]
    m1 = X[X["machineID"] == 1]
    within = m1[(m1["datetime"] < fail_t) & (m1["datetime"] >= fail_t - pd.Timedelta(hours=24))]
    assert within["fails_next_24h"].all()
    assert not m1[m1["datetime"] >= fail_t]["fails_next_24h"].any()
    # the alarm 10 h before the failure must not appear in features before it fired
    alarm_t = t["errors"]["datetime"].iloc[0]
    assert (m1[m1["datetime"] < alarm_t]["error1_24h"] == 0).all()


def test_sql_queries_run(built, tmp_path):
    t, _, h = built
    con = warehouse.build(h, t, tmp_path / "t.db")
    out = warehouse.run_queries(con, ROOT / "sql")
    assert {"monthly_oee", "failures_by_component", "time_between_failures"} <= set(out)
    assert out["failures_by_component"]["failures"].sum() == 1

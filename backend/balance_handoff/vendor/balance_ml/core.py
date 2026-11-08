"""Explicit units, feature allowlists and evaluation rules."""
from datetime import datetime
import math
import numpy as np


FEATURES = {
    "demand": (
        "area_m2", "kc_assumed", "et0_forecast_mm", "effective_rain_forecast_mm",
        "percolation_assumed_mm", "etc_forecast_L", "percolation_assumed_L",
        "effective_rain_forecast_L",
    ),
    "supply": (
        "previous_inflow_L", "base_inflow_assumed_L", "rain_forecast_mm",
        "planned_upstream_inflow_L",
    ),
}


def finite_nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: finite numeric value required, not boolean/string")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name}: finite nonnegative value required")
    return float(value)


def demand_litres(area_m2, et0_mm, kc, percolation_mm, effective_rain_mm):
    """24h established paddy lab; SAT=WL=0; 1 mm*m2 = 1 litre."""
    vals = [finite_nonnegative(x, n) for x, n in zip(
        (area_m2, et0_mm, kc, percolation_mm, effective_rain_mm),
        ("area_m2", "et0_mm", "kc", "percolation_mm", "effective_rain_mm"))]
    area, et0, crop, perc, rain = vals
    return finite_nonnegative(area * max(0.0, et0 * crop + perc - rain), "calculated_need_L")


def source_budget_litres(storage_L, inflow_forecast_L, reserve_L, other_outflows_L):
    values = [finite_nonnegative(v, n) for v, n in zip(
        (storage_L, inflow_forecast_L, reserve_L, other_outflows_L),
        ("storage_L", "inflow_forecast_L", "reserve_L", "other_outflows_L"))]
    result = values[0] + values[1] - values[2] - values[3]
    if not math.isfinite(result):
        raise ValueError("Calculated budget overflow")
    return max(0.0, result)


def timestamp(text):
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Timestamp must include timezone")
    return dt


def validate_row(row):
    if row["schema_version"] != "balance-ml-lab/0.1":
        raise ValueError("Unknown dataset schema")
    if row["context"] != "SIMULASI" or row["source_type"] != "SYNTHETIC_GENERATOR":
        raise ValueError("This lab accepts synthetic data only; real-data review is separate")
    task = row["task"]
    if task not in FEATURES or set(row["features"]) != set(FEATURES[task]):
        raise ValueError("Unexpected/missing feature; labels/future actuals cannot be features")
    start, end = timestamp(row["as_of"]), timestamp(row["target_end"])
    if (end - start).total_seconds() != 86400:
        raise ValueError("This lab requires non-overlapping 24h targets")
    if timestamp(row["features_available_at"]) > start:
        raise ValueError("Future information leakage")
    if timestamp(row["label_available_at"]) < end:
        raise ValueError("Future label incorrectly declared available at prediction time")
    if row["label_kind"] != "CALCULATED_FROM_SIMULATED_FUTURE":
        raise ValueError("Synthetic labels must never be declared MEASUREMENT")
    if row["split"] not in ("train", "validation", "test"):
        raise ValueError("Invalid split")
    if row["unit"] != "L" or row["horizon_hours"] != 24:
        raise ValueError("Expected litres per 24h; do not mix with optimizer mL")
    if task == "demand" and row["zone_id"] not in ("A", "B", "C"):
        raise ValueError("Demand zone required")
    if task == "supply" and row["zone_id"] is not None:
        raise ValueError("Supply is a shared-source target, not three independent supplies")
    for key, value in row["features"].items():
        finite_nonnegative(value, key)
    finite_nonnegative(row["label_value"], "label_value")
    if task == "demand":
        f = row["features"]
        if f["area_m2"] <= 0:
            raise ValueError("Area must be positive")
        expected = {"etc_forecast_L": f["area_m2"]*f["kc_assumed"]*f["et0_forecast_mm"],
                    "percolation_assumed_L": f["area_m2"]*f["percolation_assumed_mm"],
                    "effective_rain_forecast_L": f["area_m2"]*f["effective_rain_forecast_mm"]}
        for key, value in expected.items():
            if not math.isfinite(value) or not math.isclose(f[key], value, rel_tol=1e-9, abs_tol=1e-8):
                raise ValueError("Engineered feature disagrees with known inputs")
    if task == "supply" and timestamp(row["lag_available_at"]) > start:
        raise ValueError("Supply lag not available at prediction cutoff")


def audit_splits(rows):
    if not rows:
        raise ValueError("Empty dataset")
    seen, family_split, cycle_split = set(), {}, {}
    parts = {s: [] for s in ("train", "validation", "test")}
    cycle_rows = {}
    for row in rows:
        validate_row(row)
        if row["row_id"] in seen:
            raise ValueError("Duplicate row_id")
        seen.add(row["row_id"])
        for key, mapping in (("family_id", family_split), ("cycle_id", cycle_split)):
            old = mapping.setdefault(row[key], row["split"])
            if old != row["split"]:
                raise ValueError(f"{key} crosses split boundary")
        parts[row["split"]].append(row)
        cycle_rows.setdefault(row["cycle_id"], []).append(row)
    for group in cycle_rows.values():
        if len(group) != 4 or {(r["task"], r["zone_id"]) for r in group} != {
                ("demand", "A"), ("demand", "B"), ("demand", "C"), ("supply", None)}:
            raise ValueError("Each cycle needs three demand rows and one shared supply row")
        if len({(r["as_of"], r["target_end"], r["family_id"]) for r in group}) != 1:
            raise ValueError("Cycle metadata mismatch")
    for split, group in parts.items():
        if not group or {r["task"] for r in group} != set(FEATURES):
            raise ValueError(f"{split} must contain both demand and supply")
    for left, right in (("train", "validation"), ("validation", "test")):
        if max(timestamp(r["target_end"]) for r in parts[left]) >= min(
                timestamp(r["as_of"]) for r in parts[right]):
            raise ValueError("Split chronology/embargo violated")
    return {s: {"rows": len(g), "families": len({r["family_id"] for r in g}),
                "start": min(r["as_of"] for r in g),
                "end": max(r["target_end"] for r in g)} for s, g in parts.items()}


def matrix(rows, task):
    selected = [r for r in rows if r["task"] == task]
    for row in selected:
        validate_row(row)
    return (np.asarray([[r["features"][k] for k in FEATURES[task]] for r in selected]),
            np.asarray([r["label_value"] for r in selected]))


def metrics(actual, predicted, mape_threshold_L=100.0):
    """MAPE only for y>100 L; exclusion count is always exposed. No score gates."""
    y, p = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if y.ndim != 1 or p.shape != y.shape or not len(y):
        raise ValueError("Nonempty equal-length vectors required")
    if not np.isfinite(y).all() or not np.isfinite(p).all() or (y < 0).any():
        raise ValueError("Metrics require finite values and nonnegative labels")
    threshold = finite_nonnegative(mape_threshold_L, "mape_threshold_L")
    error = np.abs(p - y)
    mask = y > threshold
    total = float(y.sum())
    return {
        "n": int(len(y)), "mae_L": float(error.mean()),
        "rmse_L": float(np.sqrt(np.mean((p-y)**2))),
        "wape_pct": float(100*error.sum()/total) if total > 0 else None,
        "mape_pct_above_threshold": float(100*np.mean(error[mask]/y[mask])) if mask.any() else None,
        "mape_threshold_L": threshold,
        "mape_included_n": int(mask.sum()), "mape_excluded_n": int((~mask).sum()),
    }

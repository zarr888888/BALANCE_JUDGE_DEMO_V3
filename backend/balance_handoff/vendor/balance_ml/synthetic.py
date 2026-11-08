"""Reproducible, labelled assumptions, not Indramayu weather or farm records."""
from datetime import datetime, timedelta, timezone
import numpy as np
from .core import demand_litres, audit_splits


def generate(seed=20261101, families=20, days=36):
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if isinstance(families, bool) or not isinstance(families, int) or families < 5:
        raise ValueError("At least five independent families required")
    if isinstance(days, bool) or not isinstance(days, int) or days < 2:
        raise ValueError("At least two days per family required")
    rows = []
    train_n = int(families*.6)
    val_n = max(1, int(families*.2))
    origin = datetime(2020, 1, 1, tzinfo=timezone(timedelta(hours=7)))
    for family in range(families):
        rng = np.random.default_rng(np.random.SeedSequence([seed, family]))
        split = "train" if family < train_n else "validation" if family < train_n+val_n else "test"
        family_id = f"synthetic_family_{family:03d}"
        area = rng.uniform(1000, 20000, 3)
        perc = rng.uniform(4, 8, 3)
        phase_offset = rng.uniform(0, 2*np.pi, 3)
        base_inflow = float(rng.uniform(20000, 100000))
        previous = base_inflow
        previous_available_at = origin + timedelta(days=family*(days+3))
        for day in range(days):
            as_of = origin + timedelta(days=family*(days+3)+day)
            end = as_of + timedelta(days=1)
            # A prediction-time forecast is created first. The future is then
            # generated conditionally; future values are used for labels only.
            et0_forecast = float(rng.uniform(2.5, 7))
            rain_forecast = float(0 if rng.random() < .45 else rng.uniform(0, 35))
            et0_future = float(max(0, et0_forecast + rng.normal(0, .45)))
            rain_future = float(max(0, rain_forecast + rng.normal(0, 4)))
            cycle = f"{family_id}_day_{day:03d}"
            common = {
                "schema_version": "balance-ml-lab/0.1", "family_id": family_id,
                "cycle_id": cycle, "split": split, "seed": seed,
                "context": "SIMULASI", "source_type": "SYNTHETIC_GENERATOR",
                "formula_version": "paddy-established-and-inflow-toy/0.1",
                "as_of": as_of.isoformat(), "target_end": end.isoformat(),
                "features_available_at": as_of.isoformat(), "label_available_at": end.isoformat(),
                "label_kind": "CALCULATED_FROM_SIMULATED_FUTURE", "horizon_hours": 24, "unit": "L",
            }
            for z, zone in enumerate(("A", "B", "C")):
                kc = float(1.1 + .1*np.sin(day/36*2*np.pi+phase_offset[z]))
                effective_forecast = .7*rain_forecast
                features = {
                    "area_m2": float(area[z]), "kc_assumed": kc,
                    "et0_forecast_mm": et0_forecast, "effective_rain_forecast_mm": effective_forecast,
                    "percolation_assumed_mm": float(perc[z]),
                    "etc_forecast_L": float(area[z]*kc*et0_forecast),
                    "percolation_assumed_L": float(area[z]*perc[z]),
                    "effective_rain_forecast_L": float(area[z]*effective_forecast),
                }
                rows.append({**common, "task": "demand", "zone_id": zone,
                             "row_id": cycle+"_demand_"+zone, "features": features,
                             "label_value": demand_litres(float(area[z]), et0_future, kc,
                                                          float(perc[z]), .7*rain_future)})
            planned = float(rng.uniform(0, 15000))
            inflow = float(max(0, .7*previous + .3*base_inflow + 250*rain_future + planned
                              + rng.normal(0, .08*base_inflow)))
            rows.append({**common, "task": "supply", "zone_id": None,
                         "row_id": cycle+"_supply", "lag_available_at": previous_available_at.isoformat(),
                         "features": {"previous_inflow_L": previous,
                                      "base_inflow_assumed_L": base_inflow,
                                      "rain_forecast_mm": rain_forecast,
                                      "planned_upstream_inflow_L": planned}, "label_value": inflow})
            previous = inflow
            previous_available_at = end
    audit_splits(rows)
    return rows

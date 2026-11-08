"""Train only on train, select on validation, evaluate test after selection."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import joblib
import numpy as np
import scipy
import sklearn
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import GradientBoostingRegressor
from . import __version__
from .core import FEATURES, audit_splits, matrix, metrics, demand_litres, timestamp


def candidates(seed):
    return {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "gradient_boosting": GradientBoostingRegressor(
            n_estimators=120, learning_rate=.05, max_depth=2,
            min_samples_leaf=8, loss="squared_error", random_state=seed),
    }


def baseline(rows, task, name):
    values = []
    for row in rows:
        f = row["features"]
        if task == "demand" and name == "agronomic_forecast":
            value = demand_litres(f["area_m2"], f["et0_forecast_mm"], f["kc_assumed"],
                                 f["percolation_assumed_mm"], f["effective_rain_forecast_mm"])
        elif task == "supply" and name == "persistence":
            value = f["previous_inflow_L"]
        elif task == "supply" and name == "toy_inflow_formula":
            value = .7*f["previous_inflow_L"]+.3*f["base_inflow_assumed_L"]+250*f["rain_forecast_mm"]+f["planned_upstream_inflow_L"]
        else:
            raise ValueError("Unsupported baseline")
        values.append(value)
    return np.asarray(values)


def scored(y, raw):
    raw = np.asarray(raw, dtype=float)
    effective = np.maximum(0, raw)
    return {"effective": metrics(y, effective), "raw": metrics(y, raw),
            "negative_predictions_clipped_n": int((raw < 0).sum())}


def train(rows, output_dir, seed=20261101):
    split_summary = audit_splits(rows)
    # The first lag is a declared synthetic initial condition, subsequent lags
    # are earlier completed synthetic horizons. Validate availability explicitly.
    for row in rows:
        if row["task"] == "supply" and timestamp(row["lag_available_at"]) > timestamp(row["as_of"]):
            raise ValueError("Supply lag not available at prediction cutoff")
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    data_bytes = "".join(json.dumps(r, sort_keys=True, allow_nan=False)+"\n" for r in rows).encode()
    (target/"dataset_SIMULASI.jsonl").write_bytes(data_bytes)
    report = {
        "schema_version": "balance-ml-lab-report/0.1", "created_at": datetime.now(timezone.utc).isoformat(),
        "model_version": __version__, "data_context": "SIMULASI",
        "ml_status": "TRAINED_SYNTHETIC_ONLY", "real_measurement_rows": 0,
        "field_validation_status": "NOT_VALIDATED", "proposal_targets_proven": False,
        "execution_allowed": False, "prediction_interval": None,
        "dataset_sha256": hashlib.sha256(data_bytes).hexdigest(), "seed": seed,
        "parameter_registry_sha256": hashlib.sha256((Path(__file__).resolve().parent/"parameter_registry.json").read_bytes()).hexdigest(),
        "training_source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(__file__).resolve().parent.glob("*.py")},
        "split_strategy": "chronological_disjoint_families_60_20_20_no_random_row_split",
        "split_summary": split_summary,
        "selection_rule": "lowest validation MAE_L; test never used for selection; no refit on validation",
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "scipy": scipy.__version__, "scikit_learn": sklearn.__version__,
                        "joblib": joblib.__version__, "platform": platform.platform()},
        "tasks": {},
        "limitations": [
            "All inputs are generator assumptions; future labels are calculated from simulated futures.",
            "Shared simulator formula across partitions: this is not a field/generalization proof.",
            "ET0 is simulated input, not a complete FAO-56 Penman-Monteith implementation.",
            "Established paddy only, SAT=WL=0; soil storage/runoff/groundwater unmodelled.",
            "No empirical loss training, live weather, sensor, scheduler, API change or gate operation.",
            "No calibrated uncertainty or conservative operational inflow bound.",
            "Litres per 24h cannot be sent directly to optimizer fields ending _ml.",
        ],
    }
    example = {"schema_version": "balance-ml-lab-example/0.1", "context": "SIMULASI",
               "ml_status": "TRAINED_SYNTHETIC_ONLY", "unit": "L", "horizon_hours": 24,
               "actual_measured": None, "prediction_interval": None,
               "execution_allowed": False, "predictions": []}
    prediction_records = []
    for task in FEATURES:
        parts = {s: [r for r in rows if r["task"] == task and r["split"] == s]
                 for s in ("train", "validation", "test")}
        xtrain, ytrain = matrix(parts["train"], task)
        xval, yval = matrix(parts["validation"], task)
        models = candidates(seed)
        validation = {}
        for name, model in models.items():
            model.fit(xtrain, ytrain)
            validation[name] = scored(yval, model.predict(xval))
        baseline_names = ["agronomic_forecast"] if task == "demand" else ["persistence", "toy_inflow_formula"]
        for name in baseline_names:
            validation[name] = scored(yval, baseline(parts["validation"], task, name))
        selected_ml = min(models, key=lambda n: (validation[n]["effective"]["mae_L"], n))
        preferred_method = min(validation, key=lambda n: (validation[n]["effective"]["mae_L"], n))
        # Selection frozen before the holdout feature/label matrices are built.
        xtest, ytest = matrix(parts["test"], task)
        testing = {}
        for name in list(models)+baseline_names:
            raw = models[name].predict(xtest) if name in models else baseline(parts["test"], task, name)
            testing[name] = scored(ytest, raw)
            for row, actual, pred in zip(parts["test"], ytest, raw):
                prediction_records.append({"row_id": row["row_id"], "task": task,
                    "zone_id": row["zone_id"], "method": name, "label_SIMULATED_L": float(actual),
                    "raw_prediction_L": float(pred), "effective_prediction_L": max(0.0, float(pred)),
                    "context": "SIMULASI", "split": "test", "actual_measured": None})
        task_report = {
            "target": "net_received_need_L" if task == "demand" else "shared_source_inflow_L",
            "features": list(FEATURES[task]), "unit": "L", "horizon_hours": 24,
            "rows": {s: len(g) for s, g in parts.items()},
            "selected_ml": selected_ml, "preferred_method_on_validation_including_baselines": preferred_method,
            "validation": validation, "test": testing,
            "ml_model_parameters": {name: ({"alpha": 1.0, "preprocessing": "StandardScaler fit on train only"}
                if name == "ridge" else model.get_params()) for name, model in models.items()},
            "ml_feature_importance": {"method": "gradient_boosting_impurity_decrease",
                "values": dict(zip(FEATURES[task], models["gradient_boosting"].feature_importances_.tolist())),
                "interpretation": "Model dependency only; not causal explanation or LP allocation reasons"},
        }
        report["tasks"][task] = task_report
        # Persist only models trained in this run. No model binaries are shipped.
        bundle = {"task": task, "model": models[selected_ml], "feature_names": FEATURES[task],
                  "training_min": xtrain.min(axis=0), "training_max": xtrain.max(axis=0),
                  "ml_status": "TRAINED_SYNTHETIC_ONLY", "unit": "L",
                  "environment": report["environment"], "dataset_sha256": report["dataset_sha256"]}
        joblib.dump(bundle, target/f"{task}_synthetic_model.joblib")
        first_cycle = parts["test"][0]["cycle_id"]
        chosen_rows = [r for r in parts["test"] if r["cycle_id"] == first_cycle]
        for row in chosen_rows:
            x, _ = matrix([row], task)
            raw = float(models[selected_ml].predict(x)[0])
            outside = [k for i, k in enumerate(FEATURES[task])
                       if x[0, i] < bundle["training_min"][i] or x[0, i] > bundle["training_max"][i]]
            example["predictions"].append({"task": task, "zone_id": row["zone_id"],
                "cycle_id": row["cycle_id"], "as_of": row["as_of"], "target_end": row["target_end"],
                "method": selected_ml, "raw_prediction_L": raw, "effective_prediction_L": max(0.0, raw),
                "status": "REVIEW_REQUIRED" if outside else "LAB_EXAMPLE_ONLY",
                "features_outside_training_range": outside})
    (target/"training_report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    (target/"predictions_test_SIMULASI.jsonl").write_text(
        "".join(json.dumps(r, allow_nan=False)+"\n" for r in prediction_records), encoding="utf-8")
    (target/"example_predictions_SIMULASI.json").write_text(json.dumps(example, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    lines = ["BALANCE ML LAB — SIMULASI", "ML_STATUS=TRAINED_SYNTHETIC_ONLY",
             "REAL_MEASUREMENT_ROWS=0", "PROPOSAL_TARGETS_PROVEN=false", ""]
    for task, detail in report["tasks"].items():
        lines += [f"{task.upper()}: selected ML={detail['selected_ml']}; preferred including baselines={detail['preferred_method_on_validation_including_baselines']}"]
        for method, result in detail["test"].items():
            m = result["effective"]
            lines.append(f"  {method}: test MAE={m['mae_L']:.2f} L; RMSE={m['rmse_L']:.2f} L; WAPE={m['wape_pct']:.2f}%")
            mape = m['mape_pct_above_threshold']
            mape_text = f"{mape:.2f}%" if mape is not None else "N/A"
            lines.append(f"    MAPE(y>100 L)={mape_text}; excluded={m['mape_excluded_n']}/{m['n']}")
    lines += ["", "Scores describe simulator holdout only; they do not prove field accuracy.",
              "TRAINING_COMPLETE", ""]
    (target/"summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return report

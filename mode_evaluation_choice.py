import sys
from pathlib import Path

# Automatic environment delegation if sklearn is missing in the invoking interpreter
try:
    from sklearn.tree import DecisionTreeRegressor
    from sklearn.ensemble import RandomForestRegressor
except ImportError:
    import subprocess
    candidates = [
        ["py", "-3.13"],
        [r"C:\Users\datta\AppData\Local\Programs\Python\Python313\python.exe"],
    ]
    for cand in candidates:
        try:
            check = subprocess.run(cand + ["-c", "import sklearn"], capture_output=True)
            if check.returncode == 0:
                res = subprocess.run(cand + [str(Path(__file__).resolve())] + sys.argv[1:])
                sys.exit(res.returncode)
        except Exception:
            continue
    print("Error: scikit-learn is required. Please install via: pip install scikit-learn", file=sys.stderr)
    sys.exit(1)

import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CURRENT_DIR = Path(__file__).resolve().parent
ROOT = CURRENT_DIR if (CURRENT_DIR / "data").is_dir() else CURRENT_DIR.parent
DATA_PATH = ROOT / "data" / "processed" / "training_data.csv"
MODEL_PATH = ROOT / "model" / "regression_model.npz"
RESULTS_DIR = ROOT / "experiments" / "model_evaluation_choice_results"

TRAIN_CUTOFF_DATE = "2026-01-01"
CHUNK_SIZE = 100_000

NUMERIC_FEATURES = [
    "total_copies",
    "checkouts_last_1d",
    "checkouts_last_3d",
    "checkouts_last_7d",
    "returns_last_7d",
    "demand_growth",
    "days_to_first_exam",
]

DEPARTMENTS = [
    "CSE", "IT", "ECE", "EEE", "Mechanical", "Civil",
    "Chemical", "Biotechnology", "Biomedical", "Aerospace",
    "Production", "Automotive", "Mathematics", "Physics",
    "Chemistry", "Common Engineering",
]

EXAM_TYPES = ["CAT1", "CAT2", "FAT"]
TARGET = "target_next_7d_checkouts"
REQUIRED_COLUMNS = ["date", TARGET, "department", "exam_type"] + NUMERIC_FEATURES


def clean_chunk(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=REQUIRED_COLUMNS).copy()
    for col in NUMERIC_FEATURES + [TARGET]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=NUMERIC_FEATURES + [TARGET])
    df = df[
        (df["total_copies"] > 0)
        & (df["days_to_first_exam"] >= 0)
        & (df["checkouts_last_1d"] >= 0)
        & (df["checkouts_last_3d"] >= 0)
        & (df["checkouts_last_7d"] >= 0)
        & (df["returns_last_7d"] >= 0)
        & (df[TARGET] >= 0)
        & df["department"].isin(DEPARTMENTS)
        & df["exam_type"].isin(EXAM_TYPES)
    ]
    return df


def build_feature_matrix(df: pd.DataFrame) -> np.ndarray:
    N = len(df)
    intercept = np.ones((N, 1), dtype=np.float64)
    numeric = df[NUMERIC_FEATURES].to_numpy(dtype=np.float64)
    dept_arr = df["department"].to_numpy()
    dept_ohe = np.column_stack([(dept_arr == d).astype(np.float64) for d in DEPARTMENTS])
    exam_arr = df["exam_type"].to_numpy()
    exam_ohe = np.column_stack([(exam_arr == e).astype(np.float64) for e in EXAM_TYPES])
    return np.column_stack([intercept, numeric, dept_ohe, exam_ohe])


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Dataset not found at {DATA_PATH}")
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model artifact not found at {MODEL_PATH}")

    # Load existing linear regression artifact
    saved = np.load(MODEL_PATH, allow_pickle=True)
    lr_coefficients = saved["coefficients"]
    historical_mean = float(saved["historical_mean"])

    # Stream and sample training data memory-consciously
    sample_rows = []
    for chunk_idx, chunk in enumerate(pd.read_csv(DATA_PATH, usecols=REQUIRED_COLUMNS, chunksize=CHUNK_SIZE)):
        chunk = clean_chunk(chunk)
        train_chunk = chunk[chunk["date"] < TRAIN_CUTOFF_DATE]
        if train_chunk.empty:
            break
        s = train_chunk.sample(n=min(2000, len(train_chunk)), random_state=42 + chunk_idx)
        sample_rows.append(s)

    train_df = pd.concat(sample_rows, ignore_index=True)
    X_train_full = build_feature_matrix(train_df)
    X_train = X_train_full[:, 1:].astype(np.float32)
    y_train = train_df[TARGET].to_numpy(dtype=np.float32)

    # Train Decision Tree
    dt = DecisionTreeRegressor(random_state=42)
    dt.fit(X_train, y_train)

    # Train Random Forest
    rf = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=2)
    rf.fit(X_train, y_train)

    # Models list
    models = [
        "Always Predict Zero baseline",
        "Historical Average baseline",
        "Multiple Linear Regression",
        "Decision Tree Regressor",
        "Random Forest Regressor",
    ]

    # Metrics accumulators
    n_test = 0
    sum_y = 0.0
    sum_y_sq = 0.0

    mae_sums = {m: 0.0 for m in models}
    mse_sums = {m: 0.0 for m in models}

    # Nonzero actual demand subset
    n_pos_actual = 0
    mae_pos_sums = {m: 0.0 for m in models}

    # CAT-2 within 14 days subset
    n_cat2 = 0
    sum_y_cat2 = 0.0
    sum_y_sq_cat2 = 0.0
    mae_cat2_sums = {m: 0.0 for m in models}
    mse_cat2_sums = {m: 0.0 for m in models}

    # Evaluate on held-out test data
    for chunk in pd.read_csv(DATA_PATH, usecols=REQUIRED_COLUMNS, chunksize=CHUNK_SIZE):
        chunk = clean_chunk(chunk)
        test_chunk = chunk[chunk["date"] >= TRAIN_CUTOFF_DATE]
        if test_chunk.empty:
            continue

        X_full = build_feature_matrix(test_chunk)
        X_tree = X_full[:, 1:].astype(np.float32)
        y = test_chunk[TARGET].to_numpy(dtype=np.float64)
        n = len(y)

        preds = {
            "Always Predict Zero baseline": np.zeros(n, dtype=np.float64),
            "Historical Average baseline": np.full(n, historical_mean, dtype=np.float64),
            "Multiple Linear Regression": np.maximum(0.0, X_full @ lr_coefficients),
            "Decision Tree Regressor": np.maximum(0.0, dt.predict(X_tree).astype(np.float64)),
            "Random Forest Regressor": np.maximum(0.0, rf.predict(X_tree).astype(np.float64)),
        }

        n_test += n
        sum_y += np.sum(y)
        sum_y_sq += np.sum(y ** 2)

        for m in models:
            err = y - preds[m]
            mae_sums[m] += np.sum(np.abs(err))
            mse_sums[m] += np.sum(err ** 2)

        # Nonzero actual subset
        mask_pos = y > 0
        n_p = int(np.sum(mask_pos))
        if n_p > 0:
            n_pos_actual += n_p
            y_pos = y[mask_pos]
            for m in models:
                mae_pos_sums[m] += np.sum(np.abs(y_pos - preds[m][mask_pos]))

        # CAT-2 within 14 days subset
        mask_cat2 = (test_chunk["exam_type"] == "CAT2") & (test_chunk["days_to_first_exam"] <= 14)
        n_c2 = int(np.sum(mask_cat2))
        if n_c2 > 0:
            n_cat2 += n_c2
            y_c2 = y[mask_cat2]
            sum_y_cat2 += np.sum(y_c2)
            sum_y_sq_cat2 += np.sum(y_c2 ** 2)
            for m in models:
                err_c2 = y_c2 - preds[m][mask_cat2]
                mae_cat2_sums[m] += np.sum(np.abs(err_c2))
                mse_cat2_sums[m] += np.sum(err_c2 ** 2)

    # Calculate overall metrics
    test_mean_y = sum_y / n_test
    ss_tot = sum_y_sq - n_test * (test_mean_y ** 2)

    table_records = []
    for m in models:
        mae = mae_sums[m] / n_test
        rmse = np.sqrt(mse_sums[m] / n_test)
        r2 = 1.0 - (mse_sums[m] / ss_tot)
        table_records.append({
            "Model": m,
            "MAE": round(mae, 6),
            "RMSE": round(rmse, 6),
            "R-squared": round(r2, 6),
        })

    # Calculate subset metrics
    subset_records = []
    mean_y_c2 = sum_y_cat2 / n_cat2 if n_cat2 > 0 else 0.0
    ss_tot_c2 = sum_y_sq_cat2 - n_cat2 * (mean_y_c2 ** 2) if n_cat2 > 0 else 0.0

    for m in models:
        mae_nonzero = mae_pos_sums[m] / n_pos_actual if n_pos_actual > 0 else 0.0
        mae_cat2 = mae_cat2_sums[m] / n_cat2 if n_cat2 > 0 else 0.0
        rmse_cat2 = np.sqrt(mse_cat2_sums[m] / n_cat2) if n_cat2 > 0 else 0.0
        r2_cat2 = 1.0 - (mse_cat2_sums[m] / ss_tot_c2) if ss_tot_c2 > 0 else 0.0
        subset_records.append({
            "Model": m,
            "MAE_Nonzero_Demand": round(mae_nonzero, 6),
            "MAE_CAT2_Near_Exam": round(mae_cat2, 6),
            "RMSE_CAT2_Near_Exam": round(rmse_cat2, 6),
            "R2_CAT2_Near_Exam": round(r2_cat2, 6),
        })

    # Save outputs to results directory
    df_results = pd.DataFrame(table_records)
    df_results.to_csv(RESULTS_DIR / "model_comparison.csv", index=False)
    with open(RESULTS_DIR / "model_comparison.json", "w") as f:
        json.dump(table_records, f, indent=2)

    df_subsets = pd.DataFrame(subset_records)
    df_subsets.to_csv(RESULTS_DIR / "detailed_subset_metrics.csv", index=False)
    with open(RESULTS_DIR / "detailed_subset_metrics.json", "w") as f:
        json.dump({
            "test_rows": n_test,
            "nonzero_rows": n_pos_actual,
            "cat2_near_exam_rows": n_cat2,
            "subsets": subset_records,
        }, f, indent=2)

    # Save comparison plot
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    model_names_short = ["Zero", "Hist Avg", "Linear Reg", "Decision Tree", "Random Forest"]
    maes = [r["MAE"] for r in table_records]
    rmses = [r["RMSE"] for r in table_records]
    r2s = [r["R-squared"] for r in table_records]

    axes[0].bar(model_names_short, maes, color="#3b82f6")
    axes[0].set_title("Mean Absolute Error (MAE)")
    axes[0].set_ylabel("MAE")
    axes[0].tick_params(axis="x", rotation=30)

    axes[1].bar(model_names_short, rmses, color="#10b981")
    axes[1].set_title("Root Mean Squared Error (RMSE)")
    axes[1].set_ylabel("RMSE")
    axes[1].tick_params(axis="x", rotation=30)

    axes[2].bar(model_names_short, r2s, color="#f59e0b")
    axes[2].set_title("R-squared (R²)")
    axes[2].set_ylabel("R²")
    axes[2].axhline(0, color="gray", linestyle="--", linewidth=0.8)
    axes[2].tick_params(axis="x", rotation=30)

    plt.tight_layout()
    plt.savefig(RESULTS_DIR / "metrics_comparison.png", dpi=300)
    plt.close()

    # Output ONLY the clean comparison table to stdout
    print(f"| {'Model':<35} | {'MAE':<10} | {'RMSE':<10} | {'R-squared':<10} |")
    print(f"|:{'-'*35}-|-{'-'*10}:|-{'-'*10}:|-{'-'*10}:|")
    for r in table_records:
        print(f"| {r['Model']:<35} | {r['MAE']:<10.6f} | {r['RMSE']:<10.6f} | {r['R-squared']:<10.6f} |")


if __name__ == "__main__":
    main()

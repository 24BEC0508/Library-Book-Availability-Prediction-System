"""
Multiple Linear Regression Training and Chronological Evaluation from Scratch.

Uses NumPy for numerically stable ordinary least squares (lstsq) via chunked
accumulation of X^T X and X^T y. Scikit-learn is not used.
Evaluates chronologically on held-out future dates (default: 2026-01-01 onwards).
Compares against zero-prediction baseline and historical average baseline.
"""

from pathlib import Path
import time
import argparse
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "processed" / "training_data.csv"
MODEL_PATH = ROOT / "model" / "regression_model.npz"

CHUNK_SIZE = 100_000
TRAIN_CUTOFF_DATE = "2026-01-01"

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

FEATURE_NAMES = (
    ["intercept"]
    + NUMERIC_FEATURES
    + [f"dept_{d}" for d in DEPARTMENTS]
    + [f"exam_{e}" for e in EXAM_TYPES]
)
FEATURE_COUNT = len(FEATURE_NAMES)

REQUIRED_COLUMNS = ["date", TARGET, "department", "exam_type"] + NUMERIC_FEATURES


def clean_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and clean incoming chunk."""
    df = df.dropna(subset=REQUIRED_COLUMNS).copy()

    for col in NUMERIC_FEATURES + [TARGET]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=NUMERIC_FEATURES + [TARGET])

    # Filter invalid boundaries
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
    """
    Construct one-hot encoded design matrix with intercept.
    Guarantees strict, reproducible column ordering.
    """
    N = len(df)
    intercept = np.ones((N, 1), dtype=np.float64)
    numeric = df[NUMERIC_FEATURES].to_numpy(dtype=np.float64)

    # Department one-hot encoding
    dept_arr = df["department"].to_numpy()
    dept_ohe = np.column_stack([
        (dept_arr == d).astype(np.float64) for d in DEPARTMENTS
    ])

    # Exam type one-hot encoding
    exam_arr = df["exam_type"].to_numpy()
    exam_ohe = np.column_stack([
        (exam_arr == e).astype(np.float64) for e in EXAM_TYPES
    ])

    X = np.column_stack([intercept, numeric, dept_ohe, exam_ohe])
    assert X.shape[1] == FEATURE_COUNT, f"Expected {FEATURE_COUNT} features, got {X.shape[1]}"
    return X


def train_model(data_path: Path, cutoff_date: str = TRAIN_CUTOFF_DATE):
    print("=" * 60)
    print("PHASE C: TRAINING MULTIPLE LINEAR REGRESSION (NUMPY LSTSQ)")
    print("=" * 60)
    print(f"Data source: {data_path}")
    print(f"Training split: date < {cutoff_date}")
    print(f"Feature count: {FEATURE_COUNT} (Intercept + 7 Numeric + 16 Depts + 3 Exams)")

    xtx = np.zeros((FEATURE_COUNT, FEATURE_COUNT), dtype=np.float64)
    xty = np.zeros(FEATURE_COUNT, dtype=np.float64)

    train_rows = 0
    train_sum_y = 0.0
    train_sum_y_sq = 0.0

    t_start = time.time()
    for chunk_idx, chunk in enumerate(
        pd.read_csv(data_path, usecols=REQUIRED_COLUMNS, chunksize=CHUNK_SIZE),
        start=1,
    ):
        chunk = clean_chunk(chunk)
        if chunk.empty:
            continue

        # Chronological filter: training rows are strictly prior to cutoff
        train_chunk = chunk[chunk["date"] < cutoff_date]
        if train_chunk.empty:
            # If dataset is sorted chronologically and we passed cutoff, we can stop training pass!
            if chunk["date"].min() >= cutoff_date:
                print(f"Reached cutoff date {cutoff_date} at chunk {chunk_idx}. Ending training accumulation.")
                break
            continue

        X = build_feature_matrix(train_chunk)
        y = train_chunk[TARGET].to_numpy(dtype=np.float64)

        xtx += X.T @ X
        xty += X.T @ y

        n_c = len(y)
        train_rows += n_c
        train_sum_y += np.sum(y)
        train_sum_y_sq += np.sum(y ** 2)

        if train_rows % 1_000_000 < n_c:
            print(f"Accumulated {train_rows:,} training rows | Elapsed: {time.time()-t_start:.1f}s", flush=True)

    if train_rows == 0:
        raise ValueError(f"No training rows found before cutoff date {cutoff_date}.")

    historical_mean = train_sum_y / train_rows
    print(f"\nTotal training rows: {train_rows:,}")
    print(f"Training mean target: {historical_mean:.6f}")
    print(f"Solving OLS coefficients via np.linalg.lstsq...", flush=True)

    # Numerically stable linear algebra: avoids explicit matrix inversion
    coefficients, residuals, rank, s = np.linalg.lstsq(xtx, xty, rcond=None)

    # Save model artifact
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        MODEL_PATH,
        coefficients=coefficients,
        feature_names=np.array(FEATURE_NAMES),
        numeric_features=np.array(NUMERIC_FEATURES),
        departments=np.array(DEPARTMENTS),
        exam_types=np.array(EXAM_TYPES),
        historical_mean=np.array(historical_mean),
        cutoff_date=np.array(cutoff_date),
        train_rows=np.array(train_rows),
    )
    print(f"Saved model artifact to: {MODEL_PATH}")
    print(f"Condition number of X^T X: {s[0] / s[-1]:.2e}")

    return coefficients, historical_mean, train_rows


def evaluate_model(coefficients: np.ndarray, historical_mean: float, data_path: Path, cutoff_date: str = TRAIN_CUTOFF_DATE):
    print("\n" + "=" * 60)
    print("CHRONOLOGICAL EVALUATION ON HELD-OUT FUTURE DATA")
    print(f"Held-out test period: date >= {cutoff_date}")
    print("=" * 60)

    # Metric accumulators
    n_test = 0
    sum_y = 0.0
    sum_y_sq = 0.0

    # Model metrics
    mae_sum_model = 0.0
    mse_sum_model = 0.0

    # Clipped model metrics (predicted >= 0)
    mae_sum_clipped = 0.0
    mse_sum_clipped = 0.0

    # Baseline: Zero
    mae_sum_zero = 0.0
    mse_sum_zero = 0.0

    # Baseline: Historical Mean
    mae_sum_mean = 0.0
    mse_sum_mean = 0.0

    # Positive predictions count
    pred_pos_count = 0
    pred_above_half_count = 0
    pred_neg_count = 0

    # Nonzero actual demand subset
    n_pos_actual = 0
    mae_model_pos = 0.0
    mae_zero_pos = 0.0
    mae_mean_pos = 0.0
    sum_pred_pos_actual = 0.0
    sum_actual_pos_actual = 0.0

    # CAT-2 near-exam subset (within 14 days)
    n_cat2_near = 0
    sum_y_cat2 = 0.0
    sum_y_sq_cat2 = 0.0
    mae_model_cat2 = 0.0
    mse_model_cat2 = 0.0
    mae_zero_cat2 = 0.0
    mse_zero_cat2 = 0.0
    mae_mean_cat2 = 0.0
    mse_mean_cat2 = 0.0

    t_start = time.time()
    for chunk in pd.read_csv(data_path, usecols=REQUIRED_COLUMNS, chunksize=CHUNK_SIZE):
        chunk = clean_chunk(chunk)
        if chunk.empty:
            continue

        test_chunk = chunk[chunk["date"] >= cutoff_date]
        if test_chunk.empty:
            continue

        X = build_feature_matrix(test_chunk)
        y = test_chunk[TARGET].to_numpy(dtype=np.float64)

        pred = X @ coefficients
        pred_clipped = np.maximum(0.0, pred)

        n = len(y)
        n_test += n
        sum_y += np.sum(y)
        sum_y_sq += np.sum(y ** 2)

        # Errors
        err_model = y - pred
        err_clipped = y - pred_clipped
        err_zero = y
        err_mean = y - historical_mean

        mae_sum_model += np.sum(np.abs(err_model))
        mse_sum_model += np.sum(err_model ** 2)

        mae_sum_clipped += np.sum(np.abs(err_clipped))
        mse_sum_clipped += np.sum(err_clipped ** 2)

        mae_sum_zero += np.sum(np.abs(err_zero))
        mse_sum_zero += np.sum(err_zero ** 2)

        mae_sum_mean += np.sum(np.abs(err_mean))
        mse_sum_mean += np.sum(err_mean ** 2)

        pred_pos_count += int(np.sum(pred > 0))
        pred_above_half_count += int(np.sum(pred >= 0.5))
        pred_neg_count += int(np.sum(pred < 0))

        # Nonzero actual subset
        mask_pos = y > 0
        n_p = int(np.sum(mask_pos))
        if n_p > 0:
            n_pos_actual += n_p
            mae_model_pos += np.sum(np.abs(err_model[mask_pos]))
            mae_zero_pos += np.sum(np.abs(err_zero[mask_pos]))
            mae_mean_pos += np.sum(np.abs(err_mean[mask_pos]))
            sum_pred_pos_actual += np.sum(pred[mask_pos])
            sum_actual_pos_actual += np.sum(y[mask_pos])

        # CAT-2 within 14 days subset
        mask_cat2 = (test_chunk["exam_type"] == "CAT2") & (test_chunk["days_to_first_exam"] <= 14)
        n_c2 = int(np.sum(mask_cat2))
        if n_c2 > 0:
            y_c2 = y[mask_cat2]
            p_c2 = pred[mask_cat2]
            n_cat2_near += n_c2
            sum_y_cat2 += np.sum(y_c2)
            sum_y_sq_cat2 += np.sum(y_c2 ** 2)
            mae_model_cat2 += np.sum(np.abs(y_c2 - p_c2))
            mse_model_cat2 += np.sum((y_c2 - p_c2) ** 2)
            mae_zero_cat2 += np.sum(np.abs(y_c2))
            mse_zero_cat2 += np.sum(y_c2 ** 2)
            mae_mean_cat2 += np.sum(np.abs(y_c2 - historical_mean))
            mse_mean_cat2 += np.sum((y_c2 - historical_mean) ** 2)

        if n_test % 1_000_000 < n:
            print(f"Evaluated {n_test:,} held-out test rows | Elapsed: {time.time()-t_start:.1f}s", flush=True)

    if n_test == 0:
        raise ValueError("No held-out test rows evaluated.")

    # Overall Metrics Calculation
    test_mean_y = sum_y / n_test
    ss_tot = sum_y_sq - n_test * (test_mean_y ** 2)

    mae_zero = mae_sum_zero / n_test
    rmse_zero = np.sqrt(mse_sum_zero / n_test)
    r2_zero = 1.0 - (mse_sum_zero / ss_tot) if ss_tot > 0 else float("nan")

    mae_mean = mae_sum_mean / n_test
    rmse_mean = np.sqrt(mse_sum_mean / n_test)
    r2_mean = 1.0 - (mse_sum_mean / ss_tot) if ss_tot > 0 else float("nan")

    mae_model = mae_sum_model / n_test
    rmse_model = np.sqrt(mse_sum_model / n_test)
    r2_model = 1.0 - (mse_sum_model / ss_tot) if ss_tot > 0 else float("nan")

    mae_clipped = mae_sum_clipped / n_test
    rmse_clipped = np.sqrt(mse_sum_clipped / n_test)
    r2_clipped = 1.0 - (mse_sum_clipped / ss_tot) if ss_tot > 0 else float("nan")

    print("\n" + "=" * 60)
    print("OVERALL HELD-OUT PERFORMANCE (IDENTICAL TEST ROWS)")
    print(f"Total test rows: {n_test:,}")
    print(f"Actual test mean target: {test_mean_y:.6f}")
    print(f"Actual zeros percentage: {(n_test - n_pos_actual) / n_test:.2%}")
    print("-" * 60)
    print(f"{'Model / Baseline':<32} | {'MAE':<10} | {'RMSE':<10} | {'R-squared':<10}")
    print("-" * 60)
    print(f"{'1. Always Predict Zero':<32} | {mae_zero:<10.6f} | {rmse_zero:<10.6f} | {r2_zero:<10.6f}")
    print(f"{'2. Historical Average Baseline':<32} | {mae_mean:<10.6f} | {rmse_mean:<10.6f} | {r2_mean:<10.6f}")
    print(f"{'3. Multiple Linear Regression (Raw)':<32} | {mae_model:<10.6f} | {rmse_model:<10.6f} | {r2_model:<10.6f}")
    print(f"{'4. Linear Regression (Clipped >=0)':<32} | {mae_clipped:<10.6f} | {rmse_clipped:<10.6f} | {r2_clipped:<10.6f}")
    print("=" * 60)

    print("\nPREDICTION BEHAVIOR & DIAGNOSTICS")
    print(f"Positive predictions (> 0):   {pred_pos_count:,} ({pred_pos_count / n_test:.2%})")
    print(f"Negative predictions (< 0):   {pred_neg_count:,} ({pred_neg_count / n_test:.2%})")
    print(f"Significant predictions (>=0.5): {pred_above_half_count:,} ({pred_above_half_count / n_test:.2%})")

    if n_pos_actual > 0:
        print("\nPERFORMANCE ON NONZERO DEMAND ROWS (Actual > 0)")
        print(f"Rows with actual checkouts: {n_pos_actual:,} ({n_pos_actual / n_test:.2%})")
        print(f"Mean actual checkouts:      {sum_actual_pos_actual / n_pos_actual:.4f}")
        print(f"Mean model prediction:      {sum_pred_pos_actual / n_pos_actual:.4f}")
        print(f"MAE - Zero Baseline:        {mae_zero_pos / n_pos_actual:.4f}")
        print(f"MAE - Historical Mean:      {mae_mean_pos / n_pos_actual:.4f}")
        print(f"MAE - Linear Regression:    {mae_model_pos / n_pos_actual:.4f}")

    if n_cat2_near > 0:
        mean_y_c2 = sum_y_cat2 / n_cat2_near
        ss_tot_c2 = sum_y_sq_cat2 - n_cat2_near * (mean_y_c2 ** 2)
        r2_model_c2 = 1.0 - (mse_model_cat2 / ss_tot_c2) if ss_tot_c2 > 0 else float("nan")
        print("\nPERFORMANCE NEAR CAT-2 EXAM (Within 14 Days)")
        print(f"Rows near CAT-2:            {n_cat2_near:,}")
        print(f"Mean actual target:         {mean_y_c2:.4f}")
        print(f"MAE - Zero Baseline:        {mae_zero_cat2 / n_cat2_near:.4f}")
        print(f"MAE - Historical Mean:      {mae_mean_cat2 / n_cat2_near:.4f}")
        print(f"MAE - Linear Regression:    {mae_model_cat2 / n_cat2_near:.4f}")
        print(f"RMSE - Linear Regression:   {np.sqrt(mse_model_cat2 / n_cat2_near):.4f}")
        print(f"R-squared near CAT-2:       {r2_model_c2:.4f}")
    print("=" * 60)

    # Return summary dict
    return {
        "n_test": n_test,
        "mae_zero": mae_zero,
        "rmse_zero": rmse_zero,
        "r2_zero": r2_zero,
        "mae_mean": mae_mean,
        "rmse_mean": rmse_mean,
        "r2_mean": r2_mean,
        "mae_model": mae_model,
        "rmse_model": rmse_model,
        "r2_model": r2_model,
    }


def main():
    parser = argparse.ArgumentParser(description="Train and evaluate linear regression model.")
    parser.add_argument("--data", type=str, default=str(DATA_PATH), help="Path to training CSV.")
    parser.add_argument("--cutoff", type=str, default=TRAIN_CUTOFF_DATE, help="Chronological split date.")
    args = parser.parse_args()

    data_file = Path(args.data)
    if not data_file.exists():
        raise FileNotFoundError(f"Training dataset not found: {data_file}")

    coefficients, historical_mean, train_rows = train_model(data_file, cutoff_date=args.cutoff)
    evaluate_model(coefficients, historical_mean, data_file, cutoff_date=args.cutoff)


if __name__ == "__main__":
    main()

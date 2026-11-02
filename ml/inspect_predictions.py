"""
Inspect Prediction Behavior and Diagnostic Distributions.

Loads the trained regression model and inspects predicted values vs actual
targets on chronological held-out test data.
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "processed" / "training_data.csv"
MODEL_PATH = ROOT / "model" / "regression_model.npz"

NUMERIC_FEATURES = [
    "total_copies",
    "checkouts_last_1d",
    "checkouts_last_3d",
    "checkouts_last_7d",
    "returns_last_7d",
    "demand_growth",
    "days_to_first_exam",
]

TARGET = "target_next_7d_checkouts"


def main():
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Training dataset not found: {DATA_PATH}")
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model artifact not found: {MODEL_PATH}")

    model = np.load(MODEL_PATH, allow_pickle=True)
    coefficients = model["coefficients"]
    departments = list(model["departments"])
    exam_types = list(model["exam_types"])
    cutoff_date = str(model["cutoff_date"])

    columns = NUMERIC_FEATURES + ["date", "department", "exam_type", TARGET]

    print(f"Reading sample of held-out test records (date >= {cutoff_date})...", flush=True)

    # Read chunks until we gather 100,000 test rows
    test_rows_list = []
    for chunk in pd.read_csv(DATA_PATH, usecols=columns, chunksize=100_000):
        test_chunk = chunk[chunk["date"] >= cutoff_date]
        if not test_chunk.empty:
            test_rows_list.append(test_chunk)
        if sum(len(c) for c in test_rows_list) >= 100_000:
            break

    if not test_rows_list:
        raise ValueError("No held-out test rows found.")

    df = pd.concat(test_rows_list, ignore_index=True).head(100_000)

    for col in NUMERIC_FEATURES + [TARGET]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna()

    N = len(df)
    intercept = np.ones((N, 1), dtype=np.float64)
    numeric = df[NUMERIC_FEATURES].to_numpy(dtype=np.float64)

    dept_arr = df["department"].to_numpy()
    dept_ohe = np.column_stack([(dept_arr == d).astype(np.float64) for d in departments])

    exam_arr = df["exam_type"].to_numpy()
    exam_ohe = np.column_stack([(exam_arr == e).astype(np.float64) for e in exam_types])

    X = np.column_stack([intercept, numeric, dept_ohe, exam_ohe])
    actual = df[TARGET].to_numpy(dtype=np.float64)
    predicted = X @ coefficients

    print("\n" + "=" * 50)
    print("PREDICTION DIAGNOSTICS ON HELD-OUT DATA")
    print("=" * 50)
    print(f"Sample size:           {len(df):,}")
    print(f"Mean actual target:    {actual.mean():.6f}")
    print(f"Mean predicted demand: {predicted.mean():.6f}")
    print(f"Min predicted:         {predicted.min():.6f}")
    print(f"Max predicted:         {predicted.max():.6f}")
    print(f"Negative predictions:  {(predicted < 0).mean():.2%}")
    print(f"Zero actual target:    {(actual == 0).mean():.2%}")
    print(f"Predictions >= 0.5:    {(predicted >= 0.5).mean():.2%}")
    print(f"Predictions >= 1.0:    {(predicted >= 1.0).mean():.2%}")

    print("\nMean prediction grouped by actual checkout count:")
    for val in sorted(df[TARGET].unique()):
        mask = actual == val
        print(
            f"Actual {int(val)} checkouts: {mask.sum():,} rows | "
            f"Mean Predicted = {predicted[mask].mean():.6f} | "
            f"Clipped Mean = {np.maximum(0, predicted[mask]).mean():.6f}"
        )

    print("\nMean prediction by exam type:")
    for exam in exam_types:
        mask = df["exam_type"] == exam
        if mask.any():
            print(
                f"{exam:<6}: {mask.sum():,} rows | "
                f"Actual Mean = {actual[mask].mean():.4f} | "
                f"Predicted Mean = {predicted[mask].mean():.4f}"
            )
    print("=" * 50)


if __name__ == "__main__":
    main()

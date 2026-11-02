"""
Fair Comparative Evaluation of Baselines and Multiple Linear Regression.

Evaluates all models on identical chronological held-out test rows (date >= 2026-01-01):
1. Always Predict Zero
2. Historical Average Baseline (computed on training data only)
3. Multiple Linear Regression (NumPy lstsq)
4. Clipped Linear Regression (max(0, pred))

Reports MAE, RMSE, R-squared, nonzero actual demand metrics, and CAT-2 exam metrics.
"""

from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.train_regression import (
    DATA_PATH,
    MODEL_PATH,
    evaluate_model,
)


def main():
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {DATA_PATH}")

    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Model artifact not found: {MODEL_PATH}. Run ml/train_regression.py first."
        )

    print("Loading trained model artifact...", flush=True)
    saved = np.load(MODEL_PATH, allow_pickle=True)
    coefficients = saved["coefficients"]
    historical_mean = float(saved["historical_mean"])
    cutoff_date = str(saved["cutoff_date"])

    print(f"Model trained on data before: {cutoff_date}")
    print(f"Historical training mean target: {historical_mean:.6f}")

    evaluate_model(
        coefficients=coefficients,
        historical_mean=historical_mean,
        data_path=DATA_PATH,
        cutoff_date=cutoff_date,
    )


if __name__ == "__main__":
    main()

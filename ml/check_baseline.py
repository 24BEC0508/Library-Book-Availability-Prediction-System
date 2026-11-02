
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "processed" / "training_data.csv"

TARGET = "target_next_7d_checkouts"
CHUNK_SIZE = 50_000

count = 0
sum_abs = 0.0
sum_squared = 0.0
sum_y = 0.0
sum_y_squared = 0.0

print("Evaluating zero-prediction baseline...", flush=True)

for chunk in pd.read_csv(
    DATA_PATH,
    usecols=[TARGET],
    chunksize=CHUNK_SIZE,
):
    y = pd.to_numeric(chunk[TARGET], errors="coerce").dropna()
    y = y[y >= 0].to_numpy(dtype=np.float64)

    count += len(y)
    sum_abs += np.abs(y).sum()
    sum_squared += (y ** 2).sum()
    sum_y += y.sum()
    sum_y_squared += (y ** 2).sum()

mae = sum_abs / count
rmse = np.sqrt(sum_squared / count)

mean_y = sum_y / count
ss_total = sum_y_squared - count * mean_y**2
r2 = 1 - sum_squared / ss_total if ss_total > 0 else float("nan")

print("\nZERO-PREDICTION BASELINE")
print("------------------------")
print(f"Rows: {count:,}")
print(f"MAE: {mae:.6f}")
print(f"RMSE: {rmse:.6f}")
print(f"R-squared: {r2:.6f}")

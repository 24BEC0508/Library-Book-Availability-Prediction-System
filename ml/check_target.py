
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "processed" / "training_data.csv"

target = "target_next_7d_checkouts"

print("Reading target distribution in chunks...", flush=True)

counts = {}
total = 0
sum_y = 0.0
sum_y_squared = 0.0
minimum = float("inf")
maximum = float("-inf")

for chunk in pd.read_csv(
    DATA_PATH,
    usecols=[target],
    chunksize=50_000
):
    y = pd.to_numeric(chunk[target], errors="coerce").dropna()
    y = y[y >= 0]

    total += len(y)
    sum_y += y.sum()
    sum_y_squared += (y ** 2).sum()
    minimum = min(minimum, y.min())
    maximum = max(maximum, y.max())

    for value, count in y.value_counts().items():
        counts[value] = counts.get(value, 0) + int(count)

print("\nVALID TARGET ROWS:", f"{total:,}")
print("Minimum:", minimum)
print("Maximum:", maximum)
print("Mean:", round(sum_y / total, 5))

print("\nTarget value frequencies:")
for value in sorted(counts):
    print(f"{value}: {counts[value]:,} ({counts[value] / total:.2%})")

print("\nPercentage of zero-checkout targets:",
      f"{counts.get(0, 0) / total:.2%}")

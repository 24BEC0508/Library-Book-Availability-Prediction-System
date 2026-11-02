"""
Check Feature Signal and Correlations with Future Target.

Evaluates correlation between features and the 7-day future target across
representative activity periods in the dataset.
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "processed" / "training_data.csv"

COLUMNS = [
    "checkouts_last_1d",
    "checkouts_last_3d",
    "checkouts_last_7d",
    "returns_last_7d",
    "demand_growth",
    "days_to_first_exam",
    "total_copies",
    "target_next_7d_checkouts",
]


def main():
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {DATA_PATH}")

    print("Sampling rows across dataset chunks for signal analysis...", flush=True)

    # Sample rows across multiple chunks throughout the file
    samples = []
    chunk_count = 0
    for chunk in pd.read_csv(DATA_PATH, usecols=COLUMNS, chunksize=100_000):
        chunk_count += 1
        # Take 10,000 rows from each chunk up to 200,000 total sampled rows
        sample_chunk = chunk.sample(n=min(10_000, len(chunk)), random_state=42)
        samples.append(sample_chunk)
        if len(samples) >= 25:
            break

    df = pd.concat(samples, ignore_index=True)
    for col in COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna()

    print(f"Sample size: {len(df):,} rows from {chunk_count} chunks")

    print("\nCORRELATIONS WITH FUTURE 7-DAY CHECKOUT TARGET:")
    corr = df.corr(numeric_only=True)["target_next_7d_checkouts"].drop("target_next_7d_checkouts")
    print(corr.sort_values(ascending=False).to_string())

    print("\nAverage future checkouts by recent 7-day activity:")
    df["recent_activity_group"] = pd.cut(
        df["checkouts_last_7d"],
        bins=[-1, 0, 1, 3, float("inf")],
        labels=["0", "1", "2-3", "4+"],
    )
    print(
        df.groupby("recent_activity_group", observed=False)["target_next_7d_checkouts"]
        .agg(["count", "mean", "std"])
        .to_string()
    )


if __name__ == "__main__":
    main()


from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "processed" / "training_data.csv"

columns = [
    "exam_type",
    "target_next_7d_checkouts",
    "days_to_first_exam",
]

print("Checking checkout demand around exams...", flush=True)

totals = {}

for chunk in pd.read_csv(
    DATA_PATH,
    usecols=columns,
    chunksize=50_000,
):
    chunk = chunk.dropna(subset=columns)

    # Focus on the 14 days leading up to the next exam.
    chunk = chunk[
        chunk["days_to_first_exam"].between(0, 14)
    ]

    for exam_type, group in chunk.groupby("exam_type"):
        if exam_type not in totals:
            totals[exam_type] = {
                "rows": 0,
                "total_checkouts": 0.0,
                "positive_rows": 0,
            }

        target = group["target_next_7d_checkouts"]

        totals[exam_type]["rows"] += len(group)
        totals[exam_type]["total_checkouts"] += target.sum()
        totals[exam_type]["positive_rows"] += int(
            (target > 0).sum()
        )

print("\nDemand within 14 days of an exam")
print("-" * 55)

for exam_type, values in sorted(totals.items()):
    rows = values["rows"]

    if rows == 0:
        continue

    print(f"\nExam: {exam_type}")
    print(f"Rows: {rows:,}")
    print(
        "Mean predicted target: "
        f"{values['total_checkouts'] / rows:.5f}"
    )
    print(
        "Rows with any checkouts: "
        f"{values['positive_rows'] / rows:.2%}"
    )

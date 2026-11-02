
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

books = pd.read_csv(ROOT / "data" / "raw" / "books.csv")
tx = pd.read_csv(ROOT / "data" / "raw" / "transactions.csv")

tx["transaction_date"] = pd.to_datetime(tx["transaction_date"])
tx["first_exam_date"] = pd.to_datetime(tx["first_exam_date"])

print("========== VALIDATION ==========")

# 1. Duplicate IDs
print("\nDuplicate book IDs:", books["book_id"].duplicated().sum())
print("Duplicate transaction IDs:", tx["transaction_id"].duplicated().sum())

# 2. Unknown book references
print(
    "Transactions referencing unknown books:",
    (~tx["book_id"].isin(books["book_id"])).sum()
)

# 3. Future records
cutoff = pd.Timestamp("2026-09-05 23:59:59")
print("Transactions after cutoff:", (tx["transaction_date"] > cutoff).sum())

# 4. Exam dates
print(
    "Transactions dated after their first exam:",
    (tx["transaction_date"].dt.normalize()
     > tx["first_exam_date"].dt.normalize()).sum()
)

# 5. Checkout/return distribution by exam cycle
print("\nTransaction types by exam cycle:")
print(pd.crosstab(tx["exam_cycle_id"], tx["transaction_type"]))

# 6. Checkout counts by cycle
print("\nCheckouts by exam cycle:")
print(
    tx[tx["transaction_type"] == "CHECKOUT"]
    .groupby("exam_cycle_id")
    .size()
    .sort_index()
)

# 7. Missing values
print("\nMissing values:")
print("Books:\n", books.isna().sum())
print("Transactions:\n", tx.isna().sum())

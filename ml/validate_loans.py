
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

books = pd.read_csv(ROOT / "data" / "raw" / "books.csv")
tx = pd.read_csv(ROOT / "data" / "raw" / "transactions.csv")

tx["transaction_date"] = pd.to_datetime(tx["transaction_date"])
tx = tx.sort_values(["book_id", "transaction_date", "transaction_id"])

# Check whether a return has a preceding checkout for that book.
issues = []

for book_id, group in tx.groupby("book_id"):
    outstanding = 0

    for row in group.itertuples(index=False):
        if row.transaction_type == "CHECKOUT":
            outstanding += 1
        elif row.transaction_type == "RETURN":
            if outstanding == 0:
                issues.append((book_id, row.transaction_id, "Return without prior checkout"))
            else:
                outstanding -= 1

print("Transactions:", len(tx))
print("Books:", len(books))
print("Returns without a preceding checkout:", len(issues))

if issues:
    print("\nFirst 10 issues:")
    for issue in issues[:10]:
        print(issue)

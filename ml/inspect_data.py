
from pathlib import Path
import pandas as pd

# Get the project's root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Dataset paths
books_path = PROJECT_ROOT / "data" / "raw" / "books.csv"
transactions_path = PROJECT_ROOT / "data" / "raw" / "transactions.csv"

# Check whether files exist
print("Books file found:", books_path.exists())
print("Transactions file found:", transactions_path.exists())

# Load datasets
books = pd.read_csv(books_path)
transactions = pd.read_csv(transactions_path)

print("\n========== BOOKS ==========")
print("Number of books:", len(books))
print("Columns:", books.columns.tolist())
print("\nFirst 5 rows:")
print(books.head())
print("\nBooks by department:")
print(books["department"].value_counts())

print("\n====== TRANSACTIONS ======")
print("Number of transactions:", len(transactions))
print("Columns:", transactions.columns.tolist())
print("\nFirst 5 rows:")
print(transactions.head())

print("\nTransaction types:")
print(transactions["transaction_type"].value_counts())

transactions["transaction_date"] = pd.to_datetime(
    transactions["transaction_date"]
)

print("\nDate range:")
print("Start:", transactions["transaction_date"].min())
print("End:", transactions["transaction_date"].max())

print("\nTransactions by year:")
print(transactions["transaction_date"].dt.year.value_counts().sort_index())

print("\nExam cycles:")
print(transactions["exam_cycle_id"].value_counts())

print("\nDays to first exam:")
print(transactions["days_to_first_exam"].describe())
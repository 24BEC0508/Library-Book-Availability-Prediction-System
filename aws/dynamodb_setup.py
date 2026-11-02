"""
DynamoDB Setup and Catalog State Loader.

Uploads current catalog inventory state (from books.csv) to DynamoDB.
Strictly avoids uploading the full historical transaction CSV (per architectural requirements).
Can run in dry-run mode without active AWS credentials.
"""

import argparse
import os
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BOOKS_PATH = ROOT / "data" / "raw" / "books.csv"


def load_catalog_to_dynamodb(table_name: str, dry_run: bool = True, limit: int = 100):
    print("=" * 60)
    print("DYNAMODB CATALOG LOADER")
    print("=" * 60)
    print(f"Source catalog: {BOOKS_PATH}")
    print(f"Target table:   {table_name}")
    print(f"Dry run mode:   {dry_run}")

    if not BOOKS_PATH.exists():
        raise FileNotFoundError(f"Books file not found: {BOOKS_PATH}")

    df = pd.read_csv(BOOKS_PATH)
    print(f"Catalog contains {len(df):,} book records.")

    if dry_run:
        print("\n[DRY RUN] Simulating item batch formatting for DynamoDB:")
        sample_items = []
        for _, row in df.head(3).iterrows():
            item = {
                "book_id": str(row["book_id"]),
                "title": str(row["title"]),
                "author": str(row["author"]),
                "edition": int(row["edition"]),
                "department": str(row["department"]),
                "total_copies": int(row["total_copies"]),
                "available_copies": int(row["available_copies"]),
                "checked_out_copies": int(row["total_copies"] - row["available_copies"]),
            }
            sample_items.append(item)
            print(f" - {item['book_id']}: {item['title']} ({item['department']}) | "
                  f"Avail: {item['available_copies']}/{item['total_copies']}")
        print(f"\n[DRY RUN] Would write up to {min(limit, len(df))} items without incurring AWS costs.")
        print("Dry run completed successfully.")
        return

    # Real upload requiring boto3 and AWS credentials
    try:
        import boto3
    except ImportError:
        print("Error: boto3 is not installed. Install boto3 to perform live DynamoDB operations.")
        return

    dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    table = dynamodb.Table(table_name)

    print(f"\nUploading up to {limit} items to DynamoDB table '{table_name}'...")
    with table.batch_writer() as batch:
        for idx, row in df.head(limit).iterrows():
            batch.put_item(Item={
                "book_id": str(row["book_id"]),
                "title": str(row["title"]),
                "author": str(row["author"]),
                "edition": int(row["edition"]),
                "department": str(row["department"]),
                "total_copies": int(row["total_copies"]),
                "available_copies": int(row["available_copies"]),
            })
    print(f"Successfully uploaded {limit} records to {table_name}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload catalog items to DynamoDB.")
    parser.add_argument("--table", type=str, default="LibraryCatalog-dev", help="DynamoDB table name")
    parser.add_argument("--live", action="store_true", help="Perform real upload instead of dry-run")
    parser.add_argument("--limit", type=int, default=500, help="Maximum records to upload")
    args = parser.parse_args()

    load_catalog_to_dynamodb(table_name=args.table, dry_run=not args.live, limit=args.limit)

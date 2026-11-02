"""
Build Training Dataset for Library Book Availability Prediction.

This module processes raw book catalog and transaction data, constructs
rolling historical features (with strict t-1 cutoff), associates upcoming
exam metadata via forward merge against the verified academic calendar,
and calculates the future 7-day checkout target (t+1 through t+7, excluding t).

Incomplete target windows near the dataset cutoff (2026-09-05) are dropped.
The output is written sequentially in chronological date order using
chunked streaming to ensure minimal memory usage.
"""

from pathlib import Path
import time
import argparse
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BOOKS_PATH = ROOT / "data" / "raw" / "books.csv"
TRANSACTIONS_PATH = ROOT / "data" / "raw" / "transactions.csv"
OUTPUT_PATH = ROOT / "data" / "processed" / "training_data.csv"
COMPACT_OUTPUT_PATH = ROOT / "data" / "processed" / "training_data_compact.csv"


def validate_source_data(books: pd.DataFrame, tx: pd.DataFrame) -> None:
    """Validate source catalog and transaction datasets."""
    required_book_cols = {"book_id", "department", "total_copies", "available_copies"}
    required_tx_cols = {
        "transaction_id",
        "book_id",
        "transaction_date",
        "transaction_type",
        "exam_cycle_id",
        "first_exam_date",
    }

    missing_books = required_book_cols - set(books.columns)
    missing_tx = required_tx_cols - set(tx.columns)

    if missing_books:
        raise ValueError(f"Missing columns in books.csv: {sorted(missing_books)}")
    if missing_tx:
        raise ValueError(f"Missing columns in transactions.csv: {sorted(missing_tx)}")

    if books["book_id"].duplicated().any():
        raise ValueError("Duplicate book_id found in books.csv")
    if tx["transaction_id"].duplicated().any():
        raise ValueError("Duplicate transaction_id found in transactions.csv")

    unknown_books = set(tx["book_id"]) - set(books["book_id"])
    if unknown_books:
        raise ValueError(f"Transactions reference unknown books: {len(unknown_books)}")

    if tx["transaction_date"].isna().any() or tx["first_exam_date"].isna().any():
        raise ValueError("Transactions contain missing transaction or exam dates.")

    unexpected_types = set(tx["transaction_type"].unique()) - {"CHECKOUT", "RETURN"}
    if unexpected_types:
        raise ValueError(f"Unexpected transaction types: {sorted(unexpected_types)}")


def build_dataset(compact: bool = False) -> None:
    t_start = time.time()
    print("=" * 60)
    print("BUILDING TRAINING DATASET")
    print("=" * 60)

    # 1. Load source data
    print("Loading books.csv and transactions.csv...", flush=True)
    books = pd.read_csv(BOOKS_PATH)
    tx = pd.read_csv(
        TRANSACTIONS_PATH,
        parse_dates=["transaction_date", "first_exam_date"],
    )

    tx["transaction_type"] = tx["transaction_type"].astype(str).str.strip().str.upper()
    validate_source_data(books, tx)

    tx["date"] = tx["transaction_date"].dt.normalize()
    tx["first_exam_date"] = tx["first_exam_date"].dt.normalize()

    # Active books (books appearing in transactions)
    active_book_ids = np.sort(tx["book_id"].unique())
    active_books = (
        books[books["book_id"].isin(active_book_ids)]
        .sort_values("book_id")
        .reset_index(drop=True)
    )

    N = len(active_books)
    book_to_idx = {b: i for i, b in enumerate(active_books["book_id"])}

    # Date range
    min_date = tx["date"].min()
    max_date = tx["date"].max()
    all_dates = pd.date_range(min_date, max_date, freq="D")
    T = len(all_dates)
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    print(f"Active books in catalog: {N:,}")
    print(f"Timeline: {min_date.date()} to {max_date.date()} ({T} days)")

    # 2. Build 2D transaction count matrices (shape: N x T)
    print("Constructing transaction occurrence matrices...", flush=True)
    c_mat = np.zeros((N, T), dtype=np.int16)
    r_mat = np.zeros((N, T), dtype=np.int16)

    for row in tx.itertuples(index=False):
        b_idx = book_to_idx[row.book_id]
        d_idx = date_to_idx[row.date]
        if row.transaction_type == "CHECKOUT":
            c_mat[b_idx, d_idx] += 1
        elif row.transaction_type == "RETURN":
            r_mat[b_idx, d_idx] += 1

    # Cumulative matrices for O(1) rolling sums (padded with 0 at index 0)
    c_cum = np.pad(np.cumsum(c_mat, axis=1), ((0, 0), (1, 0)))
    r_cum = np.pad(np.cumsum(r_mat, axis=1), ((0, 0), (1, 0)))

    # 3. Verified Exam Calendar
    print("Validating exam calendar...", flush=True)
    exam_dates = (
        tx[["exam_cycle_id", "first_exam_date"]]
        .dropna()
        .drop_duplicates()
        .sort_values("first_exam_date")
        .reset_index(drop=True)
    )

    duplicate_exams = exam_dates[exam_dates["first_exam_date"].duplicated(keep=False)]
    if not duplicate_exams.empty:
        raise ValueError("Conflicting exam cycles for identical first_exam_date found.")

    # 4. Determine valid chronological date range
    # Features need 7 days of prior history: earliest prediction date is day 7
    # Target needs 7 days of future window: latest prediction date is day T - 8
    # (since day T-8 + 7 = day T-1, which is max_date)
    start_t_idx = 7
    end_t_idx = T - 7  # exclusive index: t in range [7, T-7)
    valid_dates = all_dates[start_t_idx:end_t_idx]

    print(
        f"Valid prediction window: {valid_dates[0].date()} to {valid_dates[-1].date()} "
        f"({len(valid_dates)} days per book)",
        flush=True,
    )

    # Pre-map exam info for every valid date
    date_df = pd.DataFrame({"date": valid_dates})
    date_merged = pd.merge_asof(
        date_df,
        exam_dates,
        left_on="date",
        right_on="first_exam_date",
        direction="forward",
        allow_exact_matches=True,
    )
    date_merged["days_to_first_exam"] = (
        (date_merged["first_exam_date"] - date_merged["date"]).dt.days
    ).astype(np.int32)
    date_merged["exam_type"] = (
        date_merged["exam_cycle_id"].str.extract(r"_(CAT1|CAT2|FAT)$", expand=False)
    )

    if date_merged["exam_cycle_id"].isna().any():
        raise ValueError("Some dates could not be mapped to an upcoming exam cycle.")

    # 5. Output file setup
    target_out_path = COMPACT_OUTPUT_PATH if compact else OUTPUT_PATH
    target_out_path.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "book_id",
        "date",
        "department",
        "total_copies",
        "checkouts_last_1d",
        "checkouts_last_3d",
        "checkouts_last_7d",
        "returns_last_7d",
        "demand_growth",
        "exam_cycle_id",
        "first_exam_date",
        "days_to_first_exam",
        "exam_type",
        "target_next_7d_checkouts",
    ]

    print(f"Writing to {target_out_path}...", flush=True)

    # Process in chronological day blocks to minimize memory and keep natural date sorting
    BLOCK_DAYS = 50
    total_valid_days = len(valid_dates)
    written_rows = 0
    rng = np.random.default_rng(42)

    # Remove existing output file if present
    if target_out_path.exists():
        target_out_path.unlink()

    for block_start in range(0, total_valid_days, BLOCK_DAYS):
        block_end = min(block_start + BLOCK_DAYS, total_valid_days)
        block_len = block_end - block_start

        # For these dates:
        t_indices = np.arange(start_t_idx + block_start, start_t_idx + block_end)
        dates_in_block = valid_dates[block_start:block_end]
        meta_in_block = date_merged.iloc[block_start:block_end]

        # Matrices for this block (N books x block_len days)
        # 1-day prior checkouts: c_mat at day t-1
        b_c_1d = c_mat[:, t_indices - 1]

        # 3-day prior checkouts: sum of days t-3, t-2, t-1
        b_c_3d = (
            c_cum[:, t_indices] - c_cum[:, t_indices - 3]
        )

        # 7-day prior checkouts: sum of days t-7 to t-1
        b_c_7d = (
            c_cum[:, t_indices] - c_cum[:, t_indices - 7]
        )

        # 7-day prior returns: sum of days t-7 to t-1
        b_r_7d = (
            r_cum[:, t_indices] - r_cum[:, t_indices - 7]
        )

        # Demand growth: 3d avg - 7d avg
        b_growth = (b_c_3d / 3.0) - (b_c_7d / 7.0)

        # 7-day future checkouts (target): sum of days t+1 through t+7
        # Note: c_cum is 1-padded, so c_cum[:, t+8] - c_cum[:, t+1] sums indices [t+1, ..., t+7]
        b_target = (
            c_cum[:, t_indices + 8] - c_cum[:, t_indices + 1]
        )

        # Flatten in (day, book) order so rows are chronologically grouped by date
        # Each day has N books
        book_ids_rep = np.tile(active_books["book_id"].to_numpy(), block_len)
        dept_rep = np.tile(active_books["department"].to_numpy(), block_len)
        copies_rep = np.tile(active_books["total_copies"].to_numpy(), block_len)

        dates_rep = np.repeat(dates_in_block.strftime("%Y-%m-%d"), N)
        exam_cycle_rep = np.repeat(meta_in_block["exam_cycle_id"].to_numpy(), N)
        first_exam_rep = np.repeat(meta_in_block["first_exam_date"].dt.strftime("%Y-%m-%d").to_numpy(), N)
        days_exam_rep = np.repeat(meta_in_block["days_to_first_exam"].to_numpy(), N)
        exam_type_rep = np.repeat(meta_in_block["exam_type"].to_numpy(), N)

        # Flatten matrices (transpose to shape (block_len, N) then ravel)
        c_1d_flat = b_c_1d.T.ravel().astype(np.float32)
        c_3d_flat = b_c_3d.T.ravel().astype(np.float32)
        c_7d_flat = b_c_7d.T.ravel().astype(np.float32)
        r_7d_flat = b_r_7d.T.ravel().astype(np.float32)
        growth_flat = b_growth.T.ravel().astype(np.float32)
        target_flat = b_target.T.ravel().astype(np.int32)

        block_df = pd.DataFrame({
            "book_id": book_ids_rep,
            "date": dates_rep,
            "department": dept_rep,
            "total_copies": copies_rep,
            "checkouts_last_1d": c_1d_flat,
            "checkouts_last_3d": c_3d_flat,
            "checkouts_last_7d": c_7d_flat,
            "returns_last_7d": r_7d_flat,
            "demand_growth": growth_flat,
            "exam_cycle_id": exam_cycle_rep,
            "first_exam_date": first_exam_rep,
            "days_to_first_exam": days_exam_rep,
            "exam_type": exam_type_rep,
            "target_next_7d_checkouts": target_flat,
        })

        if compact:
            # Compact sampling: keep all active rows + 5% stratified random sample of inactive rows
            is_active = (
                (block_df["target_next_7d_checkouts"] > 0)
                | (block_df["checkouts_last_7d"] > 0)
                | (block_df["returns_last_7d"] > 0)
            )
            sample_inactive = (~is_active) & (rng.random(len(block_df)) < 0.05)
            block_df = block_df[is_active | sample_inactive].copy()

        block_df.to_csv(
            target_out_path,
            mode="a",
            header=(written_rows == 0),
            index=False,
        )
        written_rows += len(block_df)

        progress_pct = (block_end / total_valid_days) * 100
        print(
            f"Progress: {block_end}/{total_valid_days} days ({progress_pct:.1f}%) | "
            f"Written rows: {written_rows:,} | Elapsed: {time.time()-t_start:.1f}s",
            flush=True,
        )

    print("=" * 60)
    print("TRAINING DATASET GENERATION COMPLETE")
    print(f"Output File: {target_out_path}")
    print(f"Total Rows: {written_rows:,}")
    print(f"Date Range: {valid_dates[0].date()} to {valid_dates[-1].date()}")
    print(f"Elapsed Time: {time.time()-t_start:.2f} seconds")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build library prediction training dataset.")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Build compact stratified sample dataset instead of full dataset.",
    )
    args = parser.parse_args()
    build_dataset(compact=args.compact)

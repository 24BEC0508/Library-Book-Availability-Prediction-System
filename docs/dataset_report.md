# Dataset Validation & Pipeline Engineering Report

## 1. Provenance & Synthetic Nature
- **Academic Prototype**: The catalog (`books.csv`) and historical transaction logs (`transactions.csv`) are synthetic academic datasets generated for prototyping availability warning systems.
- **Data Integrity**: These records are **not** real-world university library checkouts.

---

## 2. Raw Source Data Audit

### A. Catalog (`data/raw/books.csv`)
- **Total Records**: 24,000 books.
- **Departments**: 16 departments (CSE, IT, ECE, EEE, Mechanical, Civil, Chemical, Biotechnology, Biomedical, Aerospace, Production, Automotive, Mathematics, Physics, Chemistry, Common Engineering).
- **Physical Copies**: Each record has 25 to 30 total copies.
- **Current Availability**: Range from 0 to 30 copies. 8 books currently have $\le 3$ copies available.
- **Data Quality**: 0 duplicate book IDs, 0 null values.

### B. Transactions (`data/raw/transactions.csv`)
- **Total Records**: 42,793 events.
  - **Checkouts**: 33,634 (78.6%)
  - **Returns**: 9,159 (21.4%)
- **Active Books**: 9,451 unique books were checked out at least once. 14,549 books (60.6% of the catalog) had zero checkouts across 3.7 years.
- **Date Range**: 2023-01-01 09:59:00 to 2026-09-05 20:43:00.
- **Loan Event Consistency**: Audited with `ml/validate_loans.py`: **0 returns without a preceding checkout**.
- **Average Library Traffic**:
  - Checkouts per day across all 24,000 books: **25.1**
  - Returns per day across all 24,000 books: **6.8**

---

## 3. Exam Calendar & Demand Encoding

The academic calendar contains 23 discrete exam cycles:
- **Winter Semesters**: CAT-1 (Jan 26), CAT-2 (Mar 15), FAT (Apr 25)
- **Fall Semesters**: CAT-1 (Aug 08), CAT-2 (Sep 25), FAT (Nov 20)

### Empirical Exam Rush Verification:
- **CAT-2 Dominance**: CAT-2 accounts for **18,306 checkouts (54.4%)**, while CAT-1 accounts for 7,127 (21.2%) and FAT accounts for 8,201 (24.4%).
- **Timing Proximity**: **56.3% of all checkouts (18,942)** occur within 14 days prior to an exam. All returns occur strictly after the exam date ($days\_to\_exam \le -2$).
- **Correlation**: `days_to_first_exam` exhibits a negative correlation ($-0.100$) with future checkout demand; demand surges as the exam approaches.

---

## 4. Rebuilding the Feature & Target Pipeline

### Root Cause of Previous Data Truncation:
The previous `training_data.csv` was truncated at `2026-01-10` because `build_training_data.py` attempted to concatenate 9,451 individual book DataFrames simultaneously into memory, triggering out-of-memory crashes on Windows.

### Pipeline Repair (`ml/build_training_data.py`):
1. **Vectorized 2D Matrix Representation**: Pre-aggregates daily checkout and return occurrences into dense $9,451 \times 1,344$ NumPy arrays ($\approx 50$ MB RAM), enabling $O(1)$ rolling operations via cumulative sums.
2. **Strict Chronological Streaming**: Writes data in 50-day blocks chronologically to disk. Peak memory consumption is under 150 MB.
3. **Feature Window Rigor**: Features at date $t$ strictly use transactions up to $t-1$.
4. **Target Calculation Rigor**: Target at date $t$ strictly sums checkouts from $t+1$ through $t+7$ (excluding date $t$).
5. **Cutoff Boundary Handling**:
   - Earliest valid prediction date: `2023-01-08` (requires 7 prior days).
   - Latest valid prediction date: `2026-08-29` (requires 7 future days before dataset cutoff on `2026-09-05`).
   - Dates after `2026-08-29` are dropped to prevent incomplete target windows.
6. **Total Output Rows**: **12,569,830 rows** spanning `2023-01-08` to `2026-08-29` with 14 complete columns.

---

## 5. Target Distribution & Sparsity
- **Total Valid Target Rows**: 12,569,830
- **0 Checkouts**: 12,343,807 (**98.20%**)
- **1 Checkout**: 219,234 (1.74%)
- **2 Checkouts**: 6,396 (0.05%)
- **3 Checkouts**: 364 (0.00%)
- **4 Checkouts**: 28 (0.00%)
- **5 Checkouts**: 1 (0.00%)
- **Target Mean**: **0.01856 checkouts/week**
- **Sparsity Consequence**: 96.0% of book-days have zero past checkouts, zero past returns, and zero future checkouts.

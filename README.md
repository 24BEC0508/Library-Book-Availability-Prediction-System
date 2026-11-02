# Real-Time ML Library Book Availability Prediction & Stockout Warning

An end-to-end machine learning and cloud system designed to forecast 7-day student book demand, project physical inventory burn rates, and provide early shortage warnings driven by an **authoritative, administrator-managed university academic calendar**.

> **Borrowing Policy Notice**: The library borrowing policy strictly remains **First-Come, First-Served (FCFS)**. Predictive availability forecasts assist student study planning; they do not alter borrowing rights or create reservation priority.

---

## 1. Quickstart (Run Locally in Seconds)

No virtual environment setup, external web frameworks, or scikit-learn installations required. The system runs out-of-the-box using standard Python 3.12+ with NumPy and pandas.

### A. Run the End-to-End Walkthrough Demo:
```bash
python demo.py
```

### B. Launch Local Student Web Portal & HTTP API:
```bash
python app/api.py
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser.

### C. Run the Comprehensive Test Suite (26 Tests):
```bash
python -m unittest tests/test_data_pipeline.py tests/test_model.py tests/test_stockout.py tests/test_api.py tests/test_academic_calendar.py
```

---

## 2. Authoritative Academic Calendar & Automatic Countdown

- **No Student-Controlled Days-to-Exam**: The manual student-facing countdown input has been **completely removed**. The backend automatically queries the official academic calendar localized to `Asia/Kolkata`.
- **Automatic Exam Status Detection**:
  - `UPCOMING`: Calculates exact calendar days until start date (`(start_date - today).days`).
  - `IN_PROGRESS`: If `start_date <= today <= end_date`, represented as `Exam in progress` (0 days remaining).
  - `NO_EXAM_SCHEDULED`: If no scheduled exams exist, returns `No upcoming exam scheduled` and forecasts standard non-exam baseline demand without artificial exam rush inflation.
- **Admin-Managed Calendar Portal**:
  - Secure endpoints (`X-Admin-Key` header) allow university administrators to create, edit date ranges, correct schedules, and cancel/deactivate exams.
  - Changes immediately update ML inference countdowns across all student requests **without retraining the model**.

---

## 3. Key Engineering Achievements & Repaired Pipeline

1. **Repaired Data Truncation**:
   - The original `training_data.csv` was previously truncated at `2026-01-10` due to memory exhaustion during 12.5M row DataFrame concatenation.
   - Re-engineered [`ml/build_training_data.py`](file:///c:/DDV-Coding/Projects/AWS-Project/ml/build_training_data.py) using dense 2D matrix representations ($\approx 50$ MB RAM) and chronological block streaming, successfully generating the untruncated **12,569,830 rows** spanning `2023-01-08` through `2026-08-29`.
2. **Strict Time-Window Verification**:
   - Features at date $t$ strictly use historical transactions through $t-1$.
   - Target strictly sums future checkouts on days $t+1$ through $t+7$ (excluding $t$).
   - Incomplete target windows after `2026-08-29` are dropped.
3. **Exam Calendar Alignment**:
   - Formally validated against 23 academic exam cycles.
   - Identified that **CAT-2 drives 54.4% of all borrowing traffic**, with 56.3% of checkouts occurring within 14 days of an exam.
4. **Multiple Linear Regression from Scratch**:
   - Implemented ordinary least squares via NumPy `lstsq` with numerical stability (no explicit matrix inversion).
   - Saved 27 feature coefficients, category mappings, and training cutoff metadata in [`model/regression_model.npz`](file:///c:/DDV-Coding/Projects/AWS-Project/model/regression_model.npz).
5. **Rigorous Chronological Holdout**:
   - Trained on dates before `2026-01-01` (10,292,139 rows).
   - Evaluated on held-out year 2026 (2,277,691 rows).

---

## 4. Fair Baseline Comparison (Held-Out Test Period)

All models and baselines evaluated on identical **2,277,691** held-out rows (date $\ge$ 2026-01-01):

| Model / Baseline | MAE | RMSE | $R^2$ |
| :--- | :---: | :---: | :---: |
| **1. Always Predict Zero** | **0.013130** | 0.117035 | -0.012747 |
| **2. Historical Average Baseline (0.0198)** | 0.032378 | 0.116485 | -0.003246 |
| **3. Multiple Linear Regression (Raw OLS)** | 0.035112 | 0.116339 | -0.000737 |
| **4. Linear Regression (Clipped $\ge 0$)** | 0.033468 | **0.116241** | **+0.000942** |

### Critical ML Diagnostic Findings:
- **Zero-Inflation Reality**: 98.71% of test rows have 0 checkouts. The zero baseline wins overall MAE (0.01313) simply by predicting 0 everywhere, but is completely useless as an early warning system.
- **Superiority on Active Borrowing**: On rows where students actually borrow books (29,283 rows), Multiple Linear Regression achieves **MAE = 0.9897**, substantially outperforming the Zero Baseline (MAE = 1.0213).
- **OLS Limitation**: Standard linear regression predicts flat baseline decimals ($\approx 0.024$) due to mean shrinkage. A two-stage **Hurdle / Zero-Inflated Poisson model** is the justified next step.

---

## 5. Cloud Architecture & Deployment (DigitalOcean & AWS)

### DigitalOcean Deployment (Target Cloud Service):
See the comprehensive guide in [`digitalocean/README.md`](file:///digitalocean/README.md).

- **DigitalOcean App Platform (Zero-Ops PaaS)**:
  - Automated HTTPS, Git-ops CI/CD on `git push`, health checks, zero server maintenance.
  - Spec: [`.do/app.yaml`](file:///.do/app.yaml)
  - Quick Deploy:
    ```powershell
    # Windows Dry Run (No DO charges)
    .\digitalocean\deploy.ps1 -DryRun

    # Live Deploy via App Platform
    .\digitalocean\deploy.ps1 -Method AppPlatform
    ```
- **DigitalOcean Droplet (Docker Compose + Nginx)**:
  - Low predictable cost ($4 - $6/month) with persistent SSD storage for library transactions and calendar entries.
  - One-click setup: [`digitalocean/setup_droplet.sh`](file:///digitalocean/setup_droplet.sh)
  - Quick Deploy:
    ```powershell
    .\digitalocean\deploy.ps1 -Method Droplet -DropletIp <YOUR_DROPLET_IP>
    ```

### Serverless Architecture (AWS Fallback):
`Web Client -> API Gateway (HTTP API v2) -> AWS Lambda -> DynamoDB & S3`
- **Deployment**:
  ```powershell
  # Windows Dry Run (No AWS charges)
  .\aws\deploy.ps1 -DryRun

  # Live Deployment
  .\aws\deploy.ps1 -Environment dev -Region ap-south-1
  ```

---

## 6. Repository Structure

```
AWS-Project/
├── app/
│   ├── api.py                    # Local HTTP REST server & admin endpoints
│   └── static/index.html         # Student UI (read-only countdown) & admin portal modal
├── aws/
│   ├── cloudformation_template.yaml # IaC: DynamoDB (Catalog + Calendar), S3, IAM, Lambda, API Gateway
│   ├── deploy.ps1                # PowerShell deployment automation
│   ├── deploy.sh                 # Bash deployment automation
│   ├── dynamodb_setup.py         # DynamoDB catalog state loader
│   ├── lambda_function.py        # Cloud Lambda handler with calendar routes
│   └── sample_requests.json      # Sample API Gateway payloads
├── data/
│   ├── raw/
│   │   ├── books.csv             # 24,000 catalog books
│   │   ├── transactions.csv      # 42,793 historical loan events
│   │   └── department_summary.csv # Department copy distributions
│   ├── processed/
│   │   └── training_data.csv     # 12,569,830 rows (untruncated, verified)
│   └── academic_calendar.json    # Local authoritative university exam calendar
├── docs/
│   ├── architecture.md           # Cloud & local architectural breakdown
│   ├── aws_deployment.md         # Detailed AWS deployment instructions
│   ├── dataset_report.md         # Data pipeline audit & sparsity metrics
│   └── model_evaluation.md       # Full evaluation metrics & diagnostic analysis
├── ml/
│   ├── academic_calendar.py      # Authoritative academic calendar CRUD & countdown engine
│   ├── build_training_data.py    # Memory-safe chunked dataset builder
│   ├── check_baseline.py         # Baseline metric diagnostics
│   ├── check_exam_demand.py      # Exam surge distribution script
│   ├── check_feature_signal.py   # Multi-chunk feature correlation script
│   ├── check_target.py           # Target distribution audit
│   ├── compare_models.py         # Fair chronological baseline evaluator
│   ├── inspect_data.py           # Catalog & transaction schema inspector
│   ├── inspect_predictions.py    # Prediction diagnostic distributions
│   ├── stockout_estimator.py     # Inference engine with automatic calendar countdown
│   ├── train_regression.py       # NumPy lstsq OLS training from scratch
│   ├── validate_data.py          # Data integrity checks
│   └── validate_loans.py         # Loan consistency auditor (returns vs checkouts)
├── model/
│   └── regression_model.npz      # Trained NumPy model artifact & category metadata
├── tests/
│   ├── test_academic_calendar.py # Unit tests for calendar CRUD & countdown criteria
│   ├── test_api.py               # Unit tests for Lambda & HTTP endpoints
│   ├── test_data_pipeline.py     # Unit tests for rolling window math
│   ├── test_model.py             # Unit tests for model serialization & OLS
│   └── test_stockout.py          # Unit tests for stockout bounds & FCFS
├── demo.py                       # Self-contained walkthrough demo script
├── requirements.txt              # Core package manifest (NumPy, pandas)
└── README.md                     # Master documentation
```

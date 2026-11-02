# System Architecture: Library Book Availability Prediction

## 1. Overview & Objective
This system provides real-time demand forecasting and stockout early warning for university library books. It informs students when books may face shortages leading into examination cycles, while strictly preserving the university's **First-Come, First-Served (FCFS)** borrowing policy.

The architecture is split into two operational modes:
1. **Serverless Cloud Architecture (AWS)**: Elastic, low-cost academic prototype deployed via CloudFormation.
2. **Local Fallback Mode**: Standalone Python HTTP API and web interface runnable without AWS credentials or cloud billing.

---

## 2. Target Cloud Architecture (AWS)

```
[Student Web / Mobile Client]
              |
              v (HTTPS / REST)
       [API Gateway (HTTP API v2)]
              |
              v (Proxy Integration)
        [AWS Lambda Function]
       (Python 3.12 / Inference Engine)
         /                   \
        v                     v
 [Amazon DynamoDB]       [Amazon S3]
(Catalog & Inventory)   (Model Artifacts & Archives)
```

### Component Responsibilities:
- **API Gateway (HTTP API v2)**: Exposes public REST endpoints with low latency and cost-effective Pay-Per-Request pricing ($1.00 per million requests). Configured with permissive CORS for student web portals.
- **AWS Lambda**:
  - Serves `/health`, `/books`, and `/predict` routes.
  - Caches model weights in memory across warm invocations.
  - Prepares 27-dimensional feature vectors with verified ordering and category encodings.
  - Combines statistical ML predictions with deterministic inventory state.
- **Amazon DynamoDB (`LibraryCatalog-dev`)**:
  - Stores operational catalog records (book ID, title, author, edition, department, total copies, available copies).
  - Uses `PAY_PER_REQUEST` billing to remain completely within AWS Free Tier.
  - **Data Engineering Decision**: Avoids uploading raw 42k historical transaction logs to DynamoDB, preserving DynamoDB purely for live inventory states.
- **Amazon S3 (`library-prediction-artifacts-*`)**:
  - Stores versioned model weights (`model/v1/regression_model.npz`).
  - Archives historical transaction CSVs for batch retraining.
- **Amazon CloudWatch**:
  - Collects structured invocation logs, latency metrics, and error traces.

---

## 3. Local Fallback Architecture
For academic evaluation and development without cloud credentials, `app/api.py` replicates the API Gateway and Lambda routing using Python's standard library `http.server`:
- Runs at `http://127.0.0.1:8000`.
- Serves the student frontend (`app/static/index.html`).
- Executes local inference directly via `ml.stockout_estimator.StockoutEstimator`.
- Zero third-party web framework dependencies (no Flask, no FastAPI, no uvicorn required).

---

## 4. Borrowing Policy & Ethical Safeguards
- **FCFS Enforcement**: Predictive stockout warnings indicate probability of availability exhaustion; they do **not** enable priority queueing, reservation locking, or preferential checkout.
- **Transparent Uncertainty**: The engine explicitly separates:
  - **Deterministic Inventory**: Current physical copies in the library.
  - **Statistical Prediction**: ML projected demand over 7 days.
  - **Risk Classification**: Transparent rule-based tiers (CRITICAL, HIGH, MODERATE, LOW) grounded in physical burn rates.

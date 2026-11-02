# AWS Serverless Cloud Deployment Guide

## 1. Overview
This guide provides step-by-step instructions for deploying the **Library Availability Prediction & Stockout Early Warning Service** to AWS using Infrastructure as Code (CloudFormation).

### Architecture Diagram:
```
[Client / Web Browser]
         |
         v (HTTPS)
 [API Gateway HTTP API v2]
         |
         v (Proxy)
    [AWS Lambda]
   /            \
  v              v
[DynamoDB]     [Amazon S3]
(Catalog)     (Model Weights)
```

---

## 2. Security & Cost Optimization Principles
1. **Zero Hardcoded Secrets**: All configuration passes via Lambda environment variables and IAM execution roles.
2. **IAM Least Privilege**:
   - `dynamodb:GetItem`, `Query`, `Scan` limited strictly to `LibraryCatalog-${Environment}`.
   - `s3:GetObject` limited strictly to `library-prediction-artifacts-${AccountId}-${Environment}`.
   - `logs:CreateLogStream`, `PutLogEvents` scoped to the Lambda log group.
3. **AWS Free Tier Friendly**:
   - DynamoDB configured with `PAY_PER_REQUEST` on-demand capacity (zero idle costs).
   - API Gateway HTTP API v2 ($1.00 per million calls; 1M free requests/month).
   - Lambda 512 MB memory allocation (within free-tier compute quota).

---

## 3. Deployment Steps

### Step 1: Configure AWS CLI
Ensure your AWS credentials have permissions to provision CloudFormation, IAM Roles, Lambda, DynamoDB, and S3:
```bash
aws configure
# Verify active identity:
aws sts get-caller-identity
```

### Step 2: Automated Deployment

#### On Windows (PowerShell):
```powershell
# Dry run verification (validates template without provisioning billable resources):
.\aws\deploy.ps1 -DryRun

# Full cloud deployment:
.\aws\deploy.ps1 -Environment dev -Region us-east-1
```

#### On Linux / macOS (Bash):
```bash
chmod +x aws/deploy.sh
./aws/deploy.sh dev us-east-1
```

### Step 3: Seed Catalog State to DynamoDB
Per requirements, we upload **only** the active catalog inventory to DynamoDB, avoiding historical transaction logs:
```bash
python aws/dynamodb_setup.py --table LibraryCatalog-dev --limit 500 --live
```

---

## 4. Testing Live Cloud API Endpoints

Replace `<API_ENDPOINT>` with the URL output by CloudFormation:

### A. Health Check:
```bash
curl -X GET "<API_ENDPOINT>/health"
```
*Expected 200 Response:*
```json
{
  "status": "HEALTHY",
  "service": "Library Availability Prediction API",
  "model": {
    "methodology": "Multiple Linear Regression (NumPy OLS)",
    "training_cutoff": "2026-01-01",
    "historical_baseline_mean": 0.019756,
    "feature_count": 27
  }
}
```

### B. Catalog Search:
```bash
curl -X GET "<API_ENDPOINT>/books?q=python&limit=3"
```

### C. Predict Availability & Shortage Warning:
```bash
curl -X POST "<API_ENDPOINT>/predict" \
  -H "Content-Type: application/json" \
  -d '{
    "book_id": "B08510",
    "available_copies": 1,
    "days_to_first_exam": 7,
    "exam_type": "CAT2",
    "checkouts_last_7d": 2.0
  }'
```

---

## 5. Local Fallback Mode (No AWS Credentials Needed)
If AWS credentials or cloud permissions are unavailable:
```bash
# Start local API & student web UI:
python app/api.py

# Open web portal:
http://127.0.0.1:8000
```
Local mode runs the complete inference engine and catalog search locally.

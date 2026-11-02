#!/usr/bin/env bash
# Bash AWS Deployment Script for Library Availability Prediction
# NOTE: DO NOT RUN THIS SCRIPT UNTIL READY FOR LIVE AWS PROVISIONING.

set -euo pipefail

ENVIRONMENT="${1:-dev}"
REGION="${2:-ap-south-1}"
STACK_NAME="LibraryAvailability-${ENVIRONMENT}"
S3_BUCKET="${3:-library-management-ml-pred}"

echo "============================================================"
echo "AWS SERVERLESS DEPLOYMENT: LIBRARY AVAILABILITY PREDICTION"
echo "============================================================"
echo "Environment: ${ENVIRONMENT}"
echo "Region:      ${REGION}"
echo "Stack:       ${STACK_NAME}"
echo "S3 Bucket:   ${S3_BUCKET}"

# 1. Check AWS CLI
if ! command -v aws &> /dev/null; then
    echo "[ERROR] AWS CLI is not installed."
    exit 1
fi

# 2. Check Credentials
echo "1. Checking AWS credentials..."
aws sts get-caller-identity > /dev/null || {
    echo "[ERROR] AWS credentials invalid or expired."
    exit 1
}
echo "[OK] Authenticated."

# 3. Deploy CloudFormation Stack
echo "2. Deploying CloudFormation stack..."
aws cloudformation deploy \
    --template-file aws/cloudformation_template.yaml \
    --stack-name "${STACK_NAME}" \
    --parameter-overrides Environment="${ENVIRONMENT}" ExistingS3BucketName="${S3_BUCKET}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "${REGION}"

# 4. Extract outputs
OUTPUTS=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" --query "Stacks[0].Outputs" --output json)
API_ENDPOINT=$(echo "${OUTPUTS}" | grep -A 2 '"OutputKey": "ApiEndpoint"' | grep '"OutputValue"' | cut -d '"' -f 4)
S3_BUCKET_RESOLVED=$(echo "${OUTPUTS}" | grep -A 2 '"OutputKey": "S3BucketName"' | grep '"OutputValue"' | cut -d '"' -f 4)
LAMBDA_NAME=$(echo "${OUTPUTS}" | grep -A 2 '"OutputKey": "LambdaFunctionName"' | grep '"OutputValue"' | cut -d '"' -f 4)
TABLE_NAME=$(echo "${OUTPUTS}" | grep -A 2 '"OutputKey": "DynamoDBCatalogTableName"' | grep '"OutputValue"' | cut -d '"' -f 4)

echo " - API Endpoint: ${API_ENDPOINT}"
echo " - S3 Bucket:    ${S3_BUCKET_RESOLVED}"
echo " - Lambda:       ${LAMBDA_NAME}"
echo " - DynamoDB:     ${TABLE_NAME}"

# 5. Upload Model to S3
echo "3. Uploading model artifact to S3..."
aws s3 cp model/regression_model.npz "s3://${S3_BUCKET_RESOLVED}/model/v1/regression_model.npz" --region "${REGION}"
aws s3 cp model/regression_model.npz "s3://${S3_BUCKET_RESOLVED}/model/regression_model.npz" --region "${REGION}"

# 6. Package and update Lambda function with directory hierarchy
echo "4. Packaging and updating Lambda function (preserving module structure)..."
STAGING_DIR="/tmp/lambda_staging_${ENVIRONMENT}"
rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}/ml" "${STAGING_DIR}/model" "${STAGING_DIR}/data/raw"

cp aws/lambda_function.py "${STAGING_DIR}/lambda_function.py"
cp ml/*.py "${STAGING_DIR}/ml/"
cp model/regression_model.npz "${STAGING_DIR}/model/regression_model.npz"
cp data/raw/books.csv "${STAGING_DIR}/data/raw/books.csv"
if [ -f "data/academic_calendar.json" ]; then
    cp data/academic_calendar.json "${STAGING_DIR}/data/academic_calendar.json"
fi

ZIP_FILE="/tmp/lambda_deploy_${ENVIRONMENT}.zip"
rm -f "${ZIP_FILE}"
(cd "${STAGING_DIR}" && zip -r "${ZIP_FILE}" .)
rm -rf "${STAGING_DIR}"

aws lambda update-function-code --function-name "${LAMBDA_NAME}" --zip-file "fileb://${ZIP_FILE}" --region "${REGION}"

echo "============================================================"
echo "DEPLOYMENT COMPLETE!"
echo "API Endpoint: ${API_ENDPOINT}"
echo "============================================================"

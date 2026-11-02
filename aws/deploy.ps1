# PowerShell AWS Deployment Script for Library Availability Prediction
# NOTE: DO NOT RUN THIS SCRIPT UNTIL READY FOR LIVE AWS PROVISIONING.

param (
    [string]$Environment = "dev",
    [string]$Region = "ap-south-1",
    [string]$StackName = "LibraryAvailability-dev",
    [string]$S3Bucket = "library-management-ml-pred",
    [string]$S3ModelKey = "model/regression_model.npz",
    [string]$PandasLayerArn = "",
    [switch]$DryRun
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "AWS SERVERLESS DEPLOYMENT: LIBRARY AVAILABILITY PREDICTION" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Target Environment: $Environment"
Write-Host "AWS Region:         $Region"
Write-Host "CloudFormation:     $StackName"
Write-Host "S3 Bucket:          $S3Bucket"
Write-Host "Model Key:          $S3ModelKey"
if ($PandasLayerArn) {
    Write-Host "Pandas Layer ARN:   $PandasLayerArn"
}
Write-Host "DryRun Mode:        $DryRun"

# 1. Check AWS CLI
$awsCmd = Get-Command aws -ErrorAction SilentlyContinue
if (-not $awsCmd) {
    if (Test-Path "C:\Program Files\Amazon\AWSCLIV2\aws.exe") {
        $env:PATH = "C:\Program Files\Amazon\AWSCLIV2;$env:PATH"
        $awsCmd = Get-Command aws -ErrorAction SilentlyContinue
    }
}
if (-not $awsCmd) {
    Write-Host "[ERROR] AWS CLI is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Please install AWS CLI: https://aws.amazon.com/cli/"
    exit 1
}

# 2. Check AWS Credentials
Write-Host "`n1. Verifying AWS Credentials..." -ForegroundColor Yellow
$callerId = aws sts get-caller-identity 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] AWS credentials not configured or session expired." -ForegroundColor Red
    Write-Host "Run 'aws configure' or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY."
    Write-Host "Local testing and demo inference remain available without AWS credentials."
    exit 1
}
Write-Host "[OK] Authenticated: $callerId" -ForegroundColor Green

# 3. Check Existing Stack State (Handling ROLLBACK_COMPLETE)
Write-Host "`n2. Inspecting Existing Stack State ($StackName)..." -ForegroundColor Yellow
$stackStatusJson = aws cloudformation describe-stacks --stack-name $StackName --region $Region 2>&1
$stackNeedsCleanup = $false

if ($LASTEXITCODE -eq 0) {
    try {
        $stackInfo = $stackStatusJson | ConvertFrom-Json
        $currentStatus = $stackInfo.Stacks[0].StackStatus
        Write-Host "Found existing stack status: $currentStatus"
        if ($currentStatus -eq "ROLLBACK_COMPLETE") {
            $stackNeedsCleanup = $true
            Write-Host "[NOTICE] Stack '$StackName' is in ROLLBACK_COMPLETE (failed initial creation)." -ForegroundColor Yellow
            Write-Host "CloudFormation requires deleting a ROLLBACK_COMPLETE stack before it can be created again." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Could not parse stack description JSON. Proceeding..." -ForegroundColor Gray
    }
} else {
    Write-Host "No existing stack found. A new stack will be created." -ForegroundColor Green
}

if ($DryRun) {
    Write-Host "`n[DRY RUN] Validating CloudFormation template..." -ForegroundColor Yellow
    aws cloudformation validate-template --template-body file://aws/cloudformation_template.yaml
    if ($stackNeedsCleanup) {
        Write-Host "[DRY RUN] Note: In live deployment, the dead ROLLBACK_COMPLETE stack will be deleted before creation." -ForegroundColor Yellow
    }
    Write-Host "[DRY RUN] Complete. No billable cloud resources were created or modified." -ForegroundColor Green
    exit 0
}

# 4. Clean up Dead Stack if in ROLLBACK_COMPLETE
if ($stackNeedsCleanup) {
    Write-Host "`nDeleting dead ROLLBACK_COMPLETE stack shell ($StackName)..." -ForegroundColor Yellow
    aws cloudformation delete-stack --stack-name $StackName --region $Region
    Write-Host "Waiting for stack deletion to complete..." -ForegroundColor Yellow
    aws cloudformation wait stack-delete-complete --stack-name $StackName --region $Region
    Write-Host "[OK] Dead stack deleted successfully. Ready for clean deployment." -ForegroundColor Green
}

# 5. Deploy CloudFormation Stack
Write-Host "`n3. Deploying CloudFormation Stack ($StackName)..." -ForegroundColor Yellow
$deployParams = @(
    "Environment=$Environment",
    "ExistingS3BucketName=$S3Bucket",
    "S3ModelKey=$S3ModelKey"
)
if ($PandasLayerArn -and $PandasLayerArn.Trim() -ne "") {
    $deployParams += "PandasLayerArn=$PandasLayerArn"
}

aws cloudformation deploy `
    --template-file aws/cloudformation_template.yaml `
    --stack-name $StackName `
    --parameter-overrides @deployParams `
    --capabilities CAPABILITY_NAMED_IAM `
    --region $Region

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] CloudFormation deployment failed." -ForegroundColor Red
    exit 1
}

# 4. Extract Stack Outputs
Write-Host "`n3. Extracting Stack Outputs..." -ForegroundColor Yellow
$outputs = aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs" | ConvertFrom-Json
$apiEndpoint = ($outputs | Where-Object { $_.OutputKey -eq "ApiEndpoint" }).OutputValue
$s3BucketResolved = ($outputs | Where-Object { $_.OutputKey -eq "S3BucketName" }).OutputValue
$lambdaName = ($outputs | Where-Object { $_.OutputKey -eq "LambdaFunctionName" }).OutputValue
$tableName = ($outputs | Where-Object { $_.OutputKey -eq "DynamoDBCatalogTableName" }).OutputValue

Write-Host " - API Endpoint: $apiEndpoint"
Write-Host " - S3 Bucket:    $s3BucketResolved"
Write-Host " - Lambda:       $lambdaName"
Write-Host " - DynamoDB:     $tableName"

# 5. Upload Model Artifact to S3 (both versioned and fallback root paths)
Write-Host "`n4. Uploading Versioned Model Artifact to S3..." -ForegroundColor Yellow
aws s3 cp model/regression_model.npz "s3://$s3BucketResolved/model/v1/regression_model.npz" --region $Region
aws s3 cp model/regression_model.npz "s3://$s3BucketResolved/model/regression_model.npz" --region $Region

# 6. Package and Update Lambda Function Code
Write-Host "`n5. Packaging Lambda Code Package (preserving module hierarchy)..." -ForegroundColor Yellow
$stagingDir = "$env:TEMP\lambda_staging_$Environment"
if (Test-Path $stagingDir) { Remove-Item -Recurse -Force $stagingDir }
New-Item -ItemType Directory -Path "$stagingDir\ml" -Force | Out-Null
New-Item -ItemType Directory -Path "$stagingDir\model" -Force | Out-Null
New-Item -ItemType Directory -Path "$stagingDir\data\raw" -Force | Out-Null

Copy-Item "aws\lambda_function.py" "$stagingDir\lambda_function.py"
Copy-Item "ml\*.py" "$stagingDir\ml\"
Copy-Item "model\regression_model.npz" "$stagingDir\model\regression_model.npz"
Copy-Item "data\raw\books.csv" "$stagingDir\data\raw\books.csv"
if (Test-Path "data\academic_calendar.json") {
    Copy-Item "data\academic_calendar.json" "$stagingDir\data\academic_calendar.json"
}

$zipPath = "$env:TEMP\lambda_deploy_$Environment.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "$stagingDir\*" -DestinationPath $zipPath
Remove-Item -Recurse -Force $stagingDir

Write-Host "Updating Lambda Function code with packaged artifacts..."
aws lambda update-function-code --function-name $lambdaName --zip-file "fileb://$zipPath" --region $Region

# 7. Seed DynamoDB Catalog
Write-Host "`n6. Seeding DynamoDB Catalog from books.csv..." -ForegroundColor Yellow
python aws/dynamodb_setup.py --table $tableName --limit 500 --live

# 8. Test API Endpoint
Write-Host "`n7. Testing Live Health Check Endpoint..." -ForegroundColor Yellow
$healthUrl = "$apiEndpoint/health"
Invoke-RestMethod -Uri $healthUrl -Method Get | ConvertTo-Json

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "DEPLOYMENT SUCCEEDED!" -ForegroundColor Green
Write-Host "Public API Gateway: $apiEndpoint" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green

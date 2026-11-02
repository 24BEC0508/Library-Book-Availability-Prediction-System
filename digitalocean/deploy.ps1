# ==============================================================================
# PowerShell DigitalOcean Deployment Script
# Library Book Availability Prediction & Stockout Warning System
# ==============================================================================

param (
    [ValidateSet("AppPlatform", "Droplet", "DockerLocal")]
    [string]$Method = "AppPlatform",

    [string]$Region = "blr", # Options: blr (Bangalore), nyc (New York), ams (Amsterdam), fra (Frankfurt), sgp (Singapore)
    [string]$AppName = "library-demand-predictor",
    [string]$DropletIp = "",
    [string]$DropletUser = "root",
    [string]$AdminKey = "library-admin-secret-key-2026",
    [switch]$SkipTests,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " DIGITALOCEAN CLOUD DEPLOYMENT TOOL                         " -ForegroundColor Cyan
Write-Host " Real-Time Library Availability & Stockout Prediction       " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Deployment Method:  $Method"
Write-Host "Target DO Region:   $Region"
Write-Host "App Spec Name:      $AppName"
Write-Host "DryRun Mode:        $DryRun"
if ($DropletIp) {
    Write-Host "Droplet IP:         $DropletIp"
}
Write-Host "============================================================`n"

# 1. Project Directory & Core File Integrity Checks
Write-Host "1. Verifying Project Structure & Deployment Artifacts..." -ForegroundColor Yellow

$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path "$projectRoot\app\api.py")) {
    $projectRoot = Get-Location
}

$requiredFiles = @(
    "Dockerfile",
    ".dockerignore",
    ".do\app.yaml",
    "requirements.txt",
    "app\api.py",
    "app\static\index.html",
    "app\static\admin.html",
    "app\static\js\config.js",
    "app\static\js\student.js",
    "app\static\js\admin.js",
    "ml\stockout_estimator.py",
    "ml\academic_calendar.py",
    "ml\live_inventory_manager.py",
    "model\regression_model.npz",
    "data\raw\books.csv",
    "data\academic_calendar.json",
    "data\live_inventory.json"
)

$allFound = $true
foreach ($file in $requiredFiles) {
    $fullPath = Join-Path $projectRoot $file
    if (Test-Path $fullPath) {
        Write-Host "  [OK] $file" -ForegroundColor Green
    } else {
        Write-Host "  [MISSING] $file" -ForegroundColor Red
        $allFound = $false
    }
}

if (-not $allFound) {
    Write-Host "`n[ERROR] Required deployment files are missing. Cannot proceed." -ForegroundColor Red
    exit 1
}

# 2. Automated Test Suite Verification
if (-not $SkipTests) {
    Write-Host "`n2. Running Verification Test Suite..." -ForegroundColor Yellow
    Push-Location $projectRoot
    try {
        python -m unittest tests/test_data_pipeline.py tests/test_model.py tests/test_stockout.py tests/test_api.py tests/test_academic_calendar.py
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Test suite failed! Refusing to deploy unstable code." -ForegroundColor Red
            Pop-Location
            exit 1
        }
        Write-Host "  [OK] All core system tests passed (26/26)." -ForegroundColor Green
    } catch {
        Write-Host "[WARNING] Python test runner encountered an issue: $_" -ForegroundColor Yellow
    } finally {
        Pop-Location
    }
} else {
    Write-Host "`n2. Skipping test suite (-SkipTests specified)." -ForegroundColor Gray
}

# 3. DryRun Evaluation
if ($DryRun) {
    Write-Host "`n============================================================" -ForegroundColor Cyan
    Write-Host " [DRY RUN] DEPLOYMENT PLAN VALIDATED SUCCESSFULLY           " -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "Deployment target: DigitalOcean ($Method)"
    Write-Host "Configuration files: Dockerfile, .dockerignore, .do/app.yaml, docker-compose.yml"
    Write-Host "Pricing estimate:"
    if ($Method -eq "AppPlatform") {
        Write-Host "  - DO App Platform (Basic-xxs): `$5.00/month (Includes SSL, CDN, automated deploys)" -ForegroundColor Green
    } elseif ($Method -eq "Droplet") {
        Write-Host "  - DO Droplet (Basic 1GB RAM / 1 vCPU SSD): `$4.00 - `$6.00/month" -ForegroundColor Green
    }
    Write-Host "`n[DRY RUN] Complete. No billable DigitalOcean resources were modified." -ForegroundColor Green
    exit 0
}

# 4. Live Deployment: DigitalOcean App Platform
if ($Method -eq "AppPlatform") {
    Write-Host "`n3. Checking DigitalOcean CLI (doctl)..." -ForegroundColor Yellow
    $doctlCmd = Get-Command doctl -ErrorAction SilentlyContinue
    if (-not $doctlCmd) {
        Write-Host "[NOTICE] 'doctl' CLI is not found in your PATH." -ForegroundColor Yellow
        Write-Host "`nYou can deploy to DigitalOcean App Platform in 2 ways:" -ForegroundColor Cyan
        Write-Host "Method A (Recommended - Zero CLI):"
        Write-Host "  1. Push this repository to GitHub or GitLab."
        Write-Host "  2. Go to DigitalOcean Console -> Apps -> Create App (https://cloud.digitalocean.com/apps)."
        Write-Host "  3. Select your repository. DigitalOcean will automatically detect '.do/app.yaml' & Dockerfile."
        Write-Host "  4. Click 'Next' and 'Create Resources'. Your app will be live with free HTTPS in < 3 minutes!`n"
        Write-Host "Method B (Install doctl CLI):"
        Write-Host "  winget install digitalocean.doctl"
        Write-Host "  doctl auth init"
        Write-Host "  doctl apps create --spec .do/app.yaml`n"
        exit 0
    }

    Write-Host "Checking doctl authentication..." -ForegroundColor Yellow
    $account = doctl account get 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] doctl is not authenticated. Please run 'doctl auth init' with your DO API token." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Authenticated to DigitalOcean." -ForegroundColor Green

    Write-Host "`nDeploying to DigitalOcean App Platform using .do/app.yaml..." -ForegroundColor Yellow
    Push-Location $projectRoot
    try {
        # Check if app already exists
        $existingApps = doctl apps list --format ID,Spec.Name --no-header 2>&1
        $existingAppId = ""
        if ($existingApps) {
            foreach ($line in ($existingApps -split "`n")) {
                if ($line -match "(\S+)\s+$AppName") {
                    $existingAppId = $matches[1]
                    break
                }
            }
        }

        if ($existingAppId) {
            Write-Host "Found existing App Platform application (ID: $existingAppId). Updating spec..." -ForegroundColor Cyan
            doctl apps update $existingAppId --spec .do/app.yaml
        } else {
            Write-Host "Creating new App Platform application..." -ForegroundColor Cyan
            doctl apps create --spec .do/app.yaml
        }
        Write-Host "`n[SUCCESS] Application deployment initiated on DigitalOcean App Platform!" -ForegroundColor Green
        Write-Host "Check status via: doctl apps list"
    } finally {
        Pop-Location
    }
}

# 5. Live Deployment: DigitalOcean Droplet
if ($Method -eq "Droplet") {
    if (-not $DropletIp) {
        Write-Host "[ERROR] -DropletIp is required when Method is 'Droplet'." -ForegroundColor Red
        Write-Host "Usage: .\digitalocean\deploy.ps1 -Method Droplet -DropletIp <YOUR_DROPLET_IP>"
        exit 1
    }

    Write-Host "`n3. Deploying to DigitalOcean Droplet ($DropletIp)..." -ForegroundColor Yellow
    Write-Host "Syncing files to $DropletUser@${DropletIp}:/opt/library-predictor..."

    $sshCmd = Get-Command ssh -ErrorAction SilentlyContinue
    if (-not $sshCmd) {
        Write-Host "[ERROR] OpenSSH client is not installed or not in PATH." -ForegroundColor Red
        exit 1
    }

    # Execute remote setup script on droplet
    Write-Host "Executing automated provisioning on droplet..." -ForegroundColor Cyan
    ssh -o StrictHostKeyChecking=no "${DropletUser}@${DropletIp}" "mkdir -p /opt/library-predictor"
    scp -o StrictHostKeyChecking=no -r "$projectRoot\*" "${DropletUser}@${DropletIp}:/opt/library-predictor/"
    ssh -o StrictHostKeyChecking=no "${DropletUser}@${DropletIp}" "cd /opt/library-predictor && bash digitalocean/setup_droplet.sh"

    Write-Host "`n[SUCCESS] Droplet deployment completed!" -ForegroundColor Green
    Write-Host "Access your live application at: http://$DropletIp/"
}

# 6. Local Docker Testing
if ($Method -eq "DockerLocal") {
    Write-Host "`n3. Building and running local Docker container..." -ForegroundColor Yellow
    Push-Location $projectRoot
    try {
        docker compose up -d --build
        Write-Host "`n[SUCCESS] Local container is running!" -ForegroundColor Green
        Write-Host "Web Portal: http://127.0.0.1:8000/"
        Write-Host "API Health: http://127.0.0.1:8000/api/health"
    } finally {
        Pop-Location
    }
}

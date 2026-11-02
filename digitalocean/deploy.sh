#!/usr/bin/env bash
# ==============================================================================
# Bash DigitalOcean Deployment Script
# Library Book Availability Prediction & Stockout Warning System
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

METHOD="AppPlatform"
REGION="blr"
APP_NAME="library-demand-predictor"
DROPLET_IP=""
DROPLET_USER="root"
ADMIN_KEY="library-admin-secret-key-2026"
DRY_RUN=false
SKIP_TESTS=false

print_usage() {
    echo "Usage: ./digitalocean/deploy.sh [OPTIONS]"
    echo "Options:"
    echo "  --method <AppPlatform|Droplet|DockerLocal>  Deployment method (default: AppPlatform)"
    echo "  --region <region>                           DigitalOcean region (default: blr)"
    echo "  --app-name <name>                           App name (default: library-demand-predictor)"
    echo "  --droplet-ip <ip>                           Target Droplet IP (required for Droplet method)"
    echo "  --droplet-user <user>                       SSH user (default: root)"
    echo "  --admin-key <key>                           Admin authentication key"
    echo "  --dry-run                                   Validate files and configuration without creating resources"
    echo "  --skip-tests                                Skip running unit tests prior to deploy"
    echo "  --help                                      Show this help message"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --method)
            METHOD="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --app-name)
            APP_NAME="$2"
            shift 2
            ;;
        --droplet-ip)
            DROPLET_IP="$2"
            shift 2
            ;;
        --droplet-user)
            DROPLET_USER="$2"
            shift 2
            ;;
        --admin-key)
            ADMIN_KEY="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        --help)
            print_usage
            exit 0
            ;;
        *)
            echo -e "${RED}[ERROR] Unknown argument: $1${NC}"
            print_usage
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo -e "${CYAN}================================================================${NC}"
echo -e "${CYAN} DIGITALOCEAN CLOUD DEPLOYMENT TOOL                             ${NC}"
echo -e "${CYAN} Real-Time Library Availability & Stockout Prediction           ${NC}"
echo -e "${CYAN}================================================================${NC}"
echo -e "Deployment Method:  ${YELLOW}$METHOD${NC}"
echo -e "Target DO Region:   ${YELLOW}$REGION${NC}"
echo -e "App Spec Name:      ${YELLOW}$APP_NAME${NC}"
echo -e "DryRun Mode:        ${YELLOW}$DRY_RUN${NC}"
if [ -n "$DROPLET_IP" ]; then
    echo -e "Droplet IP:         ${YELLOW}$DROPLET_IP${NC}"
fi
echo -e "${CYAN}================================================================${NC}\n"

# 1. Project Directory & Core File Integrity Checks
echo -e "${YELLOW}1. Verifying Project Structure & Deployment Artifacts...${NC}"

REQUIRED_FILES=(
    "Dockerfile"
    ".dockerignore"
    ".do/app.yaml"
    "requirements.txt"
    "app/api.py"
    "app/static/index.html"
    "app/static/admin.html"
    "app/static/js/config.js"
    "app/static/js/student.js"
    "app/static/js/admin.js"
    "ml/stockout_estimator.py"
    "ml/academic_calendar.py"
    "ml/live_inventory_manager.py"
    "model/regression_model.npz"
    "data/raw/books.csv"
    "data/academic_calendar.json"
    "data/live_inventory.json"
)

ALL_FOUND=true
for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$PROJECT_ROOT/$file" ]; then
        echo -e "  ${GREEN}[OK]${NC} $file"
    else
        echo -e "  ${RED}[MISSING]${NC} $file"
        ALL_FOUND=false
    fi
done

if [ "$ALL_FOUND" = false ]; then
    echo -e "\n${RED}[ERROR] Required deployment files are missing. Cannot proceed.${NC}"
    exit 1
fi

# 2. Automated Test Suite Verification
if [ "$SKIP_TESTS" = false ]; then
    echo -e "\n${YELLOW}2. Running Verification Test Suite...${NC}"
    (cd "$PROJECT_ROOT" && python3 -m unittest tests/test_data_pipeline.py tests/test_model.py tests/test_stockout.py tests/test_api.py tests/test_academic_calendar.py)
    echo -e "  ${GREEN}[OK] All core system tests passed (26/26).${NC}"
else
    echo -e "\n${YELLOW}2. Skipping test suite (--skip-tests specified).${NC}"
fi

# 3. DryRun Evaluation
if [ "$DRY_RUN" = true ]; then
    echo -e "\n${CYAN}================================================================${NC}"
    echo -e "${CYAN} [DRY RUN] DEPLOYMENT PLAN VALIDATED SUCCESSFULLY               ${NC}"
    echo -e "${CYAN}================================================================${NC}"
    echo -e "Deployment target: DigitalOcean ($METHOD)"
    echo -e "Configuration files: Dockerfile, .dockerignore, .do/app.yaml, docker-compose.yml"
    echo -e "Pricing estimate:"
    if [ "$METHOD" = "AppPlatform" ]; then
        echo -e "  - DO App Platform (Basic-xxs): $5.00/month (Includes SSL, CDN, automated deploys)"
    elif [ "$METHOD" = "Droplet" ]; then
        echo -e "  - DO Droplet (Basic 1GB RAM / 1 vCPU SSD): $4.00 - $6.00/month"
    fi
    echo -e "\n${GREEN}[DRY RUN] Complete. No billable DigitalOcean resources were modified.${NC}"
    exit 0
fi

# 4. Live Deployment: DigitalOcean App Platform
if [ "$METHOD" = "AppPlatform" ]; then
    echo -e "\n${YELLOW}3. Checking DigitalOcean CLI (doctl)...${NC}"
    if ! command -v doctl &> /dev/null; then
        echo -e "${YELLOW}[NOTICE] 'doctl' CLI is not installed.${NC}"
        echo -e "\nYou can deploy to DigitalOcean App Platform in 2 ways:"
        echo -e "${CYAN}Method A (Zero CLI - Recommended):${NC}"
        echo -e "  1. Push this repository to GitHub or GitLab."
        echo -e "  2. Go to https://cloud.digitalocean.com/apps and click 'Create App'."
        echo -e "  3. Select your repo. DigitalOcean automatically detects '.do/app.yaml' and 'Dockerfile'."
        echo -e "  4. Click Next -> Deploy! Your app will be live with free HTTPS in < 3 minutes!\n"
        echo -e "${CYAN}Method B (Install doctl CLI):${NC}"
        echo -e "  brew install doctl # macOS or snap install doctl (Linux)"
        echo -e "  doctl auth init"
        echo -e "  doctl apps create --spec .do/app.yaml\n"
        exit 0
    fi

    echo -e "Checking doctl authentication..."
    doctl account get > /dev/null 2>&1 || {
        echo -e "${RED}[ERROR] doctl is not authenticated. Run 'doctl auth init'.${NC}"
        exit 1
    }
    echo -e "${GREEN}[OK] Authenticated to DigitalOcean.${NC}"

    cd "$PROJECT_ROOT"
    APP_ID=$(doctl apps list --format ID,Spec.Name --no-header | awk -v app="$APP_NAME" '$2 == app {print $1}' || true)
    if [ -n "$APP_ID" ]; then
        echo -e "${CYAN}Updating existing App Platform application ($APP_ID)...${NC}"
        doctl apps update "$APP_ID" --spec .do/app.yaml
    else
        echo -e "${CYAN}Creating new App Platform application...${NC}"
        doctl apps create --spec .do/app.yaml
    fi
    echo -e "\n${GREEN}[SUCCESS] Deployment initiated on DigitalOcean App Platform!${NC}"
    echo -e "Inspect status via: doctl apps list"
fi

# 5. Live Deployment: DigitalOcean Droplet
if [ "$METHOD" = "Droplet" ]; then
    if [ -z "$DROPLET_IP" ]; then
        echo -e "${RED}[ERROR] --droplet-ip is required when --method is 'Droplet'.${NC}"
        exit 1
    fi

    echo -e "\n${YELLOW}3. Deploying to DigitalOcean Droplet ($DROPLET_IP)...${NC}"
    ssh -o StrictHostKeyChecking=no "${DROPLET_USER}@${DROPLET_IP}" "mkdir -p /opt/library-predictor"
    rsync -avz --exclude-from="$PROJECT_ROOT/.dockerignore" "$PROJECT_ROOT/" "${DROPLET_USER}@${DROPLET_IP}:/opt/library-predictor/"
    ssh -o StrictHostKeyChecking=no "${DROPLET_USER}@${DROPLET_IP}" "cd /opt/library-predictor && bash digitalocean/setup_droplet.sh"

    echo -e "\n${GREEN}[SUCCESS] Droplet deployment completed!${NC}"
    echo -e "Access your application at: http://${DROPLET_IP}/"
fi

# 6. Local Docker Testing
if [ "$METHOD" = "DockerLocal" ]; then
    echo -e "\n${YELLOW}3. Building and running local Docker container...${NC}"
    (cd "$PROJECT_ROOT" && docker compose up -d --build)
    echo -e "\n${GREEN}[SUCCESS] Local container is running!${NC}"
    echo -e "Web Portal: http://127.0.0.1:8000/"
    echo -e "API Health: http://127.0.0.1:8000/api/health"
fi

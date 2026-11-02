#!/usr/bin/env bash
# ==============================================================================
# DigitalOcean Droplet Automated Setup & Deployment Script
# Target OS: Ubuntu 22.04 LTS / 24.04 LTS
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}================================================================${NC}"
echo -e "${CYAN} DIGITALOCEAN DROPLET AUTOMATED PROVISIONING & DEPLOYMENT       ${NC}"
echo -e "${CYAN} Library Book Availability & Stockout Prediction Engine         ${NC}"
echo -e "${CYAN}================================================================${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] Please run this script as root (sudo bash setup_droplet.sh)${NC}"
    exit 1
fi

APP_DIR="/opt/library-predictor"
REPO_URL="${REPO_URL:-}"
ADMIN_KEY="${ADMIN_KEY:-library-admin-secret-key-2026}"
DOMAIN_NAME="${DOMAIN_NAME:-}"

echo -e "\n${YELLOW}Step 1: Updating System Packages...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update && apt-get upgrade -y
apt-get install -y curl git ufw fail2ban ca-certificates gnupg lsb-release rsync

echo -e "\n${YELLOW}Step 2: Configuring UFW Firewall...${NC}"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw allow 8000/tcp comment 'Direct Web API'
echo "y" | ufw enable || true
ufw status verbose

echo -e "\n${YELLOW}Step 3: Installing Docker Engine & Docker Compose Plugin...${NC}"
if ! command -v docker &> /dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}[OK] Docker installed successfully.${NC}"
else
    echo -e "${GREEN}[OK] Docker is already installed.${NC}"
fi

echo -e "\n${YELLOW}Step 4: Installing Nginx & Certbot for Reverse Proxy & SSL...${NC}"
apt-get install -y nginx certbot python3-certbot-nginx
systemctl enable nginx

echo -e "\n${YELLOW}Step 5: Setting Up Application Directory ($APP_DIR)...${NC}"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

if [ -n "$REPO_URL" ]; then
    echo "Cloning repository from $REPO_URL..."
    if [ -d "$APP_DIR/.git" ]; then
        git pull origin main || true
    else
        git clone "$REPO_URL" .
    fi
fi

# Ensure data directory exists with appropriate permissions
mkdir -p "$APP_DIR/data"
chmod -R 777 "$APP_DIR/data"

echo -e "\n${YELLOW}Step 6: Building and Launching Docker Containers...${NC}"
if [ -f "$APP_DIR/docker-compose.yml" ]; then
    docker compose down || true
    docker compose build --pull
    docker compose up -d
else
    echo -e "${YELLOW}[NOTICE] docker-compose.yml not found in $APP_DIR. Copy project files here and run 'docker compose up -d'.${NC}"
fi

echo -e "\n${YELLOW}Step 7: Configuring Nginx Reverse Proxy...${NC}"
if [ -f "$APP_DIR/digitalocean/nginx.conf" ]; then
    cp "$APP_DIR/digitalocean/nginx.conf" /etc/nginx/sites-available/library-predictor
    ln -sf /etc/nginx/sites-available/library-predictor /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl reload nginx
    echo -e "${GREEN}[OK] Nginx reverse proxy configured and active.${NC}"
fi

# Optional SSL automation if domain was provided
if [ -n "$DOMAIN_NAME" ]; then
    echo -e "\n${YELLOW}Step 8: Generating Let's Encrypt SSL Certificate for $DOMAIN_NAME...${NC}"
    certbot --nginx -d "$DOMAIN_NAME" --non-interactive --agree-tos --register-unsafely-without-email || {
        echo -e "${RED}[WARNING] Certbot generation failed. Please verify that DNS for $DOMAIN_NAME points to this Droplet IP.${NC}"
    }
fi

SERVER_IP=$(curl -s https://ifconfig.me || curl -s https://api.ipify.org || echo "YOUR_DROPLET_IP")

echo -e "\n${GREEN}================================================================${NC}"
echo -e "${GREEN} DEPLOYMENT COMPLETED SUCCESSFULLY ON DIGITALOCEAN!             ${NC}"
echo -e "${GREEN}================================================================${NC}"
echo -e "Web Application:    ${CYAN}http://${SERVER_IP}:${NC} (via Nginx Port 80)"
echo -e "Direct Container:   ${CYAN}http://${SERVER_IP}:8000/${NC}"
echo -e "Admin Console:      ${CYAN}http://${SERVER_IP}/admin${NC}"
echo -e "Health Check:       ${CYAN}http://${SERVER_IP}/api/health${NC}"
echo -e "Admin Key:          ${YELLOW}${ADMIN_KEY}${NC}"
echo -e "================================================================"
echo -e "\nTo inspect live logs:"
echo -e "  cd $APP_DIR && docker compose logs -f"
echo -e "\nTo restart the service:"
echo -e "  cd $APP_DIR && docker compose restart"

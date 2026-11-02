# DigitalOcean Cloud Deployment Guide

An end-to-end guide for deploying the **Real-Time ML Library Book Availability Prediction & Stockout Warning System** to **DigitalOcean**.

This guide covers:
1. **Option A (Recommended)**: DigitalOcean App Platform (Managed PaaS, Git-ops, automatic SSL, zero maintenance)
2. **Option B**: DigitalOcean Droplet (Ubuntu 22.04/24.04 VM with Docker Compose & Nginx reverse proxy)
3. **Option C**: DigitalOcean Spaces (S3-compatible object storage for data & model artifact backups)
4. **Automated CLI Deployment**: Using `deploy.ps1` (Windows) and `deploy.sh` (Linux/macOS)

---

## Architecture Overview

```mermaid
graph TD
    Client["Browser (Student / Admin Portal)"] -->|HTTPS (Port 443)| DO["DigitalOcean Cloud"]
    subgraph DO["DigitalOcean Infrastructure"]
        subgraph OptionA["Option A: App Platform (PaaS)"]
            LB["DO Cloud Load Balancer & SSL"] --> C1["Docker Container (Python 3.12)"]
            C1 --> TH["ThreadingHTTPServer (:8000)"]
            C1 --> ML["Inference Engine (NumPy lstsq)"]
            C1 --> JSON_STORE["Live Operational Store (JSON)"]
        end

        subgraph OptionB["Option B: Droplet (IaaS)"]
            NGINX["Nginx Reverse Proxy (:80 / :443)"] --> C2["Docker Compose Web (:8000)"]
            C2 --> VOL["SSD Persistent Volume (/opt/library-predictor/data)"]
        end

        subgraph OptionC["Option C: Spaces (Object Storage)"]
            SPACES["DO Spaces Bucket (S3-Compatible)"]
        end
    end
```

---

## Quick Comparison: Which DigitalOcean Option Should You Choose?

| Feature | Option A: App Platform | Option B: Droplet (Docker Compose) |
| :--- | :--- | :--- |
| **Type** | Fully Managed PaaS | Virtual Private Server (IaaS) |
| **Pricing** | Starting at \$5.00 / month | Starting at \$4.00 - \$6.00 / month |
| **Setup Time** | < 3 minutes | ~5 minutes (automated via script) |
| **SSL / HTTPS** | Automatic, zero-configuration | Automated via Certbot / Let's Encrypt |
| **CI / CD** | Auto-deploy on `git push` | SSH or Git pull |
| **Best For** | Hands-off production, zero server management | Direct filesystem control, persistent local SSD volume |

---

## Option A: Deploy to DigitalOcean App Platform (Zero Server Management)

DigitalOcean App Platform is a platform-as-a-service that builds directly from your repository and provides automatic HTTPS, DDoS protection, health checking, and zero-downtime deployments.

The project is already pre-configured with [`.do/app.yaml`](file:///.do/app.yaml) and an optimized [`Dockerfile`](file:///Dockerfile).

### Method 1: Deploy via DigitalOcean Cloud Web Console (No CLI needed)

1. Push your repository to **GitHub** or **GitLab**.
2. Log into the [DigitalOcean Cloud Console](https://cloud.digitalocean.com/).
3. In the left navigation, click **Apps** -> **Create App**.
4. Choose **GitHub** (or GitLab) as the source and select this repository.
5. DigitalOcean will automatically detect `.do/app.yaml` and `Dockerfile`.
6. Review the settings:
   - **Name**: `library-demand-predictor`
   - **Region**: `Bangalore (blr)` (ideal for `Asia/Kolkata` university academic calendar) or `New York (nyc)`.
   - **Plan**: `Basic` (starts at \$5/month for 512MB RAM, 1 vCPU).
   - **HTTP Port**: `8000`
   - **Health Check**: `/api/health`
7. Click **Next** -> **Create Resources**.
8. In 2-3 minutes, your application is live at a URL like `https://library-demand-predictor-xxxx.ondigitalocean.app`!

### Method 2: Deploy via DigitalOcean CLI (`doctl`)

If you have `doctl` installed:

```bash
# 1. Authenticate with your DigitalOcean Personal Access Token
doctl auth init

# 2. Deploy using the included app specification
doctl apps create --spec .do/app.yaml

# 3. View deployment status and live URL
doctl apps list
```

On Windows PowerShell:
```powershell
.\digitalocean\deploy.ps1 -Method AppPlatform
```

---

## Option B: Deploy to a DigitalOcean Droplet (Docker Compose + Nginx)

If you prefer total control with persistent SSD storage, deploy on a standard Ubuntu Droplet.

### Step 1: Create an Ubuntu Droplet
1. Go to **DigitalOcean Console** -> **Droplets** -> **Create Droplet**.
2. **Distribution**: Ubuntu 22.04 LTS or 24.04 LTS.
3. **Plan**: Basic (`$4.00` or `$6.00`/mo, Regular SSD, 1GB RAM, 1 vCPU).
4. **Datacenter Region**: Bangalore (`BLR1`) or your closest location.
5. **Authentication**: SSH Key (recommended) or Password.
6. Click **Create Droplet** and note your Droplet IP address (e.g., `159.65.x.x`).

### Step 2: Automated One-Click Provisioning

Run the included automated deployment script from your local machine:

#### From Windows (PowerShell):
```powershell
# Validate everything first with DryRun (zero cloud charges)
.\digitalocean\deploy.ps1 -DryRun

# Deploy live to your Droplet IP
.\digitalocean\deploy.ps1 -Method Droplet -DropletIp <YOUR_DROPLET_IP>
```

#### From Linux / macOS (Bash):
```bash
# Validate everything first with DryRun
./digitalocean/deploy.sh --dry-run

# Deploy live to your Droplet IP
./digitalocean/deploy.sh --method Droplet --droplet-ip <YOUR_DROPLET_IP>
```

### Step 3: What the Automated Setup Script Does
The script [`digitalocean/setup_droplet.sh`](file:///digitalocean/setup_droplet.sh) automatically:
1. Updates all system packages and security patches.
2. Configures UFW firewall (allows ports 22 SSH, 80 HTTP, 443 HTTPS, 8000 Web).
3. Installs Docker Engine & Docker Compose plugin.
4. Mounts the persistent `./data` volume on the Droplet's SSD so all live loans, returns, and academic calendar updates survive container restarts.
5. Configures Nginx reverse proxy with gzip compression and security headers.
6. Starts the containerized application.

### Step 4: Add a Custom Domain & Free SSL (Let's Encrypt)
Once your domain's DNS `A` record points to your Droplet IP:
```bash
ssh root@<YOUR_DROPLET_IP>
certbot --nginx -d library.youruniversity.edu
```
Certbot will automatically configure HTTPS on port 443 and setup auto-renewal!

---

## Option C: DigitalOcean Spaces (S3-Compatible Object Storage)

DigitalOcean Spaces provides scalable, S3-compatible cloud storage. The application includes [`digitalocean/spaces_storage.py`](file:///digitalocean/spaces_storage.py) for backing up model weights and JSON inventory files:

### Setup:
1. In DigitalOcean Console, click **Spaces Object Storage** -> **Create a Space**.
2. Note your Space Name and Region (e.g. `blr1`).
3. Under **API** -> **Spaces Keys**, generate a new Key and Secret.
4. Export environment variables:
```bash
export DO_SPACES_KEY="YOUR_SPACES_KEY"
export DO_SPACES_SECRET="YOUR_SPACES_SECRET"
export DO_SPACES_REGION="blr1"
export DO_SPACES_BUCKET="your-library-spaces"
```
5. Trigger a snapshot backup:
```bash
python digitalocean/spaces_storage.py
```

---

## Environment Variables Reference

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `PORT` | `8000` | HTTP port the server listens on |
| `HOST` | `0.0.0.0` | Bind address (`0.0.0.0` allows container/external access) |
| `CLOUD_PROVIDER` | `DigitalOcean` | Cloud provider identifier shown in system metadata |
| `DO_REGION` | `blr1` | DigitalOcean datacenter region tag |
| `APP_TIMEZONE` | `Asia/Kolkata` | Authoritative academic calendar timezone |
| `ADMIN_API_KEY` | `library-admin-secret-key-2026` | Secret authentication key for administrative endpoints |

---

## Verification & Health Check Endpoints

Once deployed, verify your DigitalOcean service using `curl` or a web browser:

### 1. System Health & Metadata
```bash
curl http://<YOUR_HOST>/api/health
```
**Expected Response:**
```json
{
  "status": "HEALTHY",
  "service": "Library Book Availability Prediction API",
  "cloud_provider": "DigitalOcean",
  "environment": {
    "provider": "DigitalOcean",
    "region": "blr1",
    "runtime": "DigitalOcean Production"
  },
  "borrowing_policy": "First Come, First Served (FCFS)",
  "total_catalog_books": 10000
}
```

### 2. Live Student Portal
Visit: `http://<YOUR_HOST>/` in any browser.

### 3. Administrator Console
Visit: `http://<YOUR_HOST>/admin` in any browser.  
Authenticate using your Admin Key (default: `library-admin-secret-key-2026`).

### 4. Machine Learning Demand Inference Test
```bash
curl "http://<YOUR_HOST>/api/predict/B08510"
```

### 5. Automated Calendar Countdown
```bash
curl http://<YOUR_HOST>/api/calendar/upcoming
```

---

## Maintenance & Log Inspection

On a Droplet:
```bash
# View live application logs
cd /opt/library-predictor && docker compose logs -f

# Restart container after config change
cd /opt/library-predictor && docker compose restart

# Stop container
cd /opt/library-predictor && docker compose down
```

On App Platform:
Inspect real-time logs via the **Runtime Logs** tab in the DigitalOcean console, or:
```bash
doctl apps logs <APP_ID> --type run --follow
```

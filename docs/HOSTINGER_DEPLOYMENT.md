# Complete Hostinger VPS Deployment Guide — LocalLead Intelligence

> **Turnkey, production-ready guide to hosting LocalLead Intelligence on a Hostinger VPS using 100% free and open-source APIs (OpenStreetMap & Disify), with seamless future upgrade path to Google Maps API.**

---

## Table of Contents

1. [Overview & Server Requirements](#1-overview--server-requirements)
2. [Step 1: Set Up Hostinger VPS & Domain DNS](#step-1-set-up-hostinger-vps--domain-dns)
3. [Step 2: Connect to VPS & Upload Project](#step-2-connect-to-vps--upload-project)
4. [Step 3: Run the 1-Command Deployment](#step-3-run-the-1-command-deployment)
5. [Step 4: Enable Free SSL (HTTPS with Let's Encrypt)](#step-4-enable-free-ssl-https-with-lets-encrypt)
6. [How the Free & Open-Source Stack Works](#how-the-free--open-source-stack-works)
7. [How to Switch to Google Maps API in the Future](#how-to-switch-to-google-maps-api-in-the-future)
8. [Useful Management & Monitoring Commands](#useful-management--monitoring-commands)

---

## 1. Overview & Server Requirements

LocalLead Intelligence runs as a containerized microservice stack:
- **Nginx Reverse Proxy**: Listens on standard ports **80** (HTTP) and **443** (HTTPS), routing frontend traffic to Next.js and `/backend/*` to FastAPI.
- **Next.js 14 Frontend**: SSR & client dashboard streaming live search progress via SSE.
- **FastAPI Application Server**: High-throughput REST API with JWT auth and rate limiting.
- **Celery Worker**: Distributed background pipeline node executing the 6-stage lead generation process.
- **Redis 7**: In-memory job queue broker & SSE event pub/sub.
- **PostgreSQL 16**: Persistent database for users, jobs, leads, and audit telemetry.

### Recommended Hostinger Plan
- **Plan**: Hostinger VPS (KVM 1 or KVM 2).
- **Operating System**: **Ubuntu 22.04 LTS** or **Ubuntu 24.04 LTS** (64-bit).
- **RAM**: 2 GB or more. *(Our deployment script automatically provisions a 2 GB swapfile to ensure smooth builds and prevent out-of-memory errors).*

---

## Step 1: Set Up Hostinger VPS & Domain DNS

### 1.1 In Hostinger Control Panel (hPanel)
1. Go to **VPS** → Select your VPS.
2. Under **OS & Panel** → Choose **Ubuntu 22.04 LTS (64bit)** (or 24.04).
3. Note your VPS **Public IP Address** (e.g., `194.164.x.x`).

### 1.2 Point Your Domain (DNS)
1. Go to **Domains** → Select your domain → **DNS / Nameservers**.
2. Add or update the **A Record**:
   - **Type**: `A`
   - **Name**: `@` (or a subdomain like `leads`)
   - **Points to**: `YOUR_HOSTINGER_VPS_IP`
   - **TTL**: `300` (or Default)
3. *(Optional)* Add a `www` record pointing to the same VPS IP.

---

## Step 2: Connect to VPS & Upload Project

Connect to your VPS from your local terminal:
```bash
ssh root@YOUR_HOSTINGER_VPS_IP
```

### Option A: Clone via Git (Recommended)
```bash
# Update package index and install git
apt-get update -y && apt-get install -y git

# Clone your repository
git clone <YOUR_GIT_REPO_URL> /srv/leadgen
cd /srv/leadgen
```

### Option B: Upload from your local machine via SCP / rsync
On your local machine:
```bash
# Run this from your project root on your laptop
rsync -avz --exclude='.git' --exclude='node_modules' --exclude='.venv' --exclude='__pycache__' ./ root@YOUR_HOSTINGER_VPS_IP:/srv/leadgen/
```
Then on the VPS:
```bash
cd /srv/leadgen
```

---

## Step 3: Run the 1-Command Deployment

We have created an automated deployment script `scripts/deploy-hostinger.sh` specifically for Hostinger VPS.

Run the script:
```bash
chmod +x scripts/deploy-hostinger.sh
./scripts/deploy-hostinger.sh
```

### What This Script Does Automatically:
1. **Configures 2 GB Swap Space**: Prevents Hostinger memory spikes during Next.js builds.
2. **Installs Docker & Docker Compose**: Configures the latest official Docker engine.
3. **Creates Storage Directories**: `backend/data/reports`, Nginx certificates, and Certbot directories.
4. **Initializes Production `.env`**:
   - Generates a cryptographically strong 64-character `SECRET_KEY`.
   - Sets `BUSINESS_PROVIDER=nominatim` (100% free open-source default).
   - Sets `ENABLE_NOMINATIM_GEOCODE=true`.
   - Configures `NEXT_PUBLIC_API_URL=""` so the frontend seamlessly calls the API relatively on your domain or IP.
5. **Builds & Launches Containers**: Starts Postgres, Redis, API, Celery Worker, Frontend, and Nginx.
6. **Runs Database Migrations**: Automatically applies Alembic migrations to PostgreSQL.
7. **Verifies Stack Health**: Runs an internal readiness check and displays the live site URL.

You can now visit your website directly in your browser:
```
http://YOUR_HOSTINGER_VPS_IP
```

---

## Step 4: Enable Free SSL (HTTPS with Let's Encrypt)

Once your domain name's DNS points to your Hostinger VPS IP, obtain a free SSL certificate with our automated setup script:

```bash
chmod +x scripts/setup-ssl.sh
sudo ./scripts/setup-ssl.sh yourdomain.com your-email@example.com
```

### What this does:
1. Uses Certbot with an ACME webroot challenge against the live Nginx container.
2. Installs the certificate into `/etc/nginx/certs/fullchain.pem` and `privkey.pem`.
3. Activates `infrastructure/nginx/nginx-tls.conf` (enabling HTTP-to-HTTPS redirect and TLS 1.3).
4. Sets up a daily background cron job to automatically renew the certificate before it expires.

Your website is now secured at:
```
https://yourdomain.com
```

---

## How the Free & Open-Source Stack Works

By default, LocalLead Intelligence runs with **zero paid API dependencies**:

| Component | Technology | Cost | Description |
| :--- | :--- | :--- | :--- |
| **Business Discovery** | **OpenStreetMap (Nominatim)** | **$0 / Free** | Discovers businesses, categories, addresses, and coordinates. Enhanced with custom category mapping for cafes, hospitals, schools, bakeries, gyms, hotels, dentists, lawyers, and real estate. |
| **Geocoding** | **Nominatim + Open-Meteo** | **$0 / Free** | Resolves user search text (e.g. *"Viman Nagar, Pune"*) to latitude and longitude bounds. |
| **Website Discovery** | **DuckDuckGo Resolver** | **$0 / Free** | Automatically finds the official business website when OSM metadata does not include one. |
| **Contact Crawler** | **Polite Ethical Crawler** | **$0 / Free** | Crawls official business sites respecting `robots.txt`, anti-SSRF matrix, extracting emails and phone numbers. |
| **Email Validation** | **Disify API** | **$0 / Free (Keyless)** | Checks extracted emails against temporary and disposable domains (Mailinator, GuerrillaMail, etc.). |
| **Phone Formatting** | **Google `phonenumbers`** | **$0 / Free (Local Python)** | Parses and formats contact numbers into international E.164 standard without third-party API costs. |

---

## How to Switch to Google Maps API in the Future

When your company is ready to invest in Google Maps API, switching takes **less than 60 seconds with ZERO code changes**:

1. **Obtain Google Places Key**:
   - Go to [Google Cloud Console](https://console.cloud.google.com/).
   - Enable **Places API (New)**.
   - Generate an API key (e.g. `AIzaSy...`).

2. **Update `.env` on your VPS**:
   ```bash
   nano /srv/leadgen/.env
   ```
   Modify the provider and API key lines as desired:
   ```env
   # Option A: Geoapify Places (High-accuracy, 3,000 free requests/day)
   BUSINESS_PROVIDER=geoapify
   GEOAPIFY_API_KEY=22c8f318c1be4b3bbf97041358ccf09a

   # Option B: Google Places API (New)
   # BUSINESS_PROVIDER=google
   # GOOGLE_MAPS_API_KEY=AIzaSyYourActualKeyHere

   # Option C: OpenStreetMap / Nominatim (100% free, zero keys)
   # BUSINESS_PROVIDER=nominatim
   ```
   *(Save with `Ctrl+O` and exit with `Ctrl+X`)*

3. **Restart the containers**:
   ```bash
   docker compose up -d
   ```

*Tip: You can select between Geoapify, OpenStreetMap, or Google Places per-search directly from the dashboard dropdown on the web interface!*

---

## Useful Management & Monitoring Commands

All commands are run from the project root directory (`/srv/leadgen`):

### View Live Logs
```bash
# All services
docker compose logs -f

# Just the API
docker compose logs -f api

# Just the background search worker
docker compose logs -f worker
```

### Restart Services
```bash
docker compose restart
```

### Stop or Start Stack
```bash
# Stop
docker compose down

# Start
docker compose up -d
```

### Create a New Admin User
If you need to create or reset an admin user account:
```bash
docker compose exec api python -c '
from app.models.database import session_scope
from app.models.models import User
from app.core.security import get_password_hash
import uuid

with session_scope() as db:
    u = db.query(User).filter_by(email="admin@yourdomain.com").first()
    if not u:
        u = User(id=str(uuid.uuid4()), email="admin@yourdomain.com",
                 hashed_password=get_password_hash("YourSecurePassword123!"),
                 full_name="Admin", is_active=True, is_admin=True)
        db.add(u)
        print("Admin user created successfully!")
    else:
        u.hashed_password = get_password_hash("YourSecurePassword123!")
        print("Admin password updated!")
'
```

### Backup Database
```bash
docker compose exec postgres pg_dump -U locallead locallead > locallead_backup_$(date +%F).sql
```

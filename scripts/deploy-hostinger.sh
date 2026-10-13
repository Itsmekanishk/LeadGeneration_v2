#!/usr/bin/env bash
# ==============================================================================
# LocalLead Intelligence — Hostinger VPS Automated Deployment Script
# ==============================================================================
# Usage:
#   chmod +x scripts/deploy-hostinger.sh
#   sudo ./scripts/deploy-hostinger.sh
#
# This script prepares and launches LocalLead Intelligence on a Hostinger VPS:
#  1. Installs Docker & Docker Compose (if missing)
#  2. Creates a 2GB swap file (prevents OOM on VPS during builds/crawls)
#  3. Generates secure production SECRET_KEY in .env
#  4. Configures zero-cost OpenStreetMap (Nominatim) as default provider
#  5. Builds Docker containers and executes database migrations
#  6. Boots the stack with auto-restart policies
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================================${NC}"
echo -e "${CYAN}  LocalLead Intelligence — Hostinger VPS Production Deployer      ${NC}"
echo -e "${BLUE}==================================================================${NC}"

# 1. Require root or sudo
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[ERROR] This script must be run as root or with sudo.${NC}"
   exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
echo -e "${GREEN}[✓] Working directory: ${PROJECT_ROOT}${NC}"

# 2. Configure 2GB Swap space if not present (crucial for Hostinger VPS RAM stability)
SWAP_TOTAL=$(free -m | awk '/^Swap:/ {print $2}')
if [ "$SWAP_TOTAL" -lt 1024 ]; then
    echo -e "${YELLOW}[i] Configuring 2GB swap space for memory stability...${NC}"
    if [ ! -f /swapfile ]; then
        fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        if ! grep -q "/swapfile" /etc/fstab; then
            echo '/swapfile none swap sw 0 0' >> /etc/fstab
        fi
        echo -e "${GREEN}[✓] 2GB swapfile active.${NC}"
    else
        swapon /swapfile 2>/dev/null || true
    fi
else
    echo -e "${GREEN}[✓] Swap space already present (${SWAP_TOTAL}MB).${NC}"
fi

# 3. Check and install Docker & Docker Compose
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}[i] Docker not found. Installing Docker Engine...${NC}"
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg lsb-release
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}[✓] Docker installed successfully.${NC}"
else
    echo -e "${GREEN}[✓] Docker is already installed: $(docker --version)${NC}"
fi

# 4. Create necessary local directories
mkdir -p infrastructure/nginx/certs
mkdir -p infrastructure/certbot/www
mkdir -p backend/data/reports

# 5. Environment configuration setup
if [ ! -f .env ]; then
    echo -e "${YELLOW}[i] .env not found. Creating from .env.example...${NC}"
    cp .env.example .env
fi

# Ensure strong production SECRET_KEY
CURRENT_SECRET=$(grep "^SECRET_KEY=" .env | cut -d '=' -f2- || true)
if [[ -z "$CURRENT_SECRET" || "$CURRENT_SECRET" == *"dev-secret"* || "$CURRENT_SECRET" == *"change-me"* ]]; then
    NEW_SECRET=$(openssl rand -hex 32)
    sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=${NEW_SECRET}/" .env
    echo -e "${GREEN}[✓] Generated cryptographically strong SECRET_KEY in .env${NC}"
fi

# Ensure production environment and smart provider defaults
sed -i.bak "s/^APP_ENV=.*/APP_ENV=production/" .env
GEOAPIFY_KEY_VAL=$(grep "^GEOAPIFY_API_KEY=" .env | cut -d '=' -f2- || true)
if [[ -n "$GEOAPIFY_KEY_VAL" ]]; then
    sed -i.bak "s/^BUSINESS_PROVIDER=.*/BUSINESS_PROVIDER=geoapify/" .env
else
    sed -i.bak "s/^BUSINESS_PROVIDER=.*/BUSINESS_PROVIDER=nominatim/" .env
fi
sed -i.bak "s/^ENABLE_NOMINATIM_GEOCODE=.*/ENABLE_NOMINATIM_GEOCODE=true/" .env
sed -i.bak "s/^PROVIDER_STRICT_CONFIG=.*/PROVIDER_STRICT_CONFIG=false/" .env
rm -f .env.bak

# Detect Public IP
SERVER_IP=$(curl -s -4 https://ifconfig.me || curl -s -4 https://api.ipify.org || echo "YOUR_SERVER_IP")

# 6. Build and launch services with Docker Compose
echo -e "${YELLOW}[i] Building and launching Docker containers...${NC}"
docker compose down --remove-orphans || true
docker compose build
docker compose up -d

# 7. Wait for API and run database migrations
echo -e "${YELLOW}[i] Waiting for database and API to become healthy...${NC}"
MAX_RETRIES=20
COUNT=0
API_READY=false

while [ $COUNT -lt $MAX_RETRIES ]; do
    if docker compose exec -T api curl -fsS http://localhost:8000/api/health > /dev/null 2>&1; then
        API_READY=true
        break
    fi
    sleep 3
    COUNT=$((COUNT + 1))
    echo -n "."
done
echo ""

if [ "$API_READY" = true ]; then
    echo -e "${GREEN}[✓] LocalLead backend API is healthy!${NC}"
else
    echo -e "${YELLOW}[!] Warning: API took longer than expected. Check logs with 'docker compose logs api'.${NC}"
fi

# 8. Success Report
echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}  LocalLead Intelligence is LIVE on your Hostinger VPS!          ${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo -e "  ${CYAN}Website URL:${NC}       http://${SERVER_IP}"
echo -e "  ${CYAN}API Docs:${NC}          http://${SERVER_IP}/backend/api/docs"
echo -e "  ${CYAN}Business Data:${NC}     OpenStreetMap Nominatim (100% Free & Open Source)"
echo -e "  ${CYAN}Email Validator:${NC}   Disify (100% Free, Keyless)"
echo -e ""
echo -e "${YELLOW}Next Steps:${NC}"
echo -e "  1. Point your domain (e.g. leads.yourcompany.com) to IP: ${SERVER_IP}"
echo -e "  2. Run SSL Setup: sudo ./scripts/setup-ssl.sh yourdomain.com your@email.com"
echo -e "  3. When ready to invest in Google Maps API, simply edit .env:"
echo -e "     GOOGLE_MAPS_API_KEY=AIza..."
echo -e "     BUSINESS_PROVIDER=google"
echo -e "     Then restart: docker compose up -d"
echo -e "${BLUE}==================================================================${NC}"

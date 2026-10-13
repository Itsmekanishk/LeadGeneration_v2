#!/usr/bin/env bash
# ==============================================================================
# LocalLead Intelligence — Automated Free SSL (Let's Encrypt) Setup Script
# ==============================================================================
# Usage:
#   sudo ./scripts/setup-ssl.sh yourdomain.com your-email@example.com
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[ERROR] This script must be run as root or with sudo.${NC}"
   exit 1
fi

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [[ -z "$DOMAIN" ]]; then
    read -rp "Enter your domain name (e.g. leads.mycompany.com): " DOMAIN
fi

if [[ -z "$EMAIL" ]]; then
    read -rp "Enter your email for SSL renewal notifications: " EMAIL
fi

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
    echo -e "${RED}[ERROR] Both domain and email are required.${NC}"
    exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo -e "${BLUE}==================================================================${NC}"
echo -e "${CYAN}  Requesting Free Let's Encrypt SSL Certificate for ${DOMAIN}...  ${NC}"
echo -e "${BLUE}==================================================================${NC}"

# 1. Install certbot if not present
if ! command -v certbot &> /dev/null; then
    echo -e "${YELLOW}[i] Installing Certbot...${NC}"
    apt-get update -y
    apt-get install -y certbot
fi

# 2. Ensure challenge directory exists
mkdir -p "$PROJECT_ROOT/infrastructure/certbot/www"
mkdir -p "$PROJECT_ROOT/infrastructure/nginx/certs"

# 3. Request certificate via webroot challenge (served by running Nginx container)
echo -e "${YELLOW}[i] Running Certbot challenge verification...${NC}"
certbot certonly \
    --webroot \
    -w "$PROJECT_ROOT/infrastructure/certbot/www" \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --non-interactive

# 4. Copy certificate files to Nginx certs directory
echo -e "${YELLOW}[i] Installing certificates into Nginx...${NC}"
cp -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$PROJECT_ROOT/infrastructure/nginx/certs/fullchain.pem"
cp -f "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$PROJECT_ROOT/infrastructure/nginx/certs/privkey.pem"
chmod 600 "$PROJECT_ROOT/infrastructure/nginx/certs/privkey.pem"

# 5. Enable HTTPS configuration in Nginx
cp -f "$PROJECT_ROOT/infrastructure/nginx/nginx-tls.conf" "$PROJECT_ROOT/infrastructure/nginx/nginx.conf"

# 6. Update FRONTEND_ORIGIN and CORS in .env for production domain
if grep -q "^FRONTEND_ORIGIN=" .env; then
    sed -i.bak "s|^FRONTEND_ORIGIN=.*|FRONTEND_ORIGIN=https://${DOMAIN}|" .env
fi
CURRENT_CORS=$(grep "^CORS_ORIGINS=" .env | cut -d '=' -f2- || true)
if [[ "$CURRENT_CORS" != *"${DOMAIN}"* ]]; then
    sed -i.bak "s|^CORS_ORIGINS=.*|CORS_ORIGINS=${CURRENT_CORS},https://${DOMAIN},http://${DOMAIN}|" .env
fi
rm -f .env.bak

# 7. Reload Nginx container
echo -e "${YELLOW}[i] Reloading Nginx with HTTPS enabled...${NC}"
docker compose restart nginx

# 8. Setup automatic renewal cron job
CRON_JOB="0 3 1 * * certbot renew --webroot -w ${PROJECT_ROOT}/infrastructure/certbot/www --quiet && cp -f /etc/letsencrypt/live/${DOMAIN}/*.pem ${PROJECT_ROOT}/infrastructure/nginx/certs/ && docker compose -f ${PROJECT_ROOT}/docker-compose.yml restart nginx"
(crontab -l 2>/dev/null | grep -Fv "certbot renew" || true; echo "$CRON_JOB") | crontab -
echo -e "${GREEN}[✓] Auto-renewal cron job registered.${NC}"

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}  SSL Certificate Successfully Installed!                         ${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo -e "  Your website is now secure at: ${CYAN}https://${DOMAIN}${NC}"
echo -e "  HTTP requests will automatically redirect to HTTPS."
echo -e "${BLUE}==================================================================${NC}"

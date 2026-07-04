#!/bin/bash
set -e

echo "  Human Archive - EC2 Deployment Script"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo ./deploy.sh)"
  exit 1
fi

# Detect OS
if [ -f /etc/os-release ]; then
  . /etc/os-release
  OS=$ID
else
  echo "Cannot detect OS"
  exit 1
fi

echo "[1/5] Installing Docker..."

if command -v docker &> /dev/null; then
  echo "Docker already installed: $(docker --version)"
else
  if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    apt-get update
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  elif [ "$OS" = "amzn" ]; then
    yum update -y
    yum install -y docker
    systemctl start docker
    systemctl enable docker
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
  else
    echo "Unsupported OS: $OS"
    exit 1
  fi
fi

echo "[2/5] Starting Docker service..."
systemctl start docker
systemctl enable docker

echo "[3/5] Setting up application directory..."
APP_DIR="/opt/human-archive"
mkdir -p $APP_DIR
cd $APP_DIR

echo "[4/5] Checking for .env file..."
if [ ! -f .env ]; then
  echo ""
  echo "ERROR: .env file not found!"
  echo ""
  echo "Create .env file at $APP_DIR/.env with:"
  cat << 'ENVEOF'
# Database
POSTGRES_USER=ha_user
POSTGRES_PASSWORD=YOUR_STRONG_DB_PASSWORD

# Backend
JWT_SECRET=YOUR_64_CHAR_RANDOM_STRING
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=YOUR_ADMIN_PASSWORD
BACKEND_PORT=8080

# AWS (optional, for S3 frame storage)
S3_BUCKET=your-bucket-name
AWS_REGION=ap-south-1
AWS_ACCESS_KEY_ID=AKIAXXXXXXXXXXXX
AWS_SECRET_ACCESS_KEY=XXXXXXXXXXXXXXXX
ENVEOF
  echo ""
  echo "After creating .env, run: docker compose -f docker-compose.prod.yml up -d"
  exit 1
fi

echo "[5/5] Building and starting services..."
docker compose -f docker-compose.prod.yml up -d --build

echo ""
echo "  Deployment Complete!"
echo ""
echo "Services running:"
docker compose -f docker-compose.prod.yml ps
echo ""
echo "Backend API: http://$(curl -s ifconfig.me):${BACKEND_PORT:-8080}"
echo ""
echo "Logs: docker compose -f docker-compose.prod.yml logs -f"
echo "Stop: docker compose -f docker-compose.prod.yml down"
echo ""

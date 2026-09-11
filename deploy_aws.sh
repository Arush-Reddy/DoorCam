#!/bin/bash
set -e

echo "=========================================="
echo " Starting DoorCam 24/7 AWS Auto-Deployment"
echo "=========================================="

# 1. Setup 2GB Swap (Ensures smooth face recognition on Free Tier instances)
if [ ! -f /swapfile ]; then
    echo "[1/5] Setting up 2GB swap memory..."
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab
fi

# 2. System updates and build tools
echo "[2/5] Installing OS dependencies and build tools..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-pip python3-venv build-essential cmake libopenblas-dev liblapack-dev libx11-dev libgtk-3-dev git curl

# 3. Setup Python Virtual Environment
echo "[3/5] Setting up Python virtual environment..."
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip

# Prevent out-of-memory errors when building dlib C++ templates
export CMAKE_BUILD_PARALLEL_LEVEL=1
pip install -r requirements_hf.txt

# 4. Create 24/7 background systemd service
echo "[4/5] Configuring 24/7 systemd background service..."
cat << EOF > /etc/systemd/system/doorcam.service
[Unit]
Description=DoorCam CCTV 24/7 Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$DIR
Environment="PORT=5000"
Environment="ENABLE_REMOTE_TUNNEL=false"
ExecStart=$DIR/venv/bin/python $DIR/doorbell_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable doorcam.service
systemctl restart doorcam.service

# 5. Done!
IP=$(curl -s https://api.ipify.org || curl -s ifconfig.me)
echo ""
echo "============================================================"
echo " 🎉 DoorCam CCTV Server is now LIVE 24/7 on AWS!"
echo " Public Web App: http://$IP:5000"
echo " Mobile Alerts:  https://ntfy.sh/arush-doorcam-cctv"
echo "============================================================"

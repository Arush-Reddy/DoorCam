import os

# ==========================================
# DoorCam Configuration Template
# ==========================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Host & Port for Local Python Server
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000
APP_NAME = "DoorCam CCTV"

# Push Notifications (ntfy.sh - Zero Setup Required)
NTFY_ENABLED = True
NTFY_SERVER = "https://ntfy.sh"
NTFY_TOPIC = "your-custom-doorcam-topic"  # Change to your private topic name

# Telegram Bot (Optional Backup)
TELEGRAM_ENABLED = False
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

# Storage Directories
KNOWN_FACES_DIR = os.path.join(BASE_DIR, "known_faces")
VISITORS_DIR = os.path.join(BASE_DIR, "visitors")
FACE_DB_DIR = os.path.join(BASE_DIR, "face_db")
ENCODINGS_FILE = os.path.join(FACE_DB_DIR, "encodings.pkl")
DB_PATH = os.path.join(BASE_DIR, "visitors.db")

# Face Recognition Settings
# Tolerance: lower is stricter, higher is looser (0.54 is balanced for doorbells)
FACE_RECOGNITION_TOLERANCE = 0.54

# Anti-Spam Notification Cooldown (seconds)
NOTIFICATION_COOLDOWN_SECONDS = 10

# ESP32-CAM Stream Settings (IP assigned by router to ESP32-CAM)
ESP32_CAM_IP = "192.168.0.6"

# Remote Internet Access (Cloudflare Tunnel)
ENABLE_REMOTE_TUNNEL = True

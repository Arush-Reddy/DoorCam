import os

# Base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Server Configuration
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000
APP_NAME = "DoorCam CCTV"

# Dedicated Mobile Notification App (ntfy.sh)
# No account or signup required!
# 1. Install 'ntfy' app from Google Play Store or Apple App Store (or open in browser)
# 2. Add topic subscription: arush-doorcam-cctv (or your custom topic name)
# 3. Your phone will immediately receive native notifications with photos and sound!
NTFY_ENABLED = True
NTFY_SERVER = "https://ntfy.sh"
NTFY_TOPIC = "arush-doorcam-cctv"

# Telegram Bot Configuration (Optional backup)
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"
TELEGRAM_ENABLED = False

# Storage Directories
KNOWN_FACES_DIR = os.path.join(BASE_DIR, "known_faces")
VISITORS_DIR = os.path.join(BASE_DIR, "visitors")
FACE_DB_DIR = os.path.join(BASE_DIR, "face_db")
ENCODINGS_FILE = os.path.join(FACE_DB_DIR, "encodings.pkl")
DB_PATH = os.path.join(BASE_DIR, "visitors.db")

# Face Recognition Settings
# Tolerance: lower is stricter, higher is looser (0.6 is default in face_recognition, 0.54 is balanced for doorbells)
FACE_RECOGNITION_TOLERANCE = 0.54

# Anti-Spam / Cooldown Settings
# Number of seconds to wait before sending another notification for repeated triggers
NOTIFICATION_COOLDOWN_SECONDS = 10

# ESP32-CAM Streaming Settings
# When your ESP32-CAM connects to WiFi, enter its IP address here
ESP32_CAM_IP = "192.168.0.6"

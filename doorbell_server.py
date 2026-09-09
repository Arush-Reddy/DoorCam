import os
import sys
import time
import datetime
import pickle
import logging
import json
import queue
import threading
import urllib.request
from flask import Flask, request, jsonify, send_from_directory, render_template_string, Response

import config
import notify
import visitor_log

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static")

# Track last notification timestamp for cooldown
last_notification_time = 0

# SSE (Server-Sent Events) clients queue for real-time app notifications
event_subscribers = []

# Loaded encodings cache
known_faces_data = {"encodings": [], "names": []}

def load_encodings():
    global known_faces_data
    if os.path.exists(config.ENCODINGS_FILE):
        try:
            with open(config.ENCODINGS_FILE, "rb") as f:
                known_faces_data = pickle.load(f)
            logger.info("Loaded %d face encoding(s) from %s", len(known_faces_data.get("names", [])), config.ENCODINGS_FILE)
        except Exception as e:
            logger.error("Failed to load face encodings: %s", e)
            known_faces_data = {"encodings": [], "names": []}
    else:
        logger.info("No encodings file found. Running in visitor detection mode.")

def broadcast_event(event_data: dict):
    """Broadcasts a visitor event to all connected web/PWA clients."""
    dead_subscribers = []
    for q in event_subscribers:
        try:
            q.put_nowait(event_data)
        except Exception:
            dead_subscribers.append(q)
    for q in dead_subscribers:
        if q in event_subscribers:
            event_subscribers.remove(q)

def recognize_faces_in_image(image_path: str):
    """
    Attempts to recognize faces in the captured image.
    Returns (visitor_label, best_match_distance)
    """
    try:
        import face_recognition
        import numpy as np
    except ImportError:
        return "Visitor", None

    if not known_faces_data.get("encodings"):
        return "Visitor", None

    try:
        unknown_image = face_recognition.load_image_file(image_path)
        unknown_encodings = face_recognition.face_encodings(unknown_image)

        if not unknown_encodings:
            return "Visitor (No face detected)", None

        known_encodings = known_faces_data["encodings"]
        known_names = known_faces_data["names"]

        recognized_names = []
        best_distance = 1.0

        for unknown_encoding in unknown_encodings:
            distances = face_recognition.face_distance(known_encodings, unknown_encoding)
            min_dist_idx = np.argmin(distances)
            min_dist = distances[min_dist_idx]

            if min_dist <= config.FACE_RECOGNITION_TOLERANCE:
                recognized_names.append(known_names[min_dist_idx])
                if min_dist < best_distance:
                    best_distance = float(min_dist)

        if recognized_names:
            return ", ".join(list(dict.fromkeys(recognized_names))), best_distance
        else:
            return "Unknown Visitor", None
    except Exception as e:
        logger.error("Face recognition error: %s", e)
        return "Visitor", None

# ==========================================
# CCTV APP & PWA ROUTES
# ==========================================

@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")

@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")

@app.route("/api/events/stream")
def events_stream():
    """SSE endpoint for real-time live alerts."""
    def event_stream():
        client_queue = queue.Queue(maxsize=20)
        event_subscribers.append(client_queue)
        # Send initial ping
        yield f"data: {json.dumps({'type': 'connected', 'timestamp': time.time()})}\n\n"
        try:
            while True:
                try:
                    event = client_queue.get(timeout=20.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    # Keep-alive heartbeat
                    yield ": ping\n\n"
        except GeneratorExit:
            if client_queue in event_subscribers:
                event_subscribers.remove(client_queue)

    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/api/config", methods=["GET", "POST"])
def update_config():
    """Allows setting camera IP or ntfy topic directly from app."""
    if request.method == "POST":
        data = request.get_json() or {}
        if "esp32_ip" in data:
            config.ESP32_CAM_IP = data["esp32_ip"].strip()
        if "ntfy_topic" in data:
            config.NTFY_TOPIC = data["ntfy_topic"].strip()
        return jsonify({"status": "ok", "esp32_ip": config.ESP32_CAM_IP, "ntfy_topic": config.NTFY_TOPIC})
    return jsonify({
        "esp32_ip": config.ESP32_CAM_IP,
        "ntfy_topic": config.NTFY_TOPIC,
        "ntfy_server": config.NTFY_SERVER,
        "cooldown": config.NOTIFICATION_COOLDOWN_SECONDS
    })

# ==========================================
# BACKGROUND CAMERA STREAM RELAY
# ==========================================
class CameraStreamRelay:
    """Maintains a single persistent connection to ESP32-CAM and broadcasts frames to any number of clients."""
    def __init__(self):
        self.latest_frame = None
        self.last_frame_time = 0
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _worker(self):
        while self.running:
            cam_ip = getattr(config, "ESP32_CAM_IP", "").strip()
            if not cam_ip:
                time.sleep(1)
                continue
            cam_url = f"http://{cam_ip}:81/"
            try:
                logger.info("CameraStreamRelay connecting to %s...", cam_url)
                req = urllib.request.urlopen(cam_url, timeout=4)
                buffer = b""
                while self.running:
                    chunk = req.read(8192)
                    if not chunk:
                        break
                    buffer += chunk
                    start = buffer.find(b"\xff\xd8")
                    if start != -1:
                        buffer = buffer[start:]
                        end = buffer.find(b"\xff\xd9")
                        if end != -1:
                            jpg = buffer[:end+2]
                            buffer = buffer[end+2:]
                            with self.lock:
                                self.latest_frame = jpg
                                self.last_frame_time = time.time()
                    if len(buffer) > 1024 * 1024:
                        buffer = b""
            except Exception as e:
                logger.warning("CameraStreamRelay stream connection dropped: %s (reconnecting in 1s)", e)
                time.sleep(1)

camera_relay = CameraStreamRelay()

@app.route("/video_feed")
def video_feed():
    """Streams live MJPEG frames buffered in server memory to all connected browsers/phones."""
    def generate():
        last_sent_time = 0
        while True:
            with camera_relay.lock:
                frame = camera_relay.latest_frame
                frame_time = camera_relay.last_frame_time
            if frame and frame_time != last_sent_time:
                last_sent_time = frame_time
                yield (b"--123456789000000000000987654321\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                       frame + b"\r\n")
            time.sleep(0.01) # Low CPU poll, instant delivery on fresh frame

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=123456789000000000000987654321"
    )

@app.route("/")
def app_home():
    """CCTV Camera Mobile App Interface."""
    visits = visitor_log.get_recent_visits(limit=30)
    known_count = len(known_faces_data.get("names", []))
    latest_visit = visits[0] if visits else None
    
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>DoorCam Security</title>
        <link rel="manifest" href="/manifest.json">
        <meta name="theme-color" content="#090d16">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <style>
            :root {
                --bg: #090d16;
                --surface: #131b2e;
                --surface-card: #1b253b;
                --surface-hover: #24314d;
                --accent: #0ea5e9;
                --accent-light: #38bdf8;
                --danger: #ef4444;
                --success: #10b981;
                --warning: #f59e0b;
                --text-main: #f8fafc;
                --text-muted: #94a3b8;
                --border: #24314d;
            }
            * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: var(--bg);
                color: var(--text-main);
                margin: 0;
                padding: 0;
                display: flex;
                flex-direction: column;
                min-height: 100vh;
            }

            /* App Header */
            .app-bar {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 14px 18px;
                background: rgba(19, 27, 46, 0.85);
                backdrop-filter: blur(12px);
                position: sticky;
                top: 0;
                z-index: 50;
                border-bottom: 1px solid var(--border);
            }
            .app-title-box { display: flex; align-items: center; gap: 10px; }
            .status-orb {
                width: 10px;
                height: 10px;
                border-radius: 50%;
                background: var(--success);
                box-shadow: 0 0 10px var(--success);
                animation: pulse 2s infinite;
            }
            @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
            .app-title { font-size: 17px; font-weight: 700; letter-spacing: -0.2px; margin: 0; }
            .app-subtitle { font-size: 11px; color: var(--text-muted); margin-top: 1px; }

            .app-actions { display: flex; gap: 8px; }
            .icon-btn {
                background: var(--surface-card);
                border: 1px solid var(--border);
                color: var(--text-main);
                width: 38px;
                height: 38px;
                border-radius: 10px;
                display: flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                font-size: 16px;
                transition: all 0.15s ease;
            }
            .icon-btn:active { transform: scale(0.93); background: var(--surface-hover); }

            /* Content Container */
            .main-content {
                width: 100%;
                max-width: 600px;
                margin: 0 auto;
                padding: 14px;
                display: flex;
                flex-direction: column;
                gap: 16px;
            }

            /* CCTV Video Player Card */
            .cctv-card {
                background: #000;
                border: 1px solid var(--border);
                border-radius: 18px;
                overflow: hidden;
                position: relative;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
            }
            .video-viewport {
                position: relative;
                width: 100%;
                aspect-ratio: 4/3;
                background: #050811;
                display: flex;
                align-items: center;
                justify-content: center;
                overflow: hidden;
            }
            .camera-stream {
                width: 100%;
                height: 100%;
                object-fit: contain;
                display: block;
            }
            .viewport-overlay-top {
                position: absolute;
                top: 12px;
                left: 12px;
                right: 12px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                pointer-events: none;
            }
            .live-tag {
                background: rgba(239, 68, 68, 0.9);
                color: #fff;
                font-size: 11px;
                font-weight: 700;
                padding: 4px 8px;
                border-radius: 6px;
                letter-spacing: 0.5px;
                display: flex;
                align-items: center;
                gap: 5px;
            }
            .live-dot {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: #fff;
                animation: blink 1s infinite;
            }
            @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

            .timestamp-overlay {
                font-family: monospace;
                font-size: 11px;
                background: rgba(0, 0, 0, 0.6);
                padding: 4px 8px;
                border-radius: 6px;
                color: #cbd5e1;
            }

            .viewport-overlay-bottom {
                position: absolute;
                bottom: 12px;
                left: 12px;
                right: 12px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .cam-meta {
                background: rgba(0, 0, 0, 0.6);
                backdrop-filter: blur(8px);
                padding: 4px 10px;
                border-radius: 8px;
                font-size: 11px;
                color: var(--text-muted);
            }

            /* Quick Action Buttons */
            .quick-actions {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 10px;
            }
            .action-btn {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 14px;
                padding: 12px 6px;
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 6px;
                color: var(--text-main);
                cursor: pointer;
                transition: all 0.2s ease;
            }
            .action-btn:active { transform: scale(0.95); background: var(--surface-hover); }
            .action-btn .icon { font-size: 20px; }
            .action-btn .label { font-size: 11px; font-weight: 500; color: var(--text-muted); }
            .action-btn.active { border-color: var(--accent); background: rgba(14, 165, 233, 0.15); }
            .action-btn.active .label { color: var(--accent-light); }

            /* Incoming Alert Banner (Animated) */
            #alert-toast {
                display: none;
                background: linear-gradient(135deg, #1e293b, #0f172a);
                border: 1px solid var(--accent);
                border-radius: 14px;
                padding: 14px;
                box-shadow: 0 10px 30px rgba(14, 165, 233, 0.35);
                animation: slideDown 0.4s cubic-bezier(0.16, 1, 0.3, 1);
            }
            @keyframes slideDown {
                from { transform: translateY(-30px); opacity: 0; }
                to { transform: translateY(0); opacity: 1; }
            }
            .alert-toast-content {
                display: flex;
                align-items: center;
                gap: 12px;
            }
            .alert-thumb {
                width: 50px;
                height: 50px;
                border-radius: 10px;
                object-fit: cover;
                border: 1px solid var(--accent);
            }

            /* Event Timeline / Activity Feed */
            .section-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-top: 4px;
            }
            .section-title { font-size: 15px; font-weight: 700; margin: 0; }
            .feed-count { font-size: 12px; color: var(--text-muted); }

            .timeline-list {
                display: flex;
                flex-direction: column;
                gap: 10px;
            }
            .timeline-item {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 14px;
                padding: 12px;
                display: flex;
                gap: 12px;
                align-items: center;
                cursor: pointer;
                transition: transform 0.15s ease, background 0.15s ease;
            }
            .timeline-item:active { transform: scale(0.98); background: var(--surface-hover); }
            .item-thumb {
                width: 64px;
                height: 64px;
                border-radius: 10px;
                object-fit: cover;
                background: #000;
                flex-shrink: 0;
            }
            .item-info { flex: 1; min-width: 0; }
            .item-name {
                font-size: 15px;
                font-weight: 600;
                margin-bottom: 3px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            .item-name.known { color: var(--success); }
            .item-name.unknown { color: var(--warning); }
            .item-meta {
                font-size: 12px;
                color: var(--text-muted);
                display: flex;
                align-items: center;
                gap: 6px;
            }
            .item-badge {
                font-size: 10px;
                font-weight: 600;
                padding: 2px 6px;
                border-radius: 4px;
                background: var(--surface-card);
                color: var(--accent-light);
            }

            /* Modal for Full Photo Viewer */
            .modal-bg {
                display: none;
                position: fixed;
                inset: 0;
                background: rgba(0, 0, 0, 0.85);
                backdrop-filter: blur(8px);
                z-index: 100;
                align-items: center;
                justify-content: center;
                padding: 16px;
            }
            .modal-card {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 18px;
                max-width: 500px;
                width: 100%;
                overflow: hidden;
            }
            .modal-img {
                width: 100%;
                max-height: 65vh;
                object-fit: contain;
                background: #000;
                display: block;
            }
            .modal-body { padding: 16px; }
            .modal-close-btn {
                background: var(--surface-card);
                border: 1px solid var(--border);
                color: var(--text-main);
                width: 100%;
                padding: 12px;
                border-radius: 12px;
                font-weight: 600;
                margin-top: 12px;
                cursor: pointer;
            }
        </style>
    </head>
    <body>
        <!-- Top App Bar -->
        <header class="app-bar">
            <div class="app-title-box">
                <div class="status-orb" id="status-orb"></div>
                <div>
                    <h1 class="app-title">DoorCam Front Door</h1>
                    <div class="app-subtitle" id="connection-subtitle">OV3660 3MP • System Armed</div>
                </div>
            </div>
            <div class="app-actions">
                <button class="icon-btn" onclick="openConfigModal()" title="Camera Settings">⚙️</button>
                <button class="icon-btn" onclick="enablePushNotifications()" id="btn-notify" title="Enable Alerts">🔔</button>
            </div>
        </header>

        <div class="main-content">
            <!-- Incoming Alert Toast Banner -->
            <div id="alert-toast">
                <div class="alert-toast-content">
                    <img id="toast-img" class="alert-thumb" src="" alt="Snapshot">
                    <div style="flex:1;">
                        <div style="font-size:11px; text-transform:uppercase; font-weight:700; color:var(--accent-light);">⚡ Doorbell Alert</div>
                        <div id="toast-title" style="font-size:15px; font-weight:700; margin:2px 0;">Person Detected</div>
                        <div id="toast-time" style="font-size:12px; color:var(--text-muted);">Just now</div>
                    </div>
                    <button class="icon-btn" onclick="dismissToast()" style="border-radius:50%; width:30px; height:30px;">✕</button>
                </div>
            </div>

            <!-- CCTV Video Player -->
            <div class="cctv-card">
                <div class="video-viewport">
                    {% if esp32_ip %}
                        <img id="cam-feed" class="camera-stream" src="/video_feed" onerror="handleStreamError(this)" alt="Live Feed">
                    {% elif latest_visit %}
                        <img id="cam-feed" class="camera-stream" src="/photo/{{ latest_visit['photo_path'] }}" alt="Latest Snapshot">
                    {% else %}
                        <div style="text-align:center; padding: 40px 20px; color: var(--text-muted);">
                            <div style="font-size: 36px; margin-bottom: 8px;">📹</div>
                            <div style="font-size: 14px; font-weight: 600; color: #fff;">Camera Waiting for Connection</div>
                            <div style="font-size: 12px; margin-top: 4px;">Set ESP32 IP in ⚙️ settings or trigger a test snapshot</div>
                        </div>
                    {% endif %}

                    <!-- Viewport Overlays -->
                    <div class="viewport-overlay-top">
                        <div class="live-tag">
                            <div class="live-dot"></div>
                            <span id="live-label">{{ 'LIVE' if esp32_ip else 'STANDBY' }}</span>
                        </div>
                        <div class="timestamp-overlay" id="live-clock">--:--:--</div>
                    </div>

                    <div class="viewport-overlay-bottom">
                        <div class="cam-meta">OV3660 HD • AI Face Recognition</div>
                    </div>
                </div>
            </div>

            <!-- Quick Action Controls -->
            <div class="quick-actions">
                <button class="action-btn" id="btn-sound" onclick="toggleChimeSound()">
                    <span class="icon">🔊</span>
                    <span class="label">Chime: ON</span>
                </button>
                <button class="action-btn" onclick="testDoorbellChime()">
                    <span class="icon">🔔</span>
                    <span class="label">Test Ring</span>
                </button>
                <button class="action-btn" onclick="openConfigModal()">
                    <span class="icon">📹</span>
                    <span class="label">Set IP</span>
                </button>
                <button class="action-btn" onclick="window.open('https://ntfy.sh/{{ ntfy_topic }}', '_blank')">
                    <span class="icon">📲</span>
                    <span class="label">Phone App</span>
                </button>
            </div>

            <!-- Activity / Event Timeline -->
            <div class="section-header">
                <h2 class="section-title">Activity Feed</h2>
                <div class="feed-count">{{ visits|length }} events</div>
            </div>

            <div class="timeline-list" id="timeline-container">
                {% for v in visits %}
                <div class="timeline-item" onclick="openPhotoModal('/photo/{{ v['photo_path'] }}', '{{ v['name'] }}', '{{ v['timestamp'] }}', '{{ v['trigger_source'] }}')">
                    <img class="item-thumb" src="/photo/{{ v['photo_path'] }}" alt="Visitor thumbnail" loading="lazy">
                    <div class="item-info">
                        <div class="item-name {{ 'known' if 'Unknown' not in v['name'] and 'Visitor' != v['name'] else 'unknown' }}">
                            {{ v['name'] }}
                        </div>
                        <div class="item-meta">
                            <span class="item-badge">{{ v['trigger_source'] }}</span>
                            <span>{{ v['timestamp'] }}</span>
                        </div>
                    </div>
                    <div style="color: var(--text-muted); font-size: 18px;">›</div>
                </div>
                {% endfor %}

                {% if not visits %}
                <div id="no-events-msg" style="text-align: center; padding: 36px 12px; color: var(--text-muted);">
                    <div style="font-size: 32px; margin-bottom: 8px;">🛡️</div>
                    <div style="font-size: 14px; font-weight: 500;">No activity recorded yet</div>
                    <div style="font-size: 12px; margin-top: 4px;">PIR motion and doorbell button presses will appear here instantly</div>
                </div>
                {% endif %}
            </div>
        </div>

        <!-- Fullscreen Photo Modal -->
        <div class="modal-bg" id="photo-modal" onclick="closePhotoModal(event)">
            <div class="modal-card" onclick="event.stopPropagation()">
                <img id="modal-img" class="modal-img" src="" alt="Full view">
                <div class="modal-body">
                    <div id="modal-name" style="font-size: 18px; font-weight: 700;"></div>
                    <div id="modal-meta" style="color: var(--text-muted); font-size: 13px; margin-top: 4px;"></div>
                    <button class="modal-close-btn" onclick="closePhotoModal()">Close</button>
                </div>
            </div>
        </div>

        <!-- Config Modal -->
        <div class="modal-bg" id="config-modal" onclick="closeConfigModal(event)">
            <div class="modal-card" onclick="event.stopPropagation()">
                <div class="modal-body">
                    <h3 style="margin-top:0;">Camera & App Settings</h3>
                    <p style="font-size:13px; color:var(--text-muted);">Enter the IP address shown in your ESP32 Serial Monitor to connect the live stream.</p>
                    <label style="font-size:12px; font-weight:600;">ESP32-CAM Local IP:</label>
                    <input type="text" id="input-cam-ip" value="{{ esp32_ip }}" placeholder="e.g. 192.168.1.50" 
                           style="width:100%; padding:10px; margin:6px 0 16px 0; background:var(--bg); border:1px solid var(--border); color:#fff; border-radius:8px;">
                    
                    <label style="font-size:12px; font-weight:600;">ntfy Phone Alert Topic:</label>
                    <input type="text" id="input-ntfy-topic" value="{{ ntfy_topic }}" placeholder="e.g. arush-doorcam-cctv"
                           style="width:100%; padding:10px; margin:6px 0 16px 0; background:var(--bg); border:1px solid var(--border); color:#fff; border-radius:8px;">

                    <button class="modal-close-btn" style="background:var(--accent); border:none; margin-bottom:8px;" onclick="saveConfig()">Save Settings</button>
                    <button class="modal-close-btn" onclick="closeConfigModal()">Cancel</button>
                </div>
            </div>
        </div>

        <script>
            // Register PWA Service Worker
            if ('serviceWorker' in navigator) {
                navigator.serviceWorker.register('/sw.js').catch(console.error);
            }

            // Live Clock Overlay
            function updateClock() {
                const now = new Date();
                document.getElementById('live-clock').textContent = now.toLocaleTimeString();
            }
            setInterval(updateClock, 1000);
            updateClock();

            // Web Audio API Synthesizer for Doorbell Chime
            let soundEnabled = true;
            let audioCtx = null;

            function playChime() {
                if (!soundEnabled) return;
                try {
                    if (!audioCtx) {
                        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    }
                    if (audioCtx.state === 'suspended') {
                        audioCtx.resume();
                    }

                    const now = audioCtx.currentTime;

                    // Note 1: Ding (660Hz E5)
                    const osc1 = audioCtx.createOscillator();
                    const gain1 = audioCtx.createGain();
                    osc1.type = 'sine';
                    osc1.frequency.setValueAtTime(659.25, now);
                    gain1.gain.setValueAtTime(0.4, now);
                    gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.8);
                    osc1.connect(gain1);
                    gain1.connect(audioCtx.destination);
                    osc1.start(now);
                    osc1.stop(now + 0.8);

                    // Note 2: Dong (523.25Hz C5)
                    const osc2 = audioCtx.createOscillator();
                    const gain2 = audioCtx.createGain();
                    osc2.type = 'sine';
                    osc2.frequency.setValueAtTime(523.25, now + 0.35);
                    gain2.gain.setValueAtTime(0.45, now + 0.35);
                    gain2.gain.exponentialRampToValueAtTime(0.001, now + 1.4);
                    osc2.connect(gain2);
                    gain2.connect(audioCtx.destination);
                    osc2.start(now + 0.35);
                    osc2.stop(now + 1.4);
                } catch (e) {
                    console.warn("Audio playback error:", e);
                }
            }

            function toggleChimeSound() {
                soundEnabled = !soundEnabled;
                const btn = document.getElementById('btn-sound');
                btn.classList.toggle('active', soundEnabled);
                btn.querySelector('.label').textContent = 'Chime: ' + (soundEnabled ? 'ON' : 'OFF');
                if (soundEnabled) playChime();
            }

            function testDoorbellChime() {
                playChime();
                if ('vibrate' in navigator) {
                    navigator.vibrate([200, 100, 200, 100, 400]);
                }
            }

            // Real-time Event Stream (SSE)
            function connectEventStream() {
                const sse = new EventSource('/api/events/stream');
                const orb = document.getElementById('status-orb');

                sse.onopen = () => {
                    orb.style.background = 'var(--success)';
                    orb.style.boxShadow = '0 0 10px var(--success)';
                };

                sse.onmessage = (event) => {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'visitor') {
                            handleIncomingVisitor(data);
                        }
                    } catch (e) {
                        // Keep-alive or non-JSON
                    }
                };

                sse.onerror = () => {
                    orb.style.background = 'var(--danger)';
                    orb.style.boxShadow = '0 0 10px var(--danger)';
                    sse.close();
                    setTimeout(connectEventStream, 4000);
                };
            }
            connectEventStream();

            function handleIncomingVisitor(v) {
                // 1. Play chime sound
                playChime();

                // 2. Vibrate phone
                if ('vibrate' in navigator) {
                    navigator.vibrate([200, 100, 200, 100, 400]);
                }

                // 3. Show In-App Banner
                const toast = document.getElementById('alert-toast');
                document.getElementById('toast-img').src = v.photo_url;
                document.getElementById('toast-title').textContent = v.name;
                document.getElementById('toast-time').textContent = v.trigger + ' • ' + v.timestamp;
                toast.style.display = 'block';

                // 4. Trigger OS Notification
                if (Notification.permission === 'granted') {
                    new Notification("🔔 DoorCam Alert: " + v.name, {
                        body: v.trigger + " detected at front door",
                        icon: v.photo_url
                    });
                }

                // 5. Prepend to timeline list
                const container = document.getElementById('timeline-container');
                const noMsg = document.getElementById('no-events-msg');
                if (noMsg) noMsg.remove();

                const isKnown = !v.name.includes('Unknown') && v.name !== 'Visitor';
                const item = document.createElement('div');
                item.className = 'timeline-item';
                item.onclick = () => openPhotoModal(v.photo_url, v.name, v.timestamp, v.trigger);
                item.innerHTML = `
                    <img class="item-thumb" src="${v.photo_url}" alt="Thumbnail">
                    <div class="item-info">
                        <div class="item-name ${isKnown ? 'known' : 'unknown'}">${v.name}</div>
                        <div class="item-meta">
                            <span class="item-badge">${v.trigger}</span>
                            <span>${v.timestamp}</span>
                        </div>
                    </div>
                    <div style="color: var(--text-muted); font-size: 18px;">›</div>
                `;
                container.prepend(item);
            }

            function dismissToast() {
                document.getElementById('alert-toast').style.display = 'none';
            }

            function enablePushNotifications() {
                if (!('Notification' in window)) {
                    alert("This browser doesn't support notifications.");
                    return;
                }
                Notification.requestPermission().then(permission => {
                    if (permission === 'granted') {
                        document.getElementById('btn-notify').style.borderColor = 'var(--success)';
                        alert("✅ Push notifications enabled! You will be alerted when someone rings or approaches.");
                    }
                });
            }

            // Stream Fallback
            function handleStreamError(img) {
                document.getElementById('live-label').textContent = 'OFFLINE';
                console.log("Stream offline or unavailable");
            }

            // Modal Handlers
            function openPhotoModal(photoUrl, name, time, trigger) {
                document.getElementById('modal-img').src = photoUrl;
                document.getElementById('modal-name').textContent = name;
                document.getElementById('modal-meta').textContent = trigger + " • " + time;
                document.getElementById('photo-modal').style.display = 'flex';
            }
            function closePhotoModal(e) {
                if (!e || e.target === document.getElementById('photo-modal') || e.target.classList.contains('modal-close-btn')) {
                    document.getElementById('photo-modal').style.display = 'none';
                }
            }

            function openConfigModal() {
                document.getElementById('config-modal').style.display = 'flex';
            }
            function closeConfigModal(e) {
                if (!e || e.target === document.getElementById('config-modal') || e.target.classList.contains('modal-close-btn')) {
                    document.getElementById('config-modal').style.display = 'none';
                }
            }

            function saveConfig() {
                const ip = document.getElementById('input-cam-ip').value;
                const topic = document.getElementById('input-ntfy-topic').value;
                fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({esp32_ip: ip, ntfy_topic: topic})
                }).then(() => {
                    window.location.reload();
                });
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(
        html,
        visits=visits,
        known_count=known_count,
        esp32_ip=config.ESP32_CAM_IP,
        ntfy_topic=config.NTFY_TOPIC
    )

@app.route("/photo/<path:filename>")
def serve_photo(filename):
    """Serves visitor snapshot photos."""
    photo_full_path = os.path.join(config.BASE_DIR, filename)
    if os.path.exists(photo_full_path):
        return send_from_directory(os.path.dirname(photo_full_path), os.path.basename(photo_full_path))
    return ("Photo not found", 404)

@app.route("/history", methods=["GET"])
def history():
    """Returns JSON list of recent visits."""
    limit = request.args.get("limit", default=25, type=int)
    return jsonify(visitor_log.get_recent_visits(limit=limit))

@app.route("/visitor", methods=["POST"])
def visitor():
    """
    Endpoint called by ESP32-CAM when motion or doorbell button is triggered.
    Accepts multipart/form-data with file field 'photo' or raw image binary body.
    """
    global last_notification_time

    trigger_source = request.args.get("trigger", request.form.get("trigger", "PIR")).upper()
    now = datetime.datetime.now()
    current_time_sec = time.time()

    # Determine save directory: visitors/YYYY-MM-DD/
    day_folder = os.path.join(config.VISITORS_DIR, now.strftime("%Y-%m-%d"))
    os.makedirs(day_folder, exist_ok=True)

    photo_filename = now.strftime("%H-%M-%S") + ".jpg"
    photo_save_path = os.path.join(day_folder, photo_filename)

    # Save photo from multipart form, raw body, or pull directly from live stream relay buffer
    photo_saved = False
    if "photo" in request.files:
        photo_file = request.files["photo"]
        photo_file.save(photo_save_path)
        photo_saved = True
    elif request.data and len(request.data) > 0:
        with open(photo_save_path, "wb") as f:
            f.write(request.data)
        photo_saved = True

    if not photo_saved:
        with camera_relay.lock:
            frame = camera_relay.latest_frame
        if frame:
            with open(photo_save_path, "wb") as f:
                f.write(frame)
            photo_saved = True
            logger.info("Captured snapshot directly from live stream relay memory buffer (%d bytes)", len(frame))
        else:
            logger.warning("Received /visitor request with no photo data and live stream buffer is empty")
            return jsonify({"error": "No photo data or live stream available"}), 400

    logger.info("Saved snapshot: %s (Trigger: %s)", photo_save_path, trigger_source)

    # Run facial recognition
    recognized_name, confidence = recognize_faces_in_image(photo_save_path)
    logger.info("Identified: %s (dist: %s)", recognized_name, confidence)

    # Log to database
    visit_id = visitor_log.log_visit(
        name=recognized_name,
        photo_path=photo_save_path,
        trigger_source=trigger_source,
        confidence=confidence
    )

    relative_photo_path = os.path.relpath(photo_save_path, config.BASE_DIR)
    photo_url = f"/photo/{relative_photo_path.replace(chr(92), '/')}"

    # Broadcast to live Web App & PWA clients instantly
    event_payload = {
        "type": "visitor",
        "visit_id": visit_id,
        "name": recognized_name,
        "trigger": trigger_source,
        "timestamp": now.strftime("%I:%M:%S %p"),
        "photo_url": photo_url
    }
    broadcast_event(event_payload)

    # Check cooldown before sending external push notifications
    time_since_last = current_time_sec - last_notification_time
    if time_since_last >= config.NOTIFICATION_COOLDOWN_SECONDS:
        last_notification_time = current_time_sec
        timestamp_str = now.strftime("%I:%M:%S %p • %d %b %Y")
        
        # Dispatches via ntfy (dedicated CCTV app) & Telegram (if enabled)
        notify.dispatch_all_alerts(photo_save_path, recognized_name, trigger_source, timestamp_str)
    else:
        logger.info("External alert suppressed by anti-spam cooldown (elapsed: %.1fs < %ds)", 
                    time_since_last, config.NOTIFICATION_COOLDOWN_SECONDS)

    return jsonify({
        "status": "success",
        "visit_id": visit_id,
        "name": recognized_name,
        "trigger": trigger_source,
        "photo_url": photo_url,
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S")
    })

if __name__ == "__main__":
    visitor_log.init_db()
    load_encodings()
    logger.info("Starting DoorCam CCTV server on http://%s:%d", config.SERVER_HOST, config.SERVER_PORT)
    app.run(host=config.SERVER_HOST, port=config.SERVER_PORT, debug=False, threaded=True)

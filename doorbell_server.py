import os
import sys
import types
import builtins

# Intercept quit() and exit() so third-party libraries (like face_recognition) can never kill the Flask process
def _safe_quit(*args, **kwargs):
    raise RuntimeError("quit() intercepted: an internal module tried to exit Python process")
builtins.quit = _safe_quit
builtins.exit = _safe_quit

# Ensure pkg_resources is always available for face_recognition_models in Python 3.11+
try:
    import pkg_resources
except ImportError:
    class MockPkgResources(types.ModuleType):
        def resource_filename(self, package_or_requirement, resource_name):
            mod_name = getattr(package_or_requirement, '__name__', str(package_or_requirement))
            for p in sys.path:
                cand = os.path.join(p, *mod_name.split('.'), resource_name)
                if os.path.exists(cand):
                    return cand
            return os.path.abspath(resource_name)
    sys.modules['pkg_resources'] = MockPkgResources('pkg_resources')

import time
import datetime
import pickle
import logging
import json
import queue
import threading
import urllib.request
from flask import Flask, request, jsonify, send_from_directory, render_template_string, Response, make_response

import config
import notify
import visitor_log
import tunnel

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
        import cv2
        unknown_image = face_recognition.load_image_file(image_path)
        (h, w) = unknown_image.shape[:2]
        if max(h, w) > 640:
            scale = 640.0 / max(h, w)
            unknown_image = cv2.resize(unknown_image, (int(w * scale), int(h * scale)))

        # 1. First try OpenCV Haar cascade (ultra-fast, ~5ms, zero RAM spike, universal compatibility)
        face_locs = []
        try:
            gray = cv2.cvtColor(unknown_image, cv2.COLOR_RGB2GRAY)
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            rects = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
            if len(rects) > 0:
                face_locs = [(int(y), int(x + w), int(y + h), int(x)) for (x, y, w, h) in rects]
                logger.info("Face detected via OpenCV Haar cascade (%d faces)", len(face_locs))
        except Exception as he:
            logger.debug("Haar cascade check: %s", he)

        # 2. Standard HOG detector fallback if Haar missed
        if not face_locs:
            try:
                face_locs = face_recognition.face_locations(unknown_image, number_of_times_to_upsample=1)
            except Exception as dlib_e:
                logger.warning("dlib face_locations warning: %s", dlib_e)

        target_image = unknown_image

        # 3. Adaptive CLAHE contrast enhancement fallback
        if not face_locs:
            try:
                bgr = cv2.imread(image_path)
                if bgr is not None:
                    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
                    l, a, b = cv2.split(lab)
                    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8,8))
                    cl = clahe.apply(l)
                    enhanced = cv2.cvtColor(cv2.merge((cl, a, b)), cv2.COLOR_LAB2RGB)
                    face_locs = face_recognition.face_locations(enhanced, number_of_times_to_upsample=1)
                    if face_locs:
                        target_image = enhanced
            except Exception as e:
                logger.debug("Adaptive enhancement error: %s", e)

        if not face_locs:
            return "Visitor (No face detected)", None

        unknown_encodings = face_recognition.face_encodings(target_image, known_face_locations=face_locs)
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
                    event = client_queue.get(timeout=10.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    # Keep-alive heartbeat every 10s to prevent reverse proxy timeouts
                    yield ": ping\n\n"
        except GeneratorExit:
            if client_queue in event_subscribers:
                event_subscribers.remove(client_queue)

    resp = Response(event_stream(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Connection"] = "keep-alive"
    return resp

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

@app.route("/api/diagnostics")
def diagnostics():
    """System diagnostics endpoint to verify cloud environment, dlib, and face models."""
    info = {
        "status": "online",
        "known_faces_loaded": len(known_faces_data.get("encodings", [])),
        "known_names": known_faces_data.get("names", []),
        "python_version": sys.version,
    }
    
    # 1. OpenCV check
    try:
        import cv2
        info["opencv_version"] = cv2.__version__
    except Exception as e:
        info["opencv_error"] = str(e)

    # 2. dlib check
    try:
        import dlib
        info["dlib_version"] = getattr(dlib, "__version__", "unknown")
        det = dlib.get_frontal_face_detector()
        info["dlib_detector"] = "ready"
    except Exception as e:
        info["dlib_error"] = str(e)

    # 3. face_recognition & models check
    try:
        import face_recognition
        info["face_recognition"] = "imported"
    except Exception as e:
        info["face_recognition_error"] = str(e)

    try:
        import face_recognition_models
        info["face_models"] = "ready"
    except Exception as e:
        info["face_models_error"] = str(e)

    return jsonify(info)


# ==========================================
# BACKGROUND CAMERA STREAM RELAY
# ==========================================
class CameraStreamRelay:
    """Maintains a single persistent connection to ESP32-CAM and broadcasts frames to any number of clients."""
    def __init__(self):
        self.latest_frame = None
        self.last_frame_time = 0.0
        self.frame_id = 0
        self.fps = 0.0
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def push_frame(self, frame_bytes):
        """Allows remote ESP32 to push frames directly to cloud server with instant condition broadcast."""
        with self.condition:
            now = time.time()
            if self.last_frame_time > 0:
                dt = now - self.last_frame_time
                if dt > 0:
                    inst_fps = 1.0 / dt
                    self.fps = round(self.fps * 0.7 + inst_fps * 0.3, 1)
            self.latest_frame = frame_bytes
            self.last_frame_time = now
            self.frame_id += 1
            self.condition.notify_all()

    def reset(self):
        with self.condition:
            self.last_frame_time = 0.0
            self.fps = 0.0
            self.condition.notify_all()

    def is_active(self):
        with self.condition:
            if not is_streaming_requested():
                return False
            return (self.latest_frame is not None) and ((time.time() - self.last_frame_time) < 3.5)

    def _worker(self):
        while self.running:
            # On cloud hosts like Hugging Face or Render, don't attempt local subnet polling
            if os.environ.get("SPACE_ID") or os.environ.get("CLOUD_DEPLOYMENT") or os.environ.get("RENDER"):
                time.sleep(5)
                continue

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
                            self.push_frame(jpg)
                    if len(buffer) > 1024 * 1024:
                        buffer = b""
            except Exception as e:
                logger.warning("CameraStreamRelay stream connection dropped: %s (reconnecting in 1s)", e)
                time.sleep(1)

camera_relay = CameraStreamRelay()
# ==========================================
# ON-DEMAND LIVE CLOUD STREAMING CONTROLLER
# ==========================================
stream_demand_active = False
stream_demand_expiry = 0.0
stream_demand_lock = threading.Lock()
ring_stream_expiry = 0.0

def is_streaming_requested():
    """Returns True if user is actively watching live or doorbell was just rung."""
    global stream_demand_active, stream_demand_expiry, ring_stream_expiry
    now = time.time()
    with stream_demand_lock:
        if stream_demand_active and now > stream_demand_expiry:
            stream_demand_active = False
        demand = bool(stream_demand_active and now < stream_demand_expiry)
        ring = bool(now < ring_stream_expiry)
        return demand or ring

@app.route("/api/frame_push", methods=["POST"])
def receive_pushed_frame():
    """Allows ESP32 to push individual live stream frames and receive stream/stop feedback."""
    data = request.get_data()
    if data:
        if not is_streaming_requested():
            resp = Response("STOP", status=200, mimetype="text/plain")
            resp.headers["X-Stream"] = "STOP"
            return resp
        camera_relay.push_frame(data)
        resp = Response("OK", status=200, mimetype="text/plain")
        resp.headers["X-Stream"] = "CONTINUE"
        return resp
    return "No frame", 400

@app.route("/api/stream_push", methods=["POST"])
def receive_pushed_stream():
    """Allows ESP32 to push continuous 15-20 FPS multipart/chunked live stream."""
    try:
        stream = request.stream
        buffer = b""
        while True:
            chunk = stream.read(8192)
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
                    camera_relay.push_frame(jpg)
            if len(buffer) > 2 * 1024 * 1024:
                buffer = b""
        return "OK", 200
    except Exception as e:
        logger.debug("Stream push ended: %s", e)
        return "Closed", 200

@app.route("/api/stream/start", methods=["POST"])
def start_stream_demand():
    """Starts on-demand live cloud streaming session."""
    global stream_demand_active, stream_demand_expiry
    duration = 60
    try:
        req_json = request.get_json(silent=True)
        if req_json and "duration" in req_json:
            duration = min(int(req_json["duration"]), 180)
    except Exception:
        pass
    with stream_demand_lock:
        stream_demand_active = True
        stream_demand_expiry = time.time() + duration
    logger.info("On-demand live stream started (duration: %ds)", duration)
    return jsonify({"status": "started", "active": True, "expires_in": duration})

@app.route("/api/stream/stop", methods=["POST"])
def stop_stream_demand():
    """Stops on-demand live cloud streaming session immediately."""
    global stream_demand_active, stream_demand_expiry, ring_stream_expiry
    with stream_demand_lock:
        stream_demand_active = False
        stream_demand_expiry = 0.0
        ring_stream_expiry = 0.0
    camera_relay.reset()
    logger.info("On-demand live stream stopped by user")
    return jsonify({"status": "stopped", "active": False})

@app.route("/api/stream_cmd", methods=["GET"])
def stream_command():
    """Polled by ESP32-CAM every 1.5-2 seconds to know whether to stream live video."""
    return jsonify({"stream": is_streaming_requested()})

@app.route("/api/stream_status")
def stream_status():
    """Returns whether the ESP32 is actively streaming live video right now."""
    active = camera_relay.is_active()
    now = time.time()
    remaining = max(0, int(stream_demand_expiry - now)) if stream_demand_active else 0
    return jsonify({
        "active": active,
        "fps": getattr(camera_relay, "fps", 0),
        "age_sec": round(now - camera_relay.last_frame_time, 1) if camera_relay.last_frame_time else 999,
        "demand": is_streaming_requested(),
        "remaining_sec": remaining
    })

@app.route("/api/live_state")
def live_state():
    """Aggregated real-time state: live stream, latest visitor, PIR status."""
    now = time.time()
    req_active = is_streaming_requested()
    active = bool(req_active and camera_relay.is_active())
    remaining = max(0, int(stream_demand_expiry - now)) if (stream_demand_active and req_active) else 0
    if not remaining and ring_stream_expiry > now:
        remaining = max(0, int(ring_stream_expiry - now))
    if not req_active:
        active = False
        remaining = 0

    recent = visitor_log.get_recent_visits(limit=1)
    latest = None
    if recent:
        r = recent[0]
        photo_rel = r.get("photo_path", "")
        latest = {
            "id": r.get("id"),
            "name": r.get("name"),
            "trigger": r.get("trigger_source"),
            "timestamp": r.get("timestamp"),
            "photo_url": f"/photo/{photo_rel.replace(chr(92), '/')}"
        }

    return jsonify({
        "stream": {
            "active": active,
            "fps": getattr(camera_relay, "fps", 0) if active else 0.0,
            "demand": req_active,
            "remaining_sec": remaining
        },
        "latest_visit": latest,
        "pir_enabled": pir_alerts_enabled
    })

# PIR Motion Alerts State (default False to prevent false motion triggers)
pir_alerts_enabled = False

@app.route("/api/pir/toggle", methods=["POST"])
def toggle_pir_alerts():
    """Toggle PIR motion sensor alerts on/off."""
    global pir_alerts_enabled
    pir_alerts_enabled = not pir_alerts_enabled
    logger.info("PIR motion alerts toggled to: %s", pir_alerts_enabled)
    return jsonify({"enabled": pir_alerts_enabled})

@app.route("/api/feed/clear", methods=["POST"])
def clear_feed():
    """Clears all visitor history from database and notifies all connected clients."""
    visitor_log.clear_all_visits()
    broadcast_event({"type": "feed_cleared"})
    logger.info("Activity feed cleared by user")
    return jsonify({"status": "cleared", "count": 0})

@app.route("/api/latest_frame")
def get_latest_frame():
    """Returns the most recent single JPEG frame from camera memory buffer."""
    with camera_relay.lock:
        frame = camera_relay.latest_frame
    if frame:
        resp = Response(frame, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    return "No frame", 404

@app.route("/video_feed")
def video_feed():
    """Streams live MJPEG frames buffered in server memory to all connected browsers/phones with zero-latency event wakeup."""
    def generate():
        last_frame_id = 0
        start_wait = time.time()
        while time.time() - start_wait < 75:
            if not is_streaming_requested():
                break
            with camera_relay.condition:
                if camera_relay.frame_id == last_frame_id:
                    camera_relay.condition.wait(timeout=0.4)
                frame = camera_relay.latest_frame
                fid = camera_relay.frame_id
            if frame and fid != last_frame_id:
                last_frame_id = fid
                yield (b"--123456789000000000000987654321\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                       frame + b"\r\n")

    resp = Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=123456789000000000000987654321"
    )
    resp.headers["Cache-Control"] = "no-cache, private, no-store, must-revalidate, no-transform"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Connection"] = "close"
    return resp

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
                transform: translateZ(0);
                will-change: transform;
                backface-visibility: hidden;
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
            .action-btn.streaming-active {
                border-color: #ef4444 !important;
                background: rgba(239, 68, 68, 0.22) !important;
                animation: livePulse 1.5s infinite;
            }
            .action-btn.streaming-active .label {
                color: #fca5a5 !important;
                font-weight: 700 !important;
            }
            @keyframes livePulse {
                0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
                50% { box-shadow: 0 0 15px 3px rgba(239, 68, 68, 0.6); }
            }

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
            .clear-feed-btn {
                background: rgba(239, 68, 68, 0.12);
                border: 1px solid rgba(239, 68, 68, 0.35);
                color: #fca5a5;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
                border-radius: 8px;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                gap: 4px;
                transition: all 0.15s ease;
            }
            .clear-feed-btn:active {
                transform: scale(0.93);
                background: rgba(239, 68, 68, 0.28);
            }
            .cam-tool-btn {
                background: rgba(15, 23, 42, 0.8);
                backdrop-filter: blur(8px);
                border: 1px solid rgba(255, 255, 255, 0.22);
                color: #fff;
                width: 32px;
                height: 32px;
                border-radius: 8px;
                display: flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                font-size: 15px;
                transition: all 0.15s ease;
            }
            .cam-tool-btn:active {
                transform: scale(0.92);
                background: rgba(14, 165, 233, 0.5);
            }
            .item-badge.ring {
                background: rgba(239, 68, 68, 0.18);
                color: #fca5a5;
                border: 1px solid rgba(239, 68, 68, 0.35);
            }
            .item-badge.motion {
                background: rgba(245, 158, 11, 0.18);
                color: #fcd34d;
                border: 1px solid rgba(245, 158, 11, 0.35);
            }

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
                    <h1 class="app-title">🏡 Front Door</h1>
                    <div class="app-subtitle" id="connection-subtitle">✨ Safe & Protected • AI Monitoring</div>
                </div>
            </div>
            <div class="app-actions">
                <button class="icon-btn" onclick="openTunnelModal()" id="btn-tunnel" title="Worldwide Remote Access (Cloudflare)">🌐</button>
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
                    <img id="cam-feed" class="camera-stream" 
                         src="{{ '/video_feed' if is_active_stream else ('/photo/' + latest_visit['photo_path'] if latest_visit else '') }}" 
                         onerror="handleStreamError(this)" 
                         alt="Camera Feed"
                         style="{{ '' if (is_active_stream or latest_visit) else 'display: none;' }}">
                    <div id="cam-placeholder" style="text-align:center; padding: 40px 20px; color: var(--text-muted); {{ 'display: none;' if (is_active_stream or latest_visit) else '' }}">
                        <div style="font-size: 36px; margin-bottom: 8px;">📹</div>
                        <div style="font-size: 14px; font-weight: 600; color: #fff;">Camera Standby</div>
                        <div style="font-size: 12px; margin-top: 4px;">Press doorbell button or click Watch Live</div>
                    </div>

                    <!-- Viewport Overlays -->
                    <div class="viewport-overlay-top">
                        <div class="live-tag" style="background: {{ 'rgba(239, 68, 68, 0.95)' if is_active_stream else 'rgba(100, 116, 139, 0.7)' }};">
                            <div class="live-dot"></div>
                            <span id="live-label">{{ '● LIVE STREAM' if is_active_stream else 'STANDBY' }}</span>
                        </div>
                        <div class="timestamp-overlay" id="live-clock">--:--:--</div>
                    </div>

                    <div class="viewport-overlay-bottom">
                        <div class="cam-meta" id="cam-meta-text">OV3660 HD • AI Face Recognition</div>
                        <div style="display: flex; gap: 6px;">
                            <button class="cam-tool-btn" onclick="saveCurrentSnapshot(event)" title="Save Photo to Device">📸</button>
                            <button class="cam-tool-btn" onclick="toggleFullscreen(event)" title="Fullscreen View">⛶</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Live Stream On-Demand Controls -->
            <div style="margin-bottom: 12px; display: flex; gap: 10px;">
                <button id="btn-watch-live" class="action-btn" onclick="toggleWatchLive()" style="flex: 2; flex-direction: row; justify-content: center; padding: 13px 16px; border: 1px solid var(--accent); background: rgba(14, 165, 233, 0.15); border-radius: 14px;">
                    <span class="icon" id="watch-live-icon" style="font-size: 20px;">📹</span>
                    <span class="label" id="watch-live-label" style="font-size: 13px; font-weight: 700; color: var(--accent-light);">Watch Live Stream</span>
                </button>
                <button class="action-btn" id="btn-sound" onclick="toggleChimeSound()" style="flex: 1; flex-direction: row; justify-content: center; padding: 13px 10px; border-radius: 14px;">
                    <span class="icon">🔊</span>
                    <span class="label">Sound</span>
                </button>
            </div>

            <!-- Quick Action Controls -->
            <div class="quick-actions">
                <button class="action-btn" onclick="testDoorbellChime()">
                    <span class="icon">🔔</span>
                    <span class="label">Test Ring</span>
                </button>
                <button class="action-btn" onclick="openConfigModal()">
                    <span class="icon">⚙️</span>
                    <span class="label">Settings</span>
                </button>
                <button class="action-btn" onclick="window.open('https://ntfy.sh/{{ ntfy_topic }}', '_blank')">
                    <span class="icon">📲</span>
                    <span class="label">Phone App</span>
                </button>
                <button class="action-btn" id="btn-pir" onclick="togglePirAlerts()">
                    <span class="icon" id="pir-icon">{{ '🚶' if pir_enabled else '🛑' }}</span>
                    <span class="label" id="pir-label">{{ 'Motion: ON' if pir_enabled else 'Motion: OFF' }}</span>
                </button>
            </div>

            <!-- Activity / Event Timeline -->
            <div class="section-header">
                <h2 class="section-title">Activity Feed</h2>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <div class="feed-count">{{ visits|length }} events</div>
                    <button class="clear-feed-btn" onclick="clearActivityFeed()" title="Clear Activity Feed">🗑️ Clear</button>
                </div>
            </div>

            <div class="timeline-list" id="timeline-container">
                {% for v in visits %}
                {% set is_known = ('Unknown' not in v['name'] and 'Visitor' != v['name']) %}
                <div class="timeline-item" data-visit-id="{{ v['id'] }}" onclick="openPhotoModal('/photo/{{ v['photo_path'] }}', '{{ v['name'] }}', '{{ v['timestamp'] }}', '{{ v['trigger_source'] }}')">
                    <img class="item-thumb" src="/photo/{{ v['photo_path'] }}" alt="Visitor thumbnail" loading="lazy">
                    <div class="item-info">
                        <div class="item-name {{ 'known' if is_known else 'unknown' }}">
                            {{ ('💚 Family: ' + v['name']) if is_known else '👤 Visitor / Guest' }}
                        </div>
                        <div class="item-meta">
                            <span class="item-badge {{ 'ring' if v['trigger_source'] == 'BUTTON' else 'motion' }}">
                                {{ '🔔 RING' if v['trigger_source'] == 'BUTTON' else '🚶 MOTION' }}
                            </span>
                            <span class="item-time" data-raw-time="{{ v['timestamp'] }}">{{ v['timestamp'] }}</span>
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
                    <div style="display: flex; gap: 8px; margin-top: 14px;">
                        <button class="modal-close-btn" style="background: var(--accent); border: none; margin-top: 0;" onclick="downloadModalPhoto()">📸 Save Photo</button>
                        <button class="modal-close-btn" style="margin-top: 0;" onclick="closePhotoModal()">Close</button>
                    </div>
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

        <!-- Remote Access (Cloudflare Tunnel) Modal -->
        <div class="modal-bg" id="tunnel-modal" onclick="closeTunnelModal(event)">
            <div class="modal-card" onclick="event.stopPropagation()">
                <div class="modal-body">
                    <div style="display:flex; align-items:center; gap:10px; margin-bottom:12px;">
                        <span style="font-size:24px;">🌐</span>
                        <div>
                            <h3 style="margin:0; font-size:17px;">Global Remote Access</h3>
                            <div style="font-size:12px; color:var(--text-muted);">Cloudflare Zero-Trust Secure Tunnel</div>
                        </div>
                    </div>
                    <p style="font-size:13px; color:var(--text-muted); line-height:1.4;">
                        Access your DoorCam live video stream, chime, and alerts anywhere in the world on 4G/5G mobile data with zero port-forwarding.
                    </p>
                    <div style="background:var(--bg); border:1px solid var(--border); border-radius:10px; padding:12px; margin:12px 0;">
                        <label style="font-size:11px; font-weight:700; color:var(--accent-light); text-transform:uppercase; letter-spacing:0.5px;">Your Public HTTPS URL</label>
                        <div id="tunnel-url-display" style="font-family:monospace; font-size:13px; color:#fff; word-break:break-all; margin:6px 0;">
                            {{ tunnel_url or 'Initializing secure tunnel...' }}
                        </div>
                    </div>
                    <div style="display:flex; gap:8px; margin-bottom:8px;">
                        <button class="modal-close-btn" style="background:var(--accent); border:none; margin:0; flex:1;" onclick="copyTunnelUrl()">📋 Copy Link</button>
                        <button class="modal-close-btn" style="background:var(--surface-card); border:1px solid var(--border); margin:0; flex:1;" onclick="openTunnelUrl()">🚀 Open URL</button>
                    </div>
                    <button class="modal-close-btn" onclick="closeTunnelModal()">Close</button>
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

            // Human-Friendly Relative & Local Timezone Formatter
            function formatDisplayTime(rawTs) {
                if (!rawTs) return '';
                const str = String(rawTs).trim();
                let dateObj = null;

                // If legacy UTC "YYYY-MM-DD HH:MM:SS"
                if (/^\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}$/.test(str)) {
                    try {
                        dateObj = new Date(str.replace(' ', 'T') + 'Z');
                    } catch (e) {}
                }
                // If local IST "YYYY-MM-DD HH:MM:SS AM/PM"
                else if (/^\\d{4}-\\d{2}-\\d{2}\\s/.test(str)) {
                    try {
                        const parts = str.split(' ');
                        if (parts.length >= 3) {
                            const [y, m, d] = parts[0].split('-').map(Number);
                            const timeParts = parts[1].split(':').map(Number);
                            let hr = timeParts[0];
                            const min = timeParts[1];
                            const sec = timeParts[2] || 0;
                            const isPm = parts[2].toUpperCase() === 'PM';
                            if (isPm && hr < 12) hr += 12;
                            if (!isPm && hr === 12) hr = 0;
                            dateObj = new Date(y, m - 1, d, hr, min, sec);
                        }
                    } catch (e) {}
                }

                if (dateObj && !isNaN(dateObj.getTime())) {
                    const now = new Date();
                    const diffSec = Math.floor((now.getTime() - dateObj.getTime()) / 1000);
                    const timeStr = dateObj.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });

                    if (diffSec >= 0 && diffSec < 45) {
                        return 'Just now';
                    }
                    if (diffSec >= 45 && diffSec < 3600) {
                        const mins = Math.floor(diffSec / 60);
                        return mins + ' min' + (mins === 1 ? '' : 's') + ' ago';
                    }
                    const isToday = now.toDateString() === dateObj.toDateString();
                    if (isToday) {
                        return 'Today, ' + timeStr;
                    }
                    const yesterday = new Date(now);
                    yesterday.setDate(yesterday.getDate() - 1);
                    if (yesterday.toDateString() === dateObj.toDateString()) {
                        return 'Yesterday, ' + timeStr;
                    }
                    return dateObj.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ', ' + timeStr;
                }
                return str;
            }

            // Viewport Snapshot Save to Gallery
            function saveCurrentSnapshot(e) {
                if (e) e.stopPropagation();
                const now = new Date();
                const filename = 'DoorCam_' + now.getFullYear() + '-' + 
                                 String(now.getMonth()+1).padStart(2, '0') + '-' + 
                                 String(now.getDate()).padStart(2, '0') + '_' + 
                                 String(now.getHours()).padStart(2, '0') + '-' + 
                                 String(now.getMinutes()).padStart(2, '0') + '-' + 
                                 String(now.getSeconds()).padStart(2, '0') + '.jpg';
                
                const src = isStreamingLive ? ('/api/latest_frame?' + Date.now()) : (currentSnapshotUrl || '/api/latest_frame');
                fetch(src)
                    .then(r => r.blob())
                    .then(blob => {
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = filename;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                        showMiniToast('📸 Photo saved to your device!');
                    })
                    .catch(() => {
                        window.open(src, '_blank');
                    });
            }

            // Fullscreen Viewport Toggle
            function toggleFullscreen(e) {
                if (e) e.stopPropagation();
                const card = document.querySelector('.cctv-card');
                if (!card) return;
                if (!document.fullscreenElement && !document.webkitFullscreenElement) {
                    if (card.requestFullscreen) {
                        card.requestFullscreen().catch(() => {});
                    } else if (card.webkitRequestFullscreen) {
                        card.webkitRequestFullscreen();
                    }
                } else {
                    if (document.exitFullscreen) {
                        document.exitFullscreen().catch(() => {});
                    } else if (document.webkitExitFullscreen) {
                        document.webkitExitFullscreen();
                    }
                }
            }

            // Download Photo from Modal
            function downloadModalPhoto() {
                const img = document.getElementById('modal-img');
                if (!img || !img.src) return;
                const now = new Date();
                const filename = 'DoorCam_Visitor_' + now.toISOString().slice(0, 10) + '.jpg';
                fetch(img.src)
                    .then(r => r.blob())
                    .then(blob => {
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = filename;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                        showMiniToast('📸 Photo saved to your device!');
                    })
                    .catch(() => {
                        window.open(img.src, '_blank');
                    });
            }

            // Mini Toast Popup Notification
            function showMiniToast(msg) {
                let toast = document.getElementById('mini-toast');
                if (!toast) {
                    toast = document.createElement('div');
                    toast.id = 'mini-toast';
                    toast.style.cssText = 'position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%) translateY(20px); background: rgba(15, 23, 42, 0.95); border: 1px solid var(--accent); color: #fff; padding: 10px 22px; border-radius: 30px; font-size: 13px; font-weight: 600; z-index: 999; box-shadow: 0 10px 30px rgba(0,0,0,0.6); backdrop-filter: blur(10px); pointer-events: none; transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1); opacity: 0;';
                    document.body.appendChild(toast);
                }
                toast.textContent = msg;
                toast.style.opacity = '1';
                toast.style.transform = 'translateX(-50%) translateY(0)';
                setTimeout(() => {
                    toast.style.opacity = '0';
                    toast.style.transform = 'translateX(-50%) translateY(20px)';
                }, 2600);
            }

            // Web Audio API Synthesizer for Doorbell Chime
            let soundEnabled = true;
            let audioCtx = null;

            // Unlock AudioContext on first user tap/click to comply with browser autoplay policy
            const unlockAudio = () => {
                try {
                    if (!audioCtx) {
                        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    }
                    if (audioCtx && audioCtx.state === 'suspended') {
                        audioCtx.resume();
                    }
                } catch (e) {}
                document.removeEventListener('click', unlockAudio);
                document.removeEventListener('touchstart', unlockAudio);
            };
            document.addEventListener('click', unlockAudio);
            document.addEventListener('touchstart', unlockAudio);

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
                showMiniToast('🔔 Doorbell chime played!');
            }

            let latestVisitId = {{ visits[0]['id'] if visits else 0 }};
            let isStreamingLive = false;
            let currentSnapshotUrl = '{{ ("/photo/" + latest_visit["photo_path"]) if latest_visit else "" }}';
            let watchLiveRequested = false;
            let frameFallbackInterval = null;
            let sseInstance = null;
            let toastTimeout = null;

            // Real-time Event Stream (SSE)
            function connectEventStream() {
                if (sseInstance) {
                    try { sseInstance.close(); } catch (e) {}
                }
                const orb = document.getElementById('status-orb');
                sseInstance = new EventSource('/api/events/stream');

                sseInstance.onopen = () => {
                    if (orb) {
                        orb.style.background = 'var(--success)';
                        orb.style.boxShadow = '0 0 10px var(--success)';
                    }
                };

                sseInstance.onmessage = (event) => {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'visitor') {
                            handleIncomingVisitor(data, true);
                        } else if (data.type === 'visitor_update') {
                            updateVisitorInfo(data);
                        } else if (data.type === 'feed_cleared') {
                            handleFeedCleared();
                        }
                    } catch (e) {}
                };

                sseInstance.onerror = () => {
                    if (orb) {
                        orb.style.background = 'var(--danger)';
                        orb.style.boxShadow = '0 0 10px var(--danger)';
                    }
                    try { sseInstance.close(); } catch (e) {}
                    setTimeout(connectEventStream, 3500);
                };
            }
            connectEventStream();

            function handleIncomingVisitor(v, isLiveAlert = true) {
                if (!v) return;
                const visitId = v.visit_id || v.id || 0;
                if (visitId && visitId <= latestVisitId && document.querySelector(`[data-visit-id="${visitId}"]`)) {
                    return; // Already processed
                }
                if (visitId > latestVisitId) {
                    latestVisitId = visitId;
                }

                // 1. Update camera snapshot & viewport immediately
                currentSnapshotUrl = v.photo_url;
                const camFeed = document.getElementById('cam-feed');
                const placeholder = document.getElementById('cam-placeholder');
                if (camFeed && !isStreamingLive) {
                    camFeed.src = v.photo_url;
                    camFeed.style.display = 'block';
                }
                if (placeholder) {
                    placeholder.style.display = 'none';
                }

                const isKnown = v.name && !v.name.includes('Unknown') && v.name !== 'Visitor';
                const friendlyName = isKnown ? ('💚 Family: ' + v.name) : '👤 Visitor / Guest';
                const greetingTitle = isKnown ? ('👋 ' + v.name + ' is at the door!') : (v.trigger === 'BUTTON' ? '🔔 Doorbell Ringing!' : '👤 Visitor at front door');
                const displayTs = formatDisplayTime(v.timestamp);

                // 2. Play sound and vibrate if incoming live alert
                if (isLiveAlert) {
                    playChime();
                    if ('vibrate' in navigator) {
                        navigator.vibrate([200, 100, 200, 100, 400]);
                    }

                    // 3. Show In-App Toast Banner with 8s auto-dismiss
                    const toast = document.getElementById('alert-toast');
                    const toastImg = document.getElementById('toast-img');
                    const toastTitle = document.getElementById('toast-title');
                    const toastTime = document.getElementById('toast-time');
                    if (toast && toastImg && toastTitle && toastTime) {
                        toastImg.src = v.photo_url;
                        toastTitle.textContent = greetingTitle;
                        toastTime.textContent = (v.trigger === 'BUTTON' ? 'Doorbell Ring' : 'Motion') + ' • ' + (displayTs || 'Just now');
                        toast.style.display = 'block';
                        clearTimeout(toastTimeout);
                        toastTimeout = setTimeout(() => {
                            toast.style.display = 'none';
                        }, 8000);
                    }

                    // 4. Trigger OS Notification
                    if (Notification.permission === 'granted') {
                        new Notification("🔔 DoorCam: " + greetingTitle, {
                            body: "Front door activity detected",
                            icon: v.photo_url
                        });
                    }
                }

                // 5. Prepend to timeline list smoothly
                const container = document.getElementById('timeline-container');
                const noMsg = document.getElementById('no-events-msg');
                if (noMsg) noMsg.remove();

                if (container) {
                    const badgeClass = v.trigger === 'BUTTON' ? 'ring' : 'motion';
                    const badgeLabel = v.trigger === 'BUTTON' ? '🔔 RING' : '🚶 MOTION';
                    const item = document.createElement('div');
                    item.className = 'timeline-item';
                    if (visitId) item.setAttribute('data-visit-id', visitId);
                    item.onclick = () => openPhotoModal(v.photo_url, v.name, displayTs, v.trigger || 'VISITOR');
                    item.innerHTML = `
                        <img class="item-thumb" src="${v.photo_url}" alt="Thumbnail">
                        <div class="item-info">
                            <div class="item-name ${isKnown ? 'known' : 'unknown'}">${friendlyName}</div>
                            <div class="item-meta">
                                <span class="item-badge ${badgeClass}">${badgeLabel}</span>
                                <span class="item-time" data-raw-time="${v.timestamp}">${displayTs}</span>
                            </div>
                        </div>
                        <div style="color: var(--text-muted); font-size: 18px;">›</div>
                    `;
                    container.prepend(item);

                    // 6. Update feed counter live
                    const feedCount = document.querySelector('.feed-count');
                    if (feedCount) {
                        const total = container.querySelectorAll('.timeline-item').length;
                        feedCount.textContent = total + ' event' + (total === 1 ? '' : 's');
                    }
                }

                // 7. If button triggered, live stream starts automatically on ESP32 - sync immediately
                if (v.trigger === 'BUTTON') {
                    syncLiveState();
                }
            }

            function updateVisitorInfo(data) {
                if (!data || !data.visit_id) return;
                const visitId = data.visit_id;
                const newName = data.name || 'Visitor';
                const isKnown = newName && !newName.includes('Unknown') && newName !== 'Visitor' && !newName.includes('...');
                const friendlyName = isKnown ? ('💚 Family: ' + newName) : '👤 Visitor / Guest';
                const greetingTitle = isKnown ? ('👋 ' + newName + ' is at the door!') : '👤 Visitor at front door';

                // 1. Update timeline item if present
                const item = document.querySelector(`[data-visit-id="${visitId}"]`);
                if (item) {
                    const nameEl = item.querySelector('.item-name');
                    if (nameEl) {
                        nameEl.textContent = friendlyName;
                        nameEl.className = 'item-name ' + (isKnown ? 'known' : 'unknown');
                    }
                }

                // 2. Update toast banner if currently displaying this visit
                const toastTitle = document.getElementById('toast-title');
                const toast = document.getElementById('alert-toast');
                if (toast && toast.style.display !== 'none' && toastTitle) {
                    toastTitle.textContent = greetingTitle;
                }
            }

            function dismissToast() {
                const toast = document.getElementById('alert-toast');
                if (toast) toast.style.display = 'none';
                clearTimeout(toastTimeout);
            }

            function clearActivityFeed() {
                if (!confirm("Are you sure you want to clear the entire activity feed?")) {
                    return;
                }
                fetch('/api/feed/clear', { method: 'POST' })
                    .then(r => r.json())
                    .then(() => {
                        handleFeedCleared();
                    })
                    .catch(err => {
                        alert("Could not clear feed: " + err);
                    });
            }

            function handleFeedCleared() {
                latestVisitId = 0;
                const container = document.getElementById('timeline-container');
                if (container) {
                    container.innerHTML = `
                        <div id="no-events-msg" style="text-align: center; padding: 36px 12px; color: var(--text-muted);">
                            <div style="font-size: 32px; margin-bottom: 8px;">🛡️</div>
                            <div style="font-size: 14px; font-weight: 500;">No activity recorded yet</div>
                            <div style="font-size: 12px; margin-top: 4px;">PIR motion and doorbell button presses will appear here instantly</div>
                        </div>
                    `;
                }
                const feedCount = document.querySelector('.feed-count');
                if (feedCount) {
                    feedCount.textContent = '0 events';
                }
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

            // Modal Handlers
            function openPhotoModal(photoUrl, name, time, trigger) {
                const isKnown = name && !name.includes('Unknown') && name !== 'Visitor';
                const friendlyName = isKnown ? ('💚 Family: ' + name) : '👤 Visitor / Guest';
                const triggerLabel = (trigger === 'BUTTON' || trigger === 'RING') ? '🔔 Doorbell Ring' : '🚶 Motion Alert';
                document.getElementById('modal-img').src = photoUrl;
                document.getElementById('modal-name').textContent = friendlyName;
                document.getElementById('modal-meta').textContent = triggerLabel + " • " + formatDisplayTime(time);
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

            function openTunnelModal() {
                updateTunnelStatus();
                document.getElementById('tunnel-modal').style.display = 'flex';
            }
            function closeTunnelModal(e) {
                if (!e || e.target === document.getElementById('tunnel-modal') || e.target.classList.contains('modal-close-btn')) {
                    document.getElementById('tunnel-modal').style.display = 'none';
                }
            }

            let currentTunnelUrl = "{{ tunnel_url or '' }}";

            function updateTunnelStatus() {
                fetch('/api/tunnel')
                    .then(r => r.json())
                    .then(data => {
                        if (data.public_url) {
                            currentTunnelUrl = data.public_url;
                            const el = document.getElementById('tunnel-url-display');
                            if (el) el.textContent = currentTunnelUrl;
                            const btn = document.getElementById('btn-tunnel');
                            if (btn) btn.style.borderColor = 'var(--success)';
                        }
                    })
                    .catch(() => {});
            }

            function copyTunnelUrl() {
                if (currentTunnelUrl && currentTunnelUrl.startsWith('https://')) {
                    navigator.clipboard.writeText(currentTunnelUrl).then(() => {
                        alert("✅ Copied Cloudflare URL to clipboard!\\nOpen this link on your phone over 4G/5G.");
                    });
                } else {
                    alert("Tunnel is initializing. Please wait a few seconds...");
                }
            }

            function openTunnelUrl() {
                if (currentTunnelUrl && currentTunnelUrl.startsWith('https://')) {
                    window.open(currentTunnelUrl, '_blank');
                } else {
                    alert("Tunnel is initializing. Please wait a few seconds...");
                }
            }

            // Live Cloud Stream & State Controller (Zero-refresh real-time sync)
            function startFrameFallback() {
                if (frameFallbackInterval) return;
                frameFallbackInterval = setInterval(() => {
                    if (!isStreamingLive) {
                        clearInterval(frameFallbackInterval);
                        frameFallbackInterval = null;
                        return;
                    }
                    const img = document.getElementById('cam-feed');
                    if (img) {
                        const tempImg = new Image();
                        tempImg.onload = () => {
                            if (isStreamingLive) img.src = tempImg.src;
                        };
                        tempImg.src = '/api/latest_frame?' + Date.now();
                    }
                }, 120);
            }

            function stopFrameFallback() {
                if (frameFallbackInterval) {
                    clearInterval(frameFallbackInterval);
                    frameFallbackInterval = null;
                }
            }

            function handleStreamError(img) {
                if (isStreamingLive || watchLiveRequested) {
                    startFrameFallback();
                } else {
                    const label = document.getElementById('live-label');
                    if (label) label.textContent = 'STANDBY';
                }
            }

            // Screen Wake Lock API for Mobile (keeps display on during live video monitoring)
            let screenWakeLock = null;

            async function acquireWakeLock() {
                try {
                    if ('wakeLock' in navigator && !screenWakeLock) {
                        screenWakeLock = await navigator.wakeLock.request('screen');
                        screenWakeLock.addEventListener('release', () => {
                            screenWakeLock = null;
                        });
                    }
                } catch (e) {}
            }

            function releaseWakeLock() {
                if (screenWakeLock) {
                    screenWakeLock.release().catch(() => {});
                    screenWakeLock = null;
                }
            }

            let lastToggleTime = 0;
            let userStoppedUntil = 0;

            function toggleWatchLive() {
                const now = Date.now();
                const btn = document.getElementById('btn-watch-live');
                const btnIcon = document.getElementById('watch-live-icon');
                const btnLabel = document.getElementById('watch-live-label');
                const label = document.getElementById('live-label');
                const meta = document.getElementById('cam-meta-text');
                const img = document.getElementById('cam-feed');

                if (watchLiveRequested && !isStreamingLive) {
                    if (now - lastToggleTime < 2500) {
                        return;
                    }
                }
                lastToggleTime = now;

                if (isStreamingLive || (btn && btn.classList.contains('streaming-active'))) {
                    // User clicked STOP - activate 4.5s guard to ignore any in-flight frames
                    userStoppedUntil = Date.now() + 4500;
                    watchLiveRequested = false;
                    isStreamingLive = false;
                    releaseWakeLock();
                    stopFrameFallback();
                    if (btn) btn.classList.remove('streaming-active');
                    if (btnIcon) btnIcon.textContent = '📹';
                    if (btnLabel) {
                        btnLabel.textContent = 'Watch Live Stream';
                        btnLabel.style.color = 'var(--accent-light)';
                    }
                    if (label) {
                        label.textContent = 'STANDBY';
                        label.parentElement.style.background = 'rgba(100, 116, 139, 0.7)';
                    }
                    if (meta) meta.textContent = 'OV3660 HD • AI Face Recognition';
                    if (img) {
                        img.src = currentSnapshotUrl || '';
                        if (!currentSnapshotUrl) img.style.display = 'none';
                    }
                    fetch('/api/stream/stop', { method: 'POST' }).finally(() => syncLiveState());
                } else {
                    // User clicked START - clear stop guard
                    userStoppedUntil = 0;
                    watchLiveRequested = true;
                    if (btn) btn.classList.add('streaming-active');
                    if (btnIcon) btnIcon.textContent = '⏳';
                    if (btnLabel) {
                        btnLabel.textContent = 'Connecting Camera...';
                        btnLabel.style.color = '#fca5a5';
                    }
                    if (label) {
                        label.textContent = '● CONNECTING...';
                        label.parentElement.style.background = 'rgba(245, 158, 11, 0.95)';
                    }
                    if (meta) meta.textContent = 'Signaling ESP32 camera over cloud...';
                    fetch('/api/stream/start', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ duration: 60 })
                    })
                    .then(r => r.json())
                    .then(() => syncLiveState())
                    .catch(err => {
                        watchLiveRequested = false;
                        syncLiveState();
                        alert('Could not start live stream: ' + err);
                    });
                }
            }

            function syncLiveState() {
                if (Date.now() < userStoppedUntil) {
                    return; // Ignore any delayed in-flight frames after explicit STOP
                }
                fetch('/api/live_state')
                    .then(r => r.json())
                    .then(data => {
                        // 1. Sync Live Stream State
                        const stream = data.stream || {};
                        const img = document.getElementById('cam-feed');
                        const placeholder = document.getElementById('cam-placeholder');
                        const label = document.getElementById('live-label');
                        const meta = document.getElementById('cam-meta-text');
                        const btn = document.getElementById('btn-watch-live');
                        const btnIcon = document.getElementById('watch-live-icon');
                        const btnLabel = document.getElementById('watch-live-label');

                        if (stream.active) {
                            acquireWakeLock();
                            if (!isStreamingLive) {
                                isStreamingLive = true;
                                if (img) {
                                    img.src = '/video_feed?' + Date.now();
                                    img.style.display = 'block';
                                }
                                if (placeholder) placeholder.style.display = 'none';
                            }
                            if (label) {
                                label.textContent = '● LIVE (' + (stream.fps > 0 ? stream.fps + ' FPS' : 'HD') + ')';
                                label.parentElement.style.background = 'rgba(239, 68, 68, 0.95)';
                            }
                            if (meta) meta.textContent = 'Live Cloud Stream • ' + (stream.fps || 15) + ' FPS';
                            if (btn) {
                                btn.classList.add('streaming-active');
                                if (btnIcon) btnIcon.textContent = '⏹️';
                                if (btnLabel) {
                                    const secText = stream.remaining_sec ? ('Stop (' + stream.remaining_sec + 's)') : 'Stop Live Stream';
                                    btnLabel.textContent = secText;
                                    btnLabel.style.color = '#fca5a5';
                                }
                            }
                        } else if (stream.demand || watchLiveRequested) {
                            // Connecting phase
                            if (label) {
                                label.textContent = '● CONNECTING...';
                                label.parentElement.style.background = 'rgba(245, 158, 11, 0.95)';
                            }
                            if (meta) meta.textContent = 'Connecting to camera stream...';
                            if (btn) {
                                btn.classList.add('streaming-active');
                                if (btnIcon) btnIcon.textContent = '⏳';
                                if (btnLabel) {
                                    btnLabel.textContent = 'Connecting...';
                                    btnLabel.style.color = '#fca5a5';
                                }
                            }
                        } else {
                            // Standby
                            releaseWakeLock();
                            if (isStreamingLive) {
                                isStreamingLive = false;
                                stopFrameFallback();
                                if (img && currentSnapshotUrl) {
                                    img.src = currentSnapshotUrl;
                                }
                            }
                            if (label) {
                                label.textContent = 'STANDBY';
                                label.parentElement.style.background = 'rgba(100, 116, 139, 0.7)';
                            }
                            if (meta) meta.textContent = 'OV3660 HD • AI Face Recognition';
                            if (btn) {
                                watchLiveRequested = false;
                                btn.classList.remove('streaming-active');
                                if (btnIcon) btnIcon.textContent = '📹';
                                if (btnLabel) {
                                    btnLabel.textContent = 'Watch Live Stream';
                                    btnLabel.style.color = 'var(--accent-light)';
                                }
                            }
                        }

                        // 2. Sync Latest Visitor (Live Event Auto-Refresh)
                        if (data.latest_visit && data.latest_visit.id > latestVisitId) {
                            handleIncomingVisitor(data.latest_visit, true);
                        } else if (data.latest_visit && data.latest_visit.id === latestVisitId) {
                            updateVisitorInfo({ visit_id: data.latest_visit.id, name: data.latest_visit.name });
                        } else if (!data.latest_visit && latestVisitId > 0) {
                            handleFeedCleared();
                        }

                        // 3. Sync PIR Motion Alert Button State across all devices live
                        if (typeof data.pir_enabled !== 'undefined') {
                            const pirIcon = document.getElementById('pir-icon');
                            const pirLabel = document.getElementById('pir-label');
                            const pirBtn = document.getElementById('btn-pir');
                            if (pirIcon) pirIcon.textContent = data.pir_enabled ? '🚶' : '🛑';
                            if (pirLabel) pirLabel.textContent = data.pir_enabled ? 'Motion: ON' : 'Motion: OFF';
                            if (pirBtn) pirBtn.classList.toggle('active', data.pir_enabled);
                        }
                    })
                    .catch(() => {});
            }

            // High-frequency live state sync (every 1.5s)
            setInterval(syncLiveState, 1500);
            syncLiveState();

            // Format all server-rendered timestamps to user local timezone
            try {
                document.querySelectorAll('.item-time').forEach(el => {
                    el.textContent = formatDisplayTime(el.textContent);
                });
            } catch (e) {}

            // Tab visibility change: immediately sync when user returns to tab
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible') {
                    syncLiveState();
                    if (!sseInstance || sseInstance.readyState === EventSource.CLOSED) {
                        connectEventStream();
                    }
                    if (isStreamingLive) {
                        acquireWakeLock();
                    }
                } else {
                    releaseWakeLock();
                }
            });

            function togglePirAlerts() {
                fetch('/api/pir/toggle', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => {
                        const icon = document.getElementById('pir-icon');
                        const label = document.getElementById('pir-label');
                        const pirBtn = document.getElementById('btn-pir');
                        if (icon) icon.textContent = data.enabled ? '🚶' : '🛑';
                        if (label) label.textContent = data.enabled ? 'Motion: ON' : 'Motion: OFF';
                        if (pirBtn) pirBtn.classList.toggle('active', data.enabled);
                    });
            }

            function saveConfig() {
                const ip = document.getElementById('input-cam-ip').value;
                const topic = document.getElementById('input-ntfy-topic').value;
                fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({esp32_ip: ip, ntfy_topic: topic})
                }).then(() => {
                    closeConfigModal();
                    const btnNtfy = document.querySelector('button[onclick*="ntfy.sh"]');
                    if (btnNtfy) {
                        btnNtfy.setAttribute('onclick', "window.open('https://ntfy.sh/" + topic + "', '_blank')");
                    }
                    alert('✅ Settings saved successfully!');
                });
            }
        </script>
    </body>
    </html>
    """
    resp = make_response(render_template_string(
        html,
        visits=visits,
        known_count=known_count,
        esp32_ip=config.ESP32_CAM_IP,
        ntfy_topic=config.NTFY_TOPIC,
        tunnel_url=tunnel.get_public_url(),
        latest_visit=latest_visit,
        is_active_stream=camera_relay.is_active(),
        pir_enabled=pir_alerts_enabled
    ))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

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

    try:
        trigger_source = request.args.get("trigger", request.form.get("trigger", "PIR")).upper()
        now = datetime.datetime.now(visitor_log.LOCAL_TIMEZONE)
        current_time_sec = time.time()

        if trigger_source == "PIR" and not pir_alerts_enabled:
            logger.info("PIR motion trigger ignored (PIR disabled by user)")
            return jsonify({"status": "ignored", "reason": "PIR disabled"}), 200

        if trigger_source == "BUTTON":
            global ring_stream_expiry
            ring_stream_expiry = current_time_sec + 35.0

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
            # If triggered by physical button, pause 250ms so user's hand clears the lens and eyes focus on camera
            if trigger_source == "BUTTON":
                time.sleep(0.25)

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

        # Initial label for instant sub-millisecond alerting
        initial_name = "Doorbell Ringing..." if trigger_source == "BUTTON" else "Motion Detected..."

        # Log to database immediately
        visit_id = visitor_log.log_visit(
            name=initial_name,
            photo_path=photo_save_path,
            trigger_source=trigger_source,
            confidence=None
        )

        relative_photo_path = os.path.relpath(photo_save_path, config.BASE_DIR)
        photo_url = f"/photo/{relative_photo_path.replace(chr(92), '/')}"

        # ⚡ Broadcast to live Web App & PWA clients INSTANTLY (chime rings immediately!)
        event_payload = {
            "type": "visitor",
            "visit_id": visit_id,
            "name": initial_name,
            "trigger": trigger_source,
            "timestamp": now.strftime("%I:%M:%S %p"),
            "photo_url": photo_url
        }
        broadcast_event(event_payload)

        # Asynchronously run AI facial recognition and dispatch external alerts
        def _async_process_face_and_alerts():
            global last_notification_time
            rec_name = "Unknown Visitor"
            rec_conf = None
            try:
                rec_name, rec_conf = recognize_faces_in_image(photo_save_path)
                logger.info("Identified visit #%d: %s (dist: %s)", visit_id, rec_name, rec_conf)
            except Exception as fe:
                logger.error("Async face recognition exception: %s", fe)
                rec_name = "Visitor"

            # Update database record with identified face
            try:
                visitor_log.update_visit_face(visit_id, rec_name, rec_conf)
            except Exception as dbe:
                logger.error("Failed to update visit #%d in DB: %s", visit_id, dbe)

            # Broadcast update to web clients so name updates live without re-triggering sound
            broadcast_event({
                "type": "visitor_update",
                "visit_id": visit_id,
                "name": rec_name,
                "confidence": rec_conf
            })

            # Check cooldown before sending external push notifications
            ts_sec = time.time()
            time_since_last = ts_sec - last_notification_time
            if time_since_last >= config.NOTIFICATION_COOLDOWN_SECONDS:
                last_notification_time = ts_sec
                timestamp_str = now.strftime("%I:%M:%S %p • %d %b %Y")
                try:
                    notify.dispatch_all_alerts(photo_save_path, rec_name, trigger_source, timestamp_str)
                except Exception as ne:
                    logger.error("Notification dispatch error: %s", ne)
            else:
                logger.info("External alert suppressed by cooldown (elapsed: %.1fs < %ds)", 
                            time_since_last, config.NOTIFICATION_COOLDOWN_SECONDS)

        threading.Thread(target=_async_process_face_and_alerts, daemon=True).start()

        return jsonify({
            "status": "success",
            "visit_id": visit_id,
            "name": initial_name,
            "trigger": trigger_source,
            "photo_url": photo_url,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        logger.exception("Critical error handling /visitor: %s", e)
        return jsonify({"status": "error", "error": str(e)}), 500


if __name__ == "__main__":
    visitor_log.init_db()
    load_encodings()

    # Start Cloudflare Tunnel for worldwide mobile internet access
    if getattr(config, "ENABLE_REMOTE_TUNNEL", False):
        tunnel.start_tunnel(config.SERVER_PORT)

    logger.info("Starting DoorCam CCTV server on http://%s:%d", config.SERVER_HOST, config.SERVER_PORT)
    app.run(host=config.SERVER_HOST, port=config.SERVER_PORT, debug=False, threaded=True)

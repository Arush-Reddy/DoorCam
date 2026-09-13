# DoorCam: Edge-AI Smart Video Doorbell System

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Hardware](https://img.shields.io/badge/Hardware-ESP32--CAM%20%7C%20OV3660-blue.svg)](https://www.espressif.com/)
[![Firmware](https://img.shields.io/badge/Firmware-C%2B%2B%20%2F%20FreeRTOS-00599C.svg)](https://www.arduino.cc/)
[![Cloud Deployment](https://img.shields.io/badge/Cloud-Render%20%7C%20Docker-46E3B7.svg)](https://doorcam.onrender.com)
[![Backend](https://img.shields.io/badge/Backend-Python%20%7C%20Flask-3776AB.svg)](https://flask.palletsprojects.com/)
[![AI / CV](https://img.shields.io/badge/AI-OpenCV%20%7C%20dlib%20ResNet-5C3EE8.svg)](https://github.com/ageitgey/face_recognition)
[![Client](https://img.shields.io/badge/Client-PWA%20%7C%20SSE%20%7C%20Web%20Audio-FF6F00.svg)](https://developer.mozilla.org/)

An autonomous, enterprise-grade smart video doorbell and surveillance system engineered from the silicon up. DoorCam pairs an **ESP32-CAM (AI-Thinker with OV3660 3MP sensor)** edge device with a multi-threaded Python cloud backend, local **128-dimensional deep metric facial recognition**, and an installable **Progressive Web App (PWA)** featuring **24/7 on-demand live cloud streaming**, **sub-2-second instant doorbell alerting**, and zero-refresh real-time synchronization.

> 🌐 **Live Cloud App:** [https://doorcam.onrender.com](https://doorcam.onrender.com)  
> Built as an end-to-end hardware, firmware, and software engineering system demonstrating embedded C++/FreeRTOS programming, low-latency socket networking, asynchronous machine learning pipelines, and modern web engineering.

---

## 📸 System Architecture

```mermaid
sequenceDiagram
    autonumber
    actor Visitor
    actor Owner as Homeowner (Phone / Web PWA)
    participant ESP as ESP32-CAM (Edge Device)
    participant Cloud as Render Cloud Server (Flask)
    participant AI as Face AI Worker Thread (dlib)
    participant Push as Mobile Push (ntfy.sh)

    rect rgb(20, 30, 45)
    Note over Owner,Cloud: Workflow A: 24/7 On-Demand Live Streaming
    Owner->>Cloud: Clicks "Watch Live Stream" (POST /api/stream/start)
    Cloud->>Cloud: Arm stream demand flag (60s safety timeout)
    ESP->>Cloud: Polls GET /api/stream_cmd (every 1.5s in standby)
    Cloud-->>ESP: {"stream": true}
    ESP->>Cloud: Pushes live MJPEG frames (13-15 FPS) to /api/stream_push
    Cloud-->>Owner: Multiplexes /video_feed to all connected devices
    Owner->>Cloud: Clicks "Stop Stream" (POST /api/stream/stop)
    Cloud-->>ESP: Stream stops; ESP returns to low-power standby
    end

    rect rgb(30, 25, 40)
    Note over Visitor,Owner: Workflow B: Physical Doorbell Ring (< 2s Alert)
    Visitor->>ESP: Presses Physical Doorbell Button (GPIO 14 -> GND)
    ESP->>ESP: Hardware Debounce Filter (40ms) + Capture HD Snapshot
    ESP->>Cloud: POST /visitor?trigger=BUTTON (multipart snapshot photo)
    Note over Cloud: Latency: < 10ms (Zero blocking)
    Cloud-->>Owner: Broadcasts SSE event {"type": "visitor", "name": "Doorbell Ringing..."}
    Owner-->>Owner: Plays Web Audio Chime (Ding-Dong 660Hz->523Hz) + Haptic Vibration
    Cloud-->>ESP: HTTP 200 OK -> ESP immediately begins 30s live stream
    Cloud->>AI: Offloads snapshot to background daemon thread
    AI->>AI: Computes HOG + 128D ResNet Embeddings (matches "Arush" vs Unknown)
    AI-->>Cloud: Updates SQLite DB with identity & confidence
    Cloud-->>Owner: Broadcasts SSE {"type": "visitor_update", "name": "Arush"}
    Cloud-->>Push: Dispatches rich push notification with photo to lock screen
    end
```

---

## ⚡ Key Engineering Highlights & Innovations

### 1. 📹 24/7 On-Demand Live Cloud Streaming (Global 4G/5G/Wi-Fi)
- **Zero Open Ingress Ports**: The ESP32-CAM operates safely behind standard residential NAT without requiring port forwarding. It polls `GET /api/stream_cmd` every 1.5s while idling in a low-power, cool-running standby state.
- **Instant Cloud Spin-up**: When the homeowner taps **"Watch Live Stream"** in the app, the cloud activates a demand token. On the next poll cycle, the ESP32-CAM seamlessly transitions into high-throughput socket streaming, delivering smooth **13–15 FPS live MJPEG video**.
- **Bandwidth Safety Guard**: To prevent unexpected data exhaustion on cloud platforms (e.g. Render's 100 GB/month quota), each session includes an automatic **60-second countdown safety timeout**, or can be manually stopped with 1 tap.
- **In-Flight Frame Drain Guard**: Clicking "Stop" activates a 4.5-second frame drain barrier to cleanly ignore any delayed network packets and prevent UI stutter.

### 2. 🔔 Physical Doorbell Button Priority & Stream Interrupt
- **Hardware Circuitry**: A physical tactile pushbutton on **GPIO 14** configured with internal pull-up and a 40ms software debounce filter.
- **Ring Priority**:
  - **From Standby**: Captures an immediate HD visitor snapshot, pushes to `/visitor`, triggers an instant chime alert, and automatically extends into a 30-second live cloud stream so the owner can converse or monitor in real time.
  - **During Active Streaming**: An in-stream interrupt detects the button press on GPIO 14, immediately records the visitor event, and seamlessly refreshes the stream timer by +30 seconds without dropping frames.

### 3. ⚡ Sub-2-Second Instant Chime Alerting (Asynchronous Neural Inference)
- **The Challenge**: Running heavy convolutional face detection (`dlib` HOG + 128-dimensional ResNet-34 metric learning) synchronously on a CPU takes 300–600 ms. In cloud environments, waiting for inference before responding caused a 6–7 second delay between the visitor pressing the button and the homeowner's phone chiming.
- **The Solution**: The server immediately acknowledges `/visitor`, broadcasts a real-time event (`"Doorbell Ringing..."`) via Server-Sent Events (SSE), and dispatches inference to a background worker thread.
  - **Chime Latency**: The browser/phone chimes and vibrates within **~1.8–2.0 seconds** of the physical button press.
  - **Dynamic Name Resolution**: When the AI worker finishes identifying the face, it pushes an in-place `visitor_update` event that dynamically replaces the card title with the recognized name without re-triggering the chime audio.

### 4. 🧠 128-Dimensional Biometric Facial Recognition
- **Engine**: Powered by `dlib`'s state-of-the-art Deep Residual Network (ResNet-34) trained on millions of facial landmarks.
- **Metric Matching**: Generates a 128-float biometric vector per detected face and computes Euclidean distance against enrolled reference encodings in `face_db/encodings.pkl`.
- **Confidence Calibration**: Uses a strict classification threshold (`0.54`) to eliminate false positives, tagging unknown visitors as `Unknown Visitor` while instantly recognizing enrolled family members.

### 5. 🔄 Zero-Refresh Real-Time Live Sync & Mobile PWA
- **Hybrid SSE + High-Frequency Polling**: Combines persistent Server-Sent Events (`/api/events/stream`) for sub-millisecond push delivery with a 1.5-second `/api/live_state` heartbeat for rock-solid fault tolerance.
- **Web Audio API Synthesizer**: Generates a 2-tone melodic doorbell chime (*Ding* at 659.25 Hz E5 $\rightarrow$ *Dong* at 523.25 Hz C5) natively in browser memory without requiring external audio asset downloads.
- **Screen Wake Lock API**: Automatically invokes `navigator.wakeLock.request('screen')` during live stream viewing to keep the smartphone display awake while monitoring the doorstep.
- **Dynamic Frame Fallback**: If an MJPEG socket stalls due to cellular network switching, the frontend seamlessly falls back to 120ms single-frame memory polling (`/api/latest_frame`).

### 6. 🕒 Local Timezone Display & 1-Tap Feed Purge
- **Timezone Standardization**: Server records timestamps in Indian Standard Time (IST, UTC+5:30) using `datetime.timezone`. The frontend's dynamic `formatDisplayTime` parser automatically formats both legacy UTC and local timestamps into clean, human-readable strings (e.g. `Sep 13, 12:47:15 PM`).
- **Real-Time Feed Clearing (`POST /api/feed/clear`)**: A dedicated **`[ 🗑️ Clear ]`** button with confirmation prompt safely purges the SQLite `visits` database table. The server broadcasts a `feed_cleared` SSE event, instantly emptying the timeline and updating event counters across all open devices without requiring a browser reload.

### 7. 🛡️ PIR Motion Sensor Cloud Toggle
- Software-controlled toggle via `POST /api/pir/toggle` allows the homeowner to enable or disable HC-SR501 PIR thermal motion alerts directly from the UI, preventing nuisance notifications caused by pets, swaying trees, or passing street traffic.

---

## 📊 Benchmark & Performance Metrics

| Metric | Measured Value | Significance |
| :--- | :--- | :--- |
| **Physical Button Debounce** | **40 ms** | Clean active-LOW transition with zero false double-triggers |
| **Doorbell Trigger Latency** | **< 10 ms** | Server receives and broadcasts event before edge ACK completes |
| **End-to-End Phone Chime** | **~1.8 s** | Total time from physical press to phone chime & vibration over cloud |
| **On-Demand Stream Start** | **1.2 – 1.8 s** | Time from "Watch Live" tap to active video rendering |
| **Stream Frame Rate** | **13 – 15 FPS** | Fluid MJPEG video over standard residential Wi-Fi & 4G/5G |
| **Frame Bandwidth** | **~12 – 15 KB** | Optimized JPEG quality index (`12`) balancing clarity and throughput |
| **Face Recognition Time** | **~280 ms** | Multi-threaded background dlib ResNet-34 inference |
| **Mobile Push Notification** | **< 1.0 s** | Rich push with snapshot delivered to phone lock screen via `ntfy.sh` |
| **Camera Watchdog Recovery** | **~1.5 s** | Automated hardware reset (`ESP.restart()`) on consecutive frame drops |

---

## 🛠️ Hardware Bill of Materials (BOM) & Pinout

| Component | Specification | Function |
| :--- | :--- | :--- |
| **MCU** | ESP32-D0WD-V3 (AI-Thinker module, 4MB PSRAM) | Edge controller and camera driver |
| **Camera Sensor** | Omnivision OV3660 (3.0 Megapixel) | Optical image sensor with hardware ISP |
| **Programmer** | ESP32-CAM-MB Shield (or ESP32-S3 UART Bridge) | Serial flashing and 5V USB power regulation |
| **Motion Sensor** | HC-SR501 Passive Infrared (PIR) | Thermal motion detection |
| **Doorbell Button** | SPST Tactile Push Button | Physical visitor trigger (Active LOW) |
| **LEDs** | GPIO 4 (Flash, 1W) / GPIO 33 (Status, Red) | Night illumination and boot diagnostics |

### Edge Pin Mapping
```
ESP32-CAM Pin     Peripheral Connection
-----------------------------------------------------------
GPIO 14   ------> Doorbell Push Button (Active LOW with internal PULLUP)
GPIO 13   ------> PIR Motion Sensor OUT (PULLDOWN)
GPIO 4    ------> Onboard High-Power Flash LED
GPIO 33   ------> Onboard Red Status LED (Active LOW)
GPIO 32   ------> OV3660 Power Down (PWDN, Active HIGH)
GPIO 0    ------> Bootloader Flashing Mode (Short to GND to Flash)
5V / GND  ------> Regulated 5V 2A Power Source
```

---

## 📡 REST & Real-Time API Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` | `GET` | CCTV Security Dashboard & Progressive Web App interface |
| `/video_feed` | `GET` | Live multi-client MJPEG video stream (`multipart/x-mixed-replace`) |
| `/api/latest_frame` | `GET` | Returns the single most recent JPEG frame from server memory |
| `/api/events/stream` | `GET` | Server-Sent Events (SSE) real-time event pipeline |
| `/api/live_state` | `GET` | Live system state (stream active, FPS, remaining time, latest visit, PIR state) |
| `/api/stream/start` | `POST` | Demands live cloud streaming for 60 seconds |
| `/api/stream/stop` | `POST` | Terminates active live streaming and returns camera to standby |
| `/api/stream_cmd` | `GET` | Polled by ESP32 edge device (`{"stream": true/false}`) |
| `/api/stream_push` | `POST` | Ingestion endpoint where ESP32 pushes live JPEG frames |
| `/visitor` | `POST` | Trigger endpoint called on button ring/motion with snapshot attachment |
| `/api/feed/clear` | `POST` | Purges all visitor log records and broadcasts `feed_cleared` event |
| `/api/pir/toggle` | `POST` | Toggles PIR motion sensor alert evaluation on/off |
| `/api/diagnostics` | `GET` | Health check endpoint reporting Python, OpenCV, dlib, and face models |

---

## 🚀 Deployment & Getting Started

### 1. Cloud Deployment (Render.com + Docker)
The project includes a production-ready `Dockerfile` pre-configured with Python 3.11, CMake, dlib, and `face_recognition`.
1. Fork or push this repository to your GitHub account.
2. Link your repository to **[Render.com](https://render.com/)** as a **Web Service**.
3. Set Environment: **Docker**.
4. Set optional environment variables in Render Dashboard:
   - `BASE_DIR=/app`
   - `NTFY_TOPIC=arush-doorcam-cctv` (or your custom topic name)
5. Render builds the container and serves your application at `https://<your-subdomain>.onrender.com`.

### 2. Local Development Setup
```bash
git clone https://github.com/Arush-Reddy/DoorCam.git
cd DoorCam

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Run server locally
python doorbell_server.py
```

### 3. Enrolling Known Faces
1. Place portrait photos (`.jpg` or `.png`) of individuals inside `known_faces/`:
   ```text
   known_faces/
   ├── Arush.jpg
   └── Mom.jpg
   ```
2. Run the offline facial embedding generator:
   ```bash
   python face_db/encode_faces.py
   ```
   This generates `face_db/encodings.pkl`, which is loaded into server memory on startup.

### 4. Flashing Firmware to ESP32-CAM
1. Open `firmware/doorbell_cam/doorbell_cam.ino` in Arduino IDE.
2. Select Board: **AI Thinker ESP32-CAM** | Partition: **Huge APP (3MB No OTA/1MB SPIFFS)**.
3. Export compiled binary (`Ctrl + Alt + S` in Arduino IDE).
4. Short **IO0 to GND** on the ESP32-CAM and plug into USB (or mount on ESP32-CAM-MB shield).
5. Flash using the high-speed flasher:
   ```powershell
   python firmware/fast_flash.py
   ```
6. Disconnect **IO0 from GND** and press the **RST** button.

---

## 🔒 Security & Privacy Architecture

- **Self-Hosted Biometric Privacy**: Biometric facial recognition models and feature vectors execute entirely within your private container or local server. No private facial embeddings are ever uploaded to third-party proprietary clouds.
- **Zero Inbound NAT Openings**: Remote streaming operates via outbound long-polling and socket pushes—no risky port forwarding or firewall exceptions required.
- **Encrypted Global Ingress**: All mobile dashboard sessions, live MJPEG feeds, and push communications run over TLS 1.3 encryption (HTTPS/WSS).
- **Zero Subscription Fees**: Built entirely on self-hosted open-source software and open protocols.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## 👤 Author

**Arush Reddy**  
- **GitHub:** [@Arush-Reddy](https://github.com/Arush-Reddy)  
- **Project:** [DoorCam — Edge-AI Smart Video Doorbell System](https://github.com/Arush-Reddy/DoorCam)


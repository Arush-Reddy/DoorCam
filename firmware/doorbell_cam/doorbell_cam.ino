/*
 * Smart Video Doorbell - ESP32-CAM Firmware
 * Optimized for: ESP32-CAM (AI Thinker) with OV3660 Sensor & MB Programmer Shield
 * 
 * Hardware Connections:
 *   - PIR Motion Sensor (HC-SR501) OUT -> GPIO 13
 *   - Doorbell Push Button -> GPIO 14 (Connect other side of button to GND)
 *   - Flash LED -> GPIO 4 (Onboard high-power white LED)
 *   - Status LED -> GPIO 33 (Onboard small red LED, Active LOW)
 * 
 * Arduino IDE Settings:
 *   - Board: "AI Thinker ESP32-CAM"
 *   - CPU Frequency: 240MHz (WiFi/BT)
 *   - Flash Frequency: 80MHz
 *   - Flash Mode: QIO
 *   - Partition Scheme: "Huge APP (3MB No OTA/1MB SPIFFS)"
 *   - Upload Speed: 115200 or 921600
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include "esp_http_server.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// Forward declarations
bool triggerDoorbell(const char* triggerType);
void streamCloudBurst(int durationSeconds);
bool checkCloudStreamCommand();

// ==========================================
// 1. NETWORK & SERVER CONFIGURATION
// ==========================================
#if __has_include("credentials.h")
  #include "credentials.h"
#else
  const char* ssid = "YOUR_WIFI_SSID";
  const char* password = "YOUR_WIFI_PASSWORD";
  const char* serverHost = "192.168.0.4"; // Local IP of server running doorbell_server.py
  const int serverPort = 5000;
#endif

#define ENABLE_PIR false  // Disabled temporarily to prevent false motion triggers
const char* serverPath = "/visitor";

// ==========================================
// 2. PIN DEFINITIONS (AI-THINKER PINOUT)
// ==========================================
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// Peripherals
#define PIR_PIN           13     // HC-SR501 PIR Output (PULLDOWN prevents floating triggers)
#define BUTTON_PIN        14     // Doorbell Push Button (Active LOW with internal PULLUP)
#define FLASH_LED_PIN      4     // Onboard Flash LED (Active HIGH)
#define STATUS_LED_PIN    33     // Onboard Red LED (Active LOW)

// Hardware Button Interrupt State
volatile bool g_buttonTriggered = false;
unsigned long g_lastButtonInterruptTime = 0;

void IRAM_ATTR button_isr() {
  unsigned long now = millis();
  if (now - g_lastButtonInterruptTime > 300) {
    g_buttonTriggered = true;
    g_lastButtonInterruptTime = now;
  }
}

// Cooldown between captures to avoid spamming the server
const unsigned long CAPTURE_COOLDOWN_MS = 6000;
unsigned long lastCaptureTime = 0;
bool cameraInitialized = false;

// Global camera configuration structure
camera_config_t s_camera_config = {};

// HTTP server instance for live streaming
httpd_handle_t stream_httpd = NULL;

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* _STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* _STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* _STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

// Stream handler for live video preview
static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t * fb = NULL;
  esp_err_t res = ESP_OK;
  size_t _jpg_buf_len = 0;
  uint8_t * _jpg_buf = NULL;
  char part_buf[64];

  res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;

  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  int consecutive_failures = 0;

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("[CAM] Stream frame buffer grab failed");
      consecutive_failures++;
      if (consecutive_failures >= 15) {
        Serial.println("[CAM CRITICAL] 15 consecutive capture failures! Auto-restarting ESP32...");
        delay(100);
        ESP.restart();
      }
      delay(40);
      continue;
    }
    consecutive_failures = 0;

    _jpg_buf_len = fb->len;
    _jpg_buf = fb->buf;

    size_t hlen = snprintf((char *)part_buf, 64, _STREAM_PART, _jpg_buf_len);
    res = httpd_resp_send_chunk(req, (const char *)part_buf, hlen);
    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, (const char *)_jpg_buf, _jpg_buf_len);
    }
    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
    }

    esp_camera_fb_return(fb);
    fb = NULL;
    _jpg_buf = NULL;

    // If client disconnected or socket send failed, exit cleanly
    if (res != ESP_OK) {
      break;
    }

    delay(10); // ~30 FPS with FreeRTOS CPU yield
  }
  return res;
}

void startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 81;
  config.ctrl_port = 32768;
  config.lru_purge_enable = true; // Auto-purge old/stale sockets immediately
  config.send_wait_timeout = 2;   // Fast timeout prevents socket locks
  config.recv_wait_timeout = 2;
  config.stack_size = 8192;       // Prevent stack overflows in stream task

  httpd_uri_t stream_uri = {
    .uri       = "/",
    .method    = HTTP_GET,
    .handler   = stream_handler,
    .user_ctx  = NULL
  };

  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
    Serial.printf("[HTTP] Live MJPEG Stream available at http://%s:81/\n", WiFi.localIP().toString().c_str());
  }
}

// 15-20 FPS Live Cloud Stream Burst (Option C: 30-second stream on ring, or on-demand)
void streamCloudBurst(int durationSeconds) {
  Serial.printf("\n[STREAM] Starting %d-second live cloud stream to %s...\n", durationSeconds, serverHost);

  bool isHttps = (serverPort == 443);
  WiFiClient plainClient;
  WiFiClientSecure secureClient;
  WiFiClient *clientPtr = nullptr;

  if (isHttps) {
    secureClient.setInsecure();
    secureClient.setTimeout(2500);
    clientPtr = &secureClient;
  } else {
    plainClient.setTimeout(2000);
    clientPtr = &plainClient;
  }

  if (!clientPtr->connect(serverHost, serverPort)) {
    Serial.println("[STREAM WARN] Could not connect to cloud stream endpoint.");
    return;
  }

  unsigned long startTime = millis();
  unsigned long durationMs = (unsigned long)durationSeconds * 1000UL;
  int framesSent = 0;
  bool stopRequested = false;

  while ((millis() - startTime < durationMs) && !stopRequested) {
    // Check if Doorbell Button was pressed while streaming!
    if (digitalRead(BUTTON_PIN) == LOW) {
      delay(30);
      if (digitalRead(BUTTON_PIN) == LOW) {
        Serial.println("[STREAM] Button pressed during live stream! Logging visitor alert...");
        while (digitalRead(BUTTON_PIN) == LOW) { delay(10); }
        clientPtr->stop();
        triggerDoorbell("BUTTON");
        return; // triggerDoorbell initiates its own 30s stream burst
      }
    }

    if (!clientPtr->connected()) {
      if (!clientPtr->connect(serverHost, serverPort)) {
        delay(60);
        continue;
      }
    }

    camera_fb_t * fb = esp_camera_fb_get();
    if (!fb) {
      delay(20);
      continue;
    }

    // Push frame with exact Content-Length so proxy accepts it cleanly
    clientPtr->print(String("POST /api/frame_push HTTP/1.1\r\n") +
                     "Host: " + String(serverHost) + "\r\n" +
                     "Content-Type: image/jpeg\r\n" +
                     "Content-Length: " + String(fb->len) + "\r\n" +
                     "Connection: keep-alive\r\n\r\n");
    clientPtr->write(fb->buf, fb->len);

    esp_camera_fb_return(fb);
    framesSent++;

    // Fast non-blocking read of server response with STOP detection
    while (clientPtr->available() > 0) {
      char c = (char)clientPtr->read();
      if (c == 'S') {
        char sBuf[5] = {0};
        sBuf[0] = 'S';
        int r = 1;
        while (clientPtr->available() > 0 && r < 4) {
          sBuf[r++] = (char)clientPtr->read();
        }
        if (strncmp(sBuf, "STOP", 4) == 0) {
          Serial.println("[STREAM] Server signaled STOP (user ended session).");
          stopRequested = true;
          break;
        }
      }
    }

    // Blink status LED every 8 frames for live activity feedback
    if (framesSent % 8 == 0) {
      digitalWrite(STATUS_LED_PIN, !digitalRead(STATUS_LED_PIN));
    }

    delay(15); // Smooth 15-20 FPS rate without freezing
  }

  clientPtr->stop();
  digitalWrite(STATUS_LED_PIN, HIGH); // OFF

  unsigned long totalMs = millis() - startTime;
  float fps = (totalMs > 0) ? ((float)framesSent / (totalMs / 1000.0f)) : 0;
  Serial.printf("[STREAM COMPLETE] Sent %d frames in %.1fs (average %.1f FPS). Standby armed.\n", 
                framesSent, totalMs / 1000.0f, fps);
}

// Direct JPEG snapshot push to server (supports both local PC and Cloud/AWS servers!)
bool triggerDoorbell(const char* triggerType) {
  Serial.printf("\n[EVENT] Doorbell triggered by: %s!\n", triggerType);

  // Quick blink on status LED for immediate feedback
  digitalWrite(STATUS_LED_PIN, LOW); // ON (Active LOW)

  // Grab fresh high-res snapshot directly from camera sensor
  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[CAM ERROR] Failed to capture frame for upload!");
    digitalWrite(STATUS_LED_PIN, HIGH);
    return false;
  }

  bool isHttps = (serverPort == 443);
  WiFiClient plainClient;
  WiFiClientSecure secureClient;
  WiFiClient *clientPtr = nullptr;

  if (isHttps) {
    secureClient.setInsecure(); // Skip TLS cert validation for ultra-fast connection
    secureClient.setTimeout(3500);
    clientPtr = &secureClient;
  } else {
    plainClient.setTimeout(2500);
    clientPtr = &plainClient;
  }

  if (clientPtr->connect(serverHost, serverPort)) {
    String url = String(serverPath) + "?trigger=" + String(triggerType);
    clientPtr->print(String("POST ") + url + " HTTP/1.1\r\n" +
                     "Host: " + String(serverHost) + "\r\n" +
                     "Content-Type: image/jpeg\r\n" +
                     "Content-Length: " + String(fb->len) + "\r\n" +
                     "Connection: close\r\n\r\n");

    // Stream the binary JPEG bytes directly to server
    clientPtr->write(fb->buf, fb->len);

    // Brief check for acknowledgment
    unsigned long start = millis();
    while (clientPtr->connected() && millis() - start < 1500) {
      if (clientPtr->available()) {
        String line = clientPtr->readStringUntil('\n');
        if (line.indexOf("200") >= 0) {
          Serial.println("[SERVER] Doorbell event & photo acknowledged (200 OK)!");
          break;
        }
      }
    }
    clientPtr->stop();
  } else {
    Serial.printf("[WARN] Could not connect to %s:%d for trigger event.\n", serverHost, serverPort);
  }

  esp_camera_fb_return(fb);
  digitalWrite(STATUS_LED_PIN, HIGH); // OFF

  // Option C: If button pressed, stream live video to cloud for 30 seconds at 15-20 FPS!
  if (strcmp(triggerType, "BUTTON") == 0) {
    streamCloudBurst(30);
  }

  return true;
}


void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); // Disable brownout detector for stable streaming
  Serial.begin(115200);
  Serial.println("\n--- Starting Smart Video Doorbell (OV3660) ---");

  // Setup I/O pins
  pinMode(PIR_PIN, INPUT_PULLDOWN);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(FLASH_LED_PIN, OUTPUT);
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);
  digitalWrite(STATUS_LED_PIN, HIGH); // Built-in LED is Active LOW

  // Hardware power-cycle camera sensor before configuration
  pinMode(PWDN_GPIO_NUM, OUTPUT);
  digitalWrite(PWDN_GPIO_NUM, HIGH); // Power OFF
  delay(150);
  digitalWrite(PWDN_GPIO_NUM, LOW);  // Power ON
  delay(150);

  // Configure Camera
  s_camera_config.ledc_channel = LEDC_CHANNEL_0;
  s_camera_config.ledc_timer = LEDC_TIMER_0;
  s_camera_config.pin_d0 = Y2_GPIO_NUM;
  s_camera_config.pin_d1 = Y3_GPIO_NUM;
  s_camera_config.pin_d2 = Y4_GPIO_NUM;
  s_camera_config.pin_d3 = Y5_GPIO_NUM;
  s_camera_config.pin_d4 = Y6_GPIO_NUM;
  s_camera_config.pin_d5 = Y7_GPIO_NUM;
  s_camera_config.pin_d6 = Y8_GPIO_NUM;
  s_camera_config.pin_d7 = Y9_GPIO_NUM;
  s_camera_config.pin_xclk = XCLK_GPIO_NUM;
  s_camera_config.pin_pclk = PCLK_GPIO_NUM;
  s_camera_config.pin_vsync = VSYNC_GPIO_NUM;
  s_camera_config.pin_href = HREF_GPIO_NUM;
  s_camera_config.pin_sccb_sda = SIOD_GPIO_NUM;
  s_camera_config.pin_sccb_scl = SIOC_GPIO_NUM;
  s_camera_config.pin_pwdn = PWDN_GPIO_NUM;
  s_camera_config.pin_reset = RESET_GPIO_NUM;
  s_camera_config.xclk_freq_hz = 16000000; // 16MHz for clean signal integrity and reliable I2C
  s_camera_config.pixel_format = PIXFORMAT_JPEG;
  s_camera_config.grab_mode = CAMERA_GRAB_LATEST;

  // PSRAM check and frame size configuration
  if (psramFound()) {
    Serial.println("[CAM] PSRAM detected! Using VGA 640x480 for rock-solid smooth streaming.");
    s_camera_config.frame_size = FRAMESIZE_VGA; // 640x480 (silky-smooth 25-30 FPS without WiFi congestion)
    s_camera_config.jpeg_quality = 16;          // High quality, fast transmission
    s_camera_config.fb_count = 2;
    s_camera_config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    Serial.println("[CAM] Warning: PSRAM not detected. Lowering resolution to CIF.");
    s_camera_config.frame_size = FRAMESIZE_CIF;  // 400x296
    s_camera_config.jpeg_quality = 18;
    s_camera_config.fb_count = 1;
    s_camera_config.fb_location = CAMERA_FB_IN_DRAM;
  }

  // Camera Initialization with Automatic Retry
  esp_err_t err = ESP_FAIL;
  for (int attempt = 1; attempt <= 4; attempt++) {
    Serial.printf("[CAM] Initializing camera (attempt %d/4)...\n", attempt);
    err = esp_camera_init(&s_camera_config);
    if (err == ESP_OK) {
      break;
    }
    Serial.printf("[CAM] Attempt %d failed (0x%x). Power cycling sensor...\n", attempt, err);
    digitalWrite(PWDN_GPIO_NUM, HIGH);
    delay(200);
    digitalWrite(PWDN_GPIO_NUM, LOW);
    delay(200);
  }

  if (err != ESP_OK) {
    Serial.printf("\n[CAM ERROR] Camera init failed permanently with error 0x%x\n", err);
    if (err == 0x20001 || err == 0x105 || err == 0x106) {
      Serial.println("[CAM HINT] Camera sensor not detected over SCCB/I2C.");
      Serial.println("[CAM HINT] Check OV3660 ribbon cable: gold pins must face board, latch locked.");
    }
    cameraInitialized = false;
  } else {
    cameraInitialized = true;
    Serial.println("[CAM] Camera initialized successfully!");

    sensor_t * s = esp_camera_sensor_get();
    if (s && s->id.PID == OV3660_PID) {
      Serial.println("[CAM] OV3660 sensor detected. Applying image corrections...");
      s->set_vflip(s, 1);        // Correct upside-down orientation
      s->set_hmirror(s, 0);      // Horizontal mirror off
      s->set_brightness(s, 1);   // Slightly brighten picture
      s->set_saturation(s, -1);  // Slightly reduce harsh saturation
    } else if (s) {
      Serial.printf("[CAM] Sensor PID: 0x%x detected.\n", s->id.PID);
    }
  }

  // Connect to WiFi
  Serial.printf("[WIFI] Connecting to SSID: %s\n", ssid);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    digitalWrite(STATUS_LED_PIN, !digitalRead(STATUS_LED_PIN)); // Flash status LED while connecting
  }
  digitalWrite(STATUS_LED_PIN, HIGH); // Turn off after connecting

  Serial.println("\n[WIFI] Connected successfully!");
  WiFi.setSleep(false); // Ultra-low latency streaming without WiFi sleep pauses
  Serial.printf("[WIFI] Doorbell IP Address: http://%s\n", WiFi.localIP().toString().c_str());

  // Start live stream server on port 81
  startCameraServer();
  Serial.println("[SYS] Doorbell system armed and ready.");
}

// Check if cloud server is requesting an on-demand live stream
bool checkCloudStreamCommand() {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  bool isHttps = (serverPort == 443);
  WiFiClient plainClient;
  WiFiClientSecure secureClient;
  WiFiClient *clientPtr = nullptr;

  if (isHttps) {
    secureClient.setInsecure();
    secureClient.setTimeout(1800);
    clientPtr = &secureClient;
  } else {
    plainClient.setTimeout(1500);
    clientPtr = &plainClient;
  }

  if (!clientPtr->connect(serverHost, serverPort)) {
    return false;
  }

  clientPtr->print(String("GET /api/stream_cmd HTTP/1.1\r\n") +
                   "Host: " + String(serverHost) + "\r\n" +
                   "Connection: close\r\n\r\n");

  unsigned long start = millis();
  String resp = "";
  while (clientPtr->connected() && millis() - start < 1200) {
    while (clientPtr->available()) {
      char c = clientPtr->read();
      resp += c;
    }
  }
  clientPtr->stop();

  if (resp.indexOf("\"stream\":true") >= 0 || resp.indexOf("\"stream\": true") >= 0) {
    return true;
  }
  return false;
}

void loop() {
  unsigned long now = millis();

  // 1. Check Doorbell Button (Active LOW) with hardware debounce filter
  if (digitalRead(BUTTON_PIN) == LOW) {
    delay(40); // Debounce delay
    if (digitalRead(BUTTON_PIN) == LOW) {
      if (now - lastCaptureTime > CAPTURE_COOLDOWN_MS) {
        lastCaptureTime = now;
        triggerDoorbell("BUTTON");
      }
      // Hold until button is released to prevent repeated triggers
      while (digitalRead(BUTTON_PIN) == LOW) {
        delay(20);
      }
    }
  }

#if ENABLE_PIR
  // 2. Check PIR Motion Sensor (Active HIGH)
  if (digitalRead(PIR_PIN) == HIGH) {
    if (now - lastCaptureTime > CAPTURE_COOLDOWN_MS) {
      lastCaptureTime = now;
      triggerDoorbell("PIR");
    }
  }
#endif

  // 3. Poll cloud server for on-demand live stream command every 1.5 seconds
  static unsigned long lastCmdCheck = 0;
  if (now - lastCmdCheck >= 1500) {
    lastCmdCheck = now;
    if (checkCloudStreamCommand()) {
      Serial.println("[CMD] Cloud requested On-Demand Live Stream! Starting 60s stream...");
      streamCloudBurst(60);
    }
  }

  delay(20);
}

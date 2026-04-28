/**
 * IoT Proximity-Based Screen Safety System
 * ESP32 Firmware — proximity_sensor.ino
 *
 * Hardware:
 *   - ESP32 Dev Module (any variant)
 *   - HC-SR04 Ultrasonic Sensor
 *     TRIG → GPIO 5
 *     ECHO → GPIO 18
 *     VCC  → 5V (or 3.3V with voltage divider on ECHO)
 *     GND  → GND
 *   - Built-in LED (GPIO 2) for status indication
 *
 * Dependencies (Arduino Library Manager):
 *   - WiFi (built-in ESP32 core)
 *   - HTTPClient (built-in ESP32 core)
 *   - ArduinoJson >= 6.x
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ─── USER CONFIGURATION ────────────────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_WIFI_SSID";       // ← change this
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";   // ← change this
const char* SERVER_IP     = "192.168.1.100";         // ← laptop IP on same Wi-Fi
const int   SERVER_PORT   = 5000;

// Sensor GPIO pins
const int TRIG_PIN = 5;
const int ECHO_PIN = 18;
const int STATUS_LED = 2;

// Distance thresholds (cm)
const float PROXIMITY_CLOSE   = 10.0;   // Trigger brightness reduction
const float PROXIMITY_WARNING = 25.0;   // Human presence zone
const float PROXIMITY_MAX     = 200.0;  // Ignore readings beyond this (noise filter)
const float PROXIMITY_MIN     = 2.0;    // Ignore readings below this (sensor blind zone)

// Timing
const unsigned long SEND_INTERVAL_MS   = 200;    // Send data every 200ms
const unsigned long STATUS_POLL_MS     = 2000;   // Poll server ON/OFF status
const unsigned long WIFI_RETRY_MS      = 5000;   // Wi-Fi reconnect interval
const unsigned long SENSOR_TIMEOUT_US  = 30000;  // pulseIn timeout (30ms)

// ─── GLOBAL STATE ──────────────────────────────────────────────────────────
bool systemEnabled        = true;
unsigned long lastSendTime  = 0;
unsigned long lastStatusPoll = 0;
unsigned long lastWifiRetry  = 0;

// Rolling median filter for noise reduction
const int MEDIAN_WINDOW = 5;
float distanceBuffer[MEDIAN_WINDOW];
int   bufferIndex = 0;
bool  bufferFull  = false;

// ─── UTILITY: Median filter ────────────────────────────────────────────────
float getMedian(float* arr, int n) {
  float sorted[MEDIAN_WINDOW];
  memcpy(sorted, arr, n * sizeof(float));
  // Bubble sort (small array — acceptable)
  for (int i = 0; i < n - 1; i++)
    for (int j = 0; j < n - i - 1; j++)
      if (sorted[j] > sorted[j + 1]) {
        float t = sorted[j]; sorted[j] = sorted[j + 1]; sorted[j + 1] = t;
      }
  return sorted[n / 2];
}

// ─── SENSOR: Read raw distance (cm) ────────────────────────────────────────
float readRawDistance() {
  // Ensure TRIG is LOW before pulse
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);

  // Send 10µs HIGH pulse
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // Measure ECHO pulse duration
  long duration = pulseIn(ECHO_PIN, HIGH, SENSOR_TIMEOUT_US);

  if (duration == 0) return -1.0;  // Timeout — no echo received

  // Speed of sound: 343 m/s → 0.0343 cm/µs, divide by 2 for round-trip
  return (duration * 0.0343f) / 2.0f;
}

// ─── SENSOR: Read filtered distance ────────────────────────────────────────
float readFilteredDistance() {
  float raw = readRawDistance();

  // Reject invalid readings
  if (raw < PROXIMITY_MIN || raw > PROXIMITY_MAX) return -1.0;

  // Add to rolling buffer
  distanceBuffer[bufferIndex] = raw;
  bufferIndex = (bufferIndex + 1) % MEDIAN_WINDOW;
  if (bufferIndex == 0) bufferFull = true;

  int n = bufferFull ? MEDIAN_WINDOW : bufferIndex;
  if (n < 1) return raw;

  return getMedian(distanceBuffer, n);
}

// ─── NETWORK: Send proximity data to Flask ─────────────────────────────────
void sendProximityData(float distance) {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  String url = "http://" + String(SERVER_IP) + ":" + String(SERVER_PORT) + "/api/proximity";

  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(500);  // 500ms timeout — don't block the loop

  // Build JSON payload
  StaticJsonDocument<128> doc;
  doc["distance_cm"]  = distance;
  doc["timestamp_ms"] = millis();
  doc["device_id"]    = WiFi.macAddress();
  doc["zone"] = (distance < 0)           ? "no_object" :
                (distance <= PROXIMITY_CLOSE)   ? "danger" :
                (distance <= PROXIMITY_WARNING) ? "warning" : "safe";

  String payload;
  serializeJson(doc, payload);

  int httpCode = http.POST(payload);

  if (httpCode == 200) {
    // Parse response for any commands from server
    String response = http.getString();
    StaticJsonDocument<64> resp;
    if (!deserializeJson(resp, response)) {
      if (resp.containsKey("system_enabled")) {
        systemEnabled = resp["system_enabled"].as<bool>();
      }
    }
  }

  http.end();
}

// ─── NETWORK: Poll server for ON/OFF status ────────────────────────────────
void pollSystemStatus() {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  String url = "http://" + String(SERVER_IP) + ":" + String(SERVER_PORT) + "/api/status";

  http.begin(url);
  http.setTimeout(1000);

  int httpCode = http.GET();

  if (httpCode == 200) {
    String response = http.getString();
    StaticJsonDocument<128> doc;
    if (!deserializeJson(doc, response)) {
      systemEnabled = doc["system_enabled"] | true;
    }
  }

  http.end();
}

// ─── Wi-Fi: Connect / Reconnect ────────────────────────────────────────────
void ensureWiFiConnected() {
  if (WiFi.status() == WL_CONNECTED) return;

  unsigned long now = millis();
  if (now - lastWifiRetry < WIFI_RETRY_MS) return;
  lastWifiRetry = now;

  Serial.println("[WiFi] Connecting to " + String(WIFI_SSID));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // Wait up to 10 seconds
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WiFi] Connected! IP: " + WiFi.localIP().toString());
    digitalWrite(STATUS_LED, HIGH);
  } else {
    Serial.println("\n[WiFi] Failed — will retry");
    digitalWrite(STATUS_LED, LOW);
  }
}

// ─── SETUP ─────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(100);

  Serial.println("\n========================================");
  Serial.println(" IoT Screen Safety System — ESP32 Node");
  Serial.println("========================================");

  // GPIO setup
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(STATUS_LED, OUTPUT);

  // Initialize median buffer
  memset(distanceBuffer, 0, sizeof(distanceBuffer));

  // Wi-Fi
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  ensureWiFiConnected();

  Serial.println("[System] Ready. Thresholds: CLOSE=" + String(PROXIMITY_CLOSE) +
                 "cm, WARNING=" + String(PROXIMITY_WARNING) + "cm");
}

// ─── MAIN LOOP ─────────────────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  // 1. Ensure Wi-Fi connection
  ensureWiFiConnected();

  // 2. Read and transmit sensor data at defined interval
  if (now - lastSendTime >= SEND_INTERVAL_MS) {
    lastSendTime = now;

    float distance = readFilteredDistance();

    // Serial monitor output for debugging
    if (distance < 0) {
      Serial.println("[Sensor] No object detected / timeout");
    } else {
      Serial.printf("[Sensor] Distance: %.1f cm | Zone: %s | System: %s\n",
        distance,
        (distance <= PROXIMITY_CLOSE)   ? "DANGER" :
        (distance <= PROXIMITY_WARNING) ? "WARNING" : "SAFE",
        systemEnabled ? "ON" : "OFF"
      );
    }

    // Only transmit if system is enabled
    if (systemEnabled) {
      sendProximityData(distance);
    }
  }

  // 3. Poll server status periodically
  if (now - lastStatusPoll >= STATUS_POLL_MS) {
    lastStatusPoll = now;
    pollSystemStatus();
  }

  // Small yield to prevent watchdog resets
  yield();
}

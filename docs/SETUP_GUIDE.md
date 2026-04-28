# ScreenGuard — Step-by-Step Setup Guide

## Table of Contents
1. [Hardware Requirements](#1-hardware-requirements)
2. [Wiring Diagram](#2-wiring-diagram)
3. [Python Server Setup](#3-python-server-setup)
4. [Arduino IDE / ESP32 Firmware](#4-arduino-ide--esp32-firmware)
5. [Network Configuration](#5-network-configuration)
6. [Running the System](#6-running-the-system)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Hardware Requirements

| Component | Model | Notes |
|-----------|-------|-------|
| Microcontroller | ESP32 DevKit v1 | Any 30-pin or 38-pin variant |
| Ultrasonic Sensor | HC-SR04 | 2cm–400cm range, 3mm accuracy |
| USB Cable | Micro-USB | For ESP32 programming |
| Laptop/PC | Windows 10/11 | Flask server + webcam |
| Wi-Fi Router | Any 2.4GHz | ESP32 and laptop on same network |

**Optional for better accuracy:**
- External USB webcam (720p+) instead of built-in
- 10kΩ resistor + voltage divider if HC-SR04 ECHO outputs 5V

---

## 2. Wiring Diagram

```
ESP32 DevKit           HC-SR04
─────────────          ────────────────
GPIO 5   ──────────→  TRIG
GPIO 18  ←──────────  ECHO (via 1kΩ+2kΩ voltage divider if 5V)
3.3V / 5V ─────────→  VCC
GND      ──────────→  GND
GPIO 2   (Built-in LED — status indicator)
```

### Voltage Divider for 5V ECHO signal (recommended):
```
ECHO (HC-SR04) → 1kΩ → [GPIO 18] → 2kΩ → GND
```
This brings 5V down to ~3.3V, safe for ESP32 inputs.

---

## 3. Python Server Setup

### Step 3.1 — Create Virtual Environment
```powershell
cd C:\Users\YTANNU\.gemini\antigravity\scratch\screen-safety-system\server
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### Step 3.2 — Install Dependencies
```powershell
pip install -r requirements.txt
```

> **Note:** `deepface` will download model weights (~200MB) on first run. Ensure internet access.

### Step 3.3 — Verify Installation
```powershell
python -c "import cv2, deepface, flask, screen_brightness_control; print('All OK')"
```

### Step 3.4 — Check Camera
```powershell
python -c "import cv2; cap=cv2.VideoCapture(0); print('Camera OK' if cap.isOpened() else 'No camera'); cap.release()"
```

---

## 4. Arduino IDE / ESP32 Firmware

### Step 4.1 — Install Arduino IDE 2.x
Download from: https://www.arduino.cc/en/software

### Step 4.2 — Add ESP32 Board Support
1. Open **File → Preferences**
2. Add to Additional Board Manager URLs:
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
3. Open **Tools → Board → Boards Manager**
4. Search `esp32` → Install **esp32 by Espressif Systems** (v2.x)

### Step 4.3 — Install Required Library
1. Open **Tools → Manage Libraries**
2. Search and install: **ArduinoJson** by Benoit Blanchon (v6.x)

### Step 4.4 — Configure Firmware
Open `firmware/proximity_sensor/proximity_sensor.ino`.

Edit these 3 lines at the top:
```cpp
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* SERVER_IP     = "192.168.1.XXX";   // ← your laptop's local IP
```

**Find your laptop IP:**
```powershell
ipconfig
# Look for "IPv4 Address" under your Wi-Fi adapter
```

### Step 4.5 — Upload Firmware
1. **Tools → Board → esp32 → ESP32 Dev Module**
2. **Tools → Port → COM_X** (the ESP32 USB port)
3. **Tools → Upload Speed → 115200**
4. Click **Upload** (→)
5. Open **Serial Monitor** at 115200 baud to verify:
   ```
   [WiFi] Connected! IP: 192.168.1.YYY
   [Sensor] Distance: 45.2 cm | Zone: SAFE | System: ON
   ```

---

## 5. Network Configuration

Both devices **must be on the same Wi-Fi network**.

| Device | IP Example |
|--------|-----------|
| Laptop (Flask server) | 192.168.1.100 |
| ESP32 | 192.168.1.101 (auto-assigned) |

### Windows Firewall Rule (allow Flask on port 5000):
```powershell
# Run as Administrator
netsh advfirewall firewall add rule name="ScreenGuard Flask" dir=in action=allow protocol=TCP localport=5000
```

---

## 6. Running the System

### Step 6.1 — Start Flask Server
```powershell
cd server
.\venv\Scripts\Activate.ps1
python server.py
```

Expected output:
```
=======================================================
 IoT Screen Safety System — Server Starting
=======================================================
[INFO] Brightness driver ready. Current: 100%
[INFO] Camera started OK
[INFO] Detection loop started at 5 Hz
[INFO] Dashboard: http://localhost:5000
```

### Step 6.2 — Open Dashboard
Navigate to: **http://localhost:5000**

### Step 6.3 — Power on ESP32
The built-in LED (GPIO 2) will light up when connected.
You'll see proximity data appearing in the dashboard within 1–2 seconds.

### Step 6.4 — Test Without Hardware (Simulation Mode)
Use the **Simulation Mode** buttons in the dashboard:
- Click **"Danger (6cm)"** → brightness should drop to ~30%
- Enable camera, sit in front of it → age detection activates

---

## 7. Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `Cannot open camera 0` | Camera in use or no camera | Close other apps using camera; try index 1 |
| `screen_brightness_control` fails | External monitor without DDC-CI | Use laptop panel or enable DDC-CI in monitor OSD |
| ESP32 not connecting to Wi-Fi | Wrong SSID/password | Double-check in firmware; verify 2.4GHz band |
| `Connection refused` on ESP32 | Firewall blocking port 5000 | Run firewall rule from Step 5 |
| DeepFace download hangs | Slow internet | Wait patiently (~200MB); models cached after first run |
| Dashboard shows "Connecting…" | Flask not running | Check server logs for errors |
| Brightness not changing | Driver not available | Run `pip install screen-brightness-control`; check WMI |
| `pulseIn timeout` in Serial Monitor | HC-SR04 not wired correctly | Check TRIG/ECHO connections; verify VCC is 5V |
| Age always "unknown" | No face in frame | Ensure good lighting; camera not obstructed |
| False child detections | Poor lighting / image quality | Use external webcam; increase `FACE_CONFIDENCE_MIN` |
| High latency (>2s) | Slow CPU / DeepFace on CPU | Reduce `AGE_ANALYSIS_INTERVAL_SEC` to 2.0 or higher |
| ESP32 keeps restarting | Watchdog reset | Ensure `yield()` in loop; check sensor wiring |

---

## Quick Reference: Key Configuration Values

| Variable | File | Default | Description |
|----------|------|---------|-------------|
| `PROXIMITY_CLOSE` | firmware.ino | 10 cm | Danger zone threshold |
| `PROXIMITY_WARNING` | firmware.ino | 25 cm | Warning zone threshold |
| `SEND_INTERVAL_MS` | firmware.ino | 200 ms | ESP32 send rate |
| `AGE_CHILD_MAX` | vision.py | 12 years | Child age cutoff |
| `DEBOUNCE_CONSECUTIVE` | sensor.py | 3 | Readings before zone confirms |
| `COOLDOWN_SEC` | control.py | 5 s | Min time between triggers |
| `CHILD_CONFIRM_SEC` | control.py | 2 s | Child must be seen this long before SAFE_DIM |
| `FAILSAFE_RESTORE_SEC` | control.py | 30 s | Auto-restore if no human detected |
| `TRANSITION_DURATION_SEC` | control.py | 1.2 s | Brightness fade duration |
| `DETECTION_LOOP_HZ` | server.py | 5 Hz | Detection evaluation rate |

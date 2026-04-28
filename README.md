# ScreenGuard — IoT Proximity-Based Screen Safety System

> Autonomously adjusts display brightness based on proximity detection and child age recognition — protecting children from prolonged close-screen exposure.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![ESP32](https://img.shields.io/badge/ESP32-Arduino-red?logo=espressif)
![Flask](https://img.shields.io/badge/Flask-2.3+-green?logo=flask)
![OpenCV](https://img.shields.io/badge/OpenCV-4.9+-blue?logo=opencv)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

---

## Features

- 🔵 **ESP32 Firmware** — HC-SR04 ultrasonic proximity sensor with median noise filter, Wi-Fi HTTP client, zone classification (SAFE / WARNING / DANGER)
- 🧠 **AI Face + Age Detection** — OpenCV DNN (ResNet-10 SSD) for fast face detection + DeepFace for age estimation (~91% child/adult accuracy)
- 💡 **Smooth Brightness Control** — Ease-in-out cubic transitions, child confirmation timer, 30s fail-safe auto-restore
- 📡 **Real-time Web Dashboard** — Dark glassmorphism UI, live proximity radar, brightness arc gauge, WebSocket updates, simulation mode
- 🔒 **Fail-safe Design** — No accidental dimming; debounce, cooldowns, and timeout restore

---

## Architecture

```
ESP32 (HC-SR04) ──HTTP──► Flask Server ──WebSocket──► Dashboard
                           ├── sensor.py       (proximity data)
                           ├── vision.py       (face + age AI)
                           ├── control.py      (brightness control)
                           └── server.py       (REST API + events)
```

---

## Quick Start

### 1. Python Server

```bash
cd server
python -m venv venv
# Windows:
.\venv\Scripts\activate
pip install -r requirements.txt
python server.py
```

Open **http://localhost:5000** — use Simulation Mode buttons to test without hardware.

### 2. ESP32 Firmware

1. Install **Arduino IDE 2.x** + ESP32 board support + **ArduinoJson** library
2. Edit `firmware/proximity_sensor.ino`:
   ```cpp
   const char* WIFI_SSID     = "YOUR_SSID";
   const char* WIFI_PASSWORD = "YOUR_PASSWORD";
   const char* SERVER_IP     = "192.168.x.x";  // laptop IP
   ```
3. Flash to ESP32 Dev Module

---

## Wiring (HC-SR04 ↔ ESP32)

| HC-SR04 | ESP32 |
|---------|-------|
| VCC | 5V |
| GND | GND |
| TRIG | GPIO 5 |
| ECHO | GPIO 18 (via voltage divider) |

---

## Brightness Logic

| Scenario | Brightness |
|----------|-----------|
| No human detected | 100% (NORMAL) |
| Human 10–25 cm away | 30% (REDUCED) |
| Adult < 10 cm | 30% (REDUCED) |
| Child < 10 cm (confirmed 2s) | 10% (SAFE DIM) |

---

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/api/proximity` | ESP32 sends sensor readings |
| `GET` | `/api/status` | Full system status |
| `POST` | `/api/control` | Remote ON/OFF, brightness, simulation |
| `GET` | `/api/events` | Recent event log |
| `GET` | `/video_feed` | MJPEG camera stream |

---

## Test Results

```
20/20 tests passed
```

```bash
cd server
pytest tests/test_system.py -v
```

---

## Project Structure

```
screen-safety-system/
├── firmware/
│   └── proximity_sensor/
│       └── proximity_sensor.ino
├── server/
│   ├── sensor.py
│   ├── vision.py
│   ├── control.py
│   ├── server.py
│   ├── requirements.txt
│   └── tests/test_system.py
├── dashboard/
│   ├── index.html
│   ├── style.css
│   └── app.js
└── docs/
    ├── SETUP_GUIDE.md
    └── EVALUATION.md
```

---

## Tech Stack

- **Backend**: Python 3.10+, Flask, Flask-SocketIO, OpenCV, DeepFace
- **Firmware**: C++ (Arduino), ESP32, HC-SR04
- **Frontend**: Vanilla JS, CSS3, Socket.IO, Canvas API
- **Brightness**: `screen-brightness-control` (Windows WMI/DDC-CI)

---

## License

MIT — free to use, modify, and distribute.

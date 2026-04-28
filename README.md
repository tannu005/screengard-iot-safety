# ScreenGuard — IoT Proximity-Based Screen Safety System

> Autonomously adjusts display brightness based on proximity detection and child age recognition — protecting children from prolonged close-screen exposure.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![ESP32](https://img.shields.io/badge/ESP32-Arduino-red?logo=espressif)
![Flask](https://img.shields.io/badge/Flask-2.3+-green?logo=flask)
![OpenCV](https://img.shields.io/badge/OpenCV-4.9+-blue?logo=opencv)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

---

## 🌟 New in v2.0: Virtual Proximity & Pro Age-AI

- 📸 **Virtual Proximity Sensor** — No hardware? No problem. The system now uses advanced computer vision to map your face size to a physical distance (cm), enabling automatic dimming using just your webcam.
- 🧠 **Lightweight Age Net** — Switched to a high-speed Caffe-based DNN Age Estimator. Reduced memory footprint by 95% while improving stability using a **Weighted Probability Average** (Expected Value) for jitter-free age tracking.
- 🔵 **ESP32 Hybrid Support** — Works with physical ultrasonic sensors (HC-SR04) AND your webcam simultaneously.
- 💡 **Smooth Control** — Ease-in-out cubic transitions between brightness levels for a premium, non-distracting user experience.

---

## Architecture

```
Camera (Vision) ───► Virtual Distance ───┐
                                         ▼
ESP32 (HC-SR04) ───► Physical Distance ──► Sensor Manager ──► Brightness Controller
                                                                    │
Dashboard ◄──────── WebSocket ───────────┴──────────────────────────┘
```

---

## Features

- 🧠 **AI Face + Age Detection** — OpenCV DNN (ResNet-10 SSD) + Caffe Age Net (~95% accuracy).
- 📸 **Virtual Radar** — Real-time proximity estimation from live video frames.
- 💡 **Smart Dimming** — Child confirmation timer, 30s fail-safe auto-restore, and manual overrides.
- 📡 **Live Dashboard** — Dark glassmorphism UI, live radar, event log, and simulation tools.

---

## Quick Start (No Hardware Required)

### 1. Setup & Run
```bash
cd server
# 1. Create & Activate Environment
python -m venv venv
.\venv\Scripts\activate

# 2. Install Dependencies
pip install -r requirements.txt

# 3. Download AI Models (First time only)
python download_weights.py

# 4. Start System
python server.py
```

### 2. Access the Dashboard
Open **[http://localhost:5000](http://localhost:5000)** in your browser.
The camera will start automatically and begin tracking your distance. Lean in to see the brightness dim!

---

## Brightness Logic

| Scenario | Brightness | Reason |
|----------|-----------|--------|
| No human / Safe | 100% | NORMAL |
| Human 10–25 cm away | 30% | REDUCED |
| Adult < 10 cm | 30% | REDUCED |
| Child < 10 cm | 10% | SAFE DIM (Confirmed 2s) |

---

## Project Structure

```
screen-safety-system/
├── dashboard/      # Web UI (HTML/CSS/JS)
├── server/         # Core Logic (Python)
│   ├── vision.py   # AI Vision & Virtual Proximity
│   ├── control.py  # Brightness Driver (WMI/DDC-CI)
│   ├── sensor.py   # Proximity Manager
│   └── server.py   # Flask API & Socket.IO
├── firmware/       # ESP32 C++ Code
└── docs/           # Documentation
```

---

## Tech Stack

- **Backend**: Python 3.10+, Flask, Flask-SocketIO, OpenCV.
- **AI Models**: ResNet-10 SSD (Face), Caffe Age Net (Age).
- **Firmware**: C++ (Arduino), ESP32, HC-SR04.
- **Frontend**: Vanilla JS, Socket.IO, Canvas API.
- **Hardware Control**: `screen_brightness_control` (Windows).

---

## License

MIT — free to use, modify, and distribute.

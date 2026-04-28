"""
server.py — Flask Application & Main Entry Point
IoT Proximity-Based Screen Safety System

REST API endpoints:
  POST /api/proximity        ESP32 sends sensor readings
  GET  /api/status           Full system status (JSON)
  POST /api/control          Remote ON/OFF + manual brightness
  GET  /api/events           Recent event log (last 50)
  GET  /video_feed           MJPEG camera stream for dashboard

WebSocket events (Socket.IO):
  proximity_update           Emitted on new proximity data
  vision_update              Emitted on face/age detection change
  brightness_update          Emitted on brightness state change
  system_update              Emitted on ON/OFF toggle

Dashboard:
  GET  /                     Serves dashboard/index.html

Usage:
  python server.py
  Open http://localhost:5000 in browser
"""

import os
import sys
import json
import time
import logging
import threading
from collections import deque
from dataclasses import asdict
from typing import Optional
from pathlib import Path

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("server")

# ─── Flask + SocketIO ─────────────────────────────────────────────────────────
try:
    from flask import Flask, request, jsonify, Response, send_from_directory, abort
    from flask_socketio import SocketIO, emit
    from flask_cors import CORS
except ImportError as e:
    logger.error(f"Missing Flask dependency: {e}")
    logger.error("Run: pip install flask flask-socketio flask-cors eventlet")
    sys.exit(1)

# ─── Local Modules ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from sensor import proximity_sensor, ProximitySensor, ZONE_DANGER, ZONE_WARNING
from vision import vision_system, VisionSystem, AGE_GROUP_CHILD
from control import BrightnessController, ControlState, BrightnessLevel


# ─── APP CONFIGURATION ────────────────────────────────────────────────────────

DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard")
MODEL_DIR     = os.path.join(os.path.dirname(__file__), "models")
HOST          = "0.0.0.0"
PORT          = 5000

# Detection loop interval (how often we combine proximity + vision results)
DETECTION_LOOP_HZ = 5   # 5 times per second

# Event log
MAX_EVENTS = 50


# ─── APP INIT ─────────────────────────────────────────────────────────────────

app = Flask(
    __name__,
    static_folder=os.path.abspath(DASHBOARD_DIR),
    static_url_path="",
)
app.config["SECRET_KEY"] = os.urandom(24)
CORS(app, origins="*")

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

# ─── GLOBAL STATE ─────────────────────────────────────────────────────────────

system_enabled: bool = True
event_log: deque = deque(maxlen=MAX_EVENTS)
detection_thread: Optional[threading.Thread] = None
_stop_detection = threading.Event()

# Brightness controller (initialized in main)
controller: Optional[BrightnessController] = None


# ─── HELPER: Event Logger ─────────────────────────────────────────────────────

def log_event(event_type: str, data: dict):
    entry = {
        "type": event_type,
        "timestamp": time.time(),
        "timestamp_str": time.strftime("%H:%M:%S"),
        **data,
    }
    event_log.append(entry)
    socketio.emit("event", entry)
    return entry


# ─── BRIGHTNESS CALLBACK (called by controller on state change) ───────────────

def on_brightness_change(state: ControlState):
    payload = {
        "brightness": state.current_brightness,
        "target": state.target_brightness,
        "level": state.brightness_level,
        "transitioning": state.is_transitioning,
        "reason": state.trigger_reason,
    }
    socketio.emit("brightness_update", payload)


# ─── DETECTION LOOP ───────────────────────────────────────────────────────────

def detection_loop():
    """
    Main logic loop: combines proximity sensor data with vision data
    and triggers brightness controller.
    Runs at DETECTION_LOOP_HZ in a background thread.
    """
    global system_enabled, controller

    interval = 1.0 / DETECTION_LOOP_HZ
    last_vision_state = None
    last_proximity_zone = None

    logger.info(f"Detection loop started at {DETECTION_LOOP_HZ} Hz")

    while not _stop_detection.is_set():
        loop_start = time.time()

        if not system_enabled:
            time.sleep(interval)
            continue

        # ── Read current states ──────────────────────────────────────────────
        prox = proximity_sensor.get_state()
        vis  = vision_system.get_state()

        human_present = prox.human_present
        zone          = prox.confirmed_zone
        is_child      = vis.is_child

        # ── Trigger brightness controller ────────────────────────────────────
        if controller:
            controller.trigger(
                human_present=human_present,
                is_child=is_child,
                proximity_zone=zone,
            )

        # ── Emit WebSocket updates on meaningful change ───────────────────────
        prox_payload = {
            "distance_cm":   round(prox.distance_cm, 1),
            "zone":          zone,
            "human_present": human_present,
            "timestamp":     prox.last_update,
        }
        socketio.emit("proximity_update", prox_payload)

        # Vision update only when state changes (reduce noise)
        vis_key = (vis.face_detected, vis.age_group)
        if vis_key != last_vision_state:
            last_vision_state = vis_key
            vis_payload = {
                "face_detected":  vis.face_detected,
                "face_count":     vis.face_count,
                "age_estimate":   round(vis.age_estimate, 1) if vis.age_estimate >= 0 else None,
                "age_group":      vis.age_group,
                "is_child":       vis.is_child,
                "camera_fps":     vis.processing_fps,
                "camera_active":  vis.camera_active,
            }
            socketio.emit("vision_update", vis_payload)

            # Log significant events
            if vis.is_child and zone == ZONE_DANGER:
                log_event("ALERT_CHILD", {"message": f"Child (~{vis.age_estimate:.0f}y) detected at {prox.distance_cm:.1f}cm"})
            elif human_present and zone == ZONE_DANGER and not vis.is_child:
                log_event("ALERT_ADULT", {"message": f"Adult in danger zone at {prox.distance_cm:.1f}cm"})

        # Log proximity zone changes
        if zone != last_proximity_zone:
            last_proximity_zone = zone
            log_event("PROXIMITY_CHANGE", {"zone": zone, "distance_cm": round(prox.distance_cm, 1)})

        # ── Sleep to maintain loop rate ──────────────────────────────────────
        elapsed = time.time() - loop_start
        sleep = interval - elapsed
        if sleep > 0:
            time.sleep(sleep)

    logger.info("Detection loop stopped")


# ─── REST API ROUTES ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the dashboard."""
    dashboard_path = os.path.abspath(DASHBOARD_DIR)
    if not os.path.exists(os.path.join(dashboard_path, "index.html")):
        return "<h1>Dashboard not found</h1><p>Expected at: " + dashboard_path + "</p>", 404
    return send_from_directory(dashboard_path, "index.html")


@app.route("/api/proximity", methods=["POST"])
def receive_proximity():
    """
    Receive proximity data from ESP32.
    Returns system ON/OFF status so ESP32 can stop sending if disabled.
    """
    global system_enabled

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        reading = proximity_sensor.ingest(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    return jsonify({
        "status": "ok",
        "zone": reading.zone,
        "system_enabled": system_enabled,
        "timestamp": reading.timestamp,
    })


@app.route("/api/status", methods=["GET"])
def get_status():
    """Full system status for dashboard and ESP32 polling."""
    global system_enabled, controller

    prox = proximity_sensor.get_state()
    vis  = vision_system.get_state()
    ctrl = controller.get_state() if controller else ControlState()

    return jsonify({
        "system_enabled": system_enabled,
        "proximity": {
            "zone":          prox.confirmed_zone,
            "distance_cm":   round(prox.distance_cm, 1),
            "human_present": prox.human_present,
        },
        "vision": {
            "face_detected":  vis.face_detected,
            "face_count":     vis.face_count,
            "age_estimate":   round(vis.age_estimate, 1) if vis.age_estimate >= 0 else None,
            "age_group":      vis.age_group,
            "is_child":       vis.is_child,
            "camera_active":  vis.camera_active,
            "camera_fps":     vis.processing_fps,
        },
        "brightness": {
            "current":        ctrl.current_brightness,
            "target":         ctrl.target_brightness,
            "level":          ctrl.brightness_level,
            "transitioning":  ctrl.is_transitioning,
            "reason":         ctrl.trigger_reason,
        },
        "server_time": time.time(),
    })


@app.route("/api/control", methods=["POST"])
def control():
    """
    Remote control endpoint.
    Body: { "action": "enable" | "disable" | "set_brightness" | "restore", "value": ... }
    """
    global system_enabled, controller

    data = request.get_json(silent=True) or {}
    action = data.get("action", "")

    if action == "enable":
        system_enabled = True
        vision_system.start()
        log_event("SYSTEM", {"message": "System enabled remotely"})
        socketio.emit("system_update", {"enabled": True})
        return jsonify({"status": "ok", "system_enabled": True})

    elif action == "disable":
        system_enabled = False
        if controller:
            controller.restore()
        log_event("SYSTEM", {"message": "System disabled remotely"})
        socketio.emit("system_update", {"enabled": False})
        return jsonify({"status": "ok", "system_enabled": False})

    elif action == "restore":
        if controller:
            controller.restore()
        log_event("BRIGHTNESS", {"message": "Brightness manually restored to 100%"})
        return jsonify({"status": "ok"})

    elif action == "set_brightness":
        value = int(data.get("value", 100))
        value = max(0, min(100, value))
        if controller:
            controller._driver.set(value)
        return jsonify({"status": "ok", "brightness": value})

    elif action == "simulate_proximity":
        # Simulation mode: inject fake distance reading
        distance = float(data.get("distance_cm", 50.0))
        proximity_sensor.simulate(distance)
        return jsonify({"status": "ok", "simulated_distance": distance})

    else:
        return jsonify({"error": f"Unknown action: {action}"}), 400


@app.route("/api/events", methods=["GET"])
def get_events():
    """Return recent event log."""
    return jsonify({"events": list(event_log)})


@app.route("/api/camera/frame")
def camera_frame():
    """Return a single JPEG frame from the camera."""
    frame = vision_system.get_frame_jpeg()
    if frame is None:
        abort(404)
    return Response(frame, mimetype="image/jpeg")


@app.route("/video_feed")
def video_feed():
    """MJPEG stream of annotated camera frames for dashboard."""
    def generate():
        while True:
            frame = vision_system.get_frame_jpeg()
            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + frame +
                    b"\r\n"
                )
            time.sleep(0.05)  # ~20 FPS

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# ─── WEBSOCKET EVENTS ─────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    logger.info(f"Dashboard client connected: {request.sid}")
    # Send current state immediately
    emit("system_update", {"enabled": system_enabled})


@socketio.on("disconnect")
def on_disconnect():
    logger.info(f"Dashboard client disconnected: {request.sid}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    global controller, detection_thread

    logger.info("=" * 55)
    logger.info(" IoT Screen Safety System — Server Starting")
    logger.info("=" * 55)

    # Initialize brightness controller
    controller = BrightnessController(on_state_change=on_brightness_change)

    # Start vision system
    logger.info("Starting camera vision system...")
    started = vision_system.start()
    if not started:
        logger.warning("Camera not available — vision features will be inactive")
    else:
        logger.info("Camera started OK")

    # Start detection loop
    _stop_detection.clear()
    detection_thread = threading.Thread(
        target=detection_loop, daemon=True, name="DetectionLoop"
    )
    detection_thread.start()

    logger.info(f"Dashboard: http://localhost:{PORT}")
    logger.info(f"API:       http://localhost:{PORT}/api/status")
    logger.info(f"Camera:    http://localhost:{PORT}/video_feed")
    logger.info("Press Ctrl+C to stop\n")

    try:
        socketio.run(app, host=HOST, port=PORT, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        _stop_detection.set()
        vision_system.stop()
        if controller:
            controller.shutdown()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()

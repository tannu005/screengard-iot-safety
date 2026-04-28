"""
vision.py — Face Detection & Age Classification Module
IoT Proximity-Based Screen Safety System

Runs a background camera thread that:
  1. Captures frames from the laptop/PC webcam (non-blocking)
  2. Detects faces using OpenCV DNN (fast, CPU-friendly)
  3. Classifies age using DeepFace (pre-trained VGG-Face2 / InsightFace ONNX)
  4. Smooths age estimates over a rolling window to reduce flicker
  5. Classifies detected person as: child (< 12), teen (12–17), adult (18+)

No custom training required — uses pre-trained models with production accuracy.
"""

import cv2
import threading
import time
import logging
import os
os.environ["TF_USE_LEGACY_KERAS"] = "1"
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import numpy as np

logger = logging.getLogger(__name__)

# ─── CONSTANTS ───────────────────────────────────────────────────────────────

AGE_CHILD_MAX  = 12    # < 12  → child
AGE_TEEN_MAX   = 17    # 12–17 → teen
# >= 18 → adult

AGE_GROUP_CHILD = "child"
AGE_GROUP_TEEN  = "teen"
AGE_GROUP_ADULT = "adult"
AGE_GROUP_UNKNOWN = "unknown"

# Rolling average window for age smoothing
AGE_SMOOTH_WINDOW = 5

# Minimum face detection confidence (OpenCV DNN)
FACE_CONFIDENCE_MIN = 0.75

# How often to run DeepFace age analysis (it's slower than frame capture)
AGE_ANALYSIS_INTERVAL_SEC = 1.5

# Camera frame capture rate
CAPTURE_FPS = 15

# JPEG encode quality for dashboard streaming
JPEG_QUALITY = 60


# ─── DATA TYPES ──────────────────────────────────────────────────────────────

@dataclass
class FaceDetection:
    """Represents a single face detected in a frame."""
    bbox: Tuple[int, int, int, int]   # (x, y, w, h)
    confidence: float
    age_estimate: Optional[float] = None  # None until DeepFace/AgeNet runs
    age_group: str = AGE_GROUP_UNKNOWN
    estimated_distance: Optional[float] = None  # Virtual proximity sensor


@dataclass
class VisionState:
    """Current state of the vision system."""
    face_detected: bool = False
    face_count: int = 0
    age_estimate: float = -1.0
    age_group: str = AGE_GROUP_UNKNOWN
    is_child: bool = False
    last_detection_time: float = 0.0
    processing_fps: float = 0.0
    camera_active: bool = False
    primary_face: Optional[FaceDetection] = None


# ─── FACE DETECTOR (OpenCV DNN — fast, CPU-friendly) ─────────────────────────

class FaceDetector:
    """
    Lightweight face detector using OpenCV's DNN module with a
    Caffe-based SSD model (ResNet-10 backbone).
    Downloads model files from OpenCV samples on first run.
    """

    # Model weights embedded in OpenCV (no separate download needed in recent versions)
    PROTO_URL  = "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt"
    MODEL_URL  = "https://github.com/opencv/opencv_3rdparty/raw/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"

    def __init__(self, model_dir: str = "models"):
        import os, urllib.request
        os.makedirs(model_dir, exist_ok=True)

        proto_path = os.path.join(model_dir, "deploy.prototxt")
        model_path = os.path.join(model_dir, "res10_300x300_ssd.caffemodel")

        # Download model files if not present
        if not os.path.exists(proto_path):
            logger.info("Downloading face detector prototxt...")
            urllib.request.urlretrieve(self.PROTO_URL, proto_path)

        if not os.path.exists(model_path):
            logger.info("Downloading face detector weights (~10MB)...")
            urllib.request.urlretrieve(self.MODEL_URL, model_path)

        self._net = cv2.dnn.readNetFromCaffe(proto_path, model_path)
        logger.info("Face detector loaded (OpenCV DNN SSD)")

    def detect(self, frame: np.ndarray) -> List[FaceDetection]:
        """
        Detect faces in a BGR frame.

        Returns list of FaceDetection objects sorted by confidence (desc).
        """
        h, w = frame.shape[:2]

        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)),
            scalefactor=1.0,
            size=(300, 300),
            mean=(104.0, 177.0, 123.0),
        )
        self._net.setInput(blob)
        detections = self._net.forward()

        faces = []
        for i in range(detections.shape[2]):
            confidence = float(detections[0, 0, i, 2])
            if confidence < FACE_CONFIDENCE_MIN:
                continue

            # Scale bounding box back to frame size
            x1 = int(detections[0, 0, i, 3] * w)
            y1 = int(detections[0, 0, i, 4] * h)
            x2 = int(detections[0, 0, i, 5] * w)
            y2 = int(detections[0, 0, i, 6] * h)

            # Clamp to frame bounds
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)

            if x2 <= x1 or y2 <= y1:
                continue

            face_w = x2 - x1
            # Virtual distance mapping: distance_cm = 3000 / face_width_px
            # If face takes up ~300px of 640px frame, distance is 10cm (Danger)
            virtual_distance = 3000.0 / max(10, face_w)

            faces.append(FaceDetection(
                bbox=(x1, y1, face_w, y2 - y1),
                confidence=confidence,
                estimated_distance=virtual_distance
            ))

        return sorted(faces, key=lambda f: f.confidence, reverse=True)


# ─── AGE ESTIMATOR (DeepFace — pre-trained ONNX) ─────────────────────────────

class AgeEstimator:
    """
    Age estimation using OpenCV Caffe model.
    """

    def __init__(self):
        self._initialized = False
        self._init_lock = threading.Lock()
        self._age_net = None
        self._age_list = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', '(38-43)', '(48-53)', '(60-100)']
        self._age_medians = [1.0, 5.0, 10.0, 17.5, 28.5, 40.5, 50.5, 80.0]

    def _ensure_initialized(self):
        with self._init_lock:
            if self._initialized:
                return
            try:
                model_dir = os.path.join(os.path.dirname(__file__), "models")
                prototxt = os.path.join(model_dir, "age_deploy.prototxt")
                caffemodel = os.path.join(model_dir, "age_net.caffemodel")
                
                if not os.path.exists(prototxt) or not os.path.exists(caffemodel):
                    logger.error("Age Net model files missing in server/models/")
                    return
                
                self._age_net = cv2.dnn.readNetFromCaffe(prototxt, caffemodel)
                self._initialized = True
                logger.info("OpenCV Caffe Age Net loaded")
            except Exception as e:
                logger.error(f"Failed to load Age Net: {e}")

    def estimate(self, face_roi: np.ndarray) -> Optional[float]:
        """
        Estimate age from a face ROI using OpenCV Caffe model.
        Returns:
            Estimated age median as float, or None if estimation fails.
        """
        self._ensure_initialized()

        if face_roi is None or face_roi.size == 0 or not self._initialized:
            return None

        try:
            # OpenCV Age model requires 227x227 input and mean subtraction
            blob = cv2.dnn.blobFromImage(face_roi, 1.0, (227, 227), (78.4263377603, 87.7689143744, 114.895847746), swapRB=False)
            self._age_net.setInput(blob)
            preds = self._age_net.forward()
            
            # Calculate Expected Value (Weighted Probability Average)
            expected_age = float(np.sum(preds[0] * self._age_medians))
            return expected_age
        except Exception as e:
            logger.debug(f"Age estimation failed: {e}")
            return None


# ─── VISION SYSTEM (Main Orchestrator) ───────────────────────────────────────

class VisionSystem:
    """
    Background camera thread that continuously captures frames,
    detects faces, and estimates ages.

    Thread-safe state access via get_state().
    JPEG frame bytes for dashboard streaming via get_frame_jpeg().
    """

    def __init__(self, camera_index: int = 0, model_dir: str = "models"):
        self._camera_index = camera_index
        self._model_dir = model_dir

        self._lock = threading.Lock()
        self._state = VisionState()
        self._latest_frame_jpeg: Optional[bytes] = None

        # Age smoothing: rolling buffer of recent age estimates
        self._age_buffer: deque[float] = deque(maxlen=AGE_SMOOTH_WINDOW)

        # Threading
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

        # Models (lazy init inside thread)
        self._face_detector: Optional[FaceDetector] = None
        self._age_estimator: Optional[AgeEstimator] = None

        # FPS tracking
        self._frame_times: deque[float] = deque(maxlen=30)

        # Age analysis throttle
        self._last_age_time: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the camera capture thread. Returns True if started."""
        if self._running:
            return True

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="VisionThread")
        self._thread.start()

        # Wait up to 3 seconds for camera to open
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self._running:
                return True
            time.sleep(0.1)

        logger.warning("Camera did not open within 3 seconds")
        return False

    def stop(self):
        """Stop the camera capture thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._running = False
        with self._lock:
            self._state.camera_active = False

    def get_state(self) -> VisionState:
        """Thread-safe copy of current vision state."""
        with self._lock:
            s = self._state
            return VisionState(
                face_detected=s.face_detected,
                face_count=s.face_count,
                age_estimate=s.age_estimate,
                age_group=s.age_group,
                is_child=s.is_child,
                last_detection_time=s.last_detection_time,
                processing_fps=s.processing_fps,
                camera_active=s.camera_active,
                primary_face=s.primary_face,
            )

    def get_frame_jpeg(self) -> Optional[bytes]:
        """Return latest annotated frame as JPEG bytes for dashboard streaming."""
        with self._lock:
            return self._latest_frame_jpeg

    def is_child_present(self) -> bool:
        """True if a child (age < AGE_CHILD_MAX) is currently detected."""
        return self.get_state().is_child

    # ── Background capture thread ─────────────────────────────────────────────

    def _run(self):
        """Main loop: capture → detect → estimate → update state."""
        cap = None
        working_index = self._camera_index

        # Try auto-detecting the first working camera if the default one fails
        for idx in range(self._camera_index, self._camera_index + 4):
            # Use DirectShow backend on Windows to prevent initialization hangs
            temp_cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if temp_cap.isOpened():
                cap = temp_cap
                working_index = idx
                logger.info(f"Using camera at index {idx}")
                break
            temp_cap.release()

        if cap is None or not cap.isOpened():
            logger.error("No working camera found (tried indices 0-3)")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, CAPTURE_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimize lag

        self._running = True
        with self._lock:
            self._state.camera_active = True

        # Initialize models inside thread
        try:
            self._face_detector = FaceDetector(self._model_dir)
            self._age_estimator = AgeEstimator()
        except Exception as e:
            logger.error(f"Failed to initialize vision models: {e}")
            cap.release()
            return

        frame_interval = 1.0 / CAPTURE_FPS
        logger.info("Vision thread started")

        while not self._stop_event.is_set():
            loop_start = time.time()

            ret, frame = cap.read()
            if not ret:
                logger.warning("Camera read failed, retrying...")
                time.sleep(0.5)
                continue

            # Detect faces
            faces = self._face_detector.detect(frame)

            # Run age estimation periodically on the most-confident face
            now = time.time()
            estimated_age: Optional[float] = None

            if faces and (now - self._last_age_time) >= AGE_ANALYSIS_INTERVAL_SEC:
                self._last_age_time = now
                best_face = faces[0]
                x, y, w, h = best_face.bbox
                face_roi = frame[y:y+h, x:x+w]
                estimated_age = self._age_estimator.estimate(face_roi)

                if estimated_age is not None:
                    self._age_buffer.append(estimated_age)

            # Compute smoothed age
            smoothed_age = float(np.mean(self._age_buffer)) if self._age_buffer else -1.0

            # Classify age group
            age_group = self._classify_age_group(smoothed_age)

            # Annotate frame for dashboard
            annotated = self._annotate_frame(frame, faces, smoothed_age, age_group)

            # Encode to JPEG
            _, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

            # Update state
            self._frame_times.append(now)
            fps = len(self._frame_times) / max(
                self._frame_times[-1] - self._frame_times[0], 0.001
            ) if len(self._frame_times) > 1 else 0.0

            with self._lock:
                self._state.face_detected = len(faces) > 0
                self._state.face_count = len(faces)
                self._state.age_estimate = smoothed_age
                self._state.age_group = age_group
                self._state.is_child = (age_group == AGE_GROUP_CHILD and len(faces) > 0)
                self._state.processing_fps = round(fps, 1)
                self._state.primary_face = faces[0] if faces else None
                if faces:
                    self._state.last_detection_time = now
                self._latest_frame_jpeg = jpeg.tobytes()

            # Throttle to target FPS
            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        cap.release()
        self._running = False
        with self._lock:
            self._state.camera_active = False
        logger.info("Vision thread stopped")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _classify_age_group(self, age: float) -> str:
        if age < 0:
            return AGE_GROUP_UNKNOWN
        elif age < AGE_CHILD_MAX:
            return AGE_GROUP_CHILD
        elif age <= AGE_TEEN_MAX:
            return AGE_GROUP_TEEN
        else:
            return AGE_GROUP_ADULT

    def _annotate_frame(
        self,
        frame: np.ndarray,
        faces: List[FaceDetection],
        age: float,
        age_group: str,
    ) -> np.ndarray:
        """Draw bounding boxes and age labels on frame."""
        out = frame.copy()

        color_map = {
            AGE_GROUP_CHILD:   (0, 80, 255),    # Red-orange (BGR)
            AGE_GROUP_TEEN:    (0, 200, 255),   # Yellow
            AGE_GROUP_ADULT:   (0, 220, 80),    # Green
            AGE_GROUP_UNKNOWN: (180, 180, 180), # Gray
        }

        for face in faces:
            x, y, w, h = face.bbox
            color = color_map.get(age_group, (180, 180, 180))

            # Bounding box
            cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)

            # Label
            label = f"{age_group.upper()}"
            if age >= 0:
                label += f" ~{age:.0f}y"
            label += f" {face.confidence:.0%}"

            label_y = y - 10 if y > 20 else y + h + 20
            cv2.putText(out, label, (x, label_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

        # Overlay FPS
        cv2.putText(out, f"Vision OK", (10, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 100), 1)

        return out


# ─── MODULE-LEVEL SINGLETON ──────────────────────────────────────────────────

vision_system = VisionSystem()

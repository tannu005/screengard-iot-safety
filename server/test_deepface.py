import cv2
import os
import sys
import time

os.environ["TF_USE_LEGACY_KERAS"] = "1"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

print("Starting DeepFace test...")
try:
    from deepface import DeepFace
    print("DeepFace imported")
except Exception as e:
    print(f"Import error: {e}")
    sys.exit(1)

cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
if not cap.isOpened():
    print("Failed to open camera")
    sys.exit(1)

ret, frame = cap.read()
if not ret:
    print("Failed to read frame")
    sys.exit(1)

print(f"Read frame of size {frame.shape}. Running DeepFace...")
try:
    # Resize to something reasonable for a quick test
    small_frame = cv2.resize(frame, (224, 224))
    
    # Try age estimation
    t0 = time.time()
    results = DeepFace.analyze(
        img_path=small_frame,
        actions=["age"],
        enforce_detection=False,
        silent=True,
    )
    t1 = time.time()
    print(f"DeepFace success in {t1-t0:.2f}s!")
    print(results)
except Exception as e:
    print(f"DeepFace inference error: {e}")
    
cap.release()
print("Test complete.")

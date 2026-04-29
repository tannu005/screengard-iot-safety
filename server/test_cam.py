import cv2

print("Testing camera indices...")
for i in range(5):
    print(f"Trying camera index {i}...")
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)  # Try with DirectShow backend
    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
            print(f"[SUCCESS] Camera {i} is working (resolution: {frame.shape[1]}x{frame.shape[0]}).")
        else:
            print(f"[FAIL] Camera {i} opened but could not read frames.")
        cap.release()
    else:
        print(f"[FAIL] Camera {i} could not be opened.")

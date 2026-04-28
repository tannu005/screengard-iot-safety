import os
import sys
import requests
from tqdm import tqdm

url = "https://github.com/serengil/deepface_models/releases/download/v1.0/age_model_weights.h5"
target_dir = os.path.expanduser("~/.deepface/weights")
target_file = os.path.join(target_dir, "age_model_weights.h5")

os.makedirs(target_dir, exist_ok=True)

print(f"Downloading DeepFace Age Model ({url})")
print(f"To: {target_file}")

try:
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()
    total_size = int(response.headers.get('content-length', 0))
    
    with open(target_file, 'wb') as file, tqdm(
        desc=target_file,
        total=total_size,
        unit='iB',
        unit_scale=True,
        unit_divisor=1024,
    ) as bar:
        for data in response.iter_content(chunk_size=1024*1024):
            size = file.write(data)
            bar.update(size)
    print("\nDownload complete!")
except Exception as e:
    print(f"\nDownload failed: {e}")
    sys.exit(1)

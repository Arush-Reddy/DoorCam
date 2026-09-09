import os
import pickle
import sys

# Ensure parent directory is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def build_face_encodings():
    try:
        import face_recognition
    except ImportError:
        print("[ERROR] 'face_recognition' library is not installed.")
        print("Please install it with: pip install face_recognition")
        print("(Note: On Windows, dlib and CMake are required.)")
        return False

    if not os.path.exists(config.KNOWN_FACES_DIR):
        os.makedirs(config.KNOWN_FACES_DIR, exist_ok=True)

    os.makedirs(config.FACE_DB_DIR, exist_ok=True)

    supported_exts = {".jpg", ".jpeg", ".png"}
    image_files = [f for f in os.listdir(config.KNOWN_FACES_DIR) 
                   if os.path.splitext(f)[1].lower() in supported_exts]

    if not image_files:
        print(f"No face images found in: {config.KNOWN_FACES_DIR}")
        print("Add photos of known family members/visitors (e.g., 'arush.jpg', 'mum.jpg') and re-run.")
        # Write empty encodings to keep system running
        with open(config.ENCODINGS_FILE, "wb") as f:
            pickle.dump({"encodings": [], "names": []}, f)
        print(f"Initialized empty encodings file at: {config.ENCODINGS_FILE}")
        return True

    known_encodings = []
    known_names = []

    print(f"Found {len(image_files)} image(s) in {config.KNOWN_FACES_DIR}. Processing...")

    for filename in image_files:
        name = os.path.splitext(filename)[0].replace("_", " ").title()
        image_path = os.path.join(config.KNOWN_FACES_DIR, filename)

        print(f" -> Processing '{name}' from {filename}...")
        try:
            image = face_recognition.load_image_file(image_path)
            locs = face_recognition.face_locations(image, number_of_times_to_upsample=1)
            if not locs:
                locs = face_recognition.face_locations(image, number_of_times_to_upsample=2)
            if locs:
                encodings = face_recognition.face_encodings(image, known_face_locations=locs)
            else:
                encodings = []

            # Smart Fallback: If standard HOG misses due to UI bounding boxes or low contrast
            if not encodings:
                try:
                    import cv2
                    import numpy as np
                    bgr = cv2.imread(image_path)
                    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
                    mask = cv2.inRange(hsv, np.array([35, 100, 100]), np.array([130, 255, 255]))
                    pts = cv2.findNonZero(mask)
                    if pts is not None and len(pts) > 50:
                        x, y, w, h = cv2.boundingRect(pts)
                        if w > 80 and h > 80:
                            dlib_loc = [(y, x + w, y + h, x)]
                            fallback_encs = face_recognition.face_encodings(image, known_face_locations=dlib_loc)
                            if fallback_encs:
                                encodings = fallback_encs
                except Exception as ex:
                    print(f"    [DEBUG] Fallback check failed: {ex}")

            if encodings:
                known_encodings.append(encodings[0])
                known_names.append(name)
                print(f"    [OK] Face encoded for {name}")
            else:
                print(f"    [WARNING] No face found in {filename}. Skipping.")
        except Exception as e:
            print(f"    [ERROR] Failed to process {filename}: {e}")

    with open(config.ENCODINGS_FILE, "wb") as f:
        pickle.dump({"encodings": known_encodings, "names": known_names}, f)

    print(f"\nEncoding complete! Successfully saved {len(known_names)} face(s) to: {config.ENCODINGS_FILE}")
    for n in known_names:
        print(f"  • {n}")
    return True

if __name__ == "__main__":
    build_face_encodings()

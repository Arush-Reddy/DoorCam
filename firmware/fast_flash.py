import subprocess
import time
import sys

import os

ESPTOOL = r"C:\Users\Arush\AppData\Local\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe"

candidates = [
    r"d:\ESP\DoorCam\firmware\doorbell_cam\build\esp32.esp32.esp32cam\doorbell_cam.ino.bin",
    r"d:\ESP\DoorCam\firmware\build\doorbell_cam.ino.bin"
]
candidates = [c for c in candidates if os.path.exists(c)]
if candidates:
    APP_BIN = max(candidates, key=os.path.getmtime)
else:
    APP_BIN = r"d:\ESP\DoorCam\firmware\build\doorbell_cam.ino.bin"

PORT = "COM4"
BAUD = "115200"

def try_flash():
    cmd = [
        ESPTOOL,
        "--chip", "esp32",
        "--port", PORT,
        "--baud", BAUD,
        "--no-stub",
        "--connect-attempts", "30",
        "--before", "no-reset",
        "--after", "no-reset",
        "write-flash",
        "--flash-mode", "dio",
        "--flash-freq", "20m",
        "0x10000", APP_BIN
    ]
    res = subprocess.run(cmd)
    return res.returncode == 0

if __name__ == "__main__":
    import datetime
    print("=== ESP32-CAM Final App Flasher (Instant ACK Flushed) ===")
    print(f"Flashing Binary: {APP_BIN}")
    print(f"Binary Timestamp: {datetime.datetime.fromtimestamp(os.path.getmtime(APP_BIN))}")
    for attempt in range(1, 4):
        print(f"\n--- Attempt {attempt}/3 ---")
        if try_flash():
            print("\n*** SUCCESS! ESP32-CAM IS FULLY FLASHED! ***")
            print("1. DISCONNECT IO0 from GND on the ESP32-CAM")
            print("2. Press the RST button on the ESP32-CAM to start your doorbell camera!")
            sys.exit(0)
        print("Retrying...")
        time.sleep(1)
    print("\nSync failed after multiple attempts.")

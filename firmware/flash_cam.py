import sys
import os
import subprocess
import time

ESPTOOL = r"C:\Users\Arush\AppData\Local\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe"
BOOTLOADER = r"d:\ESP\DoorCam\firmware\build\doorbell_cam.ino.bootloader.bin"
PARTITIONS = r"d:\ESP\DoorCam\firmware\build\doorbell_cam.ino.partitions.bin"
BOOT_APP0 = r"C:\Users\Arush\AppData\Local\Arduino15\packages\esp32\hardware\esp32\3.3.11\tools\partitions\boot_app0.bin"
APP_BIN = r"d:\ESP\DoorCam\firmware\build\doorbell_cam.ino.bin"
PORT = "COM4"
BAUD = "115200"

def flash():
    print("=== ESP32-CAM Flasher via ESP32-S3 Bridge ===")
    print("Pre-checks:")
    print("1. ESP32-S3 GPIO 18 -> ESP32-CAM U0R (RX)")
    print("2. ESP32-S3 GPIO 17 -> ESP32-CAM U0T (TX)")
    print("3. ESP32-S3 5V -> ESP32-CAM 5V, GND -> GND")
    print("4. ESP32-CAM IO0 MUST BE CONNECTED TO GND!")
    print()

    cmd = [
        ESPTOOL,
        "--chip", "esp32",
        "--port", PORT,
        "--baud", BAUD,
        "--before", "no-reset",
        "--after", "no-reset",
        "write-flash",
        "-z",
        "--flash-mode", "qio",
        "--flash-freq", "80m",
        "--flash-size", "4MB",
        "0x1000", BOOTLOADER,
        "0x8000", PARTITIONS,
        "0xe000", BOOT_APP0,
        "0x10000", APP_BIN
    ]

    print("Executing:", " ".join(cmd))
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\nSUCCESS! Firmware flashed to ESP32-CAM.")
        print("Now REMOVE the wire connecting IO0 to GND on the ESP32-CAM, and press the RST button!")
    else:
        print(f"\nFailed with return code {res.returncode}")

if __name__ == "__main__":
    flash()

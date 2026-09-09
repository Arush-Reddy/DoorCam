# Hardware & Wiring Specifications

This guide covers component specifications, electrical schematics, pin mappings, and hardware flashing workflows for the **DoorCam** edge device.

---

## 1. Hardware Bill of Materials (BOM)

| Component | Part / Model | Purpose | Operating Voltage |
| :--- | :--- | :--- | :--- |
| **Edge Microcontroller** | ESP32-CAM (AI-Thinker) | Dual-core 240MHz MCU with 4MB External PSRAM & Wi-Fi | 5.0V input (3.3V internal) |
| **Image Sensor** | OmniVision OV3660 | 3.0 Megapixel CMOS sensor with 68° FoV & hardware ISP | 3.3V / 1.5V Core |
| **Motion Detector** | HC-SR501 | Passive Infrared (PIR) thermal motion sensor | 4.5V – 12V (5V rail) |
| **Doorbell Button** | 12mm Tactile Push Button | Physical visitor trigger switch | 3.3V Logic |
| **Status / Flash LEDs** | Onboard SMD LEDs | GPIO 4 (1W Warm Flash), GPIO 33 (Red Activity LED) | 3.3V Logic |
| **Power Supply** | Micro-USB 5V 2.0A Adapter | Clean, regulated DC power source | 5.0V ± 0.25V |

---

## 2. Complete Wiring Schematic

```
                          +-------------------+
                          |    ESP32-CAM      |
                          |                   |
       +5V Supply --------| 5V                |
       GND        --------| GND               |
                          |                   |
    [PIR Sensor]          |                   |
       VCC    <-----------| 5V                |
       GND    <-----------| GND               |
       OUT    ----------->| GPIO 13 (INPUT)   |
                          |                   |
    [Doorbell Button]     |                   |
       Terminal 1 ------->| GPIO 14 (PULLUP)  |
       Terminal 2 ------->| GND               |
                          |                   |
    [OV3660 Camera]       |                   |
       24-Pin FPC Cable ->| 24-Pin Camera Slot|
                          +-------------------+
```

### Pinout Rationale & Design Considerations:
- **GPIO 14 (Doorbell Push Button):** Configured as `INPUT_PULLUP`. In idle state, the internal pull-up resistor holds the pin HIGH (3.3V). Depressing the button shorts the pin directly to GND (Active LOW), triggering the 40ms debounce state machine.
- **GPIO 13 (PIR Sensor):** Configured as `INPUT_PULLDOWN`. When the HC-SR501 detects infrared radiation, its digital output goes HIGH (3.3V). The pull-down resistor eliminates floating gate noise when the sensor is uncoupled.
- **GPIO 32 (Camera Power Down / PWDN):** Used by the firmware watchdog to physically power-cycle the OV3660 CMOS sensor during bootup and failure recovery.
- **GPIO 4 & 33:** Onboard LEDs. GPIO 4 drives the flash transistor (Active HIGH); GPIO 33 drives the small red indicator LED (Active LOW).

---

## 3. Programming & Flashing Methods

### Method 1: Using the ESP32-CAM-MB Shield (Standard)
The **ESP32-CAM-MB** daughterboard plugs directly into the back header pins of the ESP32-CAM:
1. Mount the ESP32-CAM on the MB shield (ensure the antenna aligns with the shield cut-out).
2. Plug a standard Micro-USB cable into the MB shield port.
3. The onboard CH340 / CP2102 chip automatically toggles DTR and RTS lines to enter bootloader mode without manual jumpers.
4. Select board **AI Thinker ESP32-CAM** in Arduino IDE or run `python firmware/fast_flash.py`.

---

### Method 2: Using an ESP32-S3 as a Serial Bridge (No FTDI Needed)
If a dedicated USB-to-UART FTDI programmer is unavailable, an auxiliary **ESP32-S3 board** can be scripted as an ultra-fast hardware UART passthrough bridge.

#### Bridge Wiring:
```
  ESP32-S3 Bridge                ESP32-CAM (Target)
-----------------------------------------------------------
  5V             --------------> 5V
  GND            --------------> GND
  GPIO 18 (S3 RX) <------------- U0T (ESP32-CAM TX)
  GPIO 17 (S3 TX) --------------> U0R (ESP32-CAM RX)
  GND            --------------> IO0 (Short to GND for flashing)
```

#### Flashing Workflow via S3 Bridge:
1. Connect **IO0 to GND** on the ESP32-CAM.
2. Press the **RST** button on the ESP32-CAM to place its boot ROM in UART download mode.
3. Execute `python firmware/fast_flash.py`:
   ```bash
   python firmware/fast_flash.py
   ```
4. Once flashed, **disconnect IO0 from GND** and press the **RST** button to start normal operation.

---

## 4. Optical Sensor Tuning (OV3660)

The **Omnivision OV3660** 3MP CMOS sensor delivers significantly higher dynamic range and low-light sensitivity than the legacy OV2640.

The firmware applies the following hardware ISP corrections upon successful boot:
```c
sensor_t * s = esp_camera_sensor_get();
if (s && s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);        // Correct inverted optical lens mount
    s->set_hmirror(s, 0);      // Horizontal mirror off
    s->set_brightness(s, 1);   // Boost contrast in doorstep shadow conditions
    s->set_saturation(s, -1);  // Normalize aggressive color saturation
}
```

---

## 5. Electrical Stability & Brownout Protection

The ESP32 Wi-Fi radio draws brief current spikes up to **350–450 mA** during transmission. If powered via long USB cables or inadequate 500mA PC ports, the supply voltage can drop below 2.7V, triggering the ESP32 internal brownout detector.

**Mitigation Measures:**
1. **Software:** Brownout detector disabled in `setup()` via:
   ```c
   WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
   ```
2. **Hardware:** Use a dedicated 5V 2A USB wall adapter. A 100μF – 470μF electrolytic capacitor placed across the 5V and GND pins of the ESP32-CAM absorbs transient RF current spikes.

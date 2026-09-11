# DoorCam Hardware Assembly & Soldering Blueprint

This document provides the complete, hole-by-hole assembly guide for soldering the **DoorCam Smart Video Doorbell** onto a standard **5cm x 7cm Double-Sided FR-4 Perfboard (Zero PCB)**.

---

## Components Checklist

| Component | Quantity | Position on Board |
|---|:---:|---|
| **5x7 cm FR-4 Double-Sided Perfboard** | 1 | Main substrate |
| **40-pin Female Header Strip** | Snip two 1x8 strips | Center-top (ESP32-CAM socket) |
| **Type-C 16-Pin Breakout Board** | 1 | Bottom edge (facing outward) |
| **SPST Tactile Push Button** | 1 | Bottom center / offset |
| **3-Pin Male Header Strip** | Snip 1x3 pins | Top/side edge (PIR harness connector) |
| **100uF 35V Electrolytic Capacitor** | 1 | Near ESP32-CAM 5V/GND pins |
| **100nF (104) Ceramic Capacitor** | 1 | In parallel with 100uF cap across 5V/GND |
| **24 AWG Hookup Wire** | A few scraps | Underside point-to-point bridging |

---

## Top-Side Component Layout

A 5x7 cm perfboard has **18 holes horizontally** and **28 holes vertically** (standard 2.54mm pitch).

`
   +---------------------------------------------------+
   |  [O] M3 Mounting Hole         M3 Mounting Hole [O]|  <-- TOP
   |                                                   |
   |              [PIR HEADER: VCC OUT GND]             |  <-- Row 4
   |                     (1x3 Male)                    |
   |                                                   |
   |      [ 1x8 Female ]         [ 1x8 Female ]        |  <-- Rows 7-14
   |      (ESP32-CAM Left)       (ESP32-CAM Right)     |      (Spaced 8 holes apart)
   |       1. 5V                  1. GND               |
   |       2. GND                 2. IO0               |
   |       3. IO12                3. IO16              |
   |       4. IO13 (PIR)          4. IO14 (Button)     |
   |       5. IO15                5. GND               |
   |       6. IO2                 6. 3V3               |
   |       7. IO4 (Flash)         7. U0R               |
   |       8. GND                 8. U0T               |
   |                                                   |
   |        [CAP: 100uF]  [CAP: 100nF]                 |  <-- Rows 16-17
   |                                                   |
   |                   [PUSH BUTTON]                   |  <-- Rows 19-21
   |                   (4-pin Tactile)                 |
   |                                                   |
   |             [TYPE-C BREAKOUT BOARD]               |  <-- Rows 24-27
   |                [ USB-C PORT v ]                   |  <-- BOTTOM EDGE
   |  [O] M3 Mounting Hole         M3 Mounting Hole [O]|
   +---------------------------------------------------+
`

---

## Circuit Schematic & Wiring Connections

### 1. Power Distribution (5V & GND Rails)
`
[Type-C Breakout]
   VBUS (5V) ───────┬───────────────────> ESP32-CAM Pin 1 (5V)
                    ├───────────────────> PIR Sensor Header Pin 1 (VCC)
                    ├──────(+) [100uF]
                    └───────┤  [100nF]
   GND      ────────┬──────(-) [100uF]
                    ├───────┤  [100nF]
                    ├───────────────────> ESP32-CAM Pin 2 (GND)
                    ├───────────────────> PIR Sensor Header Pin 3 (GND)
                    └───────────────────> Push Button Leg 1 (GND)
`

### 2. Sensor & Button Signal Wiring
`
[PIR Motion Sensor]
   OUT (Signal) ────────────────────────> ESP32-CAM Pin 4 (GPIO 13)

[Doorbell Push Button]
   Button Leg 1 ────────────────────────> Ground (GND)
   Button Leg 2 ────────────────────────> ESP32-CAM Pin 12 (GPIO 14)
   (Note: GPIO 14 has internal software PULLUP enabled in firmware; no external resistor needed!)
`

### 3. Capacitor Orientation (Crucial!)
* **100uF Electrolytic Capacitor (Black cylinder):**
  * **Longer Leg = POSITIVE (+)** -> Connect to **5V Rail**.
  * **Shorter Leg / White Stripe = NEGATIVE (-)** -> Connect to **GND Rail**.
* **100nF Ceramic Capacitor (Yellow/orange bead 104):**
  * Non-polar. Solder directly across 5V and GND right next to the 100uF capacitor.

---

## Step-by-Step Soldering Sequence

Follow this order for the cleanest assembly:

### Step 1: Prepare the Sockets
1. Take your 40-pin female header strip.
2. Carefully snip off **two 8-pin pieces** using flush cutters.
3. Clean up the edges so they sit flush.

### Step 2: Solder the ESP32-CAM Socket
1. Push the two 8-pin female headers into the perfboard spaced **8 holes apart** (20.32 mm from center-to-center).
2. Insert your ESP32-CAM into the female headers temporarily **before soldering**. This keeps both rows perfectly parallel while you solder the pins on the underside!
3. Solder the corner pins first, verify alignment, then solder all remaining pins.
4. Unplug the ESP32-CAM and set it aside safely.

### Step 3: Solder the Power Capacitors
1. Place the 100nF ceramic capacitor and 100uF electrolytic capacitor close to the ESP32-CAM 5V and GND pins.
2. Double-check electrolytic polarity: **White stripe with minus signs (-) must go to GND!**

### Step 4: Solder the Push Button & PIR Header
1. Push the 4-pin tactile button into the board and bend its legs slightly on the back so it stays in place. Solder all 4 pins.
2. Snip a **3-pin piece** from your male header strip and solder it near the top of the board for the PIR sensor wire harness.

### Step 5: Solder the USB-C Breakout Board
1. Place the Type-C breakout board along the bottom edge so the metal USB-C port faces outward past the edge of the perfboard.
2. Solder male header pins or short solid-core wires from the VBUS and GND pads into the perfboard power rails.

### Step 6: Underside Point-to-Point Wiring
1. Route short insulated wire bridges (or create clean solder bridges) connecting:
   - **5V Rail:** Type-C VBUS -> Capacitors (+) -> ESP32 Pin 1 (5V) -> PIR Pin 1 (VCC).
   - **GND Rail:** Type-C GND -> Capacitors (-) -> ESP32 Pin 2 (GND) -> PIR Pin 3 (GND) -> Button Leg 1.
   - **PIR Signal:** PIR Pin 2 (OUT) -> ESP32 Pin 4 (GPIO 13).
   - **Button Signal:** Button Leg 2 -> ESP32 Pin 12 (GPIO 14).

---

## Pre-Power Safety Checklist (Do NOT Skip!)

Before plugging in your USB-C cable for the first time:

1. **Multimeter Continuity Check:**
   - Set multimeter to Continuity / Beep mode.
   - Place one probe on **5V** and the other on **GND**.
   - **It must NOT beep!** (If it beeps, you have a solder bridge short-circuit. Inspect and clean the underside pads).
2. **Polarity Check:**
   - Verify the 100uF capacitor white stripe is on Ground.
3. **Power Test (Without ESP32-CAM):**
   - Plug in your USB-C cable **without the ESP32-CAM installed**.
   - Measure DC Voltage with multimeter across the female header pins:
     - Pin 1 (5V) to Pin 2 (GND) should read **+4.9V to +5.2V**.
4. **Final Assembly:**
   - Unplug USB.
   - Seat the ESP32-CAM firmly into its sockets.
   - Plug in USB -> Watch the red status LED blink and verify live stream on your dashboard!

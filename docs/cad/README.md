# 3D Printable Doorbell Enclosure

This folder contains the CAD design, 3D printing parameters, and assembly instructions for the **DoorCam Smart Doorbell Enclosure**.

---

## 📐 Enclosure Architecture & Dimensions

```
   Front View                        Side Cross-Section
+---------------+                     +---+
|   ( [CAM] )   | <-- 9mm Lens        |   |
|               |     Recess          | C | <-- OV3660 Lens Bracket
|     (PIR)     | <-- 23.5mm Dome     | A |     (Locks ribbon rigid)
|               |     (Optional)      | M |
|               |                     |   |
|               |                     |   |
|    [  O  ]    | <-- 16mm Button     | B | <-- Tactile Push Button
|               |     Cutout          | T |
+---------------+                     +---+
     46.4 mm                          24.2 mm
```

### Key Dimensions:
- **Outer Dimensions:** `46.4 mm (W) x 109.4 mm (L) x 24.2 mm (D)`
- **Wall Thickness:** `2.2 mm` (Rigid, weather-resistant shell)
- **Mounting Method:** 2x Rear Keyholes for wall screws (`M3 / M4`) + Bottom USB cable pass-through (`9mm x 5mm`).
- **Corner Fillet:** `6.0 mm` radius for a modern, rounded aesthetic (Ring / Nest doorbell profile).

---

## 🛠️ How to Generate the STL Files

### Option 1: Browser (Zero Install, Instant Preview)
1. Open [OpenSCAD Cloud](https://openscad.cloud/) or [CadHub.xyz](https://cadhub.xyz/).
2. Copy & paste the code from [`doorbell_case.scad`](doorbell_case.scad).
3. Set `PART = "front";` $\rightarrow$ Click **Export STL** $\rightarrow$ Save as `DoorCam_Front.stl`.
4. Set `PART = "back";` $\rightarrow$ Click **Export STL** $\rightarrow$ Save as `DoorCam_Back.stl`.

### Option 2: Desktop OpenSCAD
1. Download [OpenSCAD](https://openscad.org/downloads.html) (Free & Open Source for Windows/Mac/Linux).
2. Open [`doorbell_case.scad`](doorbell_case.scad).
3. Press **F5** to preview, **F6** to compile render, and **F7** to export as STL.

---

## 🖨️ Recommended 3D Print Slicer Settings

| Parameter | Recommended Setting | Rationale |
| :--- | :--- | :--- |
| **Material** | **PETG** (or **PLA+**) | PETG resists outdoor sunlight, heat up to 75°C, and UV degradation. |
| **Layer Height** | `0.20 mm` | Ideal balance of speed and surface finish. |
| **Infill** | `20% – 25% Gyroid` | Maximum torsional strength against button presses. |
| **Perimeters / Walls** | `3 to 4 walls` | Ensures screw standoffs don't crack when tightened. |
| **Supports** | **Normal (Snug) or Tree** | Only needed for the horizontal lens/button cutouts on the front face. |
| **Orientation** | Print with outer flat faces on the build plate. | Minimizes support material and gives a smooth exterior. |

---

## 🔒 Crucial Hardware Tip: Locking the OV3660 Ribbon Cable

The OV3660 sensor uses an ultra-fine 24-pin Flexible Printed Circuit (FPC) ribbon cable. On bare desk setups, **even a 0.1mm vibration or wire tug breaks contact on the I2C bus**, causing the camera driver to time out and freeze.

### How to Permanently Lock the Sensor:
1. **Ensure Latch is Down:** Make sure the black plastic FPC retaining latch on the ESP32-CAM is flipped completely down and flat.
2. **Apply Kapton Tape or Foam Pad:** Place a small 10mm strip of Kapton tape (or 1mm double-sided foam tape) directly across the back of the FPC connector and board.
3. **Mount in the 3D Case:** In [`doorbell_case.scad`](doorbell_case.scad), the front cover includes a **built-in retaining frame** that presses the camera lens flat against the bezel. Once screwed shut, the sensor cannot shift or vibrate.

---

## 🌐 Alternative Ready-to-Print Community Models

If you prefer to download pre-sliced STL files from Thingiverse or Printables:
1. **[ESP32-CAM Doorbell Enclosure on Thingiverse](https://www.thingiverse.com/thing:4823297)** — Compact vertical snap-fit case with button hole.
2. **[ESP32-CAM Weatherproof Case on Printables](https://www.printables.com/model/163273-esp32-cam-case)** — Heavy-duty outdoor enclosure with sun visor.
3. **[ESP32-CAM + PIR + Button Housing on Thingiverse](https://www.thingiverse.com/thing:4654992)** — Full smart doorbell enclosure with PIR dome slot.

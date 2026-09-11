// ====================================================================
// DoorCam v1.0 - Production 3D Printable Smart Doorbell Enclosure
// Precision engineered for: 
//   - 5x7cm Perfboard (50mm x 70mm)
//   - AI-Thinker ESP32-CAM (OV3660 / OV2640)
//   - HC-SR501 PIR Motion Sensor (23mm Fresnel dome)
//   - 16mm Push Button
//   - Type-C 16-Pin USB Breakout Board
// Fully optimized for Robu.in / JLCPCB / Local 3D Print Services
// ====================================================================

$fn = 48; // Ultra-smooth curves for production quality

// --- PART SELECTOR ---
PART = "assembled"; // ["front", "back", "assembled"]

// --- CORE DIMENSIONS (mm) ---
wall_thick   = 2.4;     // Sturdy outdoor wall thickness
inner_w      = 54.0;    // 50mm perfboard + 2mm margin per side
inner_l      = 120.0;   // 70mm perfboard + PIR dome + wiring cavity
inner_h      = 24.0;    // Internal cavity height for full component stack
fillet_r     = 6.5;     // Smooth ergonomic rounded outer corners

outer_w      = inner_w + wall_thick * 2; // 58.8 mm
outer_l      = inner_l + wall_thick * 2; // 124.8 mm

split_z      = 13.0;    // Front shell height
back_depth   = inner_h - (split_z - wall_thick) + wall_thick; // 15.8 mm (Total height = 28.8 mm)

// --- SENSOR & APERTURE POSITIONS (from bottom inner wall) ---
cam_lens_d   = 9.5;     // OV3660 lens barrel diameter
cam_lens_y   = 98.0;    // Lens center Y
pir_d        = 23.5;    // HC-SR501 Fresnel dome diameter
pir_y        = 62.0;    // PIR dome center Y
btn_d        = 16.0;    // 16mm doorbell push button hole
btn_y        = 24.0;    // Button center Y
usbc_w       = 13.0;    // USB-C clearance width
usbc_h       = 7.0;     // USB-C clearance height

screw_inset  = 6.5;     // Corner screw center from outer edge
boss_od      = 8.0;     // Screw boss outer diameter

// --- HELPER: ROUNDED BOX ---
module rounded_box(w, l, h, r) {
    hull() {
        translate([r, r, 0]) cylinder(r=r, h=h);
        translate([w-r, r, 0]) cylinder(r=r, h=h);
        translate([r, l-r, 0]) cylinder(r=r, h=h);
        translate([w-r, l-r, 0]) cylinder(r=r, h=h);
    }
}

// ====================================================================
// 1. FRONT SHELL (Outer face flat on bed at Z=0, zero supports needed!)
// ====================================================================
module front_shell() {
    difference() {
        // --- 1. SOLID POSITIVES ---
        union() {
            // Hollow Body Tray (Outer box minus cavity)
            difference() {
                rounded_box(outer_w, outer_l, split_z, fillet_r);
                translate([wall_thick, wall_thick, wall_thick])
                    rounded_box(inner_w, inner_l, split_z + 1, max(1, fillet_r - wall_thick));
            }

            // 4 Corner Screw Bosses (solid columns inside the tray)
            for (pos = [
                [screw_inset, screw_inset],
                [outer_w - screw_inset, screw_inset],
                [screw_inset, outer_l - screw_inset],
                [outer_w - screw_inset, outer_l - screw_inset]
            ]) {
                translate([pos[0], pos[1], wall_thick])
                    cylinder(d=boss_od, h=split_z - wall_thick);
            }

            // Camera Lens Rear Positioning Collar
            translate([outer_w/2, wall_thick + cam_lens_y, wall_thick])
                cylinder(d=cam_lens_d + 4.5, h=3.0);
        }

        // --- 2. NEGATIVE CUTOUTS ---

        // 1. Camera Lens Aperture
        translate([outer_w/2, wall_thick + cam_lens_y, -1])
            cylinder(d=cam_lens_d, h=wall_thick + 5.0);

        // 2. Protective Lens Chamfer Bezel (Angled sun/rain hood)
        translate([outer_w/2, wall_thick + cam_lens_y, -0.1])
            cylinder(d1=cam_lens_d + 3.6, d2=cam_lens_d, h=1.8);

        // 3. Status / Flash LED Light-pipe (2.5mm hole)
        translate([outer_w/2 + 13.0, wall_thick + cam_lens_y + 6.0, -1])
            cylinder(d=2.5, h=wall_thick + 2.0);

        // 4. HC-SR501 PIR Dome Aperture (23.5mm)
        translate([outer_w/2, wall_thick + pir_y, -1])
            cylinder(d=pir_d, h=wall_thick + 2.0);

        // 5. Doorbell Push Button Hole (16mm)
        translate([outer_w/2, wall_thick + btn_y, -1])
            cylinder(d=btn_d, h=wall_thick + 2.0);

        // 6. USB-C Port Notch (Top half at split line)
        translate([outer_w/2 - usbc_w/2, -1, split_z - usbc_h/2])
            cube([usbc_w, wall_thick + 2, usbc_h + 1]);

        // 7. Corner Screws (M3 Clearance = 3.2mm through outer wall & boss)
        for (pos = [
            [screw_inset, screw_inset],
            [outer_w - screw_inset, screw_inset],
            [screw_inset, outer_l - screw_inset],
            [outer_w - screw_inset, outer_l - screw_inset]
        ]) {
            // 3.2mm Clearance Hole
            translate([pos[0], pos[1], -1])
                cylinder(d=3.2, h=split_z + 2);

            // M3 Countersink Head on outer face (90-degree standard cone)
            translate([pos[0], pos[1], -0.1])
                cylinder(d1=6.4, d2=3.2, h=1.8);
        }
    }
}

// ====================================================================
// 2. BACK SHELL (Mounting face flat on bed at Z=0, zero supports needed!)
// ====================================================================
module back_shell() {
    difference() {
        // --- 1. SOLID POSITIVES ---
        union() {
            // Hollow Body Tray (Outer box minus cavity)
            difference() {
                rounded_box(outer_w, outer_l, back_depth, fillet_r);
                translate([wall_thick, wall_thick, wall_thick])
                    rounded_box(inner_w, inner_l, back_depth + 1, max(1, fillet_r - wall_thick));
            }

            // 4 Corner Screw Bosses (with M3 pilot holes)
            for (pos = [
                [screw_inset, screw_inset],
                [outer_w - screw_inset, screw_inset],
                [screw_inset, outer_l - screw_inset],
                [outer_w - screw_inset, outer_l - screw_inset]
            ]) {
                translate([pos[0], pos[1], wall_thick])
                    cylinder(d=boss_od, h=back_depth - wall_thick);
            }

            // 4 Perfboard Support Standoffs (4mm height, supports 50x70mm board)
            pcb_offset_y = 12.0;
            pcb_standoff_h = 4.0;
            for (p = [
                [wall_thick + 2.0, wall_thick + pcb_offset_y],
                [outer_w - wall_thick - 2.0, wall_thick + pcb_offset_y],
                [wall_thick + 2.0, wall_thick + pcb_offset_y + 70.0],
                [outer_w - wall_thick - 2.0, wall_thick + pcb_offset_y + 70.0]
            ]) {
                translate([p[0], p[1], wall_thick])
                    cylinder(d=6.0, h=pcb_standoff_h);
            }
        }

        // --- 2. NEGATIVE CUTOUTS ---

        // 1. USB-C Port Notch (Bottom half at split line)
        translate([outer_w/2 - usbc_w/2, -1, back_depth - usbc_h/2])
            cube([usbc_w, wall_thick + 2, usbc_h + 1]);

        // 2. Wall Mounting Keyhole Top
        translate([outer_w/2, outer_l - 22.0, -1]) {
            cylinder(d=8.0, h=wall_thick + 2); // Head entry
            translate([-2.1, 0, 0]) cube([4.2, 9.0, wall_thick + 2]); // Shank slot
        }

        // 3. Wall Mounting Keyhole Bottom
        translate([outer_w/2, 22.0, -1]) {
            cylinder(d=8.0, h=wall_thick + 2);
            translate([-2.1, 0, 0]) cube([4.2, 9.0, wall_thick + 2]);
        }

        // 4. Center Cable Pass-Through Hole (10mm)
        translate([outer_w/2, outer_l/2, -1])
            cylinder(d=10.0, h=wall_thick + 2);

        // 5. Corner Screw Pilot Holes (2.7mm self-tapping for M3)
        // Drilled 11mm deep into bosses from top down, keeping outer back face sealed
        for (pos = [
            [screw_inset, screw_inset],
            [outer_w - screw_inset, screw_inset],
            [screw_inset, outer_l - screw_inset],
            [outer_w - screw_inset, outer_l - screw_inset]
        ]) {
            translate([pos[0], pos[1], back_depth - 11.5])
                cylinder(d=2.7, h=12.0);
        }
    }
}

// ====================================================================
// RENDER OUTPUT MODES
// ====================================================================
if (PART == "front") {
    front_shell();
} else if (PART == "back") {
    back_shell();
} else {
    // Assembled Inspection View
    color("#0284c7") front_shell();
    color("#334155") translate([0, outer_l, split_z + back_depth])
        rotate([180, 0, 0]) back_shell();
}

// ====================================================================
// DoorCam - Parametric 3D Printable Smart Doorbell Enclosure
// Designed for: ESP32-CAM (AI-Thinker) + OV3660 Sensor + Push Button
// Format: OpenSCAD (Open-source Parametric CAD)
// Instructions:
//   1. Open in OpenSCAD (free at openscad.org or openscad.cloud)
//   2. Set PART = "assembled", "front", or "back"
//   3. Press F6 (Render) and click File -> Export as STL
// ====================================================================

// --- RENDER SELECTOR ---
PART = "assembled"; // Options: "assembled", "front", "back"

// --- DIMENSIONS (all in mm) ---
wall_thick   = 2.2;     // Robust outer shell thickness
inner_w      = 42.0;    // Internal width (fits 27mm ESP32-CAM + button)
inner_l      = 105.0;   // Internal length (vertical doorbell form-factor)
inner_h      = 22.0;    // Internal depth (fits ESP32-CAM + MB shield + wires)
fillet_r     = 6.0;     // Sleek rounded exterior corners
tolerance    = 0.3;     // 3D printer clearance gap

// --- CUTOUTS ---
cam_lens_d   = 9.0;     // OV3660 lens barrel diameter
cam_lens_y   = 82.0;    // Y-position of camera lens from bottom
btn_d        = 16.0;    // Doorbell push button hole diameter
btn_y        = 25.0;    // Y-position of button from bottom
pir_d        = 23.5;    // Optional HC-SR501 PIR dome diameter
pir_y        = 55.0;    // Y-position of PIR from bottom
has_pir      = true;    // Set false if not using PIR dome cutout

// --- HELPER MODULES ---
module rounded_box(w, l, h, r) {
    hull() {
        translate([r, r, 0]) cylinder(r=r, h=h, $fn=32);
        translate([w-r, r, 0]) cylinder(r=r, h=h, $fn=32);
        translate([r, l-r, 0]) cylinder(r=r, h=h, $fn=32);
        translate([w-r, l-r, 0]) cylinder(r=r, h=h, $fn=32);
    }
}

// --- FRONT COVER (FACEPLATE) ---
module front_cover() {
    outer_w = inner_w + wall_thick*2;
    outer_l = inner_l + wall_thick*2;
    front_depth = 12.0;

    difference() {
        union() {
            // Main front shell
            rounded_box(outer_w, outer_l, front_depth, fillet_r);
        }

        // Hollow interior
        translate([wall_thick, wall_thick, wall_thick])
            rounded_box(inner_w, inner_l, front_depth + 1, fillet_r - wall_thick);

        // Camera Lens Aperture
        translate([outer_w/2, wall_thick + cam_lens_y, -1])
            cylinder(d=cam_lens_d, h=wall_thick + 2, $fn=36);

        // Lens Recess Bezel (protects lens from scratches)
        translate([outer_w/2, wall_thick + cam_lens_y, -0.1])
            cylinder(d1=cam_lens_d + 4, d2=cam_lens_d, h=1.0, $fn=36);

        // PIR Motion Sensor Dome Aperture (if enabled)
        if (has_pir) {
            translate([outer_w/2, wall_thick + pir_y, -1])
                cylinder(d=pir_d, h=wall_thick + 2, $fn=48);
        }

        // Push Button Aperture
        translate([outer_w/2, wall_thick + btn_y, -1])
            cylinder(d=btn_d, h=wall_thick + 2, $fn=40);

        // Status LED Light-pipe hole (1.8mm indicator)
        translate([outer_w/2 + 10, wall_thick + cam_lens_y + 8, -1])
            cylinder(d=2.0, h=wall_thick + 2, $fn=16);
    }

    // Camera Sensor Internal Retaining Bracket (locks OV3660 rigid against front wall)
    translate([outer_w/2 - 12, wall_thick + cam_lens_y - 12, wall_thick]) {
        difference() {
            cube([24, 24, 4]); // 24x24mm sensor backing frame
            translate([2, 2, -0.5]) cube([20, 20, 5]); // Pocket for OV3660 PCB
        }
    }

    // Snap-fit lip pins (left & right)
    translate([wall_thick - 0.6, outer_l*0.3, front_depth - 4])
        cube([0.8, 10, 2]);
    translate([outer_w - wall_thick - 0.2, outer_l*0.3, front_depth - 4])
        cube([0.8, 10, 2]);
    translate([wall_thick - 0.6, outer_l*0.7, front_depth - 4])
        cube([0.8, 10, 2]);
    translate([outer_w - wall_thick - 0.2, outer_l*0.7, front_depth - 4])
        cube([0.8, 10, 2]);
}

// --- BACK BASE & WALL MOUNT ---
module back_base() {
    outer_w = inner_w + wall_thick*2;
    outer_l = inner_l + wall_thick*2;
    back_depth = inner_h - 12.0 + wall_thick;

    difference() {
        // Outer back shell
        rounded_box(outer_w, outer_l, back_depth, fillet_r);

        // Hollow interior
        translate([wall_thick + tolerance, wall_thick + tolerance, wall_thick])
            rounded_box(inner_w - tolerance*2, inner_l - tolerance*2, back_depth + 1, fillet_r - wall_thick);

        // Wall Mounting Keyhole 1 (Top)
        translate([outer_w/2, outer_l - 20, -1]) {
            cylinder(d=7.0, h=wall_thick + 2, $fn=24);
            translate([-2.5, 0, 0]) cube([5.0, 7.0, wall_thick + 2]);
        }

        // Wall Mounting Keyhole 2 (Bottom)
        translate([outer_w/2, 20, -1]) {
            cylinder(d=7.0, h=wall_thick + 2, $fn=24);
            translate([-2.5, 0, 0]) cube([5.0, 7.0, wall_thick + 2]);
        }

        // USB Cable Channel (Bottom exit cutout)
        translate([outer_w/2 - 4.5, -1, wall_thick])
            cube([9.0, wall_thick + 2, 5.0]);
    }

    // ESP32-CAM PCB Standoff Posts (pins 27mm wide x 40mm long)
    standoff_h = 6.0;
    board_x = outer_w/2 - 13.5;
    board_y = wall_thick + cam_lens_y - 25;

    translate([board_x, board_y, wall_thick])
        standoff_post(standoff_h);
    translate([board_x + 27, board_y, wall_thick])
        standoff_post(standoff_h);
    translate([board_x, board_y - 35, wall_thick])
        standoff_post(standoff_h);
    translate([board_x + 27, board_y - 35, wall_thick])
        standoff_post(standoff_h);
}

module standoff_post(h) {
    difference() {
        cylinder(d=5.0, h=h, $fn=24);
        cylinder(d=2.0, h=h + 1, $fn=16); // M2 screw pilot hole
    }
}

// --- RENDER LOGIC ---
if (PART == "front") {
    front_cover();
} else if (PART == "back") {
    back_base();
} else {
    // Assembled View
    color("#1e293b") front_cover();
    color("#0f172a") translate([0, 0, 24]) rotate([0, 180, 0]) translate([-inner_w - wall_thick*2, 0, -inner_h]) back_base();
}

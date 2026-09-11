// DoorCam 16mm Tactile Button Plunger Cap
// Fits the 16mm front shell aperture and presses an internal 6x6mm tactile switch
$fn = 48;

cap_d     = 15.2;   // Outer button cap diameter (smooth slip fit in 16mm hole)
cap_h     = 3.0;    // Visible button face thickness
flange_d  = 18.0;   // Retaining flange (prevents button from falling out of front)
flange_h  = 1.2;    // Flange thickness
stem_d    = 5.0;    // Stem diameter pressing against the 6x6 tactile button
stem_len  = 12.0;   // Length extending to reach the PCB switch
nub_d     = 3.2;    // Actuator tip
nub_h     = 1.5;

union() {
    // 1. Chamfered tactile face (pokes through front hole)
    translate([0, 0, flange_h])
        cylinder(d1=cap_d, d2=cap_d - 1.5, h=cap_h);

    // 2. Retaining flange (rests inside the case against front wall)
    cylinder(d=flange_d, h=flange_h);

    // 3. Central plunger stem extending down to the 6x6mm switch
    translate([0, 0, -stem_len])
        cylinder(d=stem_d, h=stem_len);

    // 4. Contact nub that centers onto the tactile button
    translate([0, 0, -stem_len - nub_h])
        cylinder(d=nub_d, h=nub_h);
}

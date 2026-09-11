import subprocess
import os
import sys
import struct

cad_dir = r"d:\ESP\DoorCam\docs\cad"
openscad = r"C:\Program Files\OpenSCAD\openscad.com"

# 1. OpenSCAD script for the Button Plunger
button_scad = os.path.join(cad_dir, "doorbell_button.scad")
button_ascii_stl = os.path.join(cad_dir, "doorbell_button_ascii.stl")
button_stl = os.path.join(cad_dir, "doorbell_button.stl")
button_png = os.path.join(cad_dir, "doorbell_button.png")

scad_content = '''// DoorCam 16mm Tactile Button Plunger Cap
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
'''

with open(button_scad, 'w', encoding='utf-8') as f:
    f.write(scad_content)

print("Generated doorbell_button.scad")

# Compile to ASCII STL
subprocess.run([openscad, "-o", button_ascii_stl, button_scad], check=True)

# Convert to Binary STL
triangles = []
with open(button_ascii_stl, 'r', encoding='utf-8', errors='ignore') as f:
    current_normal = [0.0, 0.0, 0.0]
    current_verts = []
    for line in f:
        p = line.strip().split()
        if not p: continue
        if p[0] == 'facet' and p[1] == 'normal':
            current_normal = [float(p[2]), float(p[3]), float(p[4])]
            current_verts = []
        elif p[0] == 'vertex':
            current_verts.append([float(p[1]), float(p[2]), float(p[3])])
        elif p[0] == 'endfacet':
            if len(current_verts) == 3:
                triangles.append((current_normal, current_verts[0], current_verts[1], current_verts[2]))
            current_verts = []

with open(button_stl, 'wb') as f:
    f.write(b"DoorCam Button Plunger - Robu.in Binary STL".ljust(80, b'\0'))
    f.write(struct.pack('<I', len(triangles)))
    for norm, v1, v2, v3 in triangles:
        f.write(struct.pack('<3f', *norm))
        f.write(struct.pack('<3f', *v1))
        f.write(struct.pack('<3f', *v2))
        f.write(struct.pack('<3f', *v3))
        f.write(struct.pack('<H', 0))

print(f"Generated binary {button_stl}: {len(triangles)} triangles, {os.path.getsize(button_stl):,} bytes.")

# Render preview PNG
subprocess.run([openscad, "-o", button_png, "--imgsize=800,600", "--colorscheme=Tomorrow Night", button_scad], check=True)
print("Rendered button preview.")

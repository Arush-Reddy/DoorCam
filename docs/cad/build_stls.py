import subprocess
import os
import sys
import struct

openscad = r"C:\Program Files\OpenSCAD\openscad.com"
cad_dir = r"d:\ESP\DoorCam\docs\cad"

front_scad = os.path.join(cad_dir, "doorbell_front.scad")
back_scad = os.path.join(cad_dir, "doorbell_back.scad")
case_scad = os.path.join(cad_dir, "doorbell_case.scad")

front_stl = os.path.join(cad_dir, "doorbell_front.stl")
back_stl = os.path.join(cad_dir, "doorbell_back.stl")
front_ascii_stl = os.path.join(cad_dir, "doorbell_front_ascii.stl")
back_ascii_stl = os.path.join(cad_dir, "doorbell_back_ascii.stl")

def ascii_to_binary_stl(ascii_path, binary_path, name="DoorCam"):
    triangles = []
    current_normal = [0.0, 0.0, 0.0]
    current_verts = []
    
    with open(ascii_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            parts = line.strip().split()
            if not parts:
                continue
            if parts[0] == 'facet' and parts[1] == 'normal':
                current_normal = [float(parts[2]), float(parts[3]), float(parts[4])]
                current_verts = []
            elif parts[0] == 'vertex':
                current_verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif parts[0] == 'endfacet':
                if len(current_verts) == 3:
                    triangles.append((current_normal, current_verts[0], current_verts[1], current_verts[2]))
                current_verts = []
                
    num_triangles = len(triangles)
    with open(binary_path, 'wb') as f:
        header = f"{name} - Robu.in 3D Print Ready Binary STL".encode('ascii').ljust(80, b'\0')
        f.write(header)
        f.write(struct.pack('<I', num_triangles))
        for norm, v1, v2, v3 in triangles:
            f.write(struct.pack('<3f', *norm))
            f.write(struct.pack('<3f', *v1))
            f.write(struct.pack('<3f', *v2))
            f.write(struct.pack('<3f', *v3))
            f.write(struct.pack('<H', 0))
    print(f"Converted {binary_path}: {num_triangles} triangles, {os.path.getsize(binary_path):,} bytes.")

def compile_scad(scad_in, stl_out, desc):
    print(f"\n--- Compiling {desc} ---")
    cmd = [openscad, "-o", stl_out, scad_in]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"ERROR: {res.stderr}")
        sys.exit(res.returncode)
    print(f"OpenSCAD compiled {stl_out} ({os.path.getsize(stl_out):,} bytes)")

def render_previews():
    print("\n--- Rendering Preview Images ---")
    previews = [
        (case_scad, os.path.join(cad_dir, "doorbell_assembled.png"), ["--camera=0,60,15,45,0,225,250"]),
        (front_scad, os.path.join(cad_dir, "doorbell_front.png"), ["--camera=29,62,6,45,0,225,200"]),
        (back_scad, os.path.join(cad_dir, "doorbell_back.png"), ["--camera=29,62,8,45,0,225,200"]),
    ]
    for src, out, extra_args in previews:
        cmd = [openscad, "-o", out, "--imgsize=1024,768", "--colorscheme=Tomorrow Night"] + extra_args + [src]
        subprocess.run(cmd, capture_output=True, text=True)
        if os.path.exists(out):
            print(f"Rendered preview: {os.path.basename(out)}")

if __name__ == "__main__":
    # 1. Compile Front SCAD to ASCII STL
    compile_scad(front_scad, front_ascii_stl, "Front Shell")
    # 2. Compile Back SCAD to ASCII STL
    compile_scad(back_scad, back_ascii_stl, "Back Shell")
    
    # 3. Create high-compatibility Binary STLs
    ascii_to_binary_stl(front_ascii_stl, front_stl, "DoorCam Front Shell")
    ascii_to_binary_stl(back_ascii_stl, back_stl, "DoorCam Back Shell")
    
    # 4. Render visual preview images
    render_previews()
    
    print("\nALL 3D PRINT FILES AND PREVIEWS READY FOR ROBU.IN!")

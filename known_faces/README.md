# Known Faces Directory

Place photos of known family members, friends, or yourself here.

### Guidelines:
1. **Naming**: The filename without extension will be used as the person's name.
   - Example: `arush.jpg` -> Name: **Arush**
   - Example: `dad.png` -> Name: **Dad**
   - Example: `uncle_bob.jpg` -> Name: **Uncle Bob**

2. **Photo Quality**:
   - Clear, well-lit, frontal face photo.
   - Only ONE person per photo.
   - Neutral expression or natural smile.

3. **After adding or updating photos**:
   Run:
   ```powershell
   python face_db/encode_faces.py
   ```
   This will update `face_db/encodings.pkl`.

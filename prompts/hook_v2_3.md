Design a load-bearing hook in Blender by writing ONE deterministic Python
script (bpy/bmesh only; no external meshes, no add-on assets, no asset
libraries, no 3D generators). Put all dimensions in a parameter block at the
top. Units: millimeters (1 Blender unit = 1 mm; the scene is already set up).

SHAPE
A J-hook: a solid eye at the top, a shank running down from it, and a bowl at
the bottom that the pin hangs in.

LOAD CASE
- A Ø6.0 mm steel pin, axis along Y, centered at (X=0, Z=0), hangs in the
  hook's bowl. The hook supports it from BELOW.
- An M5 steel screw (Ø5.0) passes through the eye, centered at (X=0, Z=50),
  and pulls the hook straight up (+Z). The pin is pulled down (-Z).
- Static load, increased slowly until failure.

HARD CONSTRAINTS (designs violating any of these are invalid)
1. 2.5D part: a closed XZ profile extruded along Y from 0 to 5.0 mm.
   No fillets/chamfers on the Y faces.
2. Entire part within X ∈ [-20, 20], Z ∈ [-12, 62].
3. Bottom connection: the profile must touch the pin at P1 = (0, -3.0)
   within 0.3 mm, and there must be no material inside a Ø5.8 circle
   centered at (0, 0).
4. The pin must be insertable: a Ø6.5 mm disk must be able to travel (any
   path in the XZ plane) from outside the bounding box to within 0.6 mm of
   the pin center without overlapping material.
5. Top connection: the profile has exactly ONE hole — a Ø6.0 ±0.1 mm circle
   centered at (0, 50). The screw pulls on its top edge, P2 = (0, 53.0).
   The hole must be surrounded by a solid ring at least 3.0 mm wide
   (material covers r = 3.0..6.0 mm around (0, 50)).
6. Local material width: outside an 8 mm radius around the eye center, no
   circle larger than Ø2.3 mm fits inside the profile; no section is
   narrower than 1.6 mm. Inner (concave) corner radii ≥ 0.5 mm.
7. Material budget: volume 1150–1350 mm³ (profile area 230–270 mm²);
   total surface area ≤ 1850 mm²; no internal voids.
8. Exactly ONE hook: a single manifold mesh object named "hook",
   ≤ 50,000 triangles. Delete anything else you created.

OBJECTIVE
Maximize the load the hook carries before it breaks OR lets the pin slip
out. The part is printed in PLA, lying flat (layers parallel to the XZ
profile), 100% infill.

OUTPUT
When the hook is finished (and again after every revision), export it as a
binary STL, overwriting the previous file, with exactly this code:

    import bpy, os
    MODEL = "<your-model-name-and-version>"   # lowercase, hyphens, e.g. "claude-opus-5.5"
    out = f"/Users/orcaim/Documents/code/personal/hook-bench/output/{MODEL}/hookv1.stl"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    hook = bpy.data.objects["hook"]
    hook.select_set(True)
    bpy.context.view_layer.objects.active = hook
    bpy.ops.wm.stl_export(filepath=out, export_selected_objects=True,
                          apply_modifiers=True, global_scale=1.0,
                          use_scene_unit=False, ascii_format=False)

Then reply with:
- The path of the STL you wrote.
- A short design rationale (≤150 words).
- The final volume (mm³), surface area (mm²) and profile perimeter (mm)
  computed in Blender.
- Your predicted failure load in newtons and the predicted failure location.

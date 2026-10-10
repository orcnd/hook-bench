"""Export the object named "hook" from the open Blender scene to STL (mm, no scaling).

Run inside Blender (Text Editor, or via MCP execute_blender_code), after setting OUT:
    OUT = "/Users/orcaim/Documents/code/personal/hook-bench/designs/<model>/<run>/hook.stl"
then validate outside Blender:
    uv run validate.py designs/<model>/<run>/hook.stl
"""
import os

import bpy

OUT = globals().get("OUT") or "/Users/orcaim/Documents/code/personal/hook-bench/designs/_scratch/hook.stl"

ob = bpy.data.objects.get("hook")
if ob is None:
    raise RuntimeError('no object named "hook" in the scene')
if bpy.context.object and bpy.context.object.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
bpy.ops.object.select_all(action="DESELECT")
ob.select_set(True)
bpy.context.view_layer.objects.active = ob
# 1 Blender unit = 1 mm in base_scene.blend; write raw units, ignore the scene unit scale.
bpy.ops.wm.stl_export(
    filepath=OUT,
    export_selected_objects=True,
    apply_modifiers=True,
    global_scale=1.0,
    use_scene_unit=False,
    ascii_format=False,
)
print("exported", OUT)

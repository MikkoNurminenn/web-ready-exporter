# SPDX-License-Identifier: MIT
"""Renders docs/hero.png: the trap scene (left) next to its exported GLB re-imported (right).

    blender -b --python tools/make_hero.py -- docs/hero.png /tmp/wre-hero
"""
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from web_ready_exporter import core  # noqa: E402
import trap_scene  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
out_png, tmp = os.path.abspath(argv[0]), os.path.abspath(argv[1])
os.makedirs(tmp, exist_ok=True)

sc = trap_scene.build()
rep = core.run_export(sc, os.path.join(tmp, "hero.glb"), tri_budget=50000)
o0, a = rep["outputs"][0], rep["audit_before"]

# move the source scene to the left
src_root = bpy.data.objects.new("SRC_ROOT", None)
sc.collection.objects.link(src_root)
src_root.location = (-6.8, 0, 0)
for o in [o for o in sc.objects if o.parent is None and o is not src_root]:
    o.parent = src_root

# import the exported GLB on the right
before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=os.path.join(tmp, "hero.glb"))
imported = [o for o in bpy.data.objects if o not in before]
web_root = bpy.data.objects.new("WEB_ROOT", None)
sc.collection.objects.link(web_root)
web_root.location = (6.8, 0, 0)
for o in imported:
    if o.parent is None:
        o.parent = web_root

# Workbench uses viewport display colours: copy Principled base colour into them
for m in bpy.data.materials:
    p = core._principled(m)
    if p is not None:
        m.diffuse_color = p.inputs["Base Color"].default_value
        m.metallic = float(p.inputs["Metallic"].default_value)
        m.roughness = float(p.inputs["Roughness"].default_value)


def label(text, x, color):
    cu = bpy.data.curves.new("label", type="FONT")
    cu.body = text
    cu.size = 0.5
    cu.align_x = "CENTER"
    cu.space_line = 1.25
    ob = bpy.data.objects.new("Label", cu)
    ob.location = (x, 0, -5.4)
    ob.rotation_euler = (1.5708, 0, 0)
    m = bpy.data.materials.new("LabelMat")
    m.diffuse_color = color
    m.roughness = 1.0
    cu.materials.append(m)
    sc.collection.objects.link(ob)


label(f"SOURCE SCENE\n{a['triangles']:,} tris · {a['objects']} objects · {a['materials']} materials\n"
      f"4096² texture · one mesh without material", -6.8, (0.85, 0.87, 0.92, 1))
label(f"WEB GLB · {o0['megabytes']} MB · Draco + WebP\n{o0['triangles']:,} tris · {o0['nodes']}/{o0['nodes']} node names kept · "
      f"{o0['materials']} materials\n1024² texture · missing material flagged magenta", 6.8, (0.36, 0.72, 0.52, 1))

# camera + Workbench
cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 45
cam = bpy.data.objects.new("Cam", cam_data)
sc.collection.objects.link(cam)
cam.location = Vector((0, -36, 4.0))
cam.rotation_euler = (Vector((0, 0, -0.6)) - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.camera = cam

sc.render.engine = "BLENDER_WORKBENCH"
sc.render.resolution_x, sc.render.resolution_y = 1800, 1000
sc.render.resolution_percentage = 100
sc.render.film_transparent = False
sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.color_mode = "RGB"
sc.display.render_aa = "8"
sh = sc.display.shading
sh.light = "STUDIO"
sh.color_type = "TEXTURE"
sh.show_object_outline = True
sh.show_cavity = True
sh.show_specular_highlight = True
sh.background_type = "VIEWPORT"
sh.background_color = (0.04, 0.055, 0.1)
sc.view_settings.view_transform = "Standard"
sc.render.filepath = out_png
bpy.ops.render.render(write_still=True)
print("HERO_OK", out_png, os.path.getsize(out_png))

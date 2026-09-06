# SPDX-License-Identifier: MIT
"""Builds a scene full of web-export traps. Run inside Blender.

    blender -b --python tests/trap_scene.py -- out.blend
"""
import math
import sys

import bmesh
import bpy


def _mat(name, color, rough=0.5, metal=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    p.inputs["Base Color"].default_value = (*color, 1.0)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    return m


def build():
    """Returns the scene. Object counts: 49 base + 20 linked instances = 69."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene

    # three "different" materials that are actually identical, plus steel
    red = _mat("Red", (0.8, 0.1, 0.1))
    red2 = _mat("Red.001", (0.8, 0.1, 0.1))
    red3 = _mat("Plastic red", (0.8, 0.1, 0.1))
    steel = _mat("Steel", (0.7, 0.7, 0.7), 0.3, 1.0)

    # 4K texture
    tex = _mat("Painted", (1, 1, 1))
    img = bpy.data.images.new("hull_paint_4k", 4096, 4096)
    img.generated_type = "COLOR_GRID"
    ti = tex.node_tree.nodes.new("ShaderNodeTexImage")
    ti.image = img
    p = next(n for n in tex.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    tex.node_tree.links.new(ti.outputs["Color"], p.inputs["Base Color"])

    # high-poly hull with protected anchors in a hierarchy
    bpy.ops.mesh.primitive_uv_sphere_add(segments=256, ring_count=128, radius=2)
    hull = bpy.context.object
    hull.name = "Hull"
    hull.data.materials.append(tex)
    for i, (x, y) in enumerate([(1, 0), (-1, 0), (0, 1), (0, -1)]):
        e = bpy.data.objects.new(f"Anchor_Seat{i}", None)
        sc.collection.objects.link(e)
        e.parent = hull
        e.location = (x, y, 1)
    hs = bpy.data.objects.new("Hotspot_Helm", None)
    sc.collection.objects.link(hs)
    hs.parent = hull
    hs.location = (0, 0, 2.2)

    # 40 bolts with duplicate materials
    mats = [red, red2, red3, steel]
    for i in range(40):
        bpy.ops.mesh.primitive_cube_add(size=0.2, location=(math.cos(i) * 3, math.sin(i) * 3, i * 0.05))
        o = bpy.context.object
        o.name = f"Bolt_{i:02d}"
        o.data.materials.append(mats[i % 4])
        o.parent = hull

    # material-less mesh with loose vertices, mirrored cone, scaled torus with a modifier
    bpy.ops.mesh.primitive_cylinder_add(location=(0, 0, -3))
    nm = bpy.context.object
    nm.name = "Console_NoMaterial"
    bpy.ops.mesh.primitive_cone_add(location=(3, 3, 0))
    neg = bpy.context.object
    neg.name = "Mirror_Cone"
    neg.scale = (-1, 1, 1)
    neg.data.materials.append(steel)
    bpy.ops.mesh.primitive_torus_add(location=(-3, -3, 0))
    tor = bpy.context.object
    tor.name = "Rail"
    tor.scale = (2, 2, 2)
    tor.data.materials.append(steel)
    mod = tor.modifiers.new("Sub", "SUBSURF")
    mod.levels = 2
    bm = bmesh.new()
    bm.from_mesh(nm.data)
    for k in range(5):
        bm.verts.new((k, 0, 5))
    bm.to_mesh(nm.data)
    bm.free()

    # 20 linked duplicates sharing ONE mesh datablock (instancing test)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.15, location=(0, 0, 4))
    proto = bpy.context.object
    proto.name = "Rivet_00"
    proto.data.materials.append(steel)
    for i in range(1, 20):
        o = bpy.data.objects.new(f"Rivet_{i:02d}", proto.data)
        o.location = (math.cos(i * 0.33) * 2.5, math.sin(i * 0.33) * 2.5, 4)
        sc.collection.objects.link(o)
    return sc


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--") + 1]
    sc = build()
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print("TRAP_SCENE_OK", len(sc.objects))

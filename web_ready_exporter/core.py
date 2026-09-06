# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mikko Nurminen
"""
Web-Ready Exporter — core pipeline (no UI).

Runs on temporary copies, never touches the source scene:

  1. Audit      material-less meshes, oversized textures, duplicate materials,
                mirrored / unapplied scales, loose vertices, open edges, budget.
  2. Prepare    evaluate modifiers, weld vertices, drop loose geometry, decimate
                to the triangle budget, merge identical materials, downscale
                textures, (optional) join meshes by material. Protected names
                (Anchor_*, Hotspot_* ...) are never joined or dropped.
  3. Export     GLB with Draco + WebP.
  4. Verify     read the GLB back: every node name present (or fail loudly),
                triangles, draw calls, textures, bytes.
  5. Report     <out>.report.json + <out>.report.md next to the GLB.

Headless:
  blender -b scene.blend --python wre_cli.py -- --out out/model.glb \
      --tris 300000 --tex 1024 --lods 0.5,0.25 --strict
"""

import bpy
import bmesh
import json
import os
import struct
import sys
import time
from mathutils import Matrix

VERSION = (1, 0, 0)
TEMP_COLLECTION = "WRE_TEMP_EXPORT"
ORIG_SUFFIX = "__wreorig"
DEFAULT_PROTECT = "Anchor_,Hotspot_,Ctrl_,Pivot_,Socket_"
DEFAULT_MATERIAL_NAME = "WRE_Default"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def log(msg):
    print(f"[WRE] {msg}")


def is_protected(name, prefixes):
    return any(p and name.startswith(p) for p in prefixes)


def parse_prefixes(s):
    return [p.strip() for p in (s or "").split(",") if p.strip()]


def mesh_tris(mesh):
    return sum(max(len(p.vertices) - 2, 0) for p in mesh.polygons)


def _principled(mat):
    if not mat or not mat.use_nodes or not mat.node_tree:
        return None
    for n in mat.node_tree.nodes:
        if n.bl_idname == "ShaderNodeBsdfPrincipled":
            return n
    return None


def _socket_image(node, socket_name):
    """Image feeding a Principled input, following one chain (e.g. Normal Map)."""
    if node is None or socket_name not in node.inputs:
        return None
    sock = node.inputs[socket_name]
    if not sock.is_linked:
        return None
    src = sock.links[0].from_node
    for _ in range(4):
        if src.bl_idname == "ShaderNodeTexImage":
            return src.image
        nxt = None
        for i in src.inputs:
            if i.is_linked:
                nxt = i.links[0].from_node
                break
        if nxt is None:
            return None
        src = nxt
    return None


def material_signature(mat):
    """Fingerprint used to merge visually identical materials."""
    p = _principled(mat)
    if p is None:
        return ("nonodes", tuple(round(c, 3) for c in mat.diffuse_color),
                round(mat.metallic, 3), round(mat.roughness, 3))

    def val(name):
        if name not in p.inputs:
            return None
        v = p.inputs[name].default_value
        try:
            return tuple(round(x, 3) for x in v)
        except TypeError:
            return round(float(v), 3)

    def img(name):
        i = _socket_image(p, name)
        return i.name if i else None

    return (
        val("Base Color"), val("Metallic"), val("Roughness"), val("Alpha"),
        val("Emission Color"), val("Emission Strength"),
        img("Base Color"), img("Metallic"), img("Roughness"), img("Normal"),
        img("Alpha"), img("Emission Color"),
        getattr(mat, "blend_method", None), mat.use_backface_culling,
    )


def _image_nodes(mat):
    out = []
    if mat and mat.use_nodes and mat.node_tree:
        for n in mat.node_tree.nodes:
            if n.bl_idname == "ShaderNodeTexImage" and n.image:
                out.append(n)
    return out


# --------------------------------------------------------------------------- #
# Audit (read-only)
# --------------------------------------------------------------------------- #

def collect_export_objects(scene, use_selection, prefixes):
    objs = []
    for o in scene.objects:
        if o.type not in {"MESH", "EMPTY"}:
            continue
        if use_selection and not o.select_get():
            continue
        if not use_selection and (o.hide_render or o.hide_get()):
            continue
        objs.append(o)
    if use_selection:
        # protected empties/meshes always ride along
        for o in scene.objects:
            if o.type in {"MESH", "EMPTY"} and is_protected(o.name, prefixes) and o not in objs:
                objs.append(o)
    return objs


def audit(objs, max_tex, tri_budget, prefixes, check_topology=True):
    issues = []       # (severity, object, message)
    total_tris = 0
    mats_seen = {}
    sig_groups = {}
    images = {}
    draw_calls = 0
    dg = bpy.context.evaluated_depsgraph_get()

    for o in objs:
        if o.type == "EMPTY":
            continue
        me = o.data
        try:
            tris = mesh_tris(o.evaluated_get(dg).data) if o.modifiers else mesh_tris(me)
        except Exception:
            tris = mesh_tris(me)
        total_tris += tris
        slots = [s.material for s in o.material_slots]
        used_mats = set()
        if len(me.polygons):
            for i in {p.material_index for p in me.polygons}:
                used_mats.add(slots[i] if i < len(slots) else None)
        else:
            issues.append(("WARN", o.name, "Mesh has no polygons"))
        if not slots or None in used_mats:
            issues.append(("ERROR", o.name, "Mesh without material (renders as a white cardboard box in the browser)"))
        draw_calls += max(len(used_mats), 1)
        for m in used_mats:
            if m is None:
                continue
            mats_seen[m.name] = m
            grp = sig_groups.setdefault(material_signature(m), [])
            if m.name not in grp:
                grp.append(m.name)
            for node in _image_nodes(m):
                images[node.image.name] = node.image

        if o.matrix_world.determinant() < 0:
            issues.append(("WARN", o.name, f"Mirrored object (negative scale {tuple(round(v, 2) for v in o.scale)}) — normals may flip in the browser"))
        if any(abs(abs(v) - 1.0) > 1e-4 for v in o.scale):
            issues.append(("INFO", o.name, f"Unapplied object scale {tuple(round(v, 3) for v in o.scale)}"))
        if tri_budget and tris > tri_budget * 0.5:
            issues.append(("WARN", o.name, f"Single object uses {tris:,} triangles (>50% of budget)"))
        if check_topology and len(me.vertices) < 400_000:
            bm = bmesh.new()
            bm.from_mesh(me)
            loose = sum(1 for v in bm.verts if not v.link_edges)
            open_edges = sum(1 for e in bm.edges if len(e.link_faces) == 1)
            ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
            bm.free()
            if loose:
                issues.append(("INFO", o.name, f"{loose} loose vertices (removed on export)"))
            if open_edges and not me.has_custom_normals:
                issues.append(("INFO", o.name, f"{open_edges} open edges (may be intentional seams)"))
            if ngons:
                issues.append(("INFO", o.name, f"{ngons} n-gons (triangulated on export)"))

    for name, img in images.items():
        w, h = img.size
        if w == 0 or h == 0:
            issues.append(("ERROR", name, "Image cannot be loaded (missing file?)"))
        elif max(w, h) > max_tex:
            issues.append(("WARN", name, f"Texture {w}×{h} > {max_tex} px — downscaled on export"))

    dup_groups = [g for g in sig_groups.values() if len(g) > 1]
    for g in dup_groups:
        issues.append(("INFO", g[0], f"Identical materials will be merged: {', '.join(g)}"))

    if tri_budget and total_tris > tri_budget:
        issues.append(("WARN", "SCENE", f"{total_tris:,} triangles > budget {tri_budget:,} — decimating with ratio {tri_budget / total_tris:.2f}"))

    return {
        "objects": len(objs),
        "meshes": sum(1 for o in objs if o.type == "MESH"),
        "triangles": total_tris,
        "draw_calls_estimate": draw_calls,
        "materials": len(mats_seen),
        "duplicate_material_groups": len(dup_groups),
        "images": len(images),
        "protected_names": [o.name for o in objs if is_protected(o.name, prefixes)],
        "issues": issues,
    }


# --------------------------------------------------------------------------- #
# Temporary workspace
# --------------------------------------------------------------------------- #

class Workspace:
    """Temporary copies of the export set. Always torn down in a finally block."""

    def __init__(self, scene):
        self.scene = scene
        self.coll = bpy.data.collections.new(TEMP_COLLECTION)
        scene.collection.children.link(self.coll)
        self.copies = {}          # orig obj -> copy obj
        self.renamed = []         # (orig obj, orig name)
        self.new_meshes = []
        self.new_mats = []
        self.new_images = []

    def duplicate(self, objs):
        dg = bpy.context.evaluated_depsgraph_get()
        shared = {}               # orig mesh -> evaluated copy (modifier-free objects share it)
        for o in objs:
            c = o.copy()
            if o.type == "MESH":
                if not o.modifiers and o.data in shared:
                    me = shared[o.data]
                else:
                    me = bpy.data.meshes.new_from_object(o.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
                    me.name = o.data.name + "_web"
                    self.new_meshes.append(me)
                    if not o.modifiers:
                        shared[o.data] = me
                c.data = me
                c.modifiers.clear()
            self.coll.objects.link(c)
            self.copies[o] = c
        # re-parent between copies; detach others keeping world matrix
        for o, c in self.copies.items():
            if o.parent in self.copies:
                c.parent = self.copies[o.parent]
            elif o.parent is not None:
                mw = o.matrix_world.copy()
                c.parent = None
                c.matrix_world = mw
        # name swap so GLB node names match the source scene
        for o, c in self.copies.items():
            name = o.name
            self.renamed.append((o, name))
            o.name = name + ORIG_SUFFIX
            c.name = name
            if c.name != name:
                raise RuntimeError(f"Could not give copy the name {name!r} (got {c.name!r})")

    def mesh_copies(self):
        return [c for c in self.copies.values() if c.type == "MESH"]

    def unique_meshes(self):
        """mesh datablock -> list of copies using it (preserves instancing)."""
        out = {}
        for c in self.mesh_copies():
            out.setdefault(c.data, []).append(c)
        return out

    def copy_materials(self):
        """Own copies of materials and images so downscaling never touches the source."""
        mat_map, img_map = {}, {}
        for c in self.mesh_copies():
            for slot in c.material_slots:
                m = slot.material
                if m is None:
                    continue
                if m not in mat_map:
                    nm = m.copy()
                    nm.name = m.name + "__web"
                    mat_map[m] = nm
                    self.new_mats.append(nm)
                if slot.material is not mat_map[m]:
                    slot.material = mat_map[m]
        for nm in self.new_mats:
            for node in _image_nodes(nm):
                img = node.image
                if img.name.endswith("__web"):
                    continue
                if img not in img_map:
                    ni = img.copy()
                    ni.name = img.name + "__web"
                    img_map[img] = ni
                    self.new_images.append(ni)
                node.image = img_map[img]
        return mat_map, img_map

    def cleanup(self):
        for c in list(self.copies.values()):
            try:
                bpy.data.objects.remove(c, do_unlink=True)
            except Exception:
                pass
        for o, name in self.renamed:
            try:
                o.name = name
            except Exception:
                pass
        for coll_name in list(bpy.data.collections.keys()):
            if coll_name.startswith(TEMP_COLLECTION):
                bpy.data.collections.remove(bpy.data.collections[coll_name])
        for block, coll in ((self.new_meshes, bpy.data.meshes), (self.new_mats, bpy.data.materials),
                            (self.new_images, bpy.data.images)):
            for datablock in block:
                try:
                    if datablock.users == 0:
                        coll.remove(datablock)
                except ReferenceError:
                    pass  # already removed (e.g. pre-decimation mesh)


# --------------------------------------------------------------------------- #
# Prepare steps (operate on the workspace)
# --------------------------------------------------------------------------- #

def clean_mesh(me, weld_dist):
    bm = bmesh.new()
    bm.from_mesh(me)
    before_v = len(bm.verts)
    if weld_dist > 0 and not me.has_custom_normals:
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=weld_dist)
    loose = [v for v in bm.verts if not v.link_edges]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    degenerate = [f for f in bm.faces if f.calc_area() < 1e-12]
    if degenerate:
        bmesh.ops.delete(bm, geom=degenerate, context="FACES_ONLY")
    bm.to_mesh(me)
    bm.free()
    me.update()
    return before_v - len(me.vertices)


def decimate_mesh(ws, me, users, ratio):
    """Decimate one mesh datablock once and hand the result to every copy using it."""
    if ratio >= 0.999 or not users:
        return 0
    before = mesh_tris(me)
    host = users[0]
    mod = host.modifiers.new("WRE_Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = max(ratio, 0.02)
    mod.use_collapse_triangulate = True
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    new_me = bpy.data.meshes.new_from_object(host.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    new_me.name = me.name
    host.modifiers.remove(mod)
    for c in users:
        c.data = new_me
    ws.new_meshes.append(new_me)
    if me.users == 0:
        bpy.data.meshes.remove(me)
    return before - mesh_tris(new_me)


def dedupe_materials(copies):
    sig_to_mat = {}
    merged = 0
    for c in copies:
        for slot in c.material_slots:
            m = slot.material
            if m is None:
                continue
            keep = sig_to_mat.setdefault(material_signature(m), m)
            if keep is not m:
                slot.material = keep
                merged += 1
    return merged


def assign_default_material(copies, strict):
    fixed = []
    default = None
    for c in copies:
        me = c.data
        if not me.polygons:
            continue
        need = not c.material_slots or any(s.material is None for s in c.material_slots)
        if not need:
            need = any(i >= len(c.material_slots) for i in {p.material_index for p in me.polygons})
        if not need:
            continue
        if strict:
            raise RuntimeError(f"STRICT: mesh without material: {c.name!r}")
        if default is None:
            default = bpy.data.materials.get(DEFAULT_MATERIAL_NAME) or bpy.data.materials.new(DEFAULT_MATERIAL_NAME)
            default.use_nodes = True
            p = _principled(default)
            if p:
                p.inputs["Base Color"].default_value = (0.8, 0.2, 0.8, 1.0)  # magenta = "fix me"
        if not c.material_slots:
            me.materials.append(default)
        for s in c.material_slots:
            if s.material is None:
                s.material = default
        fixed.append(c.name)
    return fixed


def resize_images(images, max_tex):
    resized = []
    for img in images:
        w, h = img.size
        if w == 0 or h == 0 or max(w, h) <= max_tex:
            continue
        f = max_tex / max(w, h)
        nw, nh = max(int(round(w * f)), 1), max(int(round(h * f)), 1)
        img.scale(nw, nh)
        resized.append((img.name.replace("__web", ""), (w, h), (nw, nh)))
    return resized


def merge_by_material(ws, prefixes):
    """Join single-material, unparented, childless, unprotected meshes per material."""
    inverse = {c: o for o, c in ws.copies.items()}
    groups = {}
    for c in ws.mesh_copies():
        if is_protected(c.name, prefixes) or c.parent is not None or c.children:
            continue
        mats = {s.material for s in c.material_slots}
        if len(mats) != 1:
            continue
        groups.setdefault(next(iter(mats)), []).append(c)
    merged_names = []
    for mat, objs in groups.items():
        if len(objs) < 2:
            continue
        target = objs[0]
        bm = bmesh.new()
        for c in objs:
            tmp = c.data.copy()
            tmp.transform(c.matrix_world)
            bm.from_mesh(tmp)
            bpy.data.meshes.remove(tmp)
        new_me = bpy.data.meshes.new(f"Merged_{mat.name}")
        bm.to_mesh(new_me)
        bm.free()
        new_me.materials.append(mat)
        target.data = new_me
        target.matrix_world = Matrix.Identity(4)
        ws.new_meshes.append(new_me)
        merged_names.append(target.name)
        for c in objs[1:]:
            merged_names.append(c.name)
            del ws.copies[inverse[c]]
            bpy.data.objects.remove(c, do_unlink=True)
        target.name = f"Merged_{mat.name}"
    return merged_names


# --------------------------------------------------------------------------- #
# GLB verification (pure Python, no re-import)
# --------------------------------------------------------------------------- #

def verify_glb(path, expected_names=(), strict_names=True):
    with open(path, "rb") as f:
        magic, _version, _length = struct.unpack("<4sII", f.read(12))
        if magic != b"glTF":
            raise RuntimeError("Not a GLB file")
        chunk_len, _chunk_type = struct.unpack("<II", f.read(8))
        gltf = json.loads(f.read(chunk_len).decode("utf-8"))
    nodes = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    accessors = gltf.get("accessors", [])
    names = {n.get("name", "") for n in nodes}
    draw_calls = 0
    tris = 0
    for n in nodes:
        if "mesh" in n:
            prims = meshes[n["mesh"]].get("primitives", [])
            draw_calls += len(prims)
            for p in prims:
                if "indices" in p:
                    tris += accessors[p["indices"]]["count"] // 3
                else:
                    tris += accessors[p["attributes"]["POSITION"]]["count"] // 3
    draco = any("KHR_draco_mesh_compression" in p.get("extensions", {})
                for m in meshes for p in m.get("primitives", []))
    images = gltf.get("images", [])
    missing = [n for n in expected_names if n not in names]
    if missing and strict_names:
        raise RuntimeError(f"GLB is missing {len(missing)} node name(s): {missing[:10]}")
    size = os.path.getsize(path)
    return {
        "file": path,
        "bytes": size,
        "megabytes": round(size / 1e6, 2),
        "nodes": len(nodes),
        "meshes": len(meshes),
        "draw_calls": draw_calls,
        "triangles": tris,
        "materials": len(gltf.get("materials", [])),
        "images": len(images),
        "image_mime": sorted({i.get("mimeType", "?") for i in images}),
        "draco": draco,
        "extensions": gltf.get("extensionsUsed", []),
        "missing_names": missing,
    }


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #

def _gltf_export(path, collection_name, *, draco, level):
    bpy.ops.export_scene.gltf(
        filepath=path, export_format="GLB", collection=collection_name,
        export_apply=True, export_yup=True,
        export_draco_mesh_compression_enable=bool(draco),
        export_draco_mesh_compression_level=int(level),
        export_draco_position_quantization=14,
        export_draco_normal_quantization=10,
        export_draco_texcoord_quantization=12,
        export_image_format="WEBP", export_image_quality=80,
        export_animations=False, export_skins=False, export_morph=False,
        export_cameras=False, export_lights=False, export_extras=True,
        export_texcoords=True, export_normals=True, export_tangents=False,
        export_unused_images=False,
    )


def run_export(scene, out_path, *, tri_budget=300000, max_tex=1024, draco_level=6,
               weld_dist=1e-4, protect=DEFAULT_PROTECT, merge=False, strict=False,
               use_selection=False, lod_ratios=(), keep_temp=False):
    t0 = time.time()
    prefixes = parse_prefixes(protect)
    objs = collect_export_objects(scene, use_selection, prefixes)
    if not objs:
        raise RuntimeError("Nothing to export (no mesh/empty objects).")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    report = {
        "tool": "web_ready_exporter", "version": ".".join(map(str, VERSION)),
        "blender": bpy.app.version_string, "blend_file": bpy.data.filepath,
        "settings": dict(tri_budget=tri_budget, max_tex=max_tex, draco_level=draco_level,
                         weld_dist=weld_dist, protect=prefixes, merge=merge, strict=strict,
                         lod_ratios=list(lod_ratios)),
    }
    report["audit_before"] = audit(objs, max_tex, tri_budget, prefixes, check_topology=False)
    errors = [i for i in report["audit_before"]["issues"] if i[0] == "ERROR"]
    if strict and errors:
        raise RuntimeError("STRICT: audit found errors: " + "; ".join(f"{o}: {m}" for _, o, m in errors))

    expected_names = [o.name for o in objs]
    outputs = []
    ratios = [1.0] + [float(r) for r in lod_ratios]
    for li, lod_ratio in enumerate(ratios):
        ws = Workspace(scene)
        try:
            ws.duplicate(objs)
            ws.copy_materials()
            fixed = assign_default_material(ws.mesh_copies(), strict)
            welded = 0
            for me in ws.unique_meshes():
                welded += clean_mesh(me, weld_dist)
            total = sum(mesh_tris(me) * len(users) for me, users in ws.unique_meshes().items())
            budget = tri_budget * lod_ratio if tri_budget else 0
            ratio = min(1.0, budget / total) if (budget and total) else 1.0
            if lod_ratio < 1.0 and ratio >= 1.0:
                ratio = lod_ratio
            removed = 0
            if ratio < 1.0:
                for me, users in list(ws.unique_meshes().items()):
                    if mesh_tris(me) > 200:
                        removed += decimate_mesh(ws, me, users, ratio) * len(users)
            merged_mats = dedupe_materials(ws.mesh_copies())
            merged_objs = merge_by_material(ws, prefixes) if merge else []
            resized = resize_images(ws.new_images, int(max_tex * lod_ratio) if lod_ratio < 1 else max_tex)

            if li == 0:
                path = out_path
            else:
                root, ext = os.path.splitext(out_path)
                path = f"{root}_lod{li}{ext or '.glb'}"

            warnings = []
            try:
                _gltf_export(path, ws.coll.name, draco=True, level=draco_level)
            except RuntimeError as e:
                if "draco" not in str(e).lower():
                    raise
                detail = str(e).strip().splitlines()[-1] if str(e).strip() else "unknown error"
                msg = (f"Draco encoder unavailable in this Blender build ({detail}); exported UNCOMPRESSED. "
                       "On Linux add Blender's lib folder to LD_LIBRARY_PATH.")
                if strict:
                    raise RuntimeError("STRICT: " + msg) from e
                log("WARN " + msg)
                warnings.append(msg)
                _gltf_export(path, ws.coll.name, draco=False, level=draco_level)
            names_to_check = [n for n in expected_names if n not in merged_objs]
            v = verify_glb(path, names_to_check, strict_names=True)
            v.update(lod=li, lod_ratio=lod_ratio, decimate_ratio=round(ratio, 3),
                     triangles_removed=removed, verts_welded=welded,
                     materials_merged=merged_mats, objects_merged=merged_objs,
                     materialless_fixed=fixed, textures_resized=resized,
                     unique_meshes=len(ws.unique_meshes()), warnings=warnings)
            outputs.append(v)
            log(f"LOD{li}: {v['megabytes']} MB, {v['triangles']:,} tris, {v['draw_calls']} draw calls, "
                f"{v['nodes']} nodes, {v['meshes']} meshes -> {path}")
        finally:
            if not keep_temp:
                ws.cleanup()

    report["outputs"] = outputs
    report["seconds"] = round(time.time() - t0, 1)
    write_report(out_path, report)
    return report


def write_report(out_path, report):
    root, _ = os.path.splitext(out_path)
    with open(root + ".report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    a = report["audit_before"]
    o0 = report["outputs"][0]
    lines = [
        "# Web-Ready Exporter report", "",
        f"Blender {report['blender']} · {report['seconds']} s · {os.path.basename(report['blend_file']) or '(unsaved file)'}", "",
        "## Before", "",
        "| Objects | Triangles | Draw calls (est.) | Materials | Images |", "|---|---|---|---|---|",
        f"| {a['objects']} | {a['triangles']:,} | {a['draw_calls_estimate']} | {a['materials']} | {a['images']} |", "",
        "## After", "",
        "| File | MB | Triangles | Draw calls | Nodes | Meshes | Materials | Images | Draco |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for o in report["outputs"]:
        lines.append(f"| {os.path.basename(o['file'])} | {o['megabytes']} | {o['triangles']:,} | {o['draw_calls']} | "
                     f"{o['nodes']} | {o['meshes']} | {o['materials']} | {o['images']} ({', '.join(o['image_mime']) or '-'}) | "
                     f"{'yes' if o['draco'] else 'no'} |")
    lines += [
        "", "## Actions taken (LOD0)", "",
        f"- Vertices welded/removed: {o0['verts_welded']}",
        f"- Triangles decimated: {o0['triangles_removed']:,} (ratio {o0['decimate_ratio']})",
        f"- Materials merged: {o0['materials_merged']}",
        f"- Objects joined by material: {len(o0['objects_merged'])}",
        f"- Material-less meshes given the default magenta material: {', '.join(o0['materialless_fixed']) or '-'}",
        "- Textures downscaled: " + (", ".join(f"{n} {w}×{h} → {nw}×{nh}" for n, (w, h), (nw, nh) in o0['textures_resized']) or "-"),
        f"- Protected names preserved: {len(a['protected_names'])}"
        + (f" ({', '.join(a['protected_names'][:8])}{'…' if len(a['protected_names']) > 8 else ''})" if a['protected_names'] else ""),
    ]
    warns = [w for o in report["outputs"] for w in o.get("warnings", [])]
    if warns:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in warns]
    lines += ["", "## Audit findings", ""]
    for sev, obj, msg in a["issues"]:
        lines.append(f"- **{sev}** `{obj}`: {msg}")
    if not a["issues"]:
        lines.append("- None.")
    with open(root + ".report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Headless CLI
# --------------------------------------------------------------------------- #

def cli(argv=None):
    import argparse
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="web_ready_exporter",
                                 description="Audit, prepare, export and verify a web-ready GLB.")
    ap.add_argument("--out", help="output GLB path (omit for audit only)")
    ap.add_argument("--tris", type=int, default=300000, help="triangle budget for the whole model, 0 = no decimation")
    ap.add_argument("--tex", type=int, default=1024, help="max texture side in pixels")
    ap.add_argument("--draco", type=int, default=6, help="Draco compression level 0-10")
    ap.add_argument("--weld", type=float, default=1e-4, help="weld distance in metres")
    ap.add_argument("--protect", default=DEFAULT_PROTECT, help="comma-separated protected name prefixes")
    ap.add_argument("--lods", default="", help="LOD ratios, e.g. 0.5,0.25")
    ap.add_argument("--merge", action="store_true", help="join unparented single-material meshes per material")
    ap.add_argument("--strict", action="store_true", help="fail on audit errors instead of fixing them")
    ap.add_argument("--selection", action="store_true", help="export selected objects only (+ protected)")
    ap.add_argument("--audit", action="store_true", help="audit only, print JSON, do not export")
    a = ap.parse_args(argv)

    scene = bpy.context.scene
    prefixes = parse_prefixes(a.protect)
    try:
        if a.audit or not a.out:
            objs = collect_export_objects(scene, a.selection, prefixes)
            rep = audit(objs, a.tex, a.tris, prefixes)
            print(json.dumps({k: v for k, v in rep.items() if k != "issues"}, indent=2, ensure_ascii=False))
            for sev, obj, msg in rep["issues"]:
                print(f"{sev:5} {obj}: {msg}")
            if not a.out:
                return 0
        lods = [float(x) for x in a.lods.split(",") if x.strip()]
        rep = run_export(scene, a.out, tri_budget=a.tris, max_tex=a.tex, draco_level=a.draco,
                         weld_dist=a.weld, protect=a.protect, merge=a.merge, strict=a.strict,
                         use_selection=a.selection, lod_ratios=lods)
    except Exception as e:  # noqa: BLE001 — agents and CI need a non-zero exit code
        print(f"WRE_FAIL {type(e).__name__}: {e}")
        sys.exit(1)
    o = rep["outputs"][0]
    print("WRE_OK " + json.dumps({k: o[k] for k in ("file", "megabytes", "triangles", "draw_calls", "nodes", "meshes", "images", "draco")}))
    return 0


if __name__ == "__main__" and bpy.app.background:
    cli()

# SPDX-License-Identifier: MIT
"""End-to-end tests, run inside Blender:

    blender -b --python tests/test_export.py --python-exit-code 1 -- <out_dir>
"""
import json
import os
import struct
import sys
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from web_ready_exporter import core  # noqa: E402
import trap_scene  # noqa: E402

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.path.join(ROOT, "tests", "_out")
os.makedirs(OUT, exist_ok=True)
FAILURES = []


def check(cond, msg):
    status = "ok  " if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        FAILURES.append(msg)


def gltf_json(path):
    with open(path, "rb") as f:
        f.read(12)
        n, _ = struct.unpack("<II", f.read(8))
        return json.loads(f.read(n))


def no_leftovers():
    return (sum(1 for o in bpy.data.objects if core.ORIG_SUFFIX in o.name) == 0
            and sum(1 for c in bpy.data.collections if c.name.startswith(core.TEMP_COLLECTION)) == 0)


def test_default_with_lods():
    print("test_default_with_lods")
    sc = trap_scene.build()
    n_objects = len(sc.objects)
    out = os.path.join(OUT, "default", "model.glb")
    rep = core.run_export(sc, out, tri_budget=50000, max_tex=1024, lod_ratios=(0.5, 0.25))
    o0, o1, o2 = rep["outputs"]
    check(o0["missing_names"] == [], "all node names present in LOD0")
    check(o0["nodes"] == n_objects, f"node count == object count ({o0['nodes']} == {n_objects})")
    check(o0["triangles"] <= 50000 * 1.02, f"triangles within budget ({o0['triangles']:,} <= 51,000)")
    check(o0["image_mime"] == ["image/webp"], f"textures are WebP ({o0['image_mime']})")
    check(o0["draco"] is True, "Draco compression present")
    check(o0["materials"] == 4, f"identical materials merged 5 -> 4 (got {o0['materials']})")
    check(o0["materialless_fixed"] == ["Console_NoMaterial"], "material-less mesh patched")
    check(o0["textures_resized"] and o0["textures_resized"][0][2] == (1024, 1024), "4K texture downscaled to 1024")
    # 64 mesh objects, of which 20 rivets share one datablock -> 45 unique meshes in the GLB
    check(o0["meshes"] == 45, f"instances share meshes ({o0['meshes']} meshes for {o0['nodes']} nodes, expected 45)")
    check(o1["triangles"] < o0["triangles"] and o2["triangles"] < o1["triangles"], "LODs shrink triangles")
    check(o1["bytes"] < o0["bytes"] and o2["bytes"] < o1["bytes"], "LODs shrink bytes")
    check(os.path.exists(os.path.join(OUT, "default", "model_lod2.glb")), "LOD2 file exists")
    check(os.path.exists(os.path.join(OUT, "default", "model.report.md")), "markdown report written")
    g = gltf_json(out)
    nodes = {n.get("name"): n for n in g["nodes"]}
    check(nodes["Anchor_Seat0"].get("translation") == [1, 1, 0], f"Anchor_Seat0 Y-up translation {nodes['Anchor_Seat0'].get('translation')}")
    hull = nodes["Hull"]
    check(g["nodes"].index(nodes["Hotspot_Helm"]) in hull.get("children", []), "Hotspot_Helm is a child of Hull")
    check(no_leftovers(), "no working copies left in the scene")
    check(len(sc.objects) == n_objects, "source object count unchanged")
    check(bpy.data.images["hull_paint_4k"].size[0] == 4096, "source texture untouched (still 4096)")


def test_merge_respects_protected():
    print("test_merge_respects_protected")
    sc = trap_scene.build()
    out = os.path.join(OUT, "merge", "model.glb")
    rep = core.run_export(sc, out, tri_budget=0, merge=True)
    o0 = rep["outputs"][0]
    g = gltf_json(out)
    names = {n.get("name") for n in g["nodes"]}
    check(all(n in names for n in ["Anchor_Seat0", "Anchor_Seat3", "Hotspot_Helm", "Hull"]), "protected + parent names survive merge")
    check(len(o0["objects_merged"]) >= 2 and "Mirror_Cone" in o0["objects_merged"], f"unparented steel meshes merged: {o0['objects_merged']}")
    check(o0["draw_calls"] < rep["audit_before"]["draw_calls_estimate"], "merge reduces draw calls")
    check(no_leftovers(), "no working copies left in the scene")


def test_strict_fails_on_materialless():
    print("test_strict_fails_on_materialless")
    sc = trap_scene.build()
    out = os.path.join(OUT, "strict", "model.glb")
    raised = False
    try:
        core.run_export(sc, out, strict=True)
    except RuntimeError as e:
        raised = "STRICT" in str(e) and "Console_NoMaterial" in str(e)
    check(raised, "strict mode raises RuntimeError naming the offending mesh")
    check(not os.path.exists(out), "no GLB written in strict failure")
    check(no_leftovers(), "no working copies left after failure")


def test_missing_name_is_detected():
    print("test_missing_name_is_detected")
    sc = trap_scene.build()
    out = os.path.join(OUT, "names", "model.glb")
    core.run_export(sc, out, tri_budget=0)
    raised = False
    try:
        core.verify_glb(out, ["Hull", "Anchor_Seat0", "DoesNotExist"])
    except RuntimeError as e:
        raised = "DoesNotExist" in str(e)
    check(raised, "verify_glb raises on a missing node name")


def test_audit_findings():
    print("test_audit_findings")
    sc = trap_scene.build()
    prefixes = core.parse_prefixes(core.DEFAULT_PROTECT)
    objs = core.collect_export_objects(sc, False, prefixes)
    a = core.audit(objs, 1024, 50000, prefixes)
    kinds = {(sev, obj) for sev, obj, _ in a["issues"]}
    check(("ERROR", "Console_NoMaterial") in kinds, "audit flags material-less mesh")
    check(("WARN", "Mirror_Cone") in kinds, "audit flags mirrored object")
    check(("WARN", "hull_paint_4k") in kinds, "audit flags oversized texture")
    check(("WARN", "SCENE") in kinds, "audit flags budget overrun")
    check(a["duplicate_material_groups"] == 1, "audit finds one duplicate material group")
    check(len(a["protected_names"]) == 5, "audit lists 5 protected names")
    check(a["triangles"] > 80000, f"audit counts evaluated (modifier) triangles: {a['triangles']:,}")


if __name__ == "__main__":
    tests = [test_default_with_lods, test_merge_respects_protected, test_strict_fails_on_materialless,
             test_missing_name_is_detected, test_audit_findings]
    for t in tests:
        try:
            t()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            FAILURES.append(f"{t.__name__} crashed")
    print()
    if FAILURES:
        print(f"TESTS FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print(f"ALL TESTS PASSED ({len(tests)} tests) on Blender {bpy.app.version_string}")

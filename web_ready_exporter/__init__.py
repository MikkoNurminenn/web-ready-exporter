# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mikko Nurminen
"""Web-Ready Exporter — Blender UI (sidebar panel). Pipeline lives in core.py."""

import os
from pathlib import Path

import bpy

from . import core


class WRE_Settings(bpy.types.PropertyGroup):
    out_path: bpy.props.StringProperty(
        name="GLB file", subtype="FILE_PATH", default="//web/model.glb",
        description="Output GLB. The report (.report.json/.md) is written next to it")
    tri_budget: bpy.props.IntProperty(
        name="Triangle budget", default=300000, min=0, soft_max=2000000,
        description="Target triangle count for the whole model. 0 disables decimation")
    max_tex: bpy.props.IntProperty(
        name="Max texture (px)", default=1024, min=64, max=8192,
        description="Longest texture side. Larger images are downscaled on copies")
    draco_level: bpy.props.IntProperty(name="Draco level", default=6, min=0, max=10)
    weld_dist: bpy.props.FloatProperty(
        name="Weld distance", default=0.0001, min=0.0, precision=5,
        description="Merge vertices closer than this (skipped on meshes with custom normals)")
    protect: bpy.props.StringProperty(
        name="Protected prefixes", default=core.DEFAULT_PROTECT,
        description="Objects whose names start with these are never joined or dropped")
    merge: bpy.props.BoolProperty(
        name="Join meshes by material", default=False,
        description="Fewer draw calls, but joined objects lose their names. Never touches protected or parented objects")
    strict: bpy.props.BoolProperty(
        name="Strict (fail on errors)", default=False,
        description="Abort instead of patching material-less meshes")
    use_selection: bpy.props.BoolProperty(name="Selected only", default=False)
    lods: bpy.props.StringProperty(
        name="LOD ratios", default="",
        description="Comma-separated, e.g. 0.5,0.25 → model_lod1.glb, model_lod2.glb")
    keep_temp: bpy.props.BoolProperty(
        name="Keep working copies (debug)", default=False,
        description="Leave the WRE_TEMP_EXPORT collection in the scene after export")
    last_summary: bpy.props.StringProperty(default="")


class WRE_OT_audit(bpy.types.Operator):
    bl_idname = "wre.audit"
    bl_label = "Audit"
    bl_description = "Check the scene for web-export problems. Changes nothing"

    def execute(self, context):
        s = context.scene.wre
        prefixes = core.parse_prefixes(s.protect)
        objs = core.collect_export_objects(context.scene, s.use_selection, prefixes)
        a = core.audit(objs, s.max_tex, s.tri_budget, prefixes)
        n_err = sum(1 for i in a["issues"] if i[0] == "ERROR")
        n_warn = sum(1 for i in a["issues"] if i[0] == "WARN")
        s.last_summary = (f"{a['objects']} obj · {a['triangles']:,} tris · ~{a['draw_calls_estimate']} dc · "
                          f"{a['materials']} mat · {a['images']} img | {n_err} errors, {n_warn} warnings")
        for sev, obj, msg in a["issues"]:
            print(f"[WRE audit] {sev:5} {obj}: {msg}")
        self.report({"ERROR"} if n_err else {"INFO"}, s.last_summary + " (details in the system console)")
        return {"FINISHED"}


class WRE_OT_export(bpy.types.Operator):
    bl_idname = "wre.export"
    bl_label = "Export web GLB"
    bl_description = "Clean, decimate, compress, export and verify on temporary copies"

    def execute(self, context):
        s = context.scene.wre
        out = bpy.path.abspath(s.out_path)
        lods = [float(x) for x in s.lods.split(",") if x.strip()]
        try:
            rep = core.run_export(context.scene, out, tri_budget=s.tri_budget, max_tex=s.max_tex,
                                  draco_level=s.draco_level, weld_dist=s.weld_dist, protect=s.protect,
                                  merge=s.merge, strict=s.strict, use_selection=s.use_selection,
                                  lod_ratios=lods, keep_temp=s.keep_temp)
        except Exception as e:  # noqa: BLE001 — surfaced to the user as an operator error
            self.report({"ERROR"}, f"Export failed: {e}")
            return {"CANCELLED"}
        o = rep["outputs"][0]
        s.last_summary = (f"{o['megabytes']} MB · {o['triangles']:,} tris · {o['draw_calls']} dc · "
                          f"{o['nodes']} nodes · {rep['seconds']} s")
        self.report({"INFO"}, "Done: " + s.last_summary)
        return {"FINISHED"}


class WRE_OT_open_report(bpy.types.Operator):
    bl_idname = "wre.open_report"
    bl_label = "Open report"
    bl_description = "Open the Markdown report of the last export"

    def execute(self, context):
        root, _ = os.path.splitext(bpy.path.abspath(context.scene.wre.out_path))
        p = Path(root + ".report.md")
        if not p.exists():
            self.report({"WARNING"}, "No report yet — export first")
            return {"CANCELLED"}
        bpy.ops.wm.url_open(url=p.resolve().as_uri())
        return {"FINISHED"}


class WRE_PT_panel(bpy.types.Panel):
    bl_label = "Web-Ready Exporter"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Web Export"

    def draw(self, context):
        s = context.scene.wre
        col = self.layout.column(align=True)
        col.prop(s, "out_path")
        col.prop(s, "tri_budget")
        col.prop(s, "max_tex")
        col.prop(s, "draco_level")
        col.prop(s, "protect")
        col.separator()
        col.prop(s, "use_selection")
        col.prop(s, "merge")
        col.prop(s, "strict")
        col.prop(s, "lods")
        box = self.layout.box()
        box.label(text="Advanced", icon="PREFERENCES")
        box.prop(s, "weld_dist")
        box.prop(s, "keep_temp")
        row = self.layout.row(align=True)
        row.operator("wre.audit", icon="VIEWZOOM")
        row.operator("wre.export", icon="EXPORT")
        self.layout.operator("wre.open_report", icon="TEXT")
        if s.last_summary:
            self.layout.label(text=s.last_summary, icon="INFO")


CLASSES = (WRE_Settings, WRE_OT_audit, WRE_OT_export, WRE_OT_open_report, WRE_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.wre = bpy.props.PointerProperty(type=WRE_Settings)


def unregister():
    del bpy.types.Scene.wre
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

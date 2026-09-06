# Changelog

## 1.0.1 — 2026-09-06

- Draco encoder unavailable (Blender 5.x on Linux without Blender's `lib` folder on `LD_LIBRARY_PATH`) no longer crashes the export: the GLB is written uncompressed with a `WARN`, a *Warnings* section in the report and `"draco": false`; `--strict` fails instead.
- CI exposes Blender's bundled shared libraries so Draco is exercised on both Blender versions.
- README: Linux headless note.

## 1.0.0 — 2026-09-06

First public release.

- Audit: material-less meshes, oversized textures, duplicate materials, mirrored and unapplied scales, loose vertices, open edges, n-gons, triangle budget.
- Prepare on temporary copies: modifier evaluation, vertex welding, loose/degenerate cleanup, decimation to budget, material de-duplication, texture downscaling, optional join-by-material. Linked duplicates keep sharing one mesh.
- Export: GLB with Draco mesh compression and WebP textures, optional LOD chain.
- Verify: the GLB is parsed back without re-import; a missing node name fails the export. Protected prefixes (`Anchor_`, `Hotspot_`, `Ctrl_`, `Pivot_`, `Socket_`) are never joined or dropped.
- Report: `<out>.report.json` and `<out>.report.md` next to the GLB.
- Sidebar panel (N → Web Export) and headless CLI (`wre_cli.py`) with `WRE_OK` / `WRE_FAIL` output and non-zero exit code on failure.
- Blender 4.2 LTS and 5.2 LTS extension package; end-to-end test suite runs on both in CI.

Known gaps: no KTX2/Basis texture compression; decimation drops custom normals on the affected meshes; a screenshot of the sidebar panel is still pending.

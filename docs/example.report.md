# Web-Ready Exporter report

Blender 5.2.0 LTS · 1.6 s · (unsaved file)

## Before

| Objects | Triangles | Draw calls (est.) | Materials | Images |
|---|---|---|---|---|
| 69 | 90,522 | 64 | 5 | 1 |

## After

| File | MB | Triangles | Draw calls | Nodes | Meshes | Materials | Images | Draco |
|---|---|---|---|---|---|---|---|---|
| model.glb | 1.03 | 50,282 | 64 | 69 | 45 | 4 | 1 (image/webp) | yes |
| model_lod1.glb | 0.58 | 25,474 | 64 | 69 | 45 | 4 | 1 (image/webp) | yes |
| model_lod2.glb | 0.32 | 13,068 | 64 | 69 | 45 | 4 | 1 (image/webp) | yes |

## Actions taken (LOD0)

- Vertices welded/removed: 5
- Triangles decimated: 40,240 (ratio 0.552)
- Materials merged: 21
- Objects joined by material: 0
- Material-less meshes given the default magenta material: Console_NoMaterial
- Textures downscaled: hull_paint_4k 4096×4096 → 1024×1024
- Protected names preserved: 5 (Anchor_Seat0, Anchor_Seat1, Anchor_Seat2, Anchor_Seat3, Hotspot_Helm)

## Audit findings

- **WARN** `Hull`: Single object uses 65,024 triangles (>50% of budget)
- **ERROR** `Console_NoMaterial`: Mesh without material (renders as a white cardboard box in the browser)
- **WARN** `Mirror_Cone`: Mirrored object (negative scale (-1.0, 1.0, 1.0)) — normals may flip in the browser
- **INFO** `Rail`: Unapplied object scale (2.0, 2.0, 2.0)
- **WARN** `hull_paint_4k`: Texture 4096×4096 > 1024 px — downscaled on export
- **INFO** `Red`: Identical materials will be merged: Red, Red.001, Plastic red
- **WARN** `SCENE`: 90,522 triangles > budget 50,000 — decimating with ratio 0.55

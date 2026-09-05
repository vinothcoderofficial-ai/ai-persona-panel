# `data/models/` — third-party 3D assets

Everything in this directory came from somewhere else. This file records where, under what
licence, and — just as important — **what each asset is not**.

## `WaterBottle.glb`

|  |  |
|---|---|
| Source | [KhronosGroup/glTF-Sample-Assets](https://github.com/KhronosGroup/glTF-Sample-Assets), `Models/WaterBottle/glTF-Binary/WaterBottle.glb` |
| Retrieved | 2026-09-05, from `raw.githubusercontent.com` at `main` |
| Licence | **CC0 1.0 Universal** (public domain dedication) — © 2017 Public, "Microsoft for Everything" |
| Size | 8,966,700 bytes |
| SHA-256 | `b337e526fd6a162013c2984aeec163f5fbb4f717252724dfc3f3458bd51df94b` |

A glTF 2.0 binary with a metal/roughness PBR material and normal, occlusion and emissive maps.
It is the Khronos Group's own reference asset for that material model, which is why it is a
reasonable thing to point a renderer at: if it draws correctly, the renderer is handling PBR
glTF correctly.

CC0 imposes no attribution requirement. The attribution above is recorded anyway, because a
repository that cannot say where its assets came from is a repository you cannot audit.

### What it is not

**It is not a SKU, and it is not in the planogram.** `data/planograms/demo_aisle.json` is
unchanged: the same 24 SKUs, the same slots, the same facings, the same deliberately empty
eye-level position. Nothing in this directory is read by `sim/`, by `analytics/`, or by
`scripts/eval.py`, and adding it moved no number in `RESULTS.md`.

That separation is the point. The synthetic panel and the real panel are compared on the
planogram document, not on what the browser happens to draw — so a rendering asset can be added
without touching a single measured quantity. If this file ever starts feeding the simulator, the
comparison stops being about the shelf and starts being about the art, and the study is worth
less.

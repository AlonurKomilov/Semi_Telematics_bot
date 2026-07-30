# Truck Anatomy — model pipeline

The viewer renders one node per taxonomy assembly. For each it tries

```
public/models/truck-anatomy/<assembly_key>.glb
```

and falls back to a labeled placeholder box when the file is missing
or unreadable. **Dropping a correctly named GLB into that folder is
the entire integration** — no code change, no registration. The keys
are `service_assembly_library.key` values (`water_pump`,
`air_dryer`, `pads_shoes`, …) — the same vocabulary the whole product
speaks. That folder is the ONE thing outside this feature directory;
delete both and the feature is fully gone.

## Spec for a model (hand this section to any 3D freelancer)

- **Format**: `.glb` (binary glTF 2.0), self-contained (textures
  embedded), draco compression fine.
- **Scale**: real-world meters. A brake chamber ≈ 0.2 m, a radiator
  ≈ 1 m wide. The ghost rig is ~20 m long — a wrongly scaled part is
  instantly obvious.
- **Origin**: the model's natural mounting center at (0,0,0). The
  viewer places the origin at the assembly's chassis slot
  (`layout.ts`), so a part with a corner origin will sit offset.
- **Orientation**: +X toward the truck's nose, +Y up, +Z driver's
  side (matches `layout.ts`).
- **Budget**: ≤ 50k triangles per assembly, one material set; there
  can be 112 of these in one scene.
- **Node naming (future component level)**: name meshes inside the
  GLB after component concepts (`impeller`, `housing`, `pulley`) —
  when the CK33 component vocabulary lands (see the VMRS adoption
  runbook in docs/architecture/vendor-parts-master-data.md), named
  nodes become individually selectable without remodeling.
- **License**: only sources whose license allows use inside a web
  app (GrabCAD per-model terms, TurboSquid standard license or
  better, or our own photogrammetry — which is preferred for
  authenticity: scan real cores at a partner shop).

## Where positions come from

`layout.ts` — pure data. Assemblies with an authored entry render on
the chassis; everything else parks on the labeled shelf beside the
rig, grouped by system, until its position is authored. Graduating a
system = writing ~6 `pos`/`size` lines.

## Roadmap sockets already in place

- Info card = the future data layer (spend per assembly from
  `cost_by_assembly`, failure counts, intervals).
- WebXR: same codebase, r3f supports it — a later `<XRButton>` stage,
  not a rewrite.

# Architecture

## High-level flow

```text
User / FreeCAD viewport
        │
        ▼
PrintFEA wizard
  ├─ model + print settings
  ├─ build orientation
  ├─ fixed faces
  ├─ face loads
  └─ clicked contact-patch loads
        │
        ├──────────────► background FreeCADCmd structure worker
        │                  ├─ CAD cross-section sampling
        │                  ├─ perimeter/core offsets
        │                  └─ shell/core/effective-material estimate
        │
        ▼
FreeCAD FEM objects
        │
        ▼
Gmsh tetrahedral mesh
        │
        ▼
CalculiX input/deck customization
  ├─ orthotropic material orientation
  ├─ distributed face load
  └─ normalized nodal contact-patch CLOADs
        │
        ▼
CalculiX solve
        │
        ▼
FreeCAD result pipeline
        │
        ├─ FDM directional utilization
        ├─ von Mises stress
        └─ movement
        │
        ▼
Decision-first Results window
```

## GUI

`gui/wizard.py` owns the guided setup and persistent selection/load previews.

`gui/preview.py` draws non-pickable build arrows, force arrows, contact footprints, and captured-face overlays.

`gui/results_dialog.py` owns recent results, simple/advanced result presentation, and likely failure-region visualization.

`gui/setup_store.py` persists restorable setup configurations inside the FreeCAD document.

`gui/compare_dialog.py` compares saved runs.

## Print-structure worker

`workers/structure_worker.py` runs outside the main FreeCAD GUI using `FreeCADCmd`.

The worker samples representative planes perpendicular to the build direction and offsets each section inward for the requested wall count. Independent slices can be processed in parallel CPU processes. Slow/pathological slice operations are time-limited and conservatively lose additional dense-shell credit rather than hanging the main analysis.

## FEM / CalculiX

`fem/analysis.py` creates the FEM analysis, material, constraints, Gmsh mesh, and CalculiX solve.

Layer-aware mode customizes the CalculiX input so the homogenized material has orthotropic engineering constants aligned with the captured build direction.

Clicked contact-patch loads are mapped to compatible surface mesh nodes and emitted as normalized nodal `*CLOAD` entries whose vector sum equals the user-entered total force.

## Post-processing

`post/results.py` extracts stress/displacement data and computes directional utilization using the selected FDM allowables.

The default overall verdict uses a representative 99th-percentile utilization rather than blindly treating the single absolute peak node as the global failure criterion. Absolute peaks remain visible as local-hotspot warnings because fixed/load boundaries and sharp geometry can produce mesh-sensitive concentrations.

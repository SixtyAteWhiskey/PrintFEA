# Changelog

Notable changes are grouped by meaningful project milestones rather than every internal test build.

## v0.5.9 — Release-prep baseline (2026-09-03)

- Removed the experimental STL → STEP converter and intentionally narrowed supported geometry to native FreeCAD/STEP solids.
- Kept the validated v0.4.x analysis physics unchanged.
- Added/retained saved setup restore, saved-run comparison, diagnostics logging, in-app Help, and validation example.
- Prepared the project for public GitHub release with documentation, validation notes, issue templates, contributing guidance, and release checklist.

## v0.5.0 — Release-readiness features

- Added Save Setup / Load Setup persisted in the FreeCAD document.
- Added side-by-side saved-run comparison.
- Added persistent diagnostics logging.
- Added the repeatable `100 × 10 × 10 mm` validation-bar example.

## v0.4.3 — Editable contact patches and failure regions

- Clicked contact-patch loads can be selected and edited after placement.
- Added translucent contact footprint preview with diameter label.
- CAUTION/FAIL results highlight a clustered likely failure region and plain-language failure mode.

## v0.4.2 — Parallel shell/core slicing and finite contact loads

- Parallelized layer-sliced wall/infill calculations across CPU worker processes.
- Added per-slice watchdog timeouts and conservative fallback behavior.
- Changed clicked loads to finite contact patches by default; entered force is the total load distributed across surface nodes.
- Kept ideal one-node point loads under Advanced settings with singularity warnings.

## v0.4.1 — Non-blocking structure estimator

- Moved expensive CAD shell/core slicing out of the FreeCAD GUI process into a headless `FreeCADCmd` worker.
- Added progress reporting and cancellation.

## v0.3.4 — Wizard visual cleanup

- Simplified the setup UI to frameless collapsible sections with clearer hierarchy.
- Retained tooltips, in-app Help, Simple/Advanced modes, and completion indicators.

## v0.3.0 — UI/Help refresh

- Added searchable Help, hover tooltips, Simple/Advanced UI, and decision-first Results window.

## v0.2.6 — On-demand print-structure calculation

- Stopped expensive wall/infill calculations from running on every field change.
- Added explicit Calculate Print Structure and automatic refresh before Run Analysis.

## v0.2.5 — Layer-sliced shell/core model

- Replaced rough side-area wall estimates with actual 2D CAD cross-section integration perpendicular to build direction.
- Added conservative handling for failed wall offsets.

## v0.2.1 — FDM utilization heat map

- Added directional utilization visualization corresponding to the FDM PASS/FAIL screen.

## v0.2.0 — Layer-aware orthotropic FDM model

- Made the selected build-plate face define local material axes.
- Added orthotropic stiffness and directional FDM stress screening.

## v0.1.x — Guided FEM foundation

- Added FreeCAD workbench, guided model/material/fixed/load workflow, Gmsh meshing, CalculiX solving, non-modal viewport interaction, visual load/build arrows, persistent face highlighting, and recent results.

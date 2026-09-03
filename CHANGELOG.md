# Changelog

## v0.5.9

- Removed the experimental STL → STEP converter, its toolbar command, Help topic, worker, and icon.
- PrintFEA now intentionally targets native FreeCAD solids / STEP-derived CAD geometry for a smaller and more reliable workbench.
- Kept the v0.5 release-readiness features: saved setups, run comparison, diagnostics, validation example, and all v0.4.3 analysis/load/failure-region behavior.
- No intentional changes to solver physics, wall/infill modeling, contact-patch loads, or FDM failure screening.

## v0.5.8

- STL conversion workers hard-exit after safely writing the STEP and result JSON, avoiding lengthy FreeCADCmd teardown of huge faceted solids.
- Successful STL conversion no longer auto-imports the generated STEP into the live FreeCAD GUI.
- Added an explicit **Open converted STEP** action with a warning that dense faceted STEP imports can be slow.
- Closing the converter after 100% completion now leaves the finished STEP on disk without touching the active FreeCAD document.

## 0.5.7

- Fixed STL conversion assembly to use `Part.makeShell()` like FreeCAD's documented/BIM mesh-to-shape paths instead of relying on `Part.Shell(...)` construction.
- Added automatic full exact-triangle-shell fallback when coplanar-region conversion cannot form one closed solid.
- Exact conversion now builds the watertight shell directly from STL facets and skips `removeSplitter()`, avoiding the previous dense-mesh refinement stall.
- Decimation modes use the same exact-shell solidifier when simplification remains watertight.

## 0.5.6

- Fixed Safe planar merge aborting when one coplanar region produces an OCC boundary wire that cannot be turned into a single face.
- Problematic regions now fall back locally to their original exact STL triangles while all other coplanar regions remain merged.
- Added an explicit sewing pass for mixed merged/triangle surfaces and diagnostics for fallback region/face counts.
- Fast/Balanced decimation modes that revert to the original watertight mesh now benefit from the same robust local fallback.

## 0.5.5

- STL → STEP now defaults to topology-preserving coplanar-region conversion instead of triangle decimation.
- Unsafe decimation automatically falls back to the original watertight STL rather than failing.
- Dense curved meshes fail fast with a clear planar-region limit instead of entering an unbounded OpenCASCADE conversion.


## 0.5.4

- Added FEM-oriented dense-STL optimization before mesh-to-Part conversion.
- Added Fast/Balanced/Preserve/Exact conversion-detail presets.
- Balanced mode caps conversion meshes near 12k facets and verifies watertightness plus a 2% volume-change guardrail.
- Avoids feeding very large triangle counts directly into OpenCASCADE `makeShapeFromMesh`, which is documented for relatively small meshes.

## 0.5.3
- STL→STEP conversion no longer runs OpenCASCADE `removeSplitter()` as part of solid construction.
- Coplanar refinement is now optional, off by default, and auto-skipped above 5,000 STL facets.
- Dense STL conversion now prioritizes producing a valid faceted solid STEP quickly; refinement is cosmetic only.

## v0.5.2

- Fixed STL → STEP headless worker not executing under FreeCADCmd because FreeCADCmd does not guarantee `__name__ == "__main__"`.
- STL converter now runs the worker unconditionally, matching the proven PrintFEA structure-worker pattern.
- Added a clearer diagnostic when FreeCADCmd exits successfully but no converter result file is produced.

## 0.5.2
- Fixed STL → STEP background conversion on FreeCAD 1.1.x by passing worker configuration through the child environment instead of as a positional JSON file.
- Fixed the same latent positional-JSON issue in the background print-structure worker.
- Prevents FreeCAD's FEM JSON/YAML mesh importer from accidentally trying to parse PrintFEA worker configuration files.

## 0.5.0 — 2026-09-03
- Added background STL → faceted solid STEP conversion with watertight-solid validation.
- Added Save Setup / Load Setup persisted inside the FreeCAD document.
- Added side-by-side saved-run comparison.
- Added persistent runtime diagnostics log and Help shortcut to its folder.
- Added repeatable 100 × 10 × 10 mm validation-bar example script.
- No intentional changes to v0.4.3 solver physics or FDM screening equations.

## 0.4.3
- Editable contact-patch loads and clustered likely-failure regions.

## 0.4.2
- Parallel background shell/core slicing and finite contact-patch loads.

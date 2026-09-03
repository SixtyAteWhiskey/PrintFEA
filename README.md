# PrintFEA

PrintFEA is a guided FreeCAD workbench for structural screening of FDM 3D-printed parts on Linux. It uses FreeCAD FEM for geometry and result handling, Gmsh for volume meshing, and CalculiX for the structural solve.

The goal is simple: let a user answer **“how is this part printed, how is it held, and what load does it see?”** without manually building the entire FEM tree.

## v0.5.9 — Release-readiness cleanup

v0.5.9 keeps the validated solver physics unchanged and removes the experimental STL → STEP conversion utility. PrintFEA now intentionally focuses on native FreeCAD solids and STEP-derived CAD geometry, which is substantially more reliable for face selection, meshing, and structural analysis than converting dense triangle meshes into huge faceted B-Reps.

The release-readiness features remain: **Save Setup / Load Setup**, saved-run **Compare**, persistent diagnostics, the in-app Help system, and the repeatable `examples/create_test_bar.py` validation model. Saved configurations live inside the FreeCAD document and preserve model/process settings, build orientation, constraints, distributed loads, clicked contact loads, mesh quality, and target safety factor.

## v0.4.3 — Editable contact patches + failure regions

v0.4.3 makes clicked contact-patch loads editable after placement: select a saved load, change total force, direction, contact diameter, or ideal-point mode, then click **Update selected**. The 3D preview now draws a translucent footprint disk with its diameter label. CAUTION/FAIL results now highlight a small cloud of high-utilization FEM nodes around the governing hotspot and label the predicted mode in plain language (for example **Layer separation**, **Inter-layer shear**, or **Along-layer normal stress**).

## v0.4.2 — Parallel structure slicing + finite contact patches

v0.4.2 runs independent print-structure slices concurrently across CPU processes (Auto uses up to four) and applies a per-slice watchdog. A pathological OCCT slice that exceeds the Advanced **Slow-slice limit** is terminated and conservatively receives no additional dense-shell credit instead of stalling the entire structure estimate indefinitely. GPU acceleration is not used because these OCCT B-Rep slice/offset operations are CPU-bound.

Clicked loads now default to a finite **contact patch**. The force entered by the user is the **total force** for that click; after meshing, PrintFEA finds compatible surface nodes inside the entered contact diameter and distributes the total force across them with normalized CalculiX `*CLOAD` entries. An **Ideal mathematical point load** remains available under Advanced, but it deliberately places the entire force on one node and therefore creates a local stress singularity.

## v0.4.1 — Non-blocking structure calculation

v0.4.2 parallelizes the background layer-sliced wall/infill estimator across independent CPU processes and adds a per-slice watchdog so pathological OCCT offsets cannot stall indefinitely. GPU acceleration is not used because these B-Rep operations are CPU/OCCT-bound. v0.4.1 moved the expensive estimator out of FreeCAD's GUI process. PrintFEA exports the selected exact B-Rep to a temporary file and launches the matching `FreeCADCmd` executable as a headless worker. Slice/offset progress is streamed back to the wizard, and a **Cancel** button can terminate the worker. A pathological OCCT slice may still take time in the child process, but it can no longer block FreeCAD's main Qt event loop or trigger the desktop's “FreeCAD is not responding” dialog. If **Run Analysis** needs a stale print-structure estimate, PrintFEA starts the same background calculation and automatically continues into meshing/CalculiX only after the result returns successfully. Changing structure settings while a calculation is running cancels the obsolete worker rather than applying stale geometry data.

## v0.4.0 — Point forces + failure-location highlighting

v0.4.2 changes clicked loads to finite contact patches by default: the entered force is the TOTAL force and is distributed across nearby surface mesh nodes inside a user-set diameter. An advanced ideal one-node point mode remains available. v0.4.0 added arbitrary clicked point forces and automatic likely-failure-region highlighting. Point forces can be placed by clicking anywhere on the CAD model surface; each click stores its own force magnitude/direction and is mapped to the nearest compatible FEM node after meshing, then written to CalculiX as a nodal `*CLOAD`. Multiple point forces can be combined with the existing distributed face load. Because mathematical point loads create local stress singularities, PrintFEA warns users not to interpret stress immediately at the loaded node as a physical contact stress.

CAUTION and FAIL results now automatically show a 3D `LIKELY FAILURE` marker at the representative high-utilization region associated with the screening verdict. The marker is explicitly an approximate FEM hotspot rather than a predicted crack path; Advanced Details preserve both representative and absolute peak locations for mesh-convergence review.

## v0.3.4 — Cleaner frameless wizard sections

v0.3.4 keeps the responsive structure sampling introduced in v0.3.3 but removes the visually noisy card borders from the setup wizard. Collapsible sections now use a bold frameless header, whitespace/indentation, and a subtle divider. A theme-style cascade in the previous card implementation could also make ordinary labels such as Force and Direction appear boxed; v0.3.4 scopes section styling to the section body itself so labels and controls retain FreeCAD's native appearance. Solver physics and geometry estimation are unchanged.

## v0.3.3 — Responsive structure sampling + wizard polish

v0.3.3 keeps the v0.3.2 visual polish and reduces UI stalls during difficult wall/infill geometry calculations. The layer-sliced estimator now defaults to a Balanced 48-slice budget (Fast 24 and High accuracy 96 are available in Advanced mode), and Qt events are pumped between individual slice and wall-offset operations rather than only once per slice. The analysis physics are otherwise unchanged. Numbered setup sections are now collapsible cards with clearer completion indicators, spacing is tighter, common face-selection actions use compact button rows, form labels share the same hover help as their fields, and the New Analysis toolbar icon is now a purpose-built meshed-part + load-arrow symbol instead of the old mountain placeholder. The v0.3 UI remains concise and decision-oriented: technical controls live behind an Advanced toggle, nearly every field has a hover tooltip, the toolbar includes searchable in-app Help, and required setup sections show completion indicators. The underlying analysis remains the validated layer-aware orthotropic + slicer-style layer-sliced shell/core workflow. The expensive geometry estimator still runs only on demand or once when Run Analysis needs a stale estimate. Changes to walls, infill, line width, layer height, or build orientation now mark the estimate as **Needs refresh**; the user can explicitly click **Calculate print structure**, and **Run Analysis** automatically refreshes a stale estimate once before meshing. The calculation yields back to Qt between sampled layers so the FreeCAD window stays responsive. PrintFEA samples the CAD solid perpendicular to the captured build direction, reconstructs each planar section including holes/islands, and offsets that section inward one line width per requested wall. The remaining cross-sectional core is integrated through the part. This prevents opposing walls from being double-counted and handles holes, ribs, thin regions, and build orientation more like a real slicer. The remaining core is homogenized according to infill density, and the resulting stiffness/strength multipliers affect both the CalculiX solve and FDM safety screen.

In the default **Layer-aware orthotropic** mode:

- the captured **BUILD PLATE / BOTTOM** face defines the layer-stack direction;
- local material axes **1 and 2** lie in the printed layer plane;
- local material axis **3** points through the layer stack / build direction;
- CalculiX receives orthotropic engineering constants (`E1/E2/E3`, Poisson ratios, and `G12/G13/G23`);
- CalculiX is asked to return stresses in those same local material axes;
- PrintFEA separately evaluates in-layer normal stress, through-layer normal stress, in-layer shear, and inter-layer shear;
- the results window reports the governing FDM failure mode and directional utilization.

A legacy **Isotropic conservative** mode remains available for comparison and troubleshooting.

> **Important:** PrintFEA is a design-screening tool, not engineering certification. The built-in filament profiles are generic conservative approximations, not manufacturer- or printer-specific coupon data. v0.3.3 uses real CAD cross-sections for the shell/core volume split, but it still solves one homogenized continuum; it does not explicitly mesh every extrusion path, infill cell, separate top/bottom skin stack, void, seam, support interface, or print defect.

## What v0.3.3 can do

- STEP / FreeCAD solid workflow (one valid solid per analysis).
- Non-modal guided setup while the FreeCAD 3D viewport remains interactive.
- PLA, PETG, ASA, ABS, and Nylon/PA generic screening profiles.
- Experimental **wall count + infill** homogenization that affects stiffness, displacement, directional allowables, utilization, and safety factor.
- User-entered **wall line width** (default 0.42 mm) so perimeter count maps to an estimated shell thickness.
- Layer-sliced 2D perimeter/core measurement using the actual CAD solid; failed wall passes keep the last valid core conservatively, and a total slicing failure falls back to no shell credit rather than the old area estimate.
- Results report estimated effective material fraction and the applied stiffness/strength scaling.
- Default layer-aware orthotropic material model plus legacy isotropic mode.
- Capture the actual face that sat on the printer bed.
- 3D **BUILD UP** direction preview with flip control and adjustable arrow size.
- Persistent build/fixed/load face highlighting.
- Add, remove, or clear captured faces without restarting the analysis.
- Fixed / mounting face capture.
- Loaded face capture.
- Force magnitude in Newtons and ±global X/Y/Z direction.
- 3D **FORCE** preview with adjustable arrow size.
- Fast / Normal / Fine automatic Gmsh meshing.
- Gmsh high-order optimization and an automatic **refined first-order** fallback for nonpositive-Jacobian failures.
- Automated CalculiX solve with useful error reporting instead of only generic error 201.
- Automatic newest-result isolation and opening of `Pipeline_CCX_Results`.
- Native stress and displacement color maps with explicit unit guidance.
- Independent, reopenable **Analysis Results** window.
- **View Recent PrintFEA Results** toolbar/menu command.
- Saved per-run summary data in the FreeCAD document.
- Maximum displacement and peak / 99th-percentile von Mises stress.
- Directional FDM utilization and safety-factor screening in layer-aware mode.
- Governing mode reporting such as in-layer normal, through-layer/layer-separation, or inter-layer shear.

## Wall / infill model (v0.3.0 experimental)

PrintFEA does **not** generate individual infill cells. Instead it performs a slicer-style cross-section integration aligned to the captured build direction:

1. Estimate the physical layer count from the model span and entered layer height.
2. Sample up to 128 evenly-spaced print-layer cross-sections (every physical layer is used when there are 128 or fewer).
3. Reconstruct each planar material region from FreeCAD `TopoShape.slice()` wires using the Bullseye face maker, preserving holes and islands.
4. Offset that 2-D section inward **one wall line width at a time**, once per requested wall.
5. Integrate the remaining core areas through the model to obtain core volume. `shell volume = exact CAD volume - integrated core volume`.
6. Only the remaining core receives the entered infill-density reduction.
7. Effective stiffness and strength are reduced from the 100%-structural-fill material profile. Sparse-core stiffness receives a somewhat stronger nonlinear penalty than strength; both converge to the base material at 100% infill.

This is much closer to slicer perimeter logic than the old whole-solid offset or side-area estimate. Opposing walls do not double-count the same region, holes carry their own perimeters, and thin ribs naturally lose core as successive perimeter offsets consume the cross-section. Build orientation also changes the sampled slices.

If an individual 2-D wall offset cannot be completed, PrintFEA **keeps the last valid core area for that slice** and stops crediting additional walls there. That is deliberately conservative. If a slice itself cannot be reconstructed, it receives no perimeter-shell benefit. If the entire slice calculation fails, PrintFEA falls back to a conservative **no-shell** model rather than the optimistic legacy side-area estimate. Saved results record sample count, estimated print-layer count, partial-wall slices, section failures, and integration error.

This remains a screening approximation. PrintFEA does not yet collect separate top/bottom solid-layer counts or explicitly reproduce slicer path planning, overlap, gap fill, infill pattern, raster angle, seams, or voids.


### Performance / UI behavior

The layer-sliced estimator can be computationally expensive on detailed imported B-Reps. PrintFEA therefore **does not run it live** when a spinbox changes or when the BUILD PLATE face is captured. Those actions only invalidate the cached estimate. Use **Calculate print structure** when you want to preview the shell/core result. If the estimate is stale when **Run Analysis** is pressed, PrintFEA calculates it once before creating the FEM objects and reuses that exact estimate for the solve. Geometry-only slice results are cached, so changing only infill percentage can be refreshed very quickly after the first wall/core calculation.

## How the v0.2 failure screen works

FreeCAD's usual von Mises stress map is still useful for finding structural hotspots, but von Mises is an isotropic yield measure and does not by itself describe the weak layer direction of an FDM print.

For a layer-aware run, PrintFEA instead evaluates the CalculiX stress tensor in the print-material coordinate system:

| Local stress | PrintFEA interpretation |
| --- | --- |
| `S11`, `S22` | Normal stress within the layer plane |
| `S33` | Normal stress through the layer stack |
| `S12` | In-layer shear |
| `S13`, `S23` | Inter-layer shear |

For each result location, PrintFEA computes a conservative maximum-stress utilization:

`utilization = max(|directional stress| / directional allowable)`

The **99th-percentile utilization** controls the primary screening safety factor:

`screening safety factor = 1 / P99 utilization`

The absolute peak utilization is reported separately. This prevents one singular or mesh-sensitive node from silently controlling the entire broad-field verdict while still warning the user when a localized peak exceeds an allowable.

Interpretation:

- utilization `< 1.0` means the directional stress is below that generic screening allowable;
- utilization `= 1.0` reaches the selected directional allowable;
- utilization `> 1.0` exceeds it;
- **PASS** additionally requires the screening safety factor to meet the user's target (default `2.0`).

A concentrated peak can still produce **CAUTION** even when the P99 field passes.

### FDM utilization heat map

Layer-aware runs also create a persistent FreeCAD FEM Calculator Filter containing the same maximum directional stress/allowable ratio used by the numerical screening logic. This lets users see the layer-aware failure utilization directly on the model instead of inferring FDM risk from von Mises stress.

The **Show FDM UTILIZATION** result view is dimensionless: `1.0` is the selected directional allowable, values below `1.0` are below that allowable, and values above `1.0` exceed it. The target safety-factor threshold is `1 / target safety factor` (for example, `0.5` for a target safety factor of `2.0`).

## Generic material model

The built-in material presets intentionally favor conservative screening rather than pretending to be exact filament datasheets. Each profile starts with an in-plane modulus and allowable and applies lower cross-layer stiffness/strength and inter-layer shear values.

These values should eventually be replaced by a **calibrated material profile** derived from coupons printed on the same machine, filament, orientation, layer height, temperature, and process settings used for the real part.

Layer height, wall count, line width, and infill are saved with every run. In v0.3.0, wall count/line width define the sampled-layer perimeter thickness and infill changes the homogenized remaining core. Layer height now sets the estimated physical print-layer count used by the shell/core sampler; very tall/fine-layer parts are sampled at a capped number of evenly spaced cross-sections for performance.

## Requirements

- FreeCAD 1.1.x
- Python provided by FreeCAD
- Gmsh
- CalculiX (`ccx` / `calculix-ccx` package depending on distribution)

On Debian/Ubuntu-family systems the solver/mesher packages are commonly installed with:

```bash
sudo apt update
sudo apt install gmsh calculix-ccx
```

FreeCAD itself may come from your distro, AppImage, Flatpak, Conda, or another supported package. Verify the CalculiX executable under:

`Edit -> Preferences -> FEM -> CalculiX`

## Installation

FreeCAD's current user-data directory can be checked in its Python console:

```python
print(App.getUserAppDataDir())
```

For the tested FreeCAD 1.1 Linux layout:

```text
~/.local/share/FreeCAD/v1-1/
```

For a downloaded PrintFEA v0.5.9 ZIP:

```bash
rm -rf ~/.local/share/FreeCAD/v1-1/Mod/PrintFEA
unzip -o ~/Downloads/PrintFEA-v0.5.9.zip -d ~/.local/share/FreeCAD/v1-1/Mod/
```

Restart FreeCAD. **PrintFEA** should appear in the workbench selector.

The expected layout is:

```text
~/.local/share/FreeCAD/v1-1/Mod/PrintFEA/
├── Init.py
├── InitGui.py
├── PrintFEACommands.py
├── README.md
├── fem/
├── gui/
├── materials/
├── post/
└── Resources/
```

`package.xml` currently ships as `package.xml.disabled` because the classic Python-workbench loader is the tested/reliable startup path on the target FreeCAD 1.1 installation.

## Using PrintFEA

1. Import a STEP file or open a FreeCAD document containing one valid solid.
2. Select the solid object.
3. Switch to **PrintFEA**.
4. Click **New FDM Stress Analysis**.
5. Choose the filament profile.
6. Leave **Layer-aware orthotropic (recommended)** selected unless intentionally testing legacy behavior.
7. Record layer height, walls, and infill.
8. Select the flat face that physically sat on the printer build plate and capture it as **BUILD PLATE / BOTTOM**.
9. Verify the green **BUILD UP** arrow points the way the printer actually built the part. Flip it if necessary.
10. Capture the mounting/support faces as **FIXED**.
11. Capture the face(s) where the external load acts as **LOADED**.
12. Enter force magnitude and direction and verify the red force arrow.
13. Choose mesh quality and target safety factor.
14. Click **RUN ANALYSIS**.

After a successful run PrintFEA isolates the newest result pipeline and opens the independent results window. Closing the setup wizard does not close results. Use **View Recent PrintFEA Results** to reopen a saved run.

## Why PrintFEA patches the CalculiX input file in v0.2

CalculiX supports orthotropic engineering constants and local material orientations. FreeCAD's standard solid-material path used by PrintFEA currently writes the ordinary isotropic elastic material definition. For v0.2, PrintFEA therefore lets FreeCAD build the normal CalculiX input deck and then performs a narrow deterministic patch immediately before launching `ccx`:

1. replace the single isotropic `*ELASTIC` block with `*ELASTIC, TYPE=ENGINEERING CONSTANTS`;
2. add the captured print-material `*ORIENTATION`;
3. attach that orientation to the solid section;
4. request local-axis stress output with `*EL FILE, GLOBAL=NO`.

This is intentionally limited to PrintFEA's current **one solid / one material** workflow. If the expected input cards are not found, the run is stopped instead of silently solving the wrong material model.


### Mesh fallback safety note

PrintFEA normally solves with curved second-order tetrahedra. If CalculiX rejects that mesh for nonpositive Jacobians, v0.2 retries with a **finer first-order tetrahedral mesh**. It intentionally does not use FreeCAD's `SecondOrderLinear` fallback path because current FreeCAD 1.1.x has a confirmed face-load-writing issue associated with that setting. First-order tetrahedra can be artificially stiff, so a fallback run is labeled clearly and should be compared with a successful Normal/Fine second-order run when accuracy matters.

## Understanding the color map

PrintFEA can display three result views:

- **FDM utilization** — the layer-aware directional stress/allowable ratio used by PrintFEA's failure screen;
- **von Mises stress** — useful for finding general structural stress hotspots;
- **displacement magnitude** — useful for seeing how much the part moves.

For the FDM-utilization view, `1.0` means the selected directional allowable has been reached. A target safety factor of `2.0` corresponds to utilization `0.5`. FreeCAD auto-ranges the native color scale to the current result, so red means the highest utilization in that run, not automatically failure.

The native pipeline uses SI units on its color bar, while PrintFEA's summary shows convenient engineering units:

- native stress scale: `Pa`; PrintFEA summary: `MPa`;
- native displacement scale: `m`; PrintFEA summary: `mm`.

**Red means the highest value in the displayed result, not automatically failure.** In layer-aware mode the PASS/CAUTION/FAIL verdict is based on the local directional stress components and directional allowables, so the governing layer-risk location can differ from the reddest von Mises location.


## Current analysis limitations

PrintFEA v0.2 is still a screening model. Important limitations include:

- one homogenized orthotropic solid, not explicit deposited roads/layers;
- generic material properties unless the user later supplies calibrated data;
- no explicit wall/perimeter geometry;
- no sparse-infill topology or infill-pattern mechanics;
- no automatic raster-angle model;
- no seam, void, under-extrusion, warping, or adhesion-defect model;
- no support-interface effects;
- no temperature dependence;
- no creep/viscoelasticity;
- no fatigue or impact model;
- no buckling/nonlinear/contact workflow yet;
- fixed-face and applied-face idealizations can create local stress singularities;
- results remain mesh-sensitive near sharp corners, constraints, and load boundaries.

For important parts, compare Normal/Fine meshes and calibrate the material model against physical coupons or destructive tests.

## Roadmap

### v0.2.x — analysis quality

- Validate layer-aware CalculiX input/output across more FreeCAD/CalculiX builds.
- Validate and refine FDM utilization / safety-factor heat-map behavior across more FreeCAD builds.
- Optional tensile vs compression directional allowables.
- Better hotspot region averaging and convergence checks.
- Gravity / attached-mass load presets.
- Remote center-of-gravity moments.

### v0.3

- Custom printer + filament calibrated profiles.
- Coupon-calibration wizard.
- Wall/infill homogenization model.
- Raster/perimeter direction controls where useful.
- Local mesh refinement around holes, fillets, constraints, and loaded faces.

### Later

- Nonlinear/contact/buckling workflows where appropriate.
- Automatic HTML/PDF analysis report.
- Comparison of multiple print orientations in one run.

## Development notes

PrintFEA intentionally sits on top of FreeCAD's FEM stack instead of implementing another CAD mesher or finite-element solver. FreeCAD owns the model and result objects, Gmsh owns the volume mesh, and CalculiX performs the structural solve.

## License

MIT

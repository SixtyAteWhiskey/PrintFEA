# PrintFEA

PrintFEA is a guided FreeCAD workbench plugin for stress/force simulation of FDM 3D-printed parts. It uses FreeCAD FEM for geometry and result handling, Gmsh for volume meshing, and CalculiX for the structural solve.

This simplifies that process into an intuitive GUI that allows the user to see how a 3D printed part handles a load based on: **“how the part is printed, how its held, and what load it encounters.”** All without manually building the entire FEM tree.

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

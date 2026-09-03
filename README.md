# PrintFEA

**Guided, FDM 3D printed part structural stress test screening in FreeCAD.**

## AI DISCLAIMER

This was made using ChatGPT pro.

I did the validation testing.

## Its a Wrapper

PrintFEA is a FreeCAD workbench plugin that wraps FreeCAD FEM, Gmsh, and CalculiX in a guided workflow for FDM 3D-printed parts. 

It is designed for users who want to answer practical questions such as:

- Where is this part fixed?
- Where and how is it loaded?
- How much will it move?
- Which print direction is weakest?
- Does the part meet a chosen safety-factor target?
- Where is the likely high-utilization/failure region?

PrintFEA is a **stress-test screening tool**, not engineering certification. 

The built-in filament profiles are conservative generic approximations and should not be treated as manufacturer-specific material certificates.

<img width="1500" height="593" alt="results-fdm-safety" src="https://github.com/user-attachments/assets/be8e2ef8-91e9-43d8-9c3a-309a8d7c0345" />


## Highlights

- Guided, non-modal analysis wizard while the FreeCAD viewport remains interactive.
- STEP/native FreeCAD solid workflow with persistent build/fixed/load face highlighting.
- Layer-aware **orthotropic FDM material model** aligned to the selected build-plate face.
- Generic conservative PLA, PETG, ASA, ABS, and Nylon/PA screening profiles.
- Built-in isotropic **PrintFEA Validation Material** for analytical solver verification.
- User material manager for creating, duplicating, editing, and persisting custom material profiles.
- Layer-sliced wall/infill shell-core estimator that uses actual CAD cross-sections.
- Parallel background structure slicing with watchdog timeouts for pathological slices.
- Distributed face loads plus multiple editable clicked **contact-patch loads**.
- Contact-patch loads treat the entered value as the **total force**, distributed across surface mesh nodes inside the selected diameter.
- Optional advanced ideal one-node point loads for textbook/global load-path studies.
- Automatic Gmsh meshing + CalculiX solve.
- FDM directional utilization heat map, von Mises stress, and movement views.
- Decision-first **PASS / CAUTION / FAIL** result summary.
- Likely failure-region highlighting for CAUTION/FAIL results.
- Saved setups, recent-result reopening, and side-by-side run comparison.
- In-app Help, hover tooltips, and persistent diagnostics logging.

<img width="1500" height="743" alt="contact-load-setup" src="https://github.com/user-attachments/assets/fa86ee85-7208-4d8e-85ee-33ed1b5f7fd2" />


## Status

**Current release: v0.6.2**

v0.6.2 makes the installed version visible directly in the analysis wizard and adds a prominent **Self-Test** button there. v0.6.1 introduced the analytical cantilever self-test with dedicated validation results, automatic load-linearity checks, and mesh-convergence checks; v0.6.0 introduced the validation material and custom material manager.

<img width="746" height="105" alt="Screenshot from 2026-09-03 12-32-00" src="https://github.com/user-attachments/assets/8a18df0a-0cea-4b60-9ffe-afe1d7f030dd" />

<img width="1793" height="762" alt="Screenshot from 2026-09-03 12-34-28" src="https://github.com/user-attachments/assets/cdee416c-bca2-4e7a-aebe-2f97e3c56386" />


### Tested environment

- Linux
- FreeCAD 1.1.3
- FreeCAD FEM workbench available
- Gmsh available to FreeCAD FEM
- CalculiX/CCX available to FreeCAD FEM

Other platforms and FreeCAD versions may work, but are not yet the primary tested target.

## Requirements

PrintFEA expects:

1. FreeCAD with FEM support.
2. Gmsh for tetrahedral meshing.
3. CalculiX (`ccx`) for the structural solve.
4. A valid native FreeCAD solid or STEP-derived solid.

**STEP is strongly recommended.** STL is intentionally not converted inside PrintFEA: STL triangulation has already lost the original CAD faces/surfaces, and dense mesh-to-BRep conversion is unreliable and often counterproductive for FEM setup.

## Installation

PrintFEA currently uses **manual installation**. `package.xml` remains intentionally disabled in this release while FreeCAD 1.1 add-on metadata/loading behavior is validated further.

### 1. Find your FreeCAD user directory

In FreeCAD's Python console:

```python
print(App.getUserAppDataDir())
```

On the tested FreeCAD 1.1 Linux installation this is:

```text
~/.local/share/FreeCAD/v1-1/
```

### 2. Install the workbench

For the direct-install ZIP:

```bash
mkdir -p ~/.local/share/FreeCAD/v1-1/Mod
unzip -o PrintFEA-v0.6.2-install.zip -d ~/.local/share/FreeCAD/v1-1/Mod/
```

The final layout should contain:

```text
~/.local/share/FreeCAD/v1-1/Mod/PrintFEA/Init.py
~/.local/share/FreeCAD/v1-1/Mod/PrintFEA/InitGui.py
```

Restart FreeCAD completely. **PrintFEA** should appear in the workbench selector.

### Updating

Close FreeCAD first, then replace the existing folder:

```bash
rm -rf ~/.local/share/FreeCAD/v1-1/Mod/PrintFEA
unzip -o PrintFEA-v0.6.2-install.zip -d ~/.local/share/FreeCAD/v1-1/Mod/
```

## Quick Start

1. Open/import a valid solid (STEP recommended).
2. Switch to the **PrintFEA** workbench.
3. Click **New FDM Stress Analysis**.
4. Select the model.
5. Choose filament, layer height, walls, and infill.
6. Select the face that physically touches the printer build plate.
7. Capture mounting/fixed faces.
8. Add a distributed face load, one or more clicked contact-patch loads, or both.
9. Choose mesh quality and target safety factor.
10. Run the analysis.

The Results window opens independently and can be reopened later using **View Recent PrintFEA Results**.

## Loads

### Distributed face load

Use this when a real load is spread over a known face or interface.

### Clicked contact-patch load

Click anywhere on the CAD surface, choose the force magnitude/direction and a contact diameter, and PrintFEA distributes the **total force** across compatible surface mesh nodes inside that patch.

This is normally preferable to an ideal point load for local strength screening because a mathematical point load creates a stress singularity.

### Ideal mathematical point load

Available under Advanced settings. The entire force is applied to one FEM node. Useful for idealized/global deformation studies, but local stress at that node is mesh-sensitive and should not be interpreted as physical contact stress.

## Materials and Validation Preset

The **Filament / material** selector now combines built-in screening profiles with user-created material profiles. Click **Materials…** in the wizard or use **PrintFEA → Manage PrintFEA Materials** to create, duplicate, edit, or delete custom profiles.

Custom materials are stored outside the workbench folder at:

```text
<FreeCAD user data>/PrintFEA/materials.json
```

This means upgrading/replacing the PrintFEA workbench does not delete your custom material library. Built-in profiles are read-only; duplicate one when you want a starting point.

### PrintFEA Validation Material

`PrintFEA Validation (E=2000 MPa, nu=0.35)` is a locked isotropic verification preset. Selecting it automatically disables wall/infill homogenization and selects isotropic mode.

For the quickest verification, choose **PrintFEA → Run PrintFEA Validation Self-Test**. PrintFEA automatically solves five standard cases and reports:

- analytical displacement/stress error for each run;
- 5/10/20 N load linearity;
- Fast/Normal/Fine mesh convergence at 10 N.

Validation runs use a dedicated result layout instead of the normal FDM safety-factor screen.

Use **PrintFEA → Create PrintFEA Validation Bar** to create the standard `100 × 10 × 10 mm` bar. For a `100 mm` free cantilever length, fix one `10 × 10 mm` end face and apply a `10 N` distributed transverse load to the opposite end face. Euler-Bernoulli beam theory gives:

- second moment of area `I = 833.333 mm^4`;
- tip displacement `δ = 2.000 mm`;
- nominal root bending stress `σ = 6.000 MPa`.

A 3D solid FEM model can differ slightly because it includes shear and other 3D effects. Verify **mesh convergence** (Normal → Fine) and use displacement as the cleaner solver-verification metric rather than expecting the absolute peak fixed-edge node stress to equal exactly `6.000 MPa`.

See [Materials](docs/MATERIALS.md) and [Validation](docs/VALIDATION.md).

## Print Orientation and FDM Material Model

The selected **BUILD PLATE / BOTTOM** face defines the layer-stack direction:

- local axes 1/2: in the printed layer plane;
- local axis 3: through the layer stack/build direction.

In layer-aware mode, CalculiX receives orthotropic elastic properties and PrintFEA evaluates directional normal/shear utilization rather than using von Mises alone for the FDM PASS/FAIL screen.

The simple result names translate the raw material-axis components into language such as:

- Along-layer normal stress
- Layer separation / through-layer normal stress
- In-layer shear
- Inter-layer shear

## Walls and Infill

PrintFEA does not use a naive `40% infill = 40% strength` rule.

When wall/infill modeling is enabled, a background worker samples actual CAD cross-sections perpendicular to the build direction, offsets each section inward by the requested wall line width/count, and estimates:

- dense perimeter-shell fraction;
- remaining sparse core fraction;
- effective material fraction at the selected infill percentage.

That estimate modifies the homogenized stiffness/strength used by the screening model.

The worker runs outside the main FreeCAD GUI, can use multiple CPU processes, and applies conservative timeouts to individual pathological slices.

See [Validation](docs/VALIDATION.md) for a simple analytical box check of this model.

## Understanding Results

The default Results window is intentionally decision-first:

- **Safety factor** — representative directional margin relative to the selected target.
- **Maximum movement** — maximum displacement magnitude.
- **Print structure** — walls/infill and estimated effective material fraction.
- **Closest failure mode** — governing FDM directional stress mode.
- **Representative load** — 99th-percentile directional utilization used for the overall screening verdict.
- **Worst local hotspot** — absolute local peak utilization, retained as a warning because FEM peaks near constraints/load boundaries can be mesh-sensitive.

### Verdicts

- **PASS** — representative region meets the selected safety-factor target.
- **CAUTION** — close to the selected target; inspect highlighted regions and consider mesh convergence.
- **FAIL** — representative utilization exceeds the screening margin/allowable.

### Result views

- **FDM SAFETY** — directional utilization relative to the selected FDM allowables.
- **STRESS** — von Mises stress visualization.
- **MOVEMENT** — displacement magnitude.

For FDM Safety, `1.0` means the selected directional allowable has been reached. A target safety factor of `2.0` corresponds to utilization `0.5`.

The native FreeCAD color scale auto-ranges, so **red means highest in the current run, not automatically failure**.

## Likely Failure Region

CAUTION/FAIL runs highlight a small cluster of high-utilization nodes near the governing hotspot and label the likely mode. This is an approximate FEM hotspot region, **not a predicted exact crack path**.

## Save, Reload, and Compare

- **Save Setup** stores the model reference, print settings, build face, fixed/load faces, contact loads, mesh quality, and target safety factor inside the FreeCAD document.
- **Load Setup** restores a saved configuration.
- **Compare** in the Results window compares saved runs side by side.

If the underlying CAD topology changes and FreeCAD renumbers faces, saved face references may need to be recaptured.

## Validation

A repeatable `100 × 10 × 10 mm` test bar is included in `examples/create_test_bar.py`.

The layer-sliced wall/core estimator has been checked against analytical shell/core fractions for this simple geometry, including 4-wall and 8-wall cases at 0.42 mm line width. See [docs/VALIDATION.md](docs/VALIDATION.md).

This validates the shell/core geometry calculation on a simple shape; it does **not** validate generic filament allowables against every real printer/filament/process combination.

## Known Limitations

PrintFEA intentionally remains a screening tool. Current limitations include:

- generic conservative material profiles rather than printer/filament-specific coupon calibration;
- one homogenized continuum rather than explicit extrusion roads, seams, voids, raster paths, or individual infill cells;
- no separate top/bottom skin-stack model yet;
- no explicit layer adhesion defects, moisture effects, print temperature, cooling, annealing, or aging model;
- no nonlinear plasticity/fracture/damage model;
- no buckling/contact/nonlinear workflow yet;
- simple fixed supports may over-constrain real pins, bolts, bearings, or compliant joints;
- local peaks at sharp corners, fixed boundaries, and ideal point loads can be singular/mesh-sensitive;
- built-in material allowables are screening assumptions, not certification data;
- STEP/native solids are the supported geometry path; STL conversion is out of scope.

## Mesh Quality and Convergence

For important results, compare Normal/Fine runs. Representative utilization should remain reasonably stable as the mesh changes. A peak node that rises dramatically while the representative region stays stable is often a numerical/local stress concentration rather than a useful global failure metric.

PrintFEA normally uses curved second-order tetrahedra. If the mesh cannot be solved due to bad Jacobians, the fallback route is labeled in the Results window.

## Diagnostics

PrintFEA writes a diagnostics log under the FreeCAD user-data directory:

```text
<FreeCAD user data>/PrintFEA/printfea.log
```

The in-app Help window includes **Open Diagnostics Folder**.

When filing a bug, include:

- PrintFEA version;
- FreeCAD version;
- Linux distribution;
- Gmsh/CalculiX versions if known;
- exact reproduction steps;
- relevant diagnostics output;
- whether the issue occurs with the included validation bar.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Materials](docs/MATERIALS.md)
- [Validation](docs/VALIDATION.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## Contributing

Bug reports and focused pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first, especially the requirement to preserve conservative behavior when a geometry/solver shortcut fails.

## License

MIT License. See [LICENSE](LICENSE).

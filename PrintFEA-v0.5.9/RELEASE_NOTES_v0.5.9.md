# PrintFEA v0.5.9

v0.5.9 is the first public-release-prep baseline for PrintFEA: a guided, layer-aware FDM structural-screening workbench for FreeCAD.

## Key capabilities

- Guided STEP/native-solid FEM setup.
- Layer-aware orthotropic FDM screening.
- Layer-sliced wall/infill shell-core estimation.
- Parallel background geometry workers with slow-slice fallback.
- Distributed face loads and multiple editable finite contact-patch loads.
- FDM Safety utilization map plus stress and movement views.
- PASS/CAUTION/FAIL summary and likely failure-region highlighting.
- Saved setups, recent results, and run comparison.
- In-app Help and diagnostics logging.

## Scope change

The experimental STL → STEP converter has been removed. Public v0.5.9 intentionally focuses on native FreeCAD/STEP solids, where CAD topology and FEM face selection are much more reliable.

## Validation

The included `100 × 10 × 10 mm` test bar reproduces analytically expected shell/core fractions for simple 4-wall and 8-wall cases. See `docs/VALIDATION.md`.

## Important limitations

PrintFEA is a design-screening tool, not certification. Built-in filament profiles are generic conservative assumptions and do not replace material testing for a specific printer, filament, print settings, environment, or safety-critical application.

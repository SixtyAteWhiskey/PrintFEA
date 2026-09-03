# Contributing to PrintFEA

Thanks for helping improve PrintFEA.

## Principles

1. **Conservative failure behavior beats silent optimism.** If a geometry/solver shortcut fails, do not quietly grant extra strength or shell credit.
2. **Keep the default UI decision-first.** Engineering detail belongs in Advanced/Help unless it is required to avoid a misleading result.
3. **Do not hide assumptions.** Material allowables, fallbacks, mesh modes, and approximations should remain inspectable.
4. **Preserve total applied load.** Multi-node/contact-patch load distribution must normalize so the vector sum matches the requested total force.
5. **Avoid blocking the FreeCAD GUI.** Expensive geometry work should run in background worker processes when practical.

## Development environment

The primary tested target is Linux + FreeCAD 1.1.x with Gmsh and CalculiX available to FreeCAD FEM.

## Before submitting a pull request

- Run Python syntax compilation across the workbench.
- Run the validation-bar shell/core checks in `docs/VALIDATION.md` when changing wall/infill logic.
- Exercise a representative STEP analysis end-to-end.
- If changing result screening, compare Normal/Fine mesh behavior and explain how singular/local peaks are treated.
- Update Help/README/CHANGELOG when user-visible behavior changes.

## Bug reports

Please include:

- PrintFEA version;
- FreeCAD version;
- Linux distribution;
- reproduction steps;
- screenshot if useful;
- diagnostics log excerpt;
- whether the issue reproduces on the included validation bar.

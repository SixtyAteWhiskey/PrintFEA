# Release Checklist

Use this before creating a public tag/release.

## Packaging

- [ ] `VERSION` matches the intended tag.
- [ ] `package.xml.disabled` version/date are current.
- [ ] `CHANGELOG.md` contains the release entry.
- [ ] Direct-install ZIP extracts to exactly one `PrintFEA/` folder.
- [ ] No `__pycache__`, `.pyc`, logs, temporary BREP/JSON files, or local FreeCAD documents are included.
- [ ] All Python files pass `python -m compileall`.

## Clean-install smoke test

- [ ] Remove previous `Mod/PrintFEA` directory.
- [ ] Install from release ZIP.
- [ ] Restart FreeCAD.
- [ ] PrintFEA appears in the workbench selector without manual `exec()` calls.
- [ ] New Analysis, Recent Results, and Help toolbar commands appear.

## Wizard / setup

- [ ] Select model.
- [ ] Capture/clear build face.
- [ ] Capture/remove/clear fixed faces.
- [ ] Capture/remove/clear distributed load faces.
- [ ] Add/edit/remove multiple clicked contact-patch loads.
- [ ] Contact footprint diameter preview is visible.
- [ ] Save Setup and Load Setup restore expected values.
- [ ] Run Analysis stays disabled until required setup is complete.

## Structure estimator

- [ ] Background calculation leaves FreeCAD GUI responsive.
- [ ] Cancel works.
- [ ] Parallel worker progress updates.
- [ ] Validation bar 4-wall/40% result is approximately 61.5% effective material.
- [ ] Validation bar 8-wall/40% result is approximately 81.6% effective material.

## Solver / results

- [ ] Gmsh mesh succeeds.
- [ ] CalculiX solve succeeds.
- [ ] FDM Safety view opens.
- [ ] Stress view opens.
- [ ] Movement view opens.
- [ ] Results window survives closing setup wizard.
- [ ] Recent Results reopens a closed result.
- [ ] Compare opens and compares two saved runs.
- [ ] CAUTION/FAIL displays likely failure region.
- [ ] Help from Results opens Understanding Results.

## Diagnostics

- [ ] Diagnostics folder opens from Help.
- [ ] `printfea.log` receives useful runtime entries.

## Release artifacts

- [ ] Create direct-install ZIP.
- [ ] Create source/GitHub ZIP or tag repository.
- [ ] Include release notes.
- [ ] Confirm screenshots contain no personal paths/usernames/private information.

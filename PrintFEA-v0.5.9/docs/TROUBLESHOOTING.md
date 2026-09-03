# Troubleshooting

## PrintFEA does not appear in the workbench list

1. In the FreeCAD Python console run:

```python
print(App.getUserAppDataDir())
```

2. Confirm the workbench files are directly under:

```text
<user data>/Mod/PrintFEA/InitGui.py
```

and not nested one directory too deep.

3. Restart FreeCAD completely.

4. Check:

```python
print("PrintFEAWorkbench" in Gui.listWorkbenches())
```

## Run Analysis is disabled

The wizard status line lists missing required inputs. A normal analysis needs:

- model;
- print orientation/build face;
- at least one fixed face;
- at least one load (distributed face load or clicked contact load).

## Print-structure calculation is slow

The calculation runs in the background and should not freeze the main FreeCAD GUI.

Under Advanced settings:

- use **Fast** or **Balanced** structure sampling;
- use multiple structure workers;
- keep the slow-slice watchdog enabled.

Difficult oblique build orientations can create geometrically complex cross-sections. A timed-out slice is handled conservatively rather than blocking indefinitely.

## CalculiX fails

Use the diagnostics log and FreeCAD Report View/Python console.

Common causes include:

- invalid or poor-quality tetrahedral elements;
- unsupported/invalid solid geometry;
- missing external Gmsh/CalculiX installation;
- malformed constraints/load references after CAD topology changes.

PrintFEA labels mesh fallback mode in Results when an alternate tetrahedral strategy was required.

## Huge local hotspot at a clicked load

Check whether the load is an **Ideal mathematical point load**. One-node loads create stress singularities.

For physical local-strength screening, use a finite **contact-patch load** and choose a contact diameter representative of the actual interface.

## Saved setup references the wrong face after CAD edits

FreeCAD face numbering/topology can change when the model is edited. Recapture build/fixed/load faces and save a new setup.

## Color scale says red but verdict passes

FreeCAD auto-ranges the color map. Red means the highest value in that run, not automatically failure.

Use **FDM SAFETY** and the utilization numbers for the layer-aware PASS/CAUTION/FAIL picture.

## Diagnostics

The log is stored at:

```text
<FreeCAD user data>/PrintFEA/printfea.log
```

The in-app Help window can open the diagnostics folder directly.

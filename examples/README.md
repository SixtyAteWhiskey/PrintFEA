# PrintFEA validation example

`create_test_bar.py` creates a 100 × 10 × 10 mm solid bar in the active FreeCAD document.

This is useful for simple repeatable validation because the wall/core geometry can be checked analytically. For example, 8 walls at 0.42 mm line width leave a 93.28 × 3.28 mm core in each 100 × 10 mm cross-section.

Run the script from FreeCAD's Python console, then use the resulting `PrintFEA Test Bar 100x10x10 mm` object in the normal PrintFEA wizard.

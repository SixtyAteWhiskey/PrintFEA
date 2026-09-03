"""Create the simple PrintFEA validation bar used in the project examples.

Run from FreeCAD's Python console:
    exec(open('/path/to/PrintFEA/examples/create_test_bar.py').read())
"""
import FreeCAD as App
import Part

doc = App.ActiveDocument or App.newDocument("PrintFEA_Test_Bar")
obj = doc.addObject("Part::Feature", "PrintFEA_TestBar")
obj.Label = "PrintFEA Test Bar 100x10x10 mm"
obj.Shape = Part.makeBox(100.0, 10.0, 10.0)
doc.recompute()
print("Created 100 x 10 x 10 mm PrintFEA validation bar.")

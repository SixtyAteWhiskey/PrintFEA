import FreeCADGui as Gui
from FreeCADGui import Workbench


class PrintFEAWorkbench(Workbench):
    MenuText = "PrintFEA"
    ToolTip = "Simplified FDM structural analysis using FreeCAD FEM + CalculiX"

    def Initialize(self):
        import PrintFEACommands  # registers commands
        commands = ["PrintFEA_NewAnalysis", "PrintFEA_ViewResults", "PrintFEA_Help"]
        self.appendToolbar("PrintFEA", commands)
        self.appendMenu("PrintFEA", commands)

    def Activated(self):
        return

    def Deactivated(self):
        return

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(PrintFEAWorkbench())

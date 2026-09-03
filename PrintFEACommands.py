import os
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

# Keep a strong reference to the non-modal setup wizard. Result dialogs are
# managed independently by gui.results_dialog so they survive wizard closure.
_active_dialog = None


class NewAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(os.path.dirname(__file__), "Resources", "icons", "PrintFEA.svg"),
            "MenuText": "New FDM Stress Analysis",
            "ToolTip": "Open the guided PrintFEA analysis wizard",
        }

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        global _active_dialog
        from gui.wizard import PrintFEAWizard

        if _active_dialog is not None:
            try:
                if _active_dialog.isVisible():
                    _active_dialog.raise_()
                    _active_dialog.activateWindow()
                    return
            except RuntimeError:
                _active_dialog = None

        _active_dialog = PrintFEAWizard(Gui.getMainWindow())
        _active_dialog.setModal(False)
        try:
            delete_on_close = QtCore.Qt.WidgetAttribute.WA_DeleteOnClose
        except AttributeError:
            delete_on_close = QtCore.Qt.WA_DeleteOnClose
        _active_dialog.setAttribute(delete_on_close, True)
        _active_dialog.destroyed.connect(_dialog_destroyed)
        _active_dialog.show()
        _active_dialog.raise_()
        _active_dialog.activateWindow()


class ViewRecentResultsCommand:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(os.path.dirname(__file__), "Resources", "icons", "PrintFEAResults.svg"),
            "MenuText": "View Recent PrintFEA Results",
            "ToolTip": "Reopen saved PrintFEA results from the active document",
        }

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        from gui.results_dialog import recent_summary_objects, show_results_dialog

        doc = App.ActiveDocument
        summaries = recent_summary_objects(doc)
        if not summaries:
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(),
                "PrintFEA",
                "No saved PrintFEA results were found in the active document. Run an analysis first.",
            )
            return
        show_results_dialog(doc, summaries[0])




class HelpCommand:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(os.path.dirname(__file__), "Resources", "icons", "PrintFEAHelp.svg"),
            "MenuText": "PrintFEA Help",
            "ToolTip": "Open PrintFEA Help, Quick Start, and result explanations",
        }

    def IsActive(self):
        return True

    def Activated(self):
        from gui.help_dialog import show_help_dialog
        show_help_dialog("Quick Start")

def _dialog_destroyed(*_args):
    global _active_dialog
    _active_dialog = None


Gui.addCommand("PrintFEA_NewAnalysis", NewAnalysisCommand())
Gui.addCommand("PrintFEA_ViewResults", ViewRecentResultsCommand())
Gui.addCommand("PrintFEA_Help", HelpCommand())

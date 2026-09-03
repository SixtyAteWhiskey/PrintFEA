"""Interactive surface-point picking for PrintFEA point loads."""

import FreeCAD as App
import FreeCADGui as Gui


class SurfacePointPicker:
    """Capture one clicked 3D point on a specific document object.

    FreeCAD's selection observer supplies both the selected subelement and the
    exact 3D click position.  PrintFEA uses the subelement later to constrain
    nearest-node mapping to the clicked face/edge/vertex after meshing.
    """

    def __init__(self, model_obj, on_picked, on_cancel=None):
        self.model_obj = model_obj
        self.on_picked = on_picked
        self.on_cancel = on_cancel
        self.active = False

    def start(self):
        self.stop(notify=False)
        Gui.Selection.addObserver(self)
        self.active = True

    def stop(self, notify=False):
        if self.active:
            try:
                Gui.Selection.removeObserver(self)
            except Exception:
                pass
        was_active = self.active
        self.active = False
        if notify and was_active and self.on_cancel:
            try:
                self.on_cancel()
            except Exception:
                pass

    def addSelection(self, doc, obj, sub, pnt):
        if not self.active or self.model_obj is None:
            return
        if str(obj) != str(getattr(self.model_obj, "Name", "")):
            return
        try:
            pos = App.Vector(float(pnt[0]), float(pnt[1]), float(pnt[2]))
        except Exception:
            return
        sub_name = str(sub or "")
        if not sub_name.startswith(("Face", "Edge", "Vertex")):
            return

        # Stop before invoking user code so clearing the normal selection does
        # not accidentally re-enter the picker.
        self.stop(notify=False)
        try:
            Gui.Selection.clearSelection()
        except Exception:
            pass
        if self.on_picked:
            self.on_picked(pos, sub_name)

    # Selection-observer methods that FreeCAD may call on some builds.
    def removeSelection(self, *args):
        return

    def clearSelection(self, *args):
        return

    def setSelection(self, *args):
        return
